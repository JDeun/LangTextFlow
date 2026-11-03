//! Opt-in canonical-event writer into the existing, Python-migrated history schema.
//! SQLite admission failure never gates source, translation or observer publication.
use crate::caption_events::JsonFrame;
use rusqlite::{params, Connection, Error, ErrorCode, OpenFlags, TransactionBehavior};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::Duration;
use time::{format_description::well_known::Rfc3339, OffsetDateTime};
use tokio::sync::{mpsc, oneshot, watch, Semaphore};

const MAX_QUEUE: usize = 32;
const WAIT: Duration = Duration::from_secs(3);
const BUSY: Duration = Duration::from_millis(100);

#[derive(Clone, Default, Debug, Serialize)]
pub struct HistoryMetrics {
    pub accepted: u64,
    pub persisted: u64,
    pub stale: u64,
    pub invalidated: u64,
    pub dropped: u64,
    pub write_errors: u64,
    pub complete: bool,
    pub stopped: bool,
    pub error: Option<&'static str>,
}

enum Command {
    Event(JsonFrame),
    Invalidate(Vec<Invalidation>),
    Flush(oneshot::Sender<()>),
}

pub(crate) struct Invalidation {
    pub id: String,
    pub version: u64,
    pub revision: u64,
}

struct Shared {
    sender: Option<mpsc::Sender<Command>>,
    ended_at: Option<String>,
    metrics: HistoryMetrics,
}

#[derive(Clone)]
pub struct HistorySink {
    shared: Arc<Mutex<Shared>>,
    session_id: String,
    generation: u64,
    started_at: String,
    source: String,
    targets: Vec<String>,
}

impl HistorySink {
    pub fn metrics(&self) -> HistoryMetrics {
        self.shared.lock().unwrap().metrics.clone()
    }

    pub(crate) fn matches(
        &self,
        sid: &str,
        generation: u64,
        source: &str,
        targets: &[String],
    ) -> bool {
        self.session_id == sid
            && self.generation == generation
            && self.source == source
            && self.targets == targets
            && self.shared.lock().unwrap().sender.is_some()
    }

    pub(crate) fn enqueue(&self, event: JsonFrame) {
        self.enqueue_command(Command::Event(event));
    }

    pub(crate) fn invalidate(&self, ids: Vec<Invalidation>) {
        self.enqueue_command(Command::Invalidate(ids));
    }

    fn enqueue_command(&self, command: Command) {
        let mut shared = self.shared.lock().unwrap();
        let sent = shared
            .sender
            .as_ref()
            .is_some_and(|sender| sender.try_send(command).is_ok());
        if sent {
            shared.metrics.accepted += 1;
        } else {
            shared.metrics.dropped += 1;
            shared.metrics.complete = false;
        }
    }

    pub(crate) fn close(&self) {
        let mut shared = self.shared.lock().unwrap();
        shared.ended_at.get_or_insert_with(now);
        shared.sender.take(); // Last sender closes after any bounded flush operation ends.
    }
}

/// The caller retains this owner across a cancelled/timed-out stop and may retry.
/// Its supplied permit stays in the thread until exit, including owner drop/timeout.
pub struct HistoryWriter {
    sink: Option<HistorySink>,
    shared: Arc<Mutex<Shared>>,
    done: watch::Receiver<bool>,
    thread: Option<JoinHandle<()>>,
    interrupt: Arc<Mutex<Option<rusqlite::InterruptHandle>>>,
}

impl HistoryWriter {
    pub async fn open(
        path: impl AsRef<Path>,
        session_id: &str,
        generation: u64,
        capacity: usize,
        slot: Arc<Semaphore>,
    ) -> Result<Self, &'static str> {
        if session_id.is_empty()
            || session_id.len() > 128
            || generation == 0
            || capacity == 0
            || capacity > MAX_QUEUE
        {
            return Err("history_configuration");
        }
        let permit = slot.try_acquire_owned().map_err(|_| "history_capacity")?;
        let path = path.as_ref().to_owned();
        let sid = session_id.to_owned();
        let (sender, receiver) = mpsc::channel(capacity);
        let shared = Arc::new(Mutex::new(Shared {
            sender: Some(sender),
            ended_at: None,
            metrics: HistoryMetrics {
                complete: true,
                ..Default::default()
            },
        }));
        let thread_shared = shared.clone();
        let interrupt = Arc::new(Mutex::new(None));
        let thread_interrupt = interrupt.clone();
        let (ready_tx, ready_rx) = oneshot::channel();
        let (done_tx, done) = watch::channel(false);
        let thread = std::thread::Builder::new()
            .name("caption-history".into())
            .spawn(move || {
                let _permit = permit; // Keep admission through connection close and thread teardown.
                match open_existing(&path, &sid) {
                    Ok((connection, started, source, targets)) => {
                        *thread_interrupt.lock().unwrap() = Some(connection.get_interrupt_handle());
                        let scope = HistorySink {
                            shared: thread_shared.clone(),
                            session_id: sid,
                            generation,
                            started_at: started,
                            source,
                            targets,
                        };
                        if ready_tx.send(Ok(scope.clone())).is_ok() {
                            run(connection, receiver, scope);
                        }
                    }
                    Err(error) => {
                        let _ = ready_tx.send(Err(error));
                    }
                }
                thread_shared.lock().unwrap().metrics.stopped = true;
                let _ = done_tx.send(true);
            })
            .map_err(|_| "history_thread")?;
        // This owner exists before suspension: cancelled startup also closes the channel.
        let mut owner = Self {
            sink: None,
            shared,
            done,
            thread: Some(thread),
            interrupt,
        };
        owner.sink = Some(
            tokio::time::timeout(WAIT, ready_rx)
                .await
                .map_err(|_| "history_open_timeout")?
                .map_err(|_| "history_thread")??,
        );
        Ok(owner)
    }

    pub fn sink(&self) -> HistorySink {
        self.sink.as_ref().unwrap().clone()
    }

    /// A control-plane barrier; canonical publication only calls nonblocking enqueue.
    pub async fn flush(&self) -> Result<HistoryMetrics, &'static str> {
        let sender = self
            .shared
            .lock()
            .unwrap()
            .sender
            .clone()
            .ok_or("history_closed")?;
        let (tx, rx) = oneshot::channel();
        tokio::time::timeout(WAIT, async {
            sender
                .send(Command::Flush(tx))
                .await
                .map_err(|_| "history_failed")?;
            rx.await.map_err(|_| "history_failed")
        })
        .await
        .map_err(|_| "history_flush_timeout")??;
        Ok(self.sink().metrics())
    }

    pub async fn stop(&mut self) -> Result<HistoryMetrics, &'static str> {
        self.sink().close();
        let done = &mut self.done;
        if tokio::time::timeout(WAIT, async {
            while !*done.borrow_and_update() {
                done.changed().await.map_err(|_| "history_thread")?;
            }
            Ok::<_, &'static str>(())
        })
        .await
        .is_err()
        {
            if let Some(interrupt) = self.interrupt.lock().unwrap().as_ref() {
                interrupt.interrupt();
            }
            return Err("history_stop_timeout");
        }
        if let Some(thread) = self.thread.take() {
            thread.join().map_err(|_| "history_thread")?;
        }
        Ok(self.sink().metrics())
    }
}

impl Drop for HistoryWriter {
    fn drop(&mut self) {
        let mut state = self.shared.lock().unwrap();
        state.ended_at.get_or_insert_with(now);
        state.sender.take();
        if self.thread.is_some() && !*self.done.borrow() {
            state.metrics.complete = false;
            state.metrics.error = Some("history_owner_dropped");
            if let Some(interrupt) = self.interrupt.lock().unwrap().as_ref() {
                interrupt.interrupt();
            }
        }
    }
}

fn now() -> String {
    OffsetDateTime::now_utc()
        .format(&Rfc3339)
        .expect("UTC clock is RFC3339")
}

fn category(error: Error) -> &'static str {
    match error.sqlite_error_code() {
        Some(ErrorCode::DatabaseBusy | ErrorCode::DatabaseLocked) => "history_busy",
        Some(ErrorCode::DiskFull) => "history_disk_full",
        _ => "history_write_failed",
    }
}

fn open_existing(
    path: &Path,
    sid: &str,
) -> Result<(Connection, String, String, Vec<String>), &'static str> {
    let connection =
        Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_WRITE).map_err(category)?;
    connection.busy_timeout(BUSY).map_err(category)?;
    connection
        .execute_batch("PRAGMA foreign_keys=ON")
        .map_err(category)?;
    // Bootstrap/migrations remain SessionRepository.initialize's existing responsibility.
    connection.prepare("SELECT source_revision, original_text, translation_states_json,
        asr_provenance_json, translation_provenance_json, correction_json FROM transcript_segments LIMIT 0")
        .map_err(|_| "history_schema_not_ready")?;
    let (started, source, targets): (String, String, String) = connection
        .query_row(
            "SELECT started_at,source_language,target_languages_json FROM sessions
         WHERE session_id=? AND ended_at IS NULL",
            [sid],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )
        .map_err(|_| "history_session_not_open")?;
    let targets = serde_json::from_str(&targets).map_err(|_| "history_session_languages")?;
    Ok((connection, started, source, targets))
}

fn run(mut connection: Connection, mut receiver: mpsc::Receiver<Command>, sink: HistorySink) {
    while let Some(command) = receiver.blocking_recv() {
        match command {
            Command::Flush(tx) => {
                let _ = tx.send(());
                continue;
            }
            command => {
                let result = match command {
                    Command::Event(raw) => {
                        persist(&mut connection, &sink, raw.get()).map(|changed| (changed, 0))
                    }
                    Command::Invalidate(ids) => {
                        invalidate(&mut connection, &sink, ids).map(|count| (count > 0, count))
                    }
                    Command::Flush(_) => unreachable!(),
                };
                match result {
                    Ok((changed, invalidated)) => {
                        let mut shared = sink.shared.lock().unwrap();
                        shared.metrics.invalidated += invalidated;
                        if changed {
                            shared.metrics.persisted += 1;
                        } else {
                            shared.metrics.stale += 1;
                        }
                    }
                    Err(error) => {
                        let mut shared = sink.shared.lock().unwrap();
                        shared.metrics.write_errors += 1;
                        shared.metrics.complete = false;
                        shared.metrics.error = Some(error);
                        shared.sender.take();
                        receiver.close();
                        drop(shared);
                        while let Ok(command) = receiver.try_recv() {
                            if !matches!(command, Command::Flush(_)) {
                                sink.shared.lock().unwrap().metrics.dropped += 1;
                            }
                        }
                        break;
                    }
                }
            }
        }
    }
    let ended = sink.shared.lock().unwrap().ended_at.clone();
    if let Some(ended) = ended {
        if let Err(error) = connection.execute(
            "UPDATE sessions SET ended_at=?,interrupted=0
            WHERE session_id=? AND started_at=? AND ended_at IS NULL",
            params![ended, sink.session_id, sink.started_at],
        ) {
            let mut shared = sink.shared.lock().unwrap();
            shared.metrics.write_errors += 1;
            shared.metrics.complete = false;
            shared.metrics.error = Some(category(error));
        }
    }
}

fn invalidate(
    connection: &mut Connection,
    sink: &HistorySink,
    ids: Vec<Invalidation>,
) -> Result<u64, &'static str> {
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(category)?;
    let mut count = 0;
    for id in ids {
        let revision = i64::try_from(id.revision).map_err(|_| "history_event")?;
        let version = i64::try_from(id.version).map_err(|_| "history_event")?;
        count += transaction.execute("DELETE FROM transcript_segments WHERE session_id=? AND segment_id=?
            AND source_revision<=? AND version<=? AND stage!='committed'
            AND EXISTS(SELECT 1 FROM sessions WHERE session_id=? AND started_at=? AND ended_at IS NULL)",
            params![sink.session_id, id.id, revision, version, sink.session_id, sink.started_at]).map_err(category)? as u64;
    }
    transaction.commit().map_err(category)?;
    Ok(count)
}

#[derive(Deserialize)]
struct Event {
    session_id: String,
    session_generation: u64,
    segment_id: String,
    version: u64,
    source_revision: u64,
    stage: String,
    source_language: String,
    text: String,
    original_text: Option<String>,
    translations: Value,
    translation_states: Value,
    start_ms: u64,
    end_ms: Option<u64>,
    speaker: Option<String>,
    confidence: Option<f64>,
    correction: Option<Value>,
    asr_provenance: Option<Value>,
    translation_provenance: Value,
    committed: bool,
    emitted_at: String,
}

fn persist(
    connection: &mut Connection,
    sink: &HistorySink,
    raw: &str,
) -> Result<bool, &'static str> {
    let event: Event = serde_json::from_str(raw).map_err(|_| "history_event")?;
    if event.session_id != sink.session_id || event.session_generation != sink.generation {
        return Ok(false);
    }
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(category)?;
    let active: bool = transaction
        .query_row(
            "SELECT EXISTS(SELECT 1 FROM sessions
        WHERE session_id=? AND started_at=? AND ended_at IS NULL)",
            params![sink.session_id, sink.started_at],
            |row| row.get(0),
        )
        .map_err(category)?;
    if !active {
        return Ok(false);
    }
    let original = event
        .original_text
        .as_deref()
        .or_else(|| (event.stage == "stable").then_some(event.text.as_str()));
    let version = i64::try_from(event.version).map_err(|_| "history_event")?;
    let revision = i64::try_from(event.source_revision).map_err(|_| "history_event")?;
    let start = i64::try_from(event.start_ms).map_err(|_| "history_event")?;
    let end = event
        .end_ms
        .map(i64::try_from)
        .transpose()
        .map_err(|_| "history_event")?;
    let changed = transaction
        .execute(
            UPSERT,
            params![
                sink.session_id,
                event.segment_id,
                version,
                revision,
                event.stage,
                event.source_language,
                event.text,
                original,
                event.translations.to_string(),
                event.translation_states.to_string(),
                start,
                end,
                event.speaker,
                event.confidence,
                event.correction.map(|v| v.to_string()),
                event.committed,
                event.emitted_at,
                event.asr_provenance.map(|v| v.to_string()),
                event.translation_provenance.to_string(),
            ],
        )
        .map_err(category)?;
    transaction.commit().map_err(category)?;
    Ok(changed > 0)
}

const UPSERT: &str = r#"
                INSERT INTO transcript_segments (
                    session_id, segment_id, version, source_revision, stage, source_language,
                    text, original_text, translations_json, translation_states_json,
                    start_ms, end_ms, speaker, confidence, correction_json,
                    committed, emitted_at, asr_provenance_json, translation_provenance_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, segment_id) DO UPDATE SET
                    version = excluded.version,
                    source_revision = excluded.source_revision,
                    stage = excluded.stage,
                    source_language = excluded.source_language,
                    text = excluded.text,
                    original_text = CASE
                        WHEN excluded.source_revision > transcript_segments.source_revision
                        THEN excluded.original_text
                        ELSE COALESCE(transcript_segments.original_text, excluded.original_text)
                    END,
                    translations_json = excluded.translations_json,
                    translation_states_json = excluded.translation_states_json,
                    start_ms = excluded.start_ms,
                    end_ms = excluded.end_ms,
                    speaker = excluded.speaker,
                    confidence = excluded.confidence,
                    correction_json = excluded.correction_json,
                    asr_provenance_json = excluded.asr_provenance_json,
                    translation_provenance_json = excluded.translation_provenance_json,
                    committed = excluded.committed,
                    emitted_at = excluded.emitted_at
                WHERE excluded.version > transcript_segments.version
                  AND excluded.source_revision >= transcript_segments.source_revision
                  AND (
                    excluded.source_revision > transcript_segments.source_revision
                    AND excluded.source_revision = transcript_segments.source_revision + 1
                    AND excluded.stage = 'stable'
                    AND transcript_segments.stage != 'committed'
                    OR excluded.source_revision = transcript_segments.source_revision
                    AND (
                    transcript_segments.stage = 'committed'
                    AND excluded.stage = 'committed'
                    OR transcript_segments.stage != 'committed'
                    AND CASE excluded.stage
                        WHEN 'partial' THEN 0
                        WHEN 'stable' THEN 1
                        WHEN 'corrected' THEN 2
                        WHEN 'translated' THEN 3
                        WHEN 'committed' THEN 4
                        ELSE -1
                    END >= CASE transcript_segments.stage
                        WHEN 'partial' THEN 0
                        WHEN 'stable' THEN 1
                        WHEN 'corrected' THEN 2
                        WHEN 'translated' THEN 3
                        WHEN 'committed' THEN 4
                        ELSE 5
                    END
                    )
                  )
                "#;

#[cfg(test)]
#[path = "../tests/common/history.rs"]
mod fixture;

#[cfg(test)]
mod tests {
    use super::*;
    use fixture::Fixture;
    use serde_json::json;

    fn runtime() -> tokio::runtime::Runtime {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
    }
    fn event(version: u64, revision: u64, stage: &str, text: &str) -> Value {
        json!({"type":"transcript","session_id":"history-session","session_generation":1,
            "segment_id":"segment","version":version,"source_revision":revision,"stage":stage,
            "source_language":"en","text":text,"original_text":text,
            "translations":{"ja":"Japanese target"},"translation_states":{"ja":"available","ko":"failed"},
            "start_ms":10,"end_ms":990,"speaker":null,"confidence":null,"correction":null,
            "asr_provenance":{"provider":"faster-whisper","configured_model":null,"verified_model":null,
                "model_revision":null,"timestamp_basis":"sample_clock_native_hint","sample_rate":16000,
                "audio_chunk_start_sample":0,"audio_chunk_end_sample":16000,"auto_generation":null,
                "timeline_offset_samples":null,"replay_prefix_samples":null,"overlap_trimmed":false},
            "translation_provenance":{"ja":{"provider":"m2m100-ct2","requested_model":null,
                "configured_model":null,"verified_model":null,"model_revision":null}},
            "committed":stage=="committed","emitted_at":"2026-06-01T01:02:03.456Z"})
    }
    fn frame(event: Value) -> JsonFrame {
        Arc::from(serde_json::value::RawValue::from_string(event.to_string()).unwrap())
    }

    #[test]
    fn exact_python_sql_and_real_revision_commit_original_and_provenance_guards() {
        let f = Fixture::new("setup");
        assert_eq!(
            UPSERT.split_whitespace().collect::<Vec<_>>(),
            f.output("sql").split_whitespace().collect::<Vec<_>>()
        );
        runtime().block_on(async {
            let mut writer=HistoryWriter::open(f.db(),"history-session",1,32,Arc::new(Semaphore::new(1))).await.unwrap();
            let sink=writer.sink();
            for value in [event(1,1,"stable","Original"),event(2,1,"corrected","Corrected"),
                event(3,2,"stable","Revision two"),event(10,1,"committed","OLD REVISION"),
                event(4,4,"stable","SKIPPED REVISION"),event(4,2,"committed","Display"),
                event(5,3,"stable","COMMITTED REWRITE"),event(5,2,"committed","Final display")] {
                sink.enqueue(frame(value));
            }
            let mut old=event(99,2,"committed","WRONG SESSION");old["session_id"]=json!("other-session");sink.enqueue(frame(old));
            let mut old=event(99,2,"committed","OLD GENERATION");old["session_generation"]=json!(0);sink.enqueue(frame(old));
            let report=writer.flush().await.unwrap();
            assert_eq!((report.persisted,report.stale),(5,5));
            writer.stop().await.unwrap();
            let connection=Connection::open(f.db()).unwrap();
            let (text,original,version,revision,stamp,provenance):(String,String,i64,i64,String,String)=connection.query_row(
                "SELECT text,original_text,version,source_revision,emitted_at,asr_provenance_json FROM transcript_segments",
                [],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?,r.get(4)?,r.get(5)?))).unwrap();
            assert_eq!((text.as_str(),original.as_str(),version,revision),("Final display","Revision two",5,2));
            assert_eq!(stamp,"2026-06-01T01:02:03.456Z"); // Never substituted by disk flush time.
            assert_eq!(serde_json::from_str::<Value>(&provenance).unwrap(),event(5,2,"committed","x")["asr_provenance"]);
        });
    }

    #[test]
    fn actual_sqlite_full_and_failed_write_have_fixed_private_diagnostics() {
        let f = Fixture::new("setup");
        runtime().block_on(async {
            let mut writer =
                HistoryWriter::open(f.db(), "history-session", 1, 2, Arc::new(Semaphore::new(1)))
                    .await
                    .unwrap();
            let (mut connection, _, _, _) = open_existing(&f.db(), "history-session").unwrap();
            let pages: i64 = connection
                .query_row("PRAGMA page_count", [], |r| r.get(0))
                .unwrap();
            connection
                .execute_batch(&format!("PRAGMA max_page_count={pages}"))
                .unwrap();
            let oversized = event(1, 1, "stable", &"x".repeat(1024 * 1024));
            assert_eq!(
                persist(&mut connection, &writer.sink(), &oversized.to_string()),
                Err("history_disk_full")
            );
            connection
                .execute_batch("PRAGMA max_page_count=10000")
                .unwrap();
            connection
                .execute_batch(
                    "CREATE TRIGGER fail_history BEFORE INSERT ON transcript_segments BEGIN
                SELECT RAISE(FAIL,'https://user:secret@private/path?token=secret'); END",
                )
                .unwrap();
            writer
                .sink()
                .enqueue(frame(event(1, 1, "stable", "source")));
            let _ = writer.flush().await;
            let report = writer.stop().await.unwrap();
            assert!(!report.complete && report.write_errors > 0);
            assert_eq!(report.error, Some("history_write_failed"));
            assert!(!serde_json::to_string(&report).unwrap().contains("secret"));
        });
    }

    #[test]
    fn invalidation_cannot_remove_new_revision_or_committed_rows() {
        let f = Fixture::new("setup");
        runtime().block_on(async {
            let mut writer =
                HistoryWriter::open(f.db(), "history-session", 1, 8, Arc::new(Semaphore::new(1)))
                    .await
                    .unwrap();
            let sink = writer.sink();
            sink.enqueue(frame(event(1, 1, "stable", "original")));
            let mut protected = event(1, 1, "committed", "committed");
            protected["segment_id"] = json!("protected");
            sink.enqueue(frame(protected));
            writer.flush().await.unwrap();
            sink.invalidate(vec![
                Invalidation {
                    id: "segment".into(),
                    version: 0,
                    revision: 1,
                },
                Invalidation {
                    id: "protected".into(),
                    version: 99,
                    revision: 99,
                },
            ]);
            assert_eq!(writer.flush().await.unwrap().invalidated, 0);
            sink.invalidate(vec![Invalidation {
                id: "segment".into(),
                version: 1,
                revision: 1,
            }]);
            assert_eq!(writer.flush().await.unwrap().invalidated, 1);
            sink.enqueue(frame(event(3, 2, "stable", "new revision")));
            sink.invalidate(vec![Invalidation {
                id: "segment".into(),
                version: 99,
                revision: 1,
            }]);
            writer.flush().await.unwrap();
            writer.stop().await.unwrap();
            let connection = Connection::open(f.db()).unwrap();
            let rows: i64 = connection
                .query_row("SELECT COUNT(*) FROM transcript_segments", [], |r| r.get(0))
                .unwrap();
            assert_eq!(rows, 2);
            let revision: i64 = connection
                .query_row(
                    "SELECT source_revision FROM transcript_segments WHERE segment_id='segment'",
                    [],
                    |r| r.get(0),
                )
                .unwrap();
            assert_eq!(revision, 2);
        });
    }

    #[test]
    fn bounded_queue_overflow_and_locked_stop_are_explicit_and_do_not_grow_work() {
        let f = Fixture::new("setup");
        runtime().block_on(async {
            let mut writer =
                HistoryWriter::open(f.db(), "history-session", 1, 1, Arc::new(Semaphore::new(1)))
                    .await
                    .unwrap();
            let connection = Connection::open(f.db()).unwrap();
            connection.execute_batch("BEGIN IMMEDIATE").unwrap();
            for index in 1..33 {
                writer
                    .sink()
                    .enqueue(frame(event(index, 1, "stable", "source")));
            }
            let start = std::time::Instant::now();
            let report = writer.stop().await.unwrap();
            assert!(start.elapsed() < WAIT);
            assert!(
                report.dropped > 0 && report.write_errors > 0 && !report.complete && report.stopped
            );
            connection.execute_batch("ROLLBACK").unwrap();
        });
    }

    #[test]
    fn cancelled_stop_keeps_thread_owned_drains_and_can_be_retried() {
        let f = Fixture::new("setup");
        runtime().block_on(async {
            let slot = Arc::new(Semaphore::new(1));
            let mut writer = HistoryWriter::open(f.db(), "history-session", 1, 4, slot.clone())
                .await
                .unwrap();
            let connection = Connection::open(f.db()).unwrap();
            connection.execute_batch("BEGIN IMMEDIATE").unwrap();
            writer
                .sink()
                .enqueue(frame(event(1, 1, "stable", "source")));
            assert!(
                tokio::time::timeout(Duration::from_millis(5), writer.stop())
                    .await
                    .is_err()
            );
            assert_eq!(slot.available_permits(), 0);
            connection.execute_batch("ROLLBACK").unwrap();
            let report = writer.stop().await.unwrap();
            assert!(report.complete && report.stopped && report.persisted == 1);
            assert_eq!(slot.available_permits(), 1);
        });
    }

    #[test]
    fn cancelled_startup_keeps_capacity_until_owned_thread_exits_without_creating_store() {
        let f = Fixture::new("setup");
        runtime().block_on(async {
            let connection = Connection::open(f.db()).unwrap();
            connection
                .execute_batch("PRAGMA journal_mode=DELETE; BEGIN EXCLUSIVE")
                .unwrap();
            let slot = Arc::new(Semaphore::new(1));
            let childslot = slot.clone();
            let path = f.db();
            let opening = tokio::spawn(async move {
                HistoryWriter::open(path, "history-session", 1, 2, childslot).await
            });
            tokio::time::timeout(Duration::from_secs(1), async {
                while slot.available_permits() != 0 {
                    tokio::task::yield_now().await;
                }
            })
            .await
            .unwrap();
            opening.abort();
            match opening.await {
                Err(error) => assert!(error.is_cancelled()),
                Ok(_) => panic!("startup completed before cancellation"),
            }
            connection.execute_batch("ROLLBACK").unwrap();
            tokio::time::timeout(Duration::from_secs(2), async {
                while slot.available_permits() == 0 {
                    tokio::task::yield_now().await;
                }
            })
            .await
            .unwrap();
            let mut writer = HistoryWriter::open(f.db(), "history-session", 1, 2, slot)
                .await
                .unwrap();
            assert!(writer.stop().await.unwrap().complete);
        });
    }
}

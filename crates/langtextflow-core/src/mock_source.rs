//! The existing deterministic demo cadence. No audio, model or inference worker.
use crate::asr_slice::AsrSession;
use std::time::Duration;
use tokio::sync::watch;
use tokio::task::JoinHandle;

const SAMPLES: [&str; 3] = [
    "오늘 우리가 볼 말씀은 요한 보금 삼장입니다",
    "하나님이 세상을 이처럼 사랑하사 독생자를 주셨으니",
    "오늘 말씀을 통해 복음의 의미를 함께 살펴보겠습니다",
];

pub(crate) struct MockSource {
    task: Option<JoinHandle<Result<(), &'static str>>>,
    pub failure: watch::Receiver<Option<&'static str>>,
}

impl MockSource {
    pub fn start(session: AsrSession, generation: u64) -> Result<Self, &'static str> {
        session.enable_mock(generation)?;
        let (failed, failure) = watch::channel(None);
        let task = tokio::spawn(async move {
            let run = async {
                let mut index = 0;
                loop {
                    let raw = SAMPLES[index as usize % SAMPLES.len()];
                    let partial = raw
                        .chars()
                        .take(raw.chars().count() - 3)
                        .collect::<String>();
                    session.mock_source(generation, index, &partial, false)?;
                    tokio::time::sleep(Duration::from_millis(550)).await;
                    session.mock_source(generation, index, raw, true)?;
                    tokio::time::sleep(Duration::from_millis(2400)).await;
                    index += 1;
                }
                #[allow(unreachable_code)]
                Ok::<(), &'static str>(())
            }
            .await;
            if let Err(error) = run {
                failed.send_replace(Some(error));
            }
            run
        });
        Ok(Self {
            task: Some(task),
            failure,
        })
    }

    pub async fn stop(mut self) -> Result<(), &'static str> {
        if let Some(task) = self.task.take() {
            task.abort();
            match task.await {
                Ok(result) => result?,
                Err(error) if error.is_cancelled() => (),
                Err(_) => return Err("mock_owner_failed"),
            }
        }
        Ok(())
    }
}

impl Drop for MockSource {
    fn drop(&mut self) {
        if let Some(task) = self.task.take() {
            task.abort();
        }
    }
}

pub(crate) async fn failure(
    receiver: &mut Option<watch::Receiver<Option<&'static str>>>,
) -> &'static str {
    if let Some(receiver) = receiver {
        loop {
            if let Some(error) = *receiver.borrow_and_update() {
                return error;
            }
            if receiver.changed().await.is_err() {
                return "mock_owner_stopped";
            }
        }
    }
    std::future::pending().await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn synthetic_sources_keep_partial_and_retired_generation_out_of_optional_work() {
        let session = AsrSession::default();
        session
            .start_observed("demo", "en", &["ko".into()])
            .unwrap();
        let generation = session.audio_generation("en").unwrap();
        session.enable_mock(generation).unwrap();
        assert!(session.bind_audio_generation("en").is_err());
        session
            .mock_source(generation, 0, "partial", false)
            .unwrap();
        assert_eq!(session.pending_postprocessing(), 0);
        assert_eq!(session.source_metrics().active_provider, Some("mock"));
        assert!(session.source_metrics().auto_generation.is_none());
        assert!(session
            .mock_source(generation, 1, "stable without partial", true)
            .is_err());
        session
            .mock_source(generation, 1, "next partial", false)
            .unwrap();
        session
            .mock_source(generation, 1, "next stable", true)
            .unwrap();
        assert!(session
            .mock_source(
                generation,
                crate::caption_events::MAX_PUBLIC_IDS as u64,
                "outside bound",
                false
            )
            .is_err());
        session.stop_checked().unwrap();
        session
            .start_observed("new-demo", "en", &["ko".into()])
            .unwrap();
        let current = session.audio_generation("en").unwrap();
        session.enable_mock(current).unwrap();
        assert!(session
            .mock_source(generation, 0, "late source", true)
            .is_err());
        session
            .mock_source(current, 0, "new partial", false)
            .unwrap();
        session.mock_source(current, 0, "new stable", true).unwrap();
        assert!(session
            .mock_source(current, 0, "rewrite stable", true)
            .is_err());
        assert_eq!(session.pending_postprocessing(), 0);
    }
}

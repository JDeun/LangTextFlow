//! Source-scoped immutable inference context; no native model/quality evidence.
use langtextflow_core::asr_slice::{AsrSession, AudioChunk};
use langtextflow_core::mt_slice::{MtDispatcher, TranslationContext};
use langtextflow_core::source_context::SourceContext;
use langtextflow_core::supervision::worker_slot;
use serde_json::{json, Value};

fn audio() -> AudioChunk {
    AudioChunk {
        wav_path: "synthetic.wav".into(),
        start_sample: 0,
        end_sample: 1600,
        sample_rate: 16000,
    }
}

#[test]
fn context_is_source_bound_immutable_through_revision_and_reset_on_stop_restart() {
    let session = AsrSession::default();
    session
        .start_observed("first", "ko", &["en".into()])
        .unwrap();
    let mut context = SourceContext {
        preset: "church".into(),
        hotwords: vec!["original hint".into()],
        glossary: vec![json!({"term":"용어","source_language":"ko"})],
        ..SourceContext::default()
    };
    assert_eq!(
        session.configure_context("en", context.clone()),
        Err("source_context_scope")
    );
    session.configure_context("ko", context.clone()).unwrap();
    context.hotwords[0] = "later caller mutation".into();
    let first = session.admit("chunk", 1, "ko", audio(), 1000).unwrap();
    let payload: Value = serde_json::from_str(&first.request().payload).unwrap();
    assert_eq!(payload["context"]["hotwords"], json!(["original hint"]));
    assert_eq!(payload["context"]["preset"], "church");
    assert_eq!(
        session.configure_context("ko", context.clone()),
        Err("source_context_scope")
    );
    session.revise("chunk", 2).unwrap();
    assert!(first.cancelled.load(std::sync::atomic::Ordering::Acquire));
    drop(first);
    let revised = session.admit("chunk", 2, "ko", audio(), 1000).unwrap();
    let old_generation = revised.request().session_generation;
    let revised_payload: Value = serde_json::from_str(&revised.request().payload).unwrap();
    assert_eq!(revised_payload["context"], payload["context"]);
    session.stop();
    assert!(revised.cancelled.load(std::sync::atomic::Ordering::Acquire));
    drop(revised);
    session
        .start_observed("second", "en", &["ko".into()])
        .unwrap();
    context.preset = "general".into();
    context.glossary.clear();
    session.configure_context("en", context).unwrap();
    let next = session.admit("chunk", 1, "en", audio(), 1000).unwrap();
    let next_payload: Value = serde_json::from_str(&next.request().payload).unwrap();
    assert_eq!(
        next_payload["context"]["hotwords"],
        json!(["later caller mutation"])
    );
    assert_eq!(next_payload["context"]["preset"], "general");
    assert!(next.request().session_generation > old_generation);
}

#[test]
fn invalid_and_oversized_context_never_installs_a_partial_snapshot() {
    let session = AsrSession::default();
    session
        .start_observed("session", "en", &["ko".into()])
        .unwrap();
    for context in [
        SourceContext {
            preset: "unknown".into(),
            ..SourceContext::default()
        },
        SourceContext {
            hotwords: vec!["x".repeat(161)],
            ..SourceContext::default()
        },
        SourceContext {
            glossary: vec![json!({"term":"x".repeat(50_000)})],
            ..SourceContext::default()
        },
    ] {
        assert!(session.configure_context("en", context).is_err());
    }
    let context = SourceContext {
        hotwords: (0..256)
            .map(|index| format!("{index:03}{}", "x".repeat(157)))
            .collect(),
        glossary: vec![json!({"term":"x".repeat(160),"source_language":"en"}); 37],
        ..SourceContext::default()
    };
    session.configure_context("en", context).unwrap();
    let admitted = session.admit("chunk", 1, "en", audio(), 1000).unwrap();
    assert!(admitted.request().payload.len() > 47_000);
    assert!(
        serde_json::to_vec(admitted.request()).unwrap().len() < langtextflow_core::MAX_FRAME_BYTES
    );
}

#[test]
fn translation_cannot_replace_the_captured_source_context() {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap()
        .block_on(async {
            let session = AsrSession::default();
            session
                .start_observed("session", "ko", &["en".into()])
                .unwrap();
            let context = SourceContext {
                preset: "church".into(),
                ..SourceContext::default()
            };
            session.configure_context("ko", context).unwrap();
            let dispatcher =
                MtDispatcher::start("unused".into(), vec![], 1, worker_slot()).unwrap();
            assert_eq!(
                session.enable_translation(
                    &dispatcher,
                    TranslationContext {
                        preset: "general".into(),
                        glossary: vec![],
                    },
                    1000
                ),
                Err("translation_context_scope")
            );
            session
                .enable_translation(
                    &dispatcher,
                    TranslationContext {
                        preset: "church".into(),
                        glossary: vec![],
                    },
                    1000,
                )
                .unwrap();
            dispatcher.stop().await.unwrap();
        });
}

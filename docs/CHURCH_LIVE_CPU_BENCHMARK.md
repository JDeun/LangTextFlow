# Real-speech end-to-end CPU interpretation benchmark

This test drives **LangTextFlow's actual CaptionRuntime** on a standard public
GitHub-hosted Ubuntu runner (nominal 4 vCPU, 16 GB RAM, no discrete GPU).

- Audio: three pinned Google FLEURS Korean test recordings, CC BY 4.0,
  fed as 100 ms frames at real-world (wall-clock) speed; 0.5 s inter-clip pause.
- ASR: faster-whisper base, CPU INT8, 8 second decoding window.
- Correction: deterministic only (no second correction language model).
- Translation: locally running TranslateGemma 4B with Ollama on CPU.
- Captions: STABLE -> CORRECTED -> TRANSLATED -> COMMITTED through the real
  app pipeline, then local SQLite session persistence.

The CI report records pinned fixture SHA-256, CPU environment, actual Ollama
model/version, Korean-source CER/WER, translated caption coverage,
source/translation p95 lag, whole-pipeline RTF, queue telemetry, and errors.

**This is not yet field-quality certification.** The source-language FLEURS
transcripts have human references, but this speech subset lacks human-reviewed
English translations. The translation-only #108 baseline has separate
synthetic references requiring bilingual review. A CPU workflow that eventually
produces English text is not enough: rising queues or large p95 delay mean
real-time interpretation has NOT been achieved.

Additional acceptance gates: actual church microphone/mixer audio, theological
terminology and verse integrity, 30/60/90-minute continuous sessions,
reviewed bilingual semantics, repeatable target-hardware tests. See issues
#101, #103, #104, #106, #107 and PR #108.

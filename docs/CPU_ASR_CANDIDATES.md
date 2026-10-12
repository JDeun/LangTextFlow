# Five CPU ASR model experiments (strict sequence)

LangTextFlow originally targets affordable church/community Korean speech
recognition and live translation. Here we compare **real models**, not simulated
inference, on the public GitHub-hosted Linux runner (~4 vCPU, 16GB RAM, no GPU).

## Execution

[`korean-cpu-asr-challengers.yml`](../.github/workflows/korean-cpu-asr-challengers.yml)
automatically executes the following candidates **sequentially, even if an
earlier candidate fails**. Each model's failure receives a status and complete
log; no failure is silently called a model accuracy score.

| Order | Candidate | Model provenance | GitHub issue |
|---|---|---|---|
| 1 | SenseVoiceSmall INT8 | sherpa-onnx pre-trained 2025-09-09 ONNX | [#111](https://github.com/JDeun/LangTextFlow/issues/111) |
| 2 | Qwen3-ASR-0.6B INT8 | sherpa-onnx pre-trained 2026-03-25 ONNX | [#112](https://github.com/JDeun/LangTextFlow/issues/112) |
| 3 | Korean streaming Zipformer INT8 | sherpa-onnx pre-trained 2024-06-16 | [#113](https://github.com/JDeun/LangTextFlow/issues/113) |
| 4 | whisper.cpp base multilingual | ggml-org pinned code commit and downloaded GGML base weight | [#114](https://github.com/JDeun/LangTextFlow/issues/114) |
| 5 | Qwen3-ASR-1.7B INT8 | third-party HF ONNX export, actual revision digest recorded | [#115](https://github.com/JDeun/LangTextFlow/issues/115) |

The shared harness uses pinned FLEURS Korean CC BY 4.0 test recordings,
**identical audio SHA-256 and independent human Korean source text**. Records
mean per-clip CER/WER, whole utterance RTF with real 100ms input pacing,
estimated caption lag, native-streaming vs offline semantics,
model warm start, RSS peak, actual CPU/core/disk and checkpoint checksums.

Model licenses and redistribution must be separately checked before a public
installer ships any checkpoint. Sherpa-onnx Python package is pinned to 1.13.8;
third-party archives/weights are fetched only during the experiment.

**Status warning:** Execution queued does not imply tested; a repository PR
and CI job creation is not a successful ASR benchmark. Some models may
not load/fit; each is reported as unavailable or failed rather than skipped
without evidence. Existing faster-whisper tiny baseline has poor Korean CER
on an earlier 4s micro-window pilot, which is NOT a representative
benchmark of all Whisper runtimes or larger models.

## Scope and next qualification

These runs score **Korean ASR only**. A better CER does not automatically
produce accurate English interpretation. When the candidates have measured
results, compare them against faster-whisper base/small on the exact FLEURS
IDs/SHA before choosing strong candidates for an adapter behind existing
`AsrEngine`. Only then extend to the real ASR -> TranslateGemma caption
pipeline in [PR #109](https://github.com/JDeun/LangTextFlow/pull/109),
with independent human-translated references for *meaning*, critical
Bible verse/numeral and negation checks, bounded queues, p95 latency
and long microphone/sermon soak testing.

Overall tracking: [#110](https://github.com/JDeun/LangTextFlow/issues/110).

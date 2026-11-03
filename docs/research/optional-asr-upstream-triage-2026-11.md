# Optional ASR triage: Notesoop, LFM3 Audio, Hex

- **Status:** evidence collected; adoption deferred / hard gates BLOCKED
- **Date / researcher:** 2026-11-03 / LangTextFlow agent-assisted source review
- **Tracking:** [#349](https://github.com/JDeun/LangTextFlow/issues/349), [#341](https://github.com/JDeun/LangTextFlow/issues/341), [#344](https://github.com/JDeun/LangTextFlow/issues/344)
- **Scope:** upstream metadata, actual licenses/notices and selected source inspection; no weights, remote-code execution, builds, inference, adapters or defaults changed

## Decision and release boundary

All three are **optional research tracks**, not prerequisites for shipping the
currently implemented provider stack. Unresolved rights block adopting/bundling
the affected candidate, not work on existing providers. Existing installation,
privacy, routing, recovery and field-quality acceptance remain independent gates;
this report neither completes them nor selects a new architecture/model.

Apply [model selection policy](../MODEL_SELECTION_POLICY.md): missing evidence is
BLOCKED, not an invented score or definitive legal rejection. No candidate has a
new LangTextFlow measurement or qualified hardware scope here.

| Candidate | Intended role | Source-inspected contract | Disposition |
| --- | --- | --- | --- |
| Notesoop-STT-Ko-v1 | Korean-only ASR comparison | File/batch inference; default 16s windows / 1s overlap; final text + RTF | **BLOCKED** pending artifact/data-rights provenance and bounded CPU probe |
| LFM3-Audio-2B | Multilingual batch ASR/translation; separately English LiveASR | KO/EN/JA declared; LiveASR explicitly English-only | **BLOCKED** pending custom-license scope review, code audit and CPU feasibility |
| Hex | Optional helper/API or design reference, not a single checkpoint | Embedded SDK final-only; separate app stream API has conditional partials | Reference only; adoption **BLOCKED** pending selected dependency/model inventory and integration qualification |

No execution-eligible JSON registry entry is added: Notesoop's rights chain is
incomplete, LFM's custom terms do not fit the current Apache/MIT admission rule,
and Hex is a multi-model runtime. This worklist records scope without weakening
the [candidate catalog](../../benchmarks/ko_en_ja_model_candidates.v1.json).

## Evidence and method

GitHub connector reads and official HF API/text requests resolved complete
repository revisions. Only metadata, cards, licenses, notices and selected
source/docs were read. External repository instructions were research data, not
workspace instructions. No upstream source, assets or license text is copied
into this PR. Weight digests below are publisher metadata, not local verification.

| Source | Complete revision | Immutable provenance |
| --- | --- | --- |
| [Notesoop](https://huggingface.co/Phsoft-ai/Notesoop-STT-Ko-v1/tree/030e4720b56ad8157987c71e4549391c1a2e1a0d) | `030e4720b56ad8157987c71e4549391c1a2e1a0d` | [Record](../knowledge/raw/2026-11-03-notesoop-030e472.md) |
| [LFM3 Audio](https://huggingface.co/LiquidAI/LFM3-Audio-2B/tree/0295e4130594c06800fd25451d97139ac2da1aff) | `0295e4130594c06800fd25451d97139ac2da1aff` | [Record](../knowledge/raw/2026-11-03-lfm3-audio-0295e41.md) |
| [Hex](https://github.com/anomalyco/hex/tree/f501c731b60bc21a488b2fdd1628a0bc8046d536) | `f501c731b60bc21a488b2fdd1628a0bc8046d536` | [Record](../knowledge/raw/2026-11-03-hex-f501c73.md) |

## Notesoop: Korean batch inference, not proven live streaming

The [card](https://huggingface.co/Phsoft-ai/Notesoop-STT-Ko-v1/blob/030e4720b56ad8157987c71e4549391c1a2e1a0d/README.md)
declares Korean, an EmbeddingGemma-2 audio encoder, acoustic reader, 25k vocabulary
and KenLM rescoring. Speed/RAM comparisons are upstream M1 Pro 16GB reports, not
LangTextFlow CPU or first-caption measurements.

The actual [entrypoint](https://huggingface.co/Phsoft-ai/Notesoop-STT-Ko-v1/blob/030e4720b56ad8157987c71e4549391c1a2e1a0d/run_inference.py)
loads a file, constructs all chunk ranges and preprocessed chunks, batches them,
then returns one transcript and RTF. Defaults are 16s/1s overlap and batch size 4.
MPS/CUDA/CPU device branches do not establish bounded Linux CPU memory. No public
partial-event, word-time or cancellation contract was found in this inspected
path. Whole-file throughput cannot establish first-caption latency.

The actual [LICENSE](https://huggingface.co/Phsoft-ai/Notesoop-STT-Ko-v1/blob/030e4720b56ad8157987c71e4549391c1a2e1a0d/LICENSE)
is Apache-2.0. This verifies the publisher's text, not every underlying right.
The card identifies AI Hub lecture data without a dataset ID/agreement. Separate
corpus/provenance terms for the Korean LM/vector bank were not identified;
those rights are **UNKNOWN**, not a finding that Apache-2.0 is invalid. No separate
NOTICE/third-party notice was present in the inspected file listing.

The [extractor](https://huggingface.co/Phsoft-ai/Notesoop-STT-Ko-v1/blob/030e4720b56ad8157987c71e4549391c1a2e1a0d/src/step2_extract_features.py)
loads the Google encoder/processor without a revision. Google's
[fixed card](https://huggingface.co/google/embeddinggemma-2/blob/f9972ac22e696a105c482202646979e33e4b5a1e/README.md)
declares Apache-2.0; do not assume older Gemma terms. No standalone LICENSE was
listed at that revision. Reader loading uses `weights_only=False`, requiring
trusted hashes and process isolation in any later probe. Upstream recommends
unpinned KenLM master; its checked [LICENSE](https://github.com/kpu/kenlm/blob/4cb443e60b7bf2c0ddf3c745378f76cb59e254e5/LICENSE)
identifies LGPL-2.1-or-later plus exceptions, not rights to the LM's training text.

**Next gate:** resolve dataset/LM/vector provenance and distribution obligations;
pin every asset/dependency independently. Then separately authorize a tiny
KO-only CPU probe using existing faster-whisper/Qwen/Zipformer fixtures: silence,
noise, 1/2/4s windowing, overlap deduplication, truthful timestamp provenance and
kill/restart behavior. Reject EN/JA routing explicitly. No new router is needed.

## LFM3 Audio: multilingual batch scope versus English LiveASR

The [card](https://huggingface.co/LiquidAI/LFM3-Audio-2B/blob/0295e4130594c06800fd25451d97139ac2da1aff/README.md)
declares KO/EN/JA ASR/translation, but LiveASR is English-only: roughly 0.96s chunks,
carried state and renewed text context beyond 30s. Preference for translation
to/from English is source-reported, not six-direction semantic acceptance.
GGUF/MLX/ONNX are separate repositories needing independent pins and audits.
CPU loading examples, claimed first-token time and codec speed prove neither
stable caption latency nor word alignment, co-resident RAM or 4/8GB feasibility.

The actual [LICENSE](https://huggingface.co/LiquidAI/LFM3-Audio-2B/blob/0295e4130594c06800fd25451d97139ac2da1aff/LICENSE)
is **LFM Open License v1.0**, not Apache/MIT. It has a $10m revenue threshold with
commercial-use limitation, a conditional qualified-nonprofit provision for
noncommercial/research purposes, and redistribution obligations for license,
modified-file and applicable attribution notices. Do not assume any church user
or downstream distributor meets those conditions. Entity/use/distribution scope
is **BLOCKED pending review**, not automatically allowed or forbidden.

Selected [modeling](https://huggingface.co/LiquidAI/LFM3-Audio-2B/blob/0295e4130594c06800fd25451d97139ac2da1aff/modeling_lfm3_audio.py),
processor and config files have Hugging Face Apache-2.0 headers; preserve per-file
origins instead of inferring one license for all code/weights. Quickstart uses
`trust_remote_code=True` and config `auto_map` points to checkpoint Python modules;
none were imported. No root third-party NOTICE was listed. Exhaustive dependency,
data-rights, cancellation and thread-safety inventories remain **UNKNOWN**.

**Next gate:** after terms/code approval, isolate a bounded CPU probe using existing
ASR/MT fixtures. Evaluate batch KO/EN/JA and all six MT directions, with EN→KO and
JA→KO first. Test English LiveASR separately; call KO/JA windowing chunked, not
native streaming. Measure state reset, process termination, PCM-clock bounds,
startup/RSS and glossary ablation before any provider registration.

## Hex: refresh the existing audit, distinguish two interfaces

This extends the [existing audit](hex-rust-streaming-architecture-audit.md), not a
new Rust rewrite/adoption decision. The current [app stream contract](https://github.com/anomalyco/hex/blob/f501c731b60bc21a488b2fdd1628a0bc8046d536/docs/streams.md)
documents macOS/Linux/Windows, superseding the October snapshot's macOS-only
availability. It uses a loaded model, 16kHz mono PCM, one stream, conditional
partials and window times, but sends no word timings. Routes/stream logic exist
at the same pin; no build or native execution was performed here.

The separate [embedded service](https://github.com/anomalyco/hex/blob/f501c731b60bc21a488b2fdd1628a0bc8046d536/docs/specs/local-transcription-service.md)
and [SDK](https://github.com/anomalyco/hex/blob/f501c731b60bc21a488b2fdd1628a0bc8046d536/sdk/typescript/README.md)
remain final-WAV contracts requiring an explicit helper command. Ready-transcriber
in-flight cancellation closes its helper; low-level HTTP cancellation differs.
These source contracts do not prove LangTextFlow packaging or cancellation.

[Model definitions](https://github.com/anomalyco/hex/blob/f501c731b60bc21a488b2fdd1628a0bc8046d536/src/transcription/models.rs)
mark Qwen/Whisper offline and condition native partials on a GPU/successful probe.
Nemotron 3.5 is streaming with KO/JA in its [language inventory](https://github.com/anomalyco/hex/blob/f501c731b60bc21a488b2fdd1628a0bc8046d536/src/transcription/artifacts.rs).
Thus neither “all Hex is final-only” nor “only English can stream” is a correct
current blanket claim; target CPU native streaming remains unproved. Its 120s
backlog/25s window limits are not LangTextFlow caption-freshness targets.

[Root LICENSE](https://github.com/anomalyco/hex/blob/f501c731b60bc21a488b2fdd1628a0bc8046d536/LICENSE)
is MIT. [Third-party notices](https://github.com/anomalyco/hex/blob/f501c731b60bc21a488b2fdd1628a0bc8046d536/THIRD_PARTY_NOTICES.md)
separately cover icon origins and optional Parakeet Ultra weights. The pinned
Parakeet conversion has actual [CC-BY-4.0 LICENSE](https://huggingface.co/handy-computer/parakeet-ultra-gguf/blob/a1539be1b849ea7463b88343e5277b373d0c4ea9/LICENSE)
and [NOTICE](https://huggingface.co/handy-computer/parakeet-ultra-gguf/blob/a1539be1b849ea7463b88343e5277b373d0c4ea9/NOTICE).
Nemotron instead has actual [OpenMDW-1.1 LICENSE](https://huggingface.co/handy-computer/nemotron-3.5-asr-streaming-0.6b-gguf/blob/53ce288f69326b415aeef030cfcb02a08a8096dd/LICENSE)
and [NOTICE](https://huggingface.co/handy-computer/nemotron-3.5-asr-streaming-0.6b-gguf/blob/53ce288f69326b415aeef030cfcb02a08a8096dd/NOTICE).
OpenMDW grants broad permissions subject to conditions including license/origin
retention and leaves third-party-rights clearance to the user; never relabel it
“Hex MIT.” Cargo-pinned transcribe.cpp has [MIT LICENSE](https://github.com/anomalyco/transcribe.cpp/blob/7045d1c0fe7cee37747b2792977421d435e75fca/LICENSE),
but the full selected native/Cargo/model closure remains unaudited. No code was
copied; absence of clearance is not a blanket prohibition.

**Next gate:** reuse existing Qwen/Whisper/SenseVoice and glossary work rather than
duplicating adapters. Compare against [current real-speech evidence](../REAL_SPEECH_EVALUATION.md).
Only a concrete helper benefit justifies a separately authorized owned-process
probe: versioned IPC, language guards, kill/restart, generation fencing, bounded
admission and truthful partial/stable/timestamp mapping. Test CPU and GPU backends
separately; scheduling ideas do not require importing the whole application.

## Minimum experimental record after hard gates

Reuse the [benchmark protocol](../BENCHMARK.md) and existing fixtures/catalog.
Freeze audio/glossary/model hashes, hardware, threads, cold/warm and decoding
policy. Keep failures and negative results. A tiny licensed pilot must measure
CER/WER, critical names/negation/verse numbers, actual first/stable delivery,
queue/duplicate/drop counts, RSS and cancellation. Do not rank unlike timing
contracts. Only a promising pilot justifies 30/60/90-minute and physical 4/8GB
trials. Any architecture/default adoption requires its own reviewed
[ADR](../decisions/README.md) and field acceptance; this triage supplies neither.

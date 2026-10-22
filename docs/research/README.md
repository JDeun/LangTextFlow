# Research index — LangTextFlow

This directory stores **evidence and hypotheses**, not an unreviewed wiki or automatic product acceptance. All research changes are reviewed with code via GitHub PRs. See [research template](RESEARCH_TEMPLATE.md) and [decision records](../decisions/README.md).

## Existing research

| Topic | Evidence | Status | Follow-up |
|---|---|---|---|
| Open-source dictation architectures (Handy, OpenWhispr, Wisper, FluidVoice, OpenWhisper, Hush, AuraScribe) | [Comparison](OPEN_SOURCE_DICTATION_COMPARISON.md) | README-level analysis; code audit, license review and measurements **pending** | #209, #216–#222 |
| ASR CPU candidates (SenseVoiceSmall, Qwen3-ASR, Zipformer, whisper.cpp) | [Model guide](../MODEL_GUIDE.md), [benchmark protocol](../BENCHMARK.md) | Candidates; no implied field qualification | #111–#115, #127 |
| Translation model comparisons and six directions | [Gemma research](../GEMMA_TRANSLATION_RESEARCH.md), [translation protocol](../TRANSLATION_BENCHMARK.md) | Research/experiments; no implied model promotion | #117, #118, #128 |
| Apple system translation | [Platform research](../APPLE_SYSTEM_TRANSLATION.md) | API/feasibility research; qualification pending | #128 |
| Church-first quality and glossary | [Quality baseline](../CHURCH_QUALITY_BASELINE.md), [model guide](../MODEL_GUIDE.md) | Protocol/criteria; human-reviewed evidence pending | #126, #130, #139 |

## Research workflow

1. Search this index and the issue tracker **before** opening a new research task; cross-link prior work rather than duplicating it.
2. Start from [RESEARCH_TEMPLATE.md](RESEARCH_TEMPLATE.md); state a falsifiable question, source URLs and retrieval date, license constraints, and relevance to ko/en/ja.
3. Keep **external claims**, **locally reproduced results**, and **proposals** separate. Never invent benchmarks, hardware results, or code audits.
4. For experiments record fixture provenance/consent, model revision/hash, CPU/RAM/OS, command, run count, CER/WER/RTF/latency/RSS, result artifact and known limitations.
5. If a design choice is accepted, create an [ADR](../decisions/README.md); if it needs implementation, open/link an issue with an acceptance gate.
6. Update this index in the same PR when adding a research note. Link to existing canonical documents rather than copying their content.

## Boundaries

- Documentation research does **not** imply licensing clearance or commercial deployment approval.
- Synthetic smoke tests are not consented real-speech quality evidence, and GitHub Actions CPU results are not a 4GB/8GB physical-machine qualification.
- Wiki pages, if enabled later, should link to this Git-versioned source of truth rather than diverge.

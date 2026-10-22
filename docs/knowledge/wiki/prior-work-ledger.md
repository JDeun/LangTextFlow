# Prior-work reuse ledger — avoid repeated experiments

- **Status:** issue/document audit; not a new benchmark or source-code verification
- **Last reviewed:** 2026-10-22
- **Evidence class:** existing repository research and issue reports; verify original artifacts before promoting conclusions

## Check before starting work

| Area | Already documented / attempted | Known outcome or uncertainty | Reuse / next gate |
|---|---|---|---|
| Korean real-speech ASR | faster-whisper tiny CPU int8, three licensed FLEURS clips | Issue #101 reports mean CER 3.467 / WER 2.961 / RTF 0.7894 on three clips (workflow 38274657250, PR #89 SHA 9ad804df); **root cause remains under investigation**, not a general model ranking | Reuse identical licensed fixtures, reference and metrics; investigate #101 and alternatives #111–#115 before repeating |
| ASR→MT CPU end-to-end | FLEURS + TranslateGemma on GitHub Actions CPU | Issue #117 reports translated-caption p95 39.11s, pipeline RTF 2.1131 and context JSON leakage (workflow 38319834291); formatting mitigations are marked done, but **same-SHA quality re-runs remain open** | Reproduce against pinned workflow/artifact before changing translator; compare #118 and #128 |
| Six language directions | ko/en/ja bidirectional requirements | Requirement and benchmark plans exist, but six-direction field acceptance is not established | Consult #126, #128 and [translation protocol](../../TRANSLATION_BENCHMARK.md); do not infer support from a single direction |
| Glossary/hotwords | CRUD, correction, prompt hints and starter terms described | Prompt inclusion is **not** guaranteed term enforcement; source-language scoping and six-direction ON/OFF remain open | Consult #130, #139 and [quality baseline](../../CHURCH_QUALITY_BASELINE.md); evaluate term recall and harmful replacements before adding a second glossary |
| Audio/VAD and caption stability | AudioWorklet, RMS telemetry, ASR fallback, partial/stable/committed and benchmark harness documented | Neural VAD and real-world latency superiority are not established | Check [dictation comparison](../../research/OPEN_SOURCE_DICTATION_COMPARISON.md), #210–#214 and #216–#222; benchmark existing path first |
| Low-cost CPU deployment | Linux CI and hardware benchmark protocols | GitHub Actions 4-vCPU runs are **not** donated 4GB/8GB Windows/Linux hardware validation | Preserve hardware, RAM, cold/warm, soak evidence; use hardware qualification issues rather than repeating CI smoke |
| Architecture decisions | Existing architecture plans and implementation issues | [ADR index](../../decisions/README.md) currently has no accepted formal ADR | Do not retroactively treat proposals as approved; record future accepted decisions with PR provenance |

## Audit findings: verified issue state and gaps

- **#101 (open):** baseline reports 3 clips; per-clip artifact inspection, double-counted partials, waveform conversion and larger-corpus root-cause work remain unchecked. Do **not** label faster-whisper intrinsically unsuitable from this pilot alone.
- **#117 (open):** JSON envelope mitigation is checked in the issue, but same-fixture post-fix reruns and long-soak quality gates are unchecked. The 39s latency belongs to the original recorded run, **not necessarily the current code**.
- **#118 (open):** OPUS-MT, NLLB and TranslateGemma comparison is a proposal, **not** measured comparative evidence. NLLB's stated noncommercial research license requires separate review.
- **#110 (closed):** closure does **not** by itself prove SenseVoice, Qwen3-ASR, Zipformer or whisper.cpp met the benchmark; subissues #111–#115 and run artifacts must be checked.
- **#139 (open):** glossary ON/OFF six-direction end-to-end qualification remains an acceptance gap, separate from UI CRUD.
- **Coverage limitation:** this audit sampled research/benchmark issues and existing documents, not every historical PR, CI artifact or code path. Broader traceability is still pending.

## Historical PR reconciliation (spot-verified merge status)

- **Real-model evidence exists:** PRs #89 (FLEURS ASR), #108 (20-case CPU translation), #109 (full CPU speech-to-caption), #116 (ASR candidate harness) were **merged**. These PRs establish runnable harnesses, **not** passing accuracy/latency gates. Reuse their pinned workflow inputs and reports.
- **Known ASR instrumentation fix:** PR #150 was **merged** to deduplicate repeated STABLE revisions. Thus #101's original CER/WER numbers must not be used as a post-fix current baseline without a new pinned rerun.
- **Architecture decision evidence:** PR #164 was **merged** with a runtime-v2 ADR. The formal ADR index currently says none accepted: reconcile the actual ADR file/status before claiming there are no decisions. Do not overwrite historical status.
- **Existing local MT:** PR #168 was **merged** and adds an **experimental M2M100 CTranslate2 CPU provider**. Do not start a second adapter without examining it and its six-direction qualification.
- **Existing multilingual glossary:** PRs #181 and #182 were **merged** (missionary EN/JA seeds and project overlays). PR #184 was **closed without merge**; do not count it as a second implementation.
- **Consolidated ASR fallback fixes:** PRs #197 and #198 were **closed without merge**, superseded by **merged #199**. Avoid reimplementing the abandoned stacked branches.
- **Heavy benchmark scheduling:** PR #176 was **merged** to pause automatic expensive model DAG during feature stabilization. A missing recent model result may reflect intentional scheduling, not absence of tooling.
- **Knowledge infrastructure:** PRs #230–#235 were **merged** (research/ADR templates, Wiki schema, lint, offline source registration, keyword search and review-only update plans). Do not rebuild these tools.

This reconciliation checks the named PRs' merge states, **not** all historical PR diffs or CI artifacts. Unmerged PR bodies can describe code that never reached main.

## Minimum handoff record for a failed or inconclusive experiment

1. **Question and previous attempt:** link the original issue, PR, exact commit and CI run/artifact.
2. **Inputs:** fixture license/provenance, language direction, sample count, model/version/hash and glossary configuration.
3. **Environment:** OS, CPU, RAM, quantization, thread count, cold/warm state and reproducible command.
4. **Measured outcome:** CER/WER/RTF/p50/p95/RSS, errors, confidence limits and what was *not* measured.
5. **Disposition:** rejected / inconclusive / needs retest, with the concrete condition that would justify a rerun.
6. **Follow-up:** link the canonical research report and issue; update this ledger only when new evidence changes the conclusion.

## Boundaries and evidence

This ledger indexes claims already written in project issues. It does **not** independently verify workflow artifacts, external model licenses, field deployment, or current implementation. Do not treat issue titles, proposed benchmarks, or passing CI as measured performance.

- [Canonical research index](../../research/README.md)
- [Open-source dictation comparison](../../research/OPEN_SOURCE_DICTATION_COMPARISON.md)
- [Benchmark protocol](../../BENCHMARK.md)
- [Translation protocol](../../TRANSLATION_BENCHMARK.md)
- [Research landscape](research-landscape.md)
- [Issue #101](https://github.com/JDeun/LangTextFlow/issues/101), [#117](https://github.com/JDeun/LangTextFlow/issues/117), [#118](https://github.com/JDeun/LangTextFlow/issues/118), [#128](https://github.com/JDeun/LangTextFlow/issues/128), [#209](https://github.com/JDeun/LangTextFlow/issues/209)

Return to the [knowledge index](index.md).

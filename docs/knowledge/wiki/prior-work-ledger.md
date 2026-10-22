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
- **Architecture decision evidence:** PR #164 was **merged** but changed only `docs/REALTIME_ARCHITECTURE_V2.md`; that document explicitly says **Status: proposed**. The formal ADR index correctly says no **accepted formal ADR in `docs/decisions/`**. This is a proposed architecture record, not an accepted decision; no index correction is justified.
- **Existing local MT:** PR #168 was **merged** and adds an **experimental M2M100 CTranslate2 CPU provider**. Do not start a second adapter without examining it and its six-direction qualification.
- **Existing multilingual glossary:** PRs #181 and #182 were **merged** (missionary EN/JA seeds and project overlays). PR #184 was **closed without merge**; do not count it as a second implementation.
- **Consolidated ASR fallback fixes:** PRs #197 and #198 were **closed without merge**, superseded by **merged #199**. Avoid reimplementing the abandoned stacked branches.
- **Heavy benchmark scheduling:** PR #176 was **merged** to pause automatic expensive model DAG during feature stabilization. A missing recent model result may reflect intentional scheduling, not absence of tooling.
- **Knowledge infrastructure:** PRs #230–#235 were **merged** (research/ADR templates, Wiki schema, lint, offline source registration, keyword search and review-only update plans). Do not rebuild these tools.

This reconciliation checks the named PRs' merge states, **not** all historical PR diffs or CI artifacts. Unmerged PR bodies can describe code that never reached main.

## Additional merged-work inventory (verified PR states)

- **Existing benchmark harnesses:** #12 (ASR/runtime), #27 (translation), #28 (correction). Reuse these before designing new measurement scripts.
- **Real-data benchmark workflows:** #89 (FLEURS), #108 (CPU MT), #109 (CPU end-to-end), #116 (five ASR candidates). Harness or workflow merged **does not** imply model passed.
- **Multilingual and glossary qualification code:** #124 (operator editor and seeds), #125 (mapping/diagnostics), #129 (six-direction test), #134 (language-scoped terms), #135 (glossary-aware MT test), #138 (model qualification gates), #140 (Fun-ASR glossary ON/OFF test). All PRs verified **merged**, but their actual test reports and runtime coverage are not certified here.
- **Model research automation:** #146 (candidate catalog), #147 (Apple translation experiment), #148 (model experiment DAG), #149 (controller repair) are **merged**; #176 later paused automatic expensive runs. Search these workflows before scheduling repeat experiments.
- **Quality safeguards:** #150 (stable-revision deduplication) and #151 (scripture-number/negation assessment) are **merged**. Verify metric baselines against the commit that produced them.

## Audit coverage accounting

- GitHub search yielded **178 PR records** across three creation-date partitions and **58 issue records** in the repository; these are **inventory search results**, not 178 individually fetched PR diffs or 58 audited issue histories.
- Named PRs above were individually fetched for merge status. No claim is made that all 178 PR file lists, 58 issue comment histories, workflow artifacts, or current runtime call paths have been inspected.
- Before treating this ledger as exhaustive, reconcile every historical PR number and inspect relevant changed files and exact-head workflow artifacts. Preserve explicit `unverified` labels for uninspected entries.

## Current main code cross-check (file existence only)

PR changed-file lists for #12, #27, #28, #124, #125, #129, #134, #135, #138, #140, #146–#151, #168 and #199 were retrieved. Five critical implementation entrypoints were also fetched successfully from current `main`:

- `apps/server/langtextflow/benchmark.py` — existing ASR benchmark implementation (#12, #150).
- `apps/server/langtextflow/translation/m2m100_ct2.py` — existing experimental local MT adapter (#168).
- `scripts/compare_multilingual_m2m100_cpu.py` — existing six-direction CPU MT evaluation (#129, #135, #140).
- `scripts/autonomous_experiment_conveyor.py` — existing model experiment controller (#148, #149).
- `apps/server/langtextflow/asr/fallback.py` — existing ASR fallback implementation (#199).

**Evidence tier:** verified path exists in `main`, **not** proof that the feature is enabled, correct, benchmark-qualified or usable on target hardware. Next validation must inspect imports/configuration and run exact-head tests and real-model experiments.

## Current runtime wiring (static code inspection)

- **M2M100 is not an orphaned experiment script.** `pipeline.py` explicitly accepts `translation_provider="m2m100-ct2"` (lines 86–102) and constructs `M2M100CT2Translator` (lines 284–299); `translation/capabilities.py` advertises it with a local model preflight, and `config.py` provides local model/tokenizer paths and bounded CPU thread settings. **It remains opt-in and requires provisioned local assets**; no automatic model download or default activation is inferred.
- **Replay fallback is runtime-wired.** `runtime.py` imports and constructs `ReplayFallbackAsrEngine` (near line 265); `asr/fallback.py` implements the bounded replay/candidate lifecycle. Static wiring does **not** establish real-microphone recovery reliability.
- **Evaluation is separately runnable.** `scripts/compare_multilingual_m2m100_cpu.py` has a CLI `main()` and independent test fixture/glossary logic; existence is not proof that all six directions passed semantic acceptance.
- **Model readiness must be checked:** `local_mt_preflight.py` tests configured cache paths, so a provider visible in code can be unavailable on a new installation.

## Archived CI artifact verification (downloaded and inspected)

The following **original ZIP artifacts** were retrieved from GitHub Actions and their JSON reports inspected, rather than relying only on issue descriptions:

| Run / artifact | Directly verified result | Interpretation |
| --- | --- | --- |
| [38274657250](https://github.com/JDeun/LangTextFlow/actions/runs/38274657250), `licensed-fleurs-asr-ko-tiny` | `summary.json`: FLEURS revision `fdd74f7...`, three Korean clips, faster-whisper tiny CPU INT8; mean CER **3.4669667**, WER **2.9614667**, RTF **0.7893667**. Per-clip CER **1.2679 / 8.8095 / 0.3235**. | Severe variance and error in this **pre-#150 pilot**; not a current baseline. |
| [38318475793](https://github.com/JDeun/LangTextFlow/actions/runs/38318475793), `church-translation-cpu-baseline` | `church-translation-quality.json`: **needs-improvement**, 20 synthetic/unreviewed cases, chrF++ **42.134**, terminology **0.8182**, p95 **12916.1 ms**, eight cases with missing critical terms; model manifest TranslateGemma 4B Q4_K_M. | Model execution succeeded, translation quality **did not pass**. Metadata JSON appears in `church-translation-inference.json` hypotheses. |
| [38319834291](https://github.com/JDeun/LangTextFlow/actions/runs/38319834291), `church-live-cpu-e2e` | `live-interpretation-report.json`: 33.22 s FLEURS KO speech → EN; ASR CER **0.1894**, WER **0.5**; 4/4 translated committed, first translation **28838.3 ms**, processing RTF **2.1131**, `field_ready=false`. `translated_hypothesis` contains JSON envelopes. | Functional end-to-end run **not real-time or field-qualified**; uses **base** ASR, not tiny. |

All three artifact listings reported `expired=false` at inspection time. These reports are historical snapshots, **not** fresh same-SHA reruns or independent human translation assessment. In particular the FLEURS ASR pilot uses **tiny** while the full E2E run uses **base**; their CER/WER must not be directly compared as a controlled before/after.

## Later evidence discovered in issue comments (2026-10-13–14)

**Do not mistake the original negative baseline for the latest available measurement.** These entries were read from issue comment histories; unlike the three archived ZIPs above, their **newer artifact contents have not yet been independently re-extracted in this audit**.

- [#101](https://github.com/JDeun/LangTextFlow/issues/101), matched FLEURS audio (same IDs and PCM SHA-256), 4-second vs 30-second decode, run [38315153468](https://github.com/JDeun/LangTextFlow/actions/runs/38315153468): tiny CPU INT8 mean CER **3.46697 → 0.21973**, WER **2.96147 → 0.66437**, RTF **0.78937 → 0.04540**. Strong evidence for chunk/context sensitivity; **not** evidence that 30-second live caption startup delay is acceptable.
- [#117](https://github.com/JDeun/LangTextFlow/issues/117), post-fix runs [38341728611](https://github.com/JDeun/LangTextFlow/actions/runs/38341728611) and [38341593011](https://github.com/JDeun/LangTextFlow/actions/runs/38341593011): context JSON absent in measured public translation, live p95 **10,990.7ms** (down from 39,113ms), processing RTF **1.1801** (down from 2.1131); chrF++ **55.202** (from 42.134) but critical-term accuracy **78.18%** (from 81.82%). Still **field_ready=false**, with semantic errors.
- [#117](https://github.com/JDeun/LangTextFlow/issues/117), later **regression/failure** in merged-runtime Actions run [38570380566](https://github.com/JDeun/LangTextFlow/actions/runs/38570380566): TranslateGemma 4B CPU target translation **timed out after 20.0s**. This is negative evidence after the above improvement, not a passed sustained-latency result.
- [#118](https://github.com/JDeun/LangTextFlow/issues/118), same-three-clip ASR candidate measurements cited in comment: Qwen3-ASR 0.6B INT8 CER **0.0554**, RTF **0.2902**, peak RSS **3528 MB**; Qwen3-ASR 1.7B INT8 CER **0.0475**, RTF **0.5524**, peak RSS **4858 MB**; Zipformer Korean INT8 CER **0.1641**, RTF **0.0609**, peak RSS **2255 MB**; whisper.cpp base CER **0.1203**, RTF **0.2230**, peak RSS **2155 MB**. Different latency semantics (offline vs native partial); do not rank solely by CER/RTF or assume co-resident 4GB feasibility.

**Priority follow-up:** download newer #101/#117 run artifacts, compare exact hashes/configurations and extract quality/latency regressions; then check #119 co-resident ASR+MT reports and current CI. #101, #117, #118 and #139 remain open despite historical merged test harnesses.

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

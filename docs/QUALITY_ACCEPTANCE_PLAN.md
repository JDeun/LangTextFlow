# LangTextFlow — Quality & Field Acceptance Plan v1

**Status:** Proposed preregistration baseline; thresholds are **provisional engineering targets**, not achieved measurements or a signed release SLA.  
**Date:** 2026-10-22  
**Related:** [PRD](PRD.md), [North Star](PRODUCT_NORTH_STAR.md), [evidence audit](knowledge/wiki/audit-coverage-matrix.md).

## 1. Separate test levels

1. **Contract/unit:** event version monotonicity, project isolation, snapshot persistence, bounded queue and degraded status.
2. **Pinned CPU benchmark:** GitHub Actions Linux CPU with recorded model/data SHA, CPU, thread count, peak RSS, license, cold/warm start; paired same-input baselines.
3. **Real-speech end-to-end:** continuous KO/EN/JA audio → ASR → optional correction → two MT targets → audience UI; collect independent human bilingual references.
4. **Field:** 30, 60 and 90 minutes on actual supported devices, real microphone/audio interface, reconnect, silence, applause, interruptions and restart.

## 2. Preregistered provisional gates

| Metric | Provisional gate | Measurement |
|---|---|---|
| First useful **source** caption | p95 ≤ 3 s on tested continuous-audio profile | microphone PCM timestamp → first readable audience event; include buffering |
| Stable source caption | p95 ≤ 5 s | audio segment end → STABLE audience event |
| Translated caption | p95 ≤ 8 s and p99 ≤ 12 s | spoken segment end → corresponding target visible; report per direction |
| Long-run backlog | No unbounded growth; no sustained > 10 s oldest pending work | sample queue age/depth at 1 s intervals |
| Dropped/incorrectly committed segments | 0 silent losses and 0 stale-version overwrites in test | event reconciliation with source recording |
| ASR CER/WER | Report both per source; **no universal CER pass threshold until human reference corpus approved** | normalizations frozen; include proper nouns and negation slices |
| MT meaning | **No numerical pass until bilingual rubric and calibration set are approved** | blind independent bilingual human review, serious error and omission rate |
| Glossary | No demonstrated benefit unless reference has matching terms; no serious new false insertion/meaning regression | OFF/ON matched audio and text; report recall, precision and per-direction deltas |
| 90-minute stability | No crash, loss of source-caption continuity, unbounded RSS growth or unbounded backlog | repeated real sessions with CPU/RAM/latency traces |
| Cold start and peak RAM | Measure and publish per runner/device profile; pass only against explicit supported hardware budget | full process-tree memory including models and sidecars |

**These timing numbers are design targets for review, not externally validated commitments.** Do not label a test PASS where the rubric or corpus is not yet approved; use `BLOCKED: acceptance criterion incomplete`.

## 2a. Threshold provenance and approval status (2026-10-22)

**Important correction:** the 3 s / 5 s / 8 s / 12 s timing thresholds and 10 s queue-age alert were **author-proposed heuristic design targets**, not derived from a published user study, competitor measurement, field trial, or statistical analysis of this project's actual latency distribution. They must **not** be described as evidence-based pass/fail standards or accepted SLAs. The zero-silent-loss/stale-overwrite conditions are architectural safety invariants, not empirical performance estimates. The 30/60/90-minute durations are scenario coverage goals from the existing roadmap, not proven endurance limits.

| Proposed value | Provenance | Confidence / next action |
|---|---|---|
| First source caption p95 ≤ 3 s | Heuristic UX hypothesis; no cited human usability study | UNVALIDATED: measure source-caption reading usability and real audio timestamps |
| Stable source p95 ≤ 5 s | Heuristic budget; not derived from model latency | UNVALIDATED: trace VAD, ASR chunking and UI separately |
| Translated p95 ≤ 8 s / p99 ≤ 12 s | Heuristic end-to-end budget; not calibrated to actual hardware or audience | UNVALIDATED: collect six-direction continuous speech distributions and user acceptance |
| Oldest pending work ≤ 10 s | Heuristic overload warning, not an approved threshold | UNVALIDATED: characterize queue age and recovery under sustained load |
| Zero silent loss / stale overwrite | Product safety invariants from versioned event architecture | REQUIRED INVARIANT: verify with deterministic fault injection |
| 30/60/90 min soak | Existing roadmap's real-event test durations | SCENARIO TARGET: measure, do not claim passed |
| ASR CER and MT meaning | No approved numerical thresholds | BLOCKED pending licensed corpus, bilingual rubric and review |

**Evidence already in repository:** [prior-work ledger](knowledge/wiki/prior-work-ledger.md), [multilingual baseline](MULTILINGUAL_MISSIONARY_INTERPRETATION.md), [Fun-ASR A/B](FUNASR_MLT_GLOSSARY_CPU.md) and [TranslateGemma investigations](knowledge/wiki/audit-coverage-matrix.md) show that actual results depend on model, direction, clip length and runner; they **do not** establish these proposed timing cutoffs. Before making release decisions, freeze a measured baseline and user-reviewed target in a separate PR/ADR. Until then, report actual distributions and mark numerical timing qualification **BLOCKED (threshold unapproved)** rather than PASS or FAIL.

## External reference evidence

See [External Benchmark & Commercial Service Evidence](EXTERNAL_BENCHMARK_RESEARCH.md) for source-linked IWSLT 2024/2025 simultaneous translation lag regimes, academic warnings about segmented-speech and computation-aware latency, and documented Microsoft Teams/Google Meet caption capabilities. **IWSLT EN→JA StreamLAAL 0–3.5s low / 3.5–5s high is a research lag regime, not an audio→audience p95 bound**; commercial help pages establish features but do not substantiate this project's 3/5/8/12-second timing hypotheses. Keep the provisional end-to-end numbers unapproved pending baseline and human UX study.

**Specialist market cross-check:** [commercial service research](EXTERNAL_BENCHMARK_RESEARCH.md#specialist-commercial-services-primary-source-capability-comparison-2026-10-22) includes vendor-reported KUDO **4.1 s mean**, sotonis **within 2 s of first word**, and Mission21 **~1 s after sentence end**. These are **incompatible definitions and unverified vendor claims**, so they do not establish LangTextFlow's p95 3/5/8/12 s thresholds. Cuckoo and KUDO are useful feature-parity comparators for glossary, context, QR, projector and OBS.

## 3. Sampling and evaluation

- All six directed language pairs; mandatory EN→KO and JA→KO. Report each separately and **never average away failures**.
- Establish a **licensed/consented, nonpublic where necessary** set of real sermons or representative lectures, with 20+ glossary-bearing speech clips **per direction** as a minimum research target, plus no-glossary negative controls; not sufficient alone for field readiness.
- Include negation, numbers, Scripture books/chapters/verses, names, theological terms, accents, pauses, code-mixed distractors and ambiguous terminology.
- Freeze independent source transcription and target meaning references **before** inference. No selection of prompts from test reference.
- Human bilingual reviewers judge meaning, omissions, insertions, doctrinal reversals and usability; track disagreement/adjudication and anonymize reviewer data.
- Paired OFF/ON must fix audio, model SHA, CPU budget, language, decoding and glossary snapshot; counterbalance execution order, warmups and repeats. Record false insertions.
- Report sample counts, uncertainty, p50/p95/p99, cold vs warm, timeouts, failures and negative results. Small FLEURS samples are smoke tests only.

## 4. Failure and recovery scenarios

Exercise MT timeout, ASR exception, CPU overload, memory pressure, audio device loss, audience disconnect/reconnect, restart during session, malformed glossary, unauthorized project edit, and source/target mismatch. Source captions remain available where the ASR is functioning. Verify independent target-language failures and no silent cloud fallback.

## 5. Required evidence bundle

Commit SHA, workflow/run URL, exact model/weights SHA and license, dataset/audio SHA and rights, source/target pair, glossary export SHA, commands, hardware, thread/memory profile, original per-sample hypotheses/references (only if licensed), events/queue logs, p50/p95/p99, CER/WER/chrF++, human rubric and disagreements, negative controls, failures, and a scope-specific PASS/FAIL/BLOCKED verdict.

## 6. Promotion and rollback

Gate order: code → benchmark → real speech → field. A failure in EN→KO or JA→KO blocks the founding missionary release. If semantic rubric, rights, physical test or signed installer is missing, mark **BLOCKED** rather than PASS. Threshold changes must be reviewed in a new PR **before** rerunning qualification; preserve earlier reports.

## 7. Known evidence gap

The recorded Fun-ASR MLT 3×3 FLEURS ON/OFF run has **zero glossary-bearing references** and does not qualify church hotword efficacy; M2M100 runtime does not yet use glossary context. See [Fun-ASR report](FUNASR_MLT_GLOSSARY_CPU.md) and [glossary research](GLOSSARY_CPU_QUALIFICATION.md).

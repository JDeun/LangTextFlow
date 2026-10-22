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

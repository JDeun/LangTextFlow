# M0 GitHub issue and milestone assignment audit

## Full issue acceptance audit (2026-11-03)

Inspected main: `e60365c35adfffe7dd76009de0e89b32f21808bc`.
GitHub Issues API pagination excluded PRs and found **65 issues: initially 46 open /
19 closed**. All 65 bodies and their complete comment histories were re-read in
three parallel workstreams plus root review, with linked implementation/closure
records compared against current source, tests and canonical reports. This is an
issue-acceptance audit, not a line-by-line rereview of every historical PR or field
certification. The dated snapshots below are historical.

After checking original acceptance rather than adding field gates to research:
**#101 and #128 closed completed; #159 reopened** because its explicit keep-open
acceptance had not been fulfilled. The resulting inventory is **45 open / 20 closed**
(19 completed, one not_planned consolidation). No model default changed and no
milestone or release was declared complete.

| Verified stage | Open issues |
|---|---:|
| M0 / ID 1 | 11 |
| M1 / ID 2 | 8 |
| M2 / ID 3 | 17 |
| M3 / ID 4 | 4 |
| M4 / ID 5 | 5 |
| **Total** | **45** |

All 45 open issues have existing labels/milestones and no assignee; all six exact
M0–M5 milestone titles were verified through the repository API. Counts exclude
PRs and are not a completion percentage.

### Findings and active implementation

**Model selection is not the only remaining work.** Required code includes #86's
project digest/review and complete restorable archive; #344's recorded Rust-core
target; native-MT install/repair UX; benchmark/experiment access; and the specific
correction/metric defects below. Several research deliverables are also unfinished.

The [owner decision in #344](https://github.com/JDeun/LangTextFlow/issues/344#issuecomment-6536557339)
records Rust authoritative orchestration + Python inference workers as the target,
preserving Python until vertical-slice parity and rollback pass. Existing Python
failure isolation is real completed work, but echo IPC does not fulfill that
target. Earlier optional-only descriptions were incorrect and are corrected in
the [protocol](architecture/rust-python-worker-protocol.md) and
[source triage](research/optional-asr-upstream-triage-2026-11.md).
Third-party Hex code/model adoption remains a separate decision.

The audit reproduced two distinct defects: deterministic aliases can change
`not saved` to `saved` or `John 3:16` to `John 3:17` (#160), and the
observation metric counts `act` inside `action` (#122). Their focused fix is in
progress separately from efficacy qualification. [PR #407](https://github.com/JDeun/LangTextFlow/pull/407)
subsequently merged as `66822c40a2a9d69be09ce194e2613905ee92ff4a`: it exposes
existing M2M100 to the runtime benchmark and prevents unavailable/failed MT from
reporting source-only success. Exact-head CI41642506656/CodeQL41642506657 and
15 independently rerun focused tests passed. Its long-run matrix remains unexecuted.
Project digest and supervised real-model Rust worker slices are separate parallel
implementation tracks; they are not claimed complete by this document.

### Open issues — every issue individually reconciled

CODE identifies absent implementation or a reproduced defect; VALIDATION requires
execution/review, RESEARCH a specific unfinished study, EXTERNAL/RIGHTS/INPUT an
unavailable resource or decision. Multiple labels can apply. Conditional research
does not authorize blindly adding every model or production experiment.

| Issue | Established scope | Remaining acceptance / next action |
|---|---|---|
| [#349](https://github.com/JDeun/LangTextFlow/issues/349) | Pinned Notesoop source/Apache license triage completed; no weights executed. | RESEARCH / RIGHTS: AI Hub/LM/derived-artifact provenance, bounded KO-only probe and conditional adapter remain; Apache labeling alone does not close rights. |
| [#344](https://github.com/JDeun/LangTextFlow/issues/344) | Pinned Hex source/design audit and tested Rust↔Python echo IPC scaffold exist. | CODE: recorded user-approved Rust authoritative core is not implemented. Real-model typed IPC, enforced deadline/cancel/restart, audio→event→WS parity and rollback precede cutover; optional Hex adoption is separate. |
| [#341](https://github.com/JDeun/LangTextFlow/issues/341) | Pinned LFM3 source/license triage completed; no model/adaptor admitted. | RESEARCH / RIGHTS: custom terms, remote code and CPU feasibility unresolved; optional model non-adoption/deferment must be explicit, not a fabricated performance failure. |
| [#274](https://github.com/JDeun/LangTextFlow/issues/274) | Whistle optional English-only research protocol exists. | RESEARCH / RIGHTS: primary runtime/model pins, platform/license feasibility and actual English CPU comparison; no adapter or independent inference yet. |
| [#224](https://github.com/JDeun/LangTextFlow/issues/224) | Milestones M0–M5, labels, issue/PR triage and enforced CI/security gates verified. | ADMIN / VALIDATION: name accountable approvers; finish baseline, hardware, privacy, signing and installer acceptance. Keep open. |
| [#222](https://github.com/JDeun/LangTextFlow/issues/222) | AuraScribe README/runtime comparison exists; Zipformer was independently measured. | RESEARCH / RIGHTS: pinned functions, selected model rights/languages/platforms and bounded KO/JA CPU/cancel comparisons remain. |
| [#221](https://github.com/JDeun/LangTextFlow/issues/221) | Hush README capture comparison exists; local audio ownership fixes are implemented independently. | RESEARCH: pin capture/silence/teardown functions, review selected licenses and compare specific lifecycle fixtures; OS paste remains excluded. |
| [#220](https://github.com/JDeun/LangTextFlow/issues/220) | OpenWhisper README multi-engine/preview comparison exists. | RESEARCH: pinned batch/partial/fallback semantics, licenses and same-input revision/preview fixture; existing local stages do not complete upstream audit. |
| [#219](https://github.com/JDeun/LangTextFlow/issues/219) | FluidVoice README lifecycle comparison and local sidecar/cache mechanisms exist. | RESEARCH: pinned capture/load/lifecycle functions, rights and source-specific cold/warm/RAM comparison remain. |
| [#218](https://github.com/JDeun/LangTextFlow/issues/218) | Wisper README dictionary/code-switch comparison exists; local glossary is implemented independently. | RESEARCH: actual pinned feedback/language paths, licenses and mixed-speech comparison; manual routing is not automatic code-switching. |
| [#217](https://github.com/JDeun/LangTextFlow/issues/217) | OpenWhispr README provider/correction comparison exists. | RESEARCH: pin and trace routing/cancel/backpressure/correction, review selected licenses and define executable thresholds; distinct from OpenWhisper #220. |
| [#216](https://github.com/JDeun/LangTextFlow/issues/216) | Handy README comparison and existing LangTextFlow VAD seams identified. | RESEARCH: pin source/functions/licenses; finish capture/VAD/Parakeet lifecycle audit and a specific licensed silence/music/resume comparison. |
| [#214](https://github.com/JDeun/LangTextFlow/issues/214) | Generic runtime runner supports repeated targets/durations; merged #407 fixes native-MT selection and false source-only success. | VALIDATION: same-machine repeated cold/warm language/fanout/long-event baseline remains unexecuted; a runnable CLI is not its result. |
| [#213](https://github.com/JDeun/LangTextFlow/issues/213) | Fixed-language routing and separate-language fixtures exist. | RESEARCH CODE / INPUT: within-utterance KO/EN corpus/runner and CER/WER/terms/hallucination/MT-contamination comparison absent; separate-language tests do not qualify code-switching. |
| [#212](https://github.com/JDeun/LangTextFlow/issues/212) | Glossary CRUD/scopes/recommendations, snapshot review and transcript review exist. | RESEARCH CODE / UX: correction→proposal→approval/undo/session-boundary prototype/evaluation absent; active snapshots must remain immutable until explicitly defined applicability. |
| [#211](https://github.com/JDeun/LangTextFlow/issues/211) | Energy-VAD monitoring and Whisper internal VAD already exist, with different roles. | RESEARCH CODE / INPUT: matched RMS/Silero/WebRTC false-trigger/missed-speech/resume scorer and annotated noise/music/pause experiment are absent. |
| [#210](https://github.com/JDeun/LangTextFlow/issues/210) | Stage telemetry, monotonic revision/commit contracts and regression fixtures exist. | RESEARCH CODE: same-fixture stabilizer comparison for revision rate, premature commits, rework and WER-worsening rejection not implemented; production change is conditional. |
| [#209](https://github.com/JDeun/LangTextFlow/issues/209) | Competitive research and optimization roadmap delivered. | RESEARCH / VALIDATION: five experiment children and seven source audits remain incomplete; public marketing is not measured superiority. |
| [#160](https://github.com/JDeun/LangTextFlow/issues/160) | Source-scoped four-tier snapshots, preview digest, hotword routing and event provenance implemented. | CODE / VALIDATION: deterministic aliases can remove negation or change verse numbers; fix/regressions in progress. Native six-way glossary efficacy remains separate. |
| [#159](https://github.com/JDeun/LangTextFlow/issues/159) | Python nonblocking publication, bounded client/persistence queues, loss/eviction metrics and shutdown policy implemented. | REOPENED / VALIDATION: original body/comments explicitly kept deployed multi-client/long-soak/disk acceptance open. Restore tracking in M1; required Rust authority is separate #344 work. |
| [#158](https://github.com/JDeun/LangTextFlow/issues/158) | Truthful ASR stages, PCM clock, bounded VAD recovery/replay/failover and persisted timing provenance implemented. | VALIDATION / ARCHITECTURE: independently aligned real word/mic/failover evidence and Rust audio/VAD authority; estimates must stay labeled. |
| [#157](https://github.com/JDeun/LangTextFlow/issues/157) | Opt-in six-way M2M adapter, pinned CLI provisioner, child deadlines/cancel/reap and truthful provenance implemented. | CODE / VALIDATION: shell-free install/repair UX is absent. Fail-closed term checking is not native bias. Qualify selected checkpoints, native fanout, rights and devices. |
| [#156](https://github.com/JDeun/LangTextFlow/issues/156) | Python revision fencing/per-language state persist through actual API/WS/SQLite/reconnect/export; browser mixed-target proof exists. | VALIDATION / ARCHITECTURE: native six-way/two-target sustained quality and physical acceptance; Rust authoritative state remains #154/#344. |
| [#155](https://github.com/JDeun/LangTextFlow/issues/155) | Python immediate source publication, bounded admission, independent target outcomes, cancellation/drain implemented. | VALIDATION / ARCHITECTURE: no additional Python scheduler defect found; native two-target CPU/soak and required Rust scheduling ownership remain. |
| [#154](https://github.com/JDeun/LangTextFlow/issues/154) | Python bounded failure isolation, revisions/provenance, killable native MT and mixed-target browser paths implemented. | CODE / VALIDATION: approved Rust authority and real-worker→Rust commit path absent; turnkey setup and native long-run qualification remain. Native runner access fixed by #407. |
| [#139](https://github.com/JDeun/LangTextFlow/issues/139) | Separate native ASR hotword and MT reranker experiments executed. | VALIDATION / INPUT: same-session ASR×MT four-arm experiment with ≥20 relevant annotated cases/direction, semantic gold and ≥30-minute runtime evidence; no-relevant clips are not_exercised. |
| [#137](https://github.com/JDeun/LangTextFlow/issues/137) | Public feature comparison and owned Operator/Glossary REA observation executed. | VALIDATION: meaningful multilingual glossary ON/OFF matrix, current-preview/audience observation and linked gap reconciliation; no competitor parity/quality certification. |
| [#127](https://github.com/JDeun/LangTextFlow/issues/127) | Offline three-language ASR and six-way MT helpers executed; manual source override exists. | CODE / VALIDATION: native-streaming/LID confusion evaluator and representative annotated terms; current FLEURS runtime runner lacks JA. Automatic adoption is conditional. |
| [#126](https://github.com/JDeun/LangTextFlow/issues/126) | Dynamic source/target selection, fail-closed display and six-way helper runs exist. | VALIDATION: native six-way/two-target noisy long-event quality/CPU acceptance remains. Manual source selection does not establish automatic/code-switch routing; adoption is conditional on evidence. |
| [#122](https://github.com/JDeun/LangTextFlow/issues/122) | Target resolution, prompt hints, lexical validation and research reranking exist; rerank changed no outputs. | CODE / VALIDATION: source-term substring false positives and mixed-source metric contract; effective safe model-aware term intervention and semantic A/B remain. |
| [#121](https://github.com/JDeun/LangTextFlow/issues/121) | JA seed/editor/import/snapshot/prompt/lexical diagnostics implemented. | VALIDATION / INPUT: independent JA theological review and model ON/OFF efficacy; lexical matches do not certify meaning. |
| [#118](https://github.com/JDeun/LangTextFlow/issues/118) | OPUS research and current same-host 16-case MT comparison executed; all three candidates eligible. | VALIDATION: full 20-case scope, matched real audio, continuous queue, total RAM and reviewed meaning. Text-only OPUS speed is not a deployed optimum. |
| [#117](https://github.com/JDeun/LangTextFlow/issues/117) | Prompt/envelope-leak fixes and post-fix text/actual-speech reruns exist. | VALIDATION: current live latency, long continuous run and independent semantic gold; retain the later real 20-second timeout. |
| [#115](https://github.com/JDeun/LangTextFlow/issues/115) | Qwen 1.7B measured; this pilot shows no CER advantage over 0.6B and higher RSS. | VALIDATION / RIGHTS: matched baseline, converted rights and semantic/live/MT evidence; explicit low-resource deprioritization is possible. |
| [#114](https://github.com/JDeun/LangTextFlow/issues/114) | whisper.cpp multilingual base measured through the real CPU CLI. | CODE / VALIDATION: CLI load is inside decode, so generic cold-load timing is misleading; small and matched runtime/MT comparisons remain unmeasured. |
| [#113](https://github.com/JDeun/LangTextFlow/issues/113) | KO Zipformer native incremental input and OPUS helper pairing measured. | VALIDATION: number/word errors, timing/retractions and product-runtime soak; KO-only is not an EN/JA solution. |
| [#112](https://github.com/JDeun/LangTextFlow/issues/112) | Qwen 0.6B actual offline inference and ASR→MT helper combinations measured. | VALIDATION: matched base/small comparison, requested TG pairing, native/long-run semantics/RAM. A product adapter is conditional on selection. |
| [#111](https://github.com/JDeun/LangTextFlow/issues/111) | SenseVoice actual KO inference/metrics exist; CER 0.8995 on the three-clip pilot. | VALIDATION / RIGHTS: matched baseline and converted-weight rights; explicit current-shortlist non-adoption is possible without pretending field success. |
| [#107](https://github.com/JDeun/LangTextFlow/issues/107) | Physical-matrix protocol and memory/latency/queue tooling exist; native-MT CLI available after #407. | VALIDATION / EXTERNAL: actual 4GB/8GB/16GB 30/60/90-minute workload, swap/CPU/device/quality receipts remain. |
| [#106](https://github.com/JDeun/LangTextFlow/issues/106) | CPU profiles, offline six-way adapter, bounded runtime and provisioner/benchmark tools exist. | CODE / EXTERNAL: unified low-spec/church setup lacks native-MT install/repair; no qualified all-six model combination on physical 4GB/8GB or long cleared sermons. |
| [#104](https://github.com/JDeun/LangTextFlow/issues/104) | Killable cached CPU retranscription, versions/review/approval, explicit summary source and retry implemented. | VALIDATION / SCOPE: controlled model/window and native long-WAV lifecycle/resource tests; restart retries instead of continuing a partial attempt. Resolve that wording explicitly. |
| [#100](https://github.com/JDeun/LangTextFlow/issues/100) | Actual queued ASR→M2M and serialized hub delivery executed KO↔EN; generic native two-target CLI added in #407. | CODE / VALIDATION: live helper remains single-target KO/EN; consolidated adverse matrix, aligned MT gold and actual audience latency remain. |
| [#86](https://github.com/JDeun/LangTextFlow/issues/86) | Project search, citations, metadata manifests and page-scoped non-LLM reports implemented. | CODE: no project AI digest/chronology/version/review/stale graph, linked knowledge artifacts or complete package import/restore. Three-session round-trip acceptance remains. |
| [#79](https://github.com/JDeun/LangTextFlow/issues/79) | Opt-in WAV, SQLite linkage, seek/download, failure isolation and verified crash recovery implemented. | CODE-SCOPE / EXTERNAL: rows imply completion but lack explicit status/finalized_at; standalone recorder endpoints are absent. Reconcile those requirements and privacy/retention review. |
| [#78](https://github.com/JDeun/LangTextFlow/issues/78) | Per-session transcript/WAV/summary/review/export and durable media journal implemented. | CODE / VALIDATION / EXTERNAL: no FLAC choice or user retention policy; reconcile literal lifecycle scope, then native long-event meaning, physical mic and shipping privacy. |

### Closed issues — completion scope and exclusions

The initial 19 closed records were checked; #159 was reopened, while #101/#128
completed their original scoped acceptance. The 20 resulting closed issues are
listed here. A narrow implementation or research closure never certifies field
quality; #110 was consolidated, not completed as a benchmark.

| Issue | Current reason | Audited conclusion |
|---|---|---|
| [#342](https://github.com/JDeun/LangTextFlow/issues/342) | completed | Actual merged #340 and current hash-locked msgpack 1.2.4 fix the named advisory; normal security CI remains required. |
| [#276](https://github.com/JDeun/LangTextFlow/issues/276) | completed | Merged #277 and current hash-locked filelock 4.2.4 fix the original CVEs; no audit suppression substituted. |
| [#254](https://github.com/JDeun/LangTextFlow/issues/254) | completed | Opt-in privacy-bounded audience receive-to-paint estimator implemented and automated end-to-end checked (#352); physical audio→screen delay is separate. |
| [#172](https://github.com/JDeun/LangTextFlow/issues/172) | completed | Diagnosed locale/React/CDP startup race fixed by actual UI-driven locale selection (#173); later unrelated flakes do not invalidate that fix. |
| [#161](https://github.com/JDeun/LangTextFlow/issues/161) | completed | UI lifecycle/refactor and mocked mixed-target/reconnect acceptance verified (#358/#371/#391/#400); native hardware and human accessibility remain separate. |
| [#133](https://github.com/JDeun/LangTextFlow/issues/133) | completed | Editable/idempotent 157-term KO seed and bounded hints exist; field/theological efficacy remains #139, not a seed-implementation failure. |
| [#131](https://github.com/JDeun/LangTextFlow/issues/131) | completed | Shared caption/export selectors never present missing target text as a translation; mixed-target browser evidence corroborates the scoped fix. |
| [#130](https://github.com/JDeun/LangTextFlow/issues/130) | completed | Source metadata/migration/KO target/editor/import/snapshot routing implemented; unsafe alias protection is a distinct #160 defect. |
| [#128](https://github.com/JDeun/LangTextFlow/issues/128) | completed (this audit) | Original seven six-way research-execution criteria met; real helper/metrics/pins plus matched-direction comparison inspected. Native product quality remains with successor gates. |
| [#123](https://github.com/JDeun/LangTextFlow/issues/123) | completed | Four-tier resolution, import atomicity, snapshot review/precondition and immutable history tested (#392/#395/#396/#399); efficacy stays open. |
| [#120](https://github.com/JDeun/LangTextFlow/issues/120) | completed | Dedicated hash-route glossary editor, CRUD/import/export and return-live workflow exist; actual model efficacy was excluded. |
| [#110](https://github.com/JDeun/LangTextFlow/issues/110) | not_planned | Umbrella consolidated into multilingual/provider children; closure is neither a passing benchmark nor rejection of every candidate. |
| [#101](https://github.com/JDeun/LangTextFlow/issues/101) | completed (this audit) | Original six diagnostic criteria fulfilled by code regressions and independently inspected eight-clip base/small 4s/30s reports; no model/field approval. |
| [#85](https://github.com/JDeun/LangTextFlow/issues/85) | completed | Project metadata/session linkage/move/delete migration scope implemented; project AI synthesis and full restore are separate #86 work. |
| [#82](https://github.com/JDeun/LangTextFlow/issues/82) | completed | Named failure-test risk coverage now exists; historical post-close keep-open comments are stale. No newly confirmed code gap within this test task; hardware is separate. |
| [#81](https://github.com/JDeun/LangTextFlow/issues/81) | completed | History UI Generate→API/job→fake-provider HTTP→SQLite→review/citation/export verified; semantic/native/field acceptance stays with parents. |
| [#80](https://github.com/JDeun/LangTextFlow/issues/80) | completed | Grounded bounded per-session summary/review/provenance/consent service implemented; valid citations do not establish factual entailment. |
| [#77](https://github.com/JDeun/LangTextFlow/issues/77) | completed | Licensed real-speech evaluator infrastructure produced its required actual Linux artifact (#89); the bad original tiny quality remains negative evidence. |
| [#76](https://github.com/JDeun/LangTextFlow/issues/76) | completed | Named dependency/lock/CI blockers fixed and audit gates retained; official signing and future dependency safety are separate obligations. |
| [#47](https://github.com/JDeun/LangTextFlow/issues/47) | completed | Scoped automatable desktop resilience implemented; original physical-device/signing/privacy/human exclusions remain, and later #344 is separate. |

### Evidence and qualification boundaries

- [Current model artifact audit](research/model-issue-evidence-coverage-2026-11.md)
  distinguishes actual inference, mock wiring, negative results and rights blocks.
- [Actual runtime and ASR reports](REAL_SPEECH_EVALUATION.md) include the inspected
  native KO↔EN source `900d211` ZIP and all four eight-clip base/small 4s/30s ZIPs.
  Reference-byte/downloaded-weight identity limits and context-driven ranking
  changes remain explicit; these are not field-optimal model measurements.
- [Common MT result](CACHED_MT_TEXT_COMPARISON.md#first-common-corpus-result-2026-11-03):
  same host, ordered 16 KO→EN synthetic cases and two inference threads; original
  ZIP and sample statistics independently checked. No six-way/human approval.
- Core source `900d211` passed local 1176 backend / 91 frontend tests and unsigned
  Windows/macOS [packaging/frozen smoke](https://github.com/JDeun/LangTextFlow/actions/runs/41628928751).
  These are their recorded source scope, not unrun checks on future changes.
- The issue audit itself included 85 focused glossary/correction/MT tests, eight
  export tests and 18 frontend tests. Artifact verification and source inspection
  are distinguished from fresh execution. Independent human meaning review,
  annotated/cleared event audio, real 4/8GB devices, native 30/60/90-minute soak,
  privacy/retention signoff, signing/notarization and installer lifecycle remain
  open. No arbitrary closure or v1.0 approval follows from green CI.

## Historical audit (2026-10-26)

2026-10-26 · tracking #224 · incremental update to the individually verified 2026-10-22 baseline.

## Open-issue inventory (GitHub issue search)

GitHub issue search returned **49 open issues** and **11 closed issues** on 2026-10-26. This is an issue-search snapshot, **not** the GitHub Milestones API and **not** a milestone completion percentage. Search results omit milestone metadata, so do not interpret search-result `milestone: null` as an unassigned issue.

| Planned stage | GitHub milestone numeric ID observed | Open issue numbers | Count |
|---|---:|---|---:|
| M0 | 1 | #224, #222, #221, #220, #219, #218, #217, #216, #214, #209 | 10 |
| M1 | 2 | #210, #211, #161, #158, #157, #156, #155, #154 | 8 |
| M2 | 3 | #274, #213, #212, #160, #139, #130, #128, #126, #123, #122, #121, #118, #117, #115, #114, #113, #112, #111 | 18 |
| M3 | 4 | #254, #127, #107, #106, #101, #100 | 6 |
| M4 | 5 | #137, #104, #86, #81, #80, #79, #78 | 7 |
| **Total** | | | **49** |

**Evidence:** On 2026-10-22, all 47 then-open issues were individually fetched and their numeric milestone IDs and classification labels verified. On 2026-10-26, the two new open issues were individually fetched and updated: #274 → milestone numeric ID 3 (`type:research`, `area:asr`); #254 → numeric ID 4 (`type:test`, `area:runtime`). The 47 previous assignments are carried forward from the earlier audit, **not reverified individually today**. Thus the table is a reconciled assignment inventory, not a fresh 49/49 verification.

## Open pull requests (13)

- Functional/instrumentation: #264 (audience latency; relates to #254).
- CPU ASR/MT experiment: #119 (draft research; milestone numeric ID 3 observed previously). Do not merge without actual model-conversion and quality evidence.
- Dependency and CI updates: #202, #206, #265–#273. Review individual CI/security and desktop compatibility before merge.

## M0 decision and outstanding gates

- **Keep the existing M0–M5 plan.** Numeric milestone IDs 1–5 were observed on issues, but the connector has no milestone-list endpoint: exact GitHub milestone titles, due dates, aggregate closed/open counts, and M5 existence are **not verified**.
- Do not claim M0 complete or compute milestone completion percentages from the 49 open / 11 closed issue-search snapshot.
- #100 vs #214: overlapping real-audio E2E benchmarking; retain both until code-level coverage and ownership are compared.
- #209 and #216–#222: competitor research and candidate studies; related, not automatically duplicates.
- #154–#158: runtime implementation work remains subject to code-level inspection and deterministic regression tests.
- Actual sermon audio/consent, independent reference transcripts, six-direction ko/en/ja translation, 4GB/8GB hardware soak, signing/notarization and named release/field/privacy owners remain unverified.
- Never close issues or merge PRs based on a documentation-only audit.

## Next implementation gates

1. Inspect existing runtime code/tests against #155–#158 and identify the smallest missing failure-isolation invariant.
2. Add a deterministic regression for that invariant, then implement a bounded fix and open a focused PR.
3. Reconcile #100/#214 benchmark overlap; preserve distinct micro-ASR versus full-runtime acceptance evidence.
4. Review PR #119 and #264 independently, with CI and sample results; no automatic merge.
5. Once the GitHub milestone inventory and CI statuses can be read, update the exact milestone completion percentages and M0 exit gate.

## Benchmark overlap decision (#100 vs #214)

- **#100 (M3 / ID 4):** CI reproducibility for *licensed actual speech* through streaming ASR → correction/MT fan-out → audience WebSocket; independent reference transcripts, CER/WER, real versus mocked MT and failover/reconnect evidence. This is the **integration/fixture qualification** issue.
- **#214 (M0 / ID 1):** common baseline and regression protocol across Korean, English and Japanese, cold/warm, repeated same-hardware runs, p50/p95, RTF, RSS, 30/60/90-minute acceptance and candidate comparisons. This is the **measurement protocol/baseline** issue.
- These overlap in CER/WER and runtime reporting but have different exit gates. **Do not close either as duplicate.** #214 defines the repeatable protocol; #100 supplies licensed actual-audio CI integration evidence; #106/#107 remain physical field/hardware acceptance.
- Evidence gap: neither issue body establishes an accepted reproducible real-sermon run; no benchmark score or CI success is inferred here.

## #155 runtime implementation handoff

Issue #155 records prior implementation in stacked PRs #162/#165/#167 and outstanding revision coalescing, per-segment failure status, generation fencing, RSS and actual-audio throughput. Those PRs are not among the 13 open PRs at the 2026-10-26 snapshot. **Inspect merged runtime code and existing tests before introducing new queue workers**; target missing invariants, not reimplementation. The issue remains open.

## Exact-head CI investigation (2026-10-26, PR #275)

- Commit `51dde131153439a67fe45800bfe2c4c8065ed330`: CI run **40259337100 failed**, CodeQL run **40259337095 passed**.
- CI jobs: frontend, Browser E2E, Windows and macOS smoke **passed**; backend **failed** at **Python vulnerability audit and CycloneDX SBOM**. Backend tests themselves reported **699 passed, 78.50% coverage** (70% minimum).
- Audit log reports `filelock==3.32.6` with **CVE-2026-112940, CVE-2026-112913, CVE-2026-112907**, fix version **4.2.1**. `uv.lock` pins `filelock==3.32.6`; it is not a direct `pyproject.toml` dependency.
- **Security gate remains blocking.** Upgrade via reproducible `uv lock --upgrade-package filelock` with dependency compatibility checked, commit the regenerated `uv.lock`, and re-run `uv run pip-audit` and all CI checks. Do **not** manually alter only the version or skip the audit. Lock regeneration could not be verified from this connector-only editing environment.
- **PR #275 must not merge while backend security audit fails.** Documentation-only changes did not cause the pinned vulnerable dependency; the failure reveals a baseline blocker.

## Security blocker ownership

- Opened [#276](https://github.com/JDeun/LangTextFlow/issues/276) as M0/P0 security remediation, explicitly requiring regenerated lockfile hashes, locked install, pip-audit and exact-head CI verification. This is a **real unresolved blocker**, not a documentation-only waiver.
- The CI workflow runs `uv sync --locked --extra dev`, so changing only `pyproject.toml` or editing a `uv.lock` version without wheel hashes would break reproducibility. Do not merge #275 or mark M0 green until the lock is actually regenerated and security audit passes.

## Snapshot delta (2026-10-26, after audit)

The 49-issue table above is a **point-in-time snapshot before issue #276**. Creating M0/P0 security issue #276 increased the open issue count to **at least 50**, and the M0 count to **at least 11**; the other stage counts are unchanged unless further GitHub activity occurred. Do not use the 49-row table as a live dashboard. Meta #224 has been updated with this caveat and the CI blocker.

## M0 security remediation verified (2026-10-26)

- PR #277 regenerated `uv.lock` with `filelock==4.2.4` on GitHub Actions using pinned uv; one-shot regeneration workflow removed.
- PR #277 exact-head CI run **40263298938 succeeded**; CodeQL run **40263299053 succeeded**. Security issue #276 closed on squash merge `fe560f3e5fbf95b3e8ecb95595f4a4aee8ceaddb`.
- The preceding audit PR #275 was created before this remediation, so its historical failed run is not evidence of the updated main branch. This refreshed audit branch is based on the patched main branch.
- M0 repository security gate is green. **M0 milestone closure still requires administrative milestone-title/M5 verification, accountable owners and any required accepted baseline measurements**; do not equate security clearance with the entire release milestone.

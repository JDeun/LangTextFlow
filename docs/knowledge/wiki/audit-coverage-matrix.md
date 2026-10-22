# Research and implementation audit coverage matrix

- **Status:** issue-state audit in progress; not a field qualification
- **Last reviewed:** 2026-10-22

**Snapshot:** 2026-10-22. This page is a **triage inventory**, not a claim that every issue comment, changed file, or run artifact has been inspected. Refer to [prior-work ledger](prior-work-ledger.md) for independently extracted CPU artifacts and numerical results.

## Evidence labels

| Label | Meaning | Permitted decision |
|---|---|---|
| VERIFIED-ARTIFACT | Original CI ZIP / JSON was downloaded and inspected | Report exact measured scope, not field readiness |
| VERIFIED-CODE | Current implementation / runtime wiring inspected | Claim code path exists, not successful inference |
| ISSUE-REPORTED | Issue or PR narrative/comment only | Follow-up required before relying on measurements |
| PLANNED | Open proposal, issue or draft experiment | Do not claim implemented or qualified |
| BLOCKED | Failed, unmerged, or missing qualification | Resolve gate before promotion |

## Workstream cross-check

| Workstream | Issue / PR anchors | What is established | Open verification / disposition |
|---|---|---|---|
| Korean FLEURS ASR | [#101](https://github.com/JDeun/LangTextFlow/issues/101), #103, #116 | VERIFIED-ARTIFACT: tiny 4s vs 30s, five-model comparison reported in #118 | Distinguish offline CER/RTF from first-partial and live p95; real sermon clips missing |
| TranslateGemma CPU MT | [#117](https://github.com/JDeun/LangTextFlow/issues/117), [#118](https://github.com/JDeun/LangTextFlow/issues/118) | VERIFIED-ARTIFACT: original, post-fix, later timeout regression | Field-ready=false; latency, term fidelity and stability blocked |
| OPUS CPU ASR+MT | [#119](https://github.com/JDeun/LangTextFlow/pull/119) | VERIFIED-ARTIFACT: small arm measured, tc-big failed after decoder change | BLOCKED: draft/unmerged; model workflow failed; native-vs-CT2 target decoding differential needed |
| Six-direction MT | [#139](https://github.com/JDeun/LangTextFlow/issues/139), #134, #135, #168 | VERIFIED-CODE: M2M100 CT2 provider runtime path exists | PLANNED/OPEN: six-direction glossary ON/OFF E2E and production quality |
| Glossary and sermon terms | #123–#140, #181, #182 | Issue-state verified: #124/#125/#129/#132–#136/#138/#140 closed; #123/#126–#128/#130/#137/#139 open; #131 closed as a bug report and #132 fix closed | Do not equate observational terminology diagnostics or six-direction test scaffolding with qualified ON/OFF ASR+MT; reconcile parent acceptance separately |
| Runtime modularization | #154–#174, #199–#201 | Issue-state verified: architecture epic #154 and child #155–#158, #160–#161 **OPEN**, #159 **CLOSED**; implementation tasks #162–#167, #169–#171, #174 **CLOSED**; #164 docs ADR task closed, but design document remains proposed | Audit merged implementation against still-open acceptance criteria; closed implementation subtasks do not close their parent epic |
| Hardware / acceptance | [#224](https://github.com/JDeun/LangTextFlow/issues/224), #106, #107 | Hosted Linux CPU experiment evidence | Physical 4GB/8GB and 30/60/90-min real audio acceptance absent |
| Product ASR competitor research | #209–#222 | ISSUE-STATE VERIFIED: #215 research documentation **CLOSED**; parent #209, performance/UX #210–#214 and source investigations #216–#222 **OPEN** | Public product behavior and README claims are not verified source-code paths or benchmarks; audit source commit and compare to local implementation |
| Post-event archive | #80–#85 | VERIFIED-ISSUE-STATE: #83 initial vertical slice, #85 Event Projects, #84 benchmark fix, #82 test hardening **closed**; #80 grounded summary and #81 completed-event history UI **open** | Closed issue ≠ complete post-event product; inspect merged code and UX for #80/#81 before closing epic |
| Knowledge reuse wiki | #230–#237 | Wiki scaffolding, ingestion/search/proposals and CPU ledger merged | Coverage completeness and navigation/index validation still pending |

## Issue-state audit pass: #80–85 and #200–208 (2026-10-22)

Individual issue records were fetched (title, state, description); this is **issue-state verification only**, not full PR diff or runtime verification.

| Issue | State | Scope / important qualification |
|---|---|---|
| [#80](https://github.com/JDeun/LangTextFlow/issues/80) | OPEN | Grounded post-event summary with provenance; not delivered by closing initial slice |
| [#81](https://github.com/JDeun/LangTextFlow/issues/81) | OPEN | Complete transcript/recording/summary history UI |
| #82 | CLOSED | Coverage and failure-path tests; inspect associated PR before claiming every path covered |
| #83 | CLOSED | Initial recording/archive/summary **vertical slice**, not final acceptance |
| #84 | CLOSED | Invalid source=target ASR benchmark fix |
| #85 | CLOSED | Event Projects and session linkage |
| #200 | CLOSED | Project glossary capacity consistency fix |
| #201 | CLOSED | WebSocket disconnect/reconnect stale caption projection fix |
| #202–#208 | OPEN | Seven dependency-update/Dependabot items (Tauri, FastAPI, Actions); do **not** classify as runtime modularization |

**Correction:** Earlier workstream grouping incorrectly placed #202–#208 under runtime modularization and implied #80–85 were uniformly unverified. The above state check separates shipped initial slices, still-open acceptance issues and dependency maintenance.

## Architecture issue-state audit: #154–174 (2026-10-22)

Fetched 20 individual issue records; #168 is a PR rather than an issue in this subset and was covered in the MT ledger. State means GitHub issue state only, **not verified implementation or acceptance**.

- **OPEN architecture acceptance:** [#154](https://github.com/JDeun/LangTextFlow/issues/154) epic; [#155](https://github.com/JDeun/LangTextFlow/issues/155) bounded workers; [#156](https://github.com/JDeun/LangTextFlow/issues/156) per-target isolation; [#157](https://github.com/JDeun/LangTextFlow/issues/157) local CPU provider; [#158](https://github.com/JDeun/LangTextFlow/issues/158) ASR revision contracts; [#160](https://github.com/JDeun/LangTextFlow/issues/160) glossary language scope; [#161](https://github.com/JDeun/LangTextFlow/issues/161) UI session/provider separation.
- **CLOSED architecture issue:** [#159](https://github.com/JDeun/LangTextFlow/issues/159) nonblocking caption publication.
- **CLOSED implementation / documentation tasks:** #162 (per-target MT), #163 (source-language glossary), #164 (architecture design), #165 (segment workers), #166 (fanout), #167 (progressive caption revision), #169 (late callback fencing), #170 (model-soak scheduling), #171 (ASR capability declarations), #174 (capability-driven provider UI).
- **CLOSED browser smoke fixes:** #172 and #173 are test stability work, not core runtime architecture acceptance.

**Interpretation:** several concrete refactors were tracked as completed, while parent acceptance issues remain open. Do not rerun a refactor from scratch without comparing its closed implementation task to the parent's remaining acceptance criteria. Verify code and behavioral tests before declaring architecture complete.

## Competitor research audit: #209–222 (2026-10-22)

Individually fetched 14 issue records and checked issue title, state, and stated evidence requirements. **No external competitor source code or runtime benchmark was verified in this pass.**

| Issue(s) | GitHub state | Evidence / remaining gate |
|---|---|---|
| [#209](https://github.com/JDeun/LangTextFlow/issues/209) | OPEN | Iptaja product behavior described; underlying algorithm undisclosed, privacy claims attributed to vendor |
| #210 | OPEN | Incremental stable/commit first-stable p50/p95 and revision regressions; requires same-fixture baseline |
| #211 | OPEN | RMS vs Silero/WebRTC VAD, silence/restart and noise; needs false trigger and missed speech measures |
| #212 | OPEN | Correction→glossary/hotword registration UX, undo, duplicate prevention; not implemented by research issue |
| #213 | OPEN | Korean-English code-switch corpus, CER/WER and term recall; auto language detection contingent on benchmark |
| #214 | OPEN | E2E multilingual ASR baseline, cold/warm and long soak; existing microbenchmark is not equivalent |
| [#215](https://github.com/JDeun/LangTextFlow/issues/215) | CLOSED | Iptaja research document delivered; **does not close** #209 or #210–214 |
| #216 Handy | OPEN | README-level Silero/Parakeet claim; actual source call path, license and lifecycle pending |
| #217 OpenWhispr | OPEN | Provider/correction pipeline source audit pending |
| #218 Wisper | OPEN | Dictionary and code-switch implementation audit pending |
| #219 FluidVoice | OPEN | Local model lifecycle and cold-start source audit pending |
| #220 OpenWhisper | OPEN | Multi-engine/preview and streaming contract audit pending |
| #221 Hush | OPEN | Audio capture/silence/session lifecycle audit pending; OS global paste is out of scope |
| #222 AuraScribe | OPEN | sherpa-onnx engine support, model language/license and CPU metrics audit pending |

**Reuse decision:** do not create separate implementation PRs based solely on the seven README-level research issues. First capture upstream source path + commit, map to LangTextFlow implementation, then define a matched local test. The completed #215 is documentary evidence only, not an optimization result.

## Glossary and missionary translation audit: #123–140 (2026-10-22)

Fetched 18 individual issue records. These are **issue-state and description checks**, not a fresh six-direction model benchmark or full merged-code audit.

| Issue(s) | State | Scope and evidence boundary |
|---|---|---|
| [#123](https://github.com/JDeun/LangTextFlow/issues/123) | OPEN | Per-project glossary overlays and session snapshots; #200 fixes capacity consistency but does not by itself close parent acceptance |
| #124 | CLOSED | KO/EN/JA operator glossary editor and church terminology seed implementation |
| #125 | CLOSED | Provider-neutral mapping and **observational** adherence diagnostics, not proof of glossary enforcement |
| [#126](https://github.com/JDeun/LangTextFlow/issues/126) | OPEN | Bidirectional missionary interpretation product acceptance |
| #127, #128 | OPEN | Native multilingual streaming ASR and all six CT2 MT directions still require qualification |
| #129 | CLOSED | Six-direction CPU test experiment tracked as completed; inspect run results before accepting model quality |
| [#130](https://github.com/JDeun/LangTextFlow/issues/130) | OPEN | Source-language metadata and Korean target terms parent acceptance; foundational #134 is closed |
| #131, #132 | CLOSED | Incorrect source-text-as-translation bug and fix closed; verify UI behavior separately |
| #133, #134 | CLOSED | Broader Korean church seed and language-scoped glossary foundation |
| #135 | CLOSED | Six-direction glossary-aware MT test infrastructure; **not** end-to-end ASR+MT ON/OFF certification |
| #136 | CLOSED | Public Cuckoo/REA research documentation; #137 remains OPEN for first-party UI audit |
| #137 | OPEN | Evidence-based feature parity and actual UI validation |
| #138 | CLOSED | Model licensing/language/glossary qualification gates documented or implemented in task scope |
| [#139](https://github.com/JDeun/LangTextFlow/issues/139) | OPEN | **P0** full ASR+MT glossary ON/OFF effect across all six directions |
| #140 | CLOSED | Native Fun-ASR MLT glossary hotword comparison task; do not extrapolate from a single model to all ASR providers |

**Direction matrix:** KO→EN, KO→JA, EN→KO, EN→JA, JA→KO, JA→EN. The EN/JA→KO missionary sermon use case is a first-class P0 requirement. Record quality, latency, terminology ON/OFF, and model/license evidence **per direction**; one successful direction does not qualify the other five.

## Merged-code versus issue cross-check (2026-10-22)

This pass inspected **current `main` source files** and changed-file lists for PRs #124, #125, #129, #132, #134, #135, #140, #162, #163, #165, #166, #167, #174. This verifies the named code paths exist, **not** their runtime quality or every PR's full diff.

| Acceptance claim | Current main evidence | Conclusion / remaining gate |
|---|---|---|
| Source-scoped glossary hints (#130/#134/#160) | `apps/server/langtextflow/translation/glossary_selection.py`: `relevant_glossary` requires enabled entry, matching `source_language`, and a target-language translation; hint cap 24 | VERIFIED-CODE selection filter; check each MT provider actually consumes the hints, not just selects them |
| Glossary adherence (#125/#139) | `apps/server/langtextflow/glossary_adherence.py`: module explicitly says **observational**, `measure_glossary_adherence` detects missing target terms | VERIFIED-CODE measurement only; not ON/OFF evidence of causal ASR or MT improvement |
| No source-language fallback (#131/#132) | `apps/web/src/captionLanguage.ts`: source text returned only if requested language equals `segment.source_language`; missing MT displays translating/unavailable state | VERIFIED-CODE helper behavior; audit every display/export consumer and E2E test for complete product acceptance |
| Bounded translation (#155/#156/#162/#165) | `apps/server/langtextflow/pipeline.py`: documented bounded post-ASR worker; session-wide `asyncio.Semaphore(1)` for inference | VERIFIED-CODE concurrency control exists; compare actual backlog/deadline, cancellation and multi-target p95 under load before closing parents |
| Six-direction and native hotword benchmarks (#129/#135/#140) | Changed-file lists include `scripts/compare_multilingual_m2m100_cpu.py`, `scripts/glossary_cpu_contract.py`, `scripts/benchmark_funasr_mlt_glossary_cpu.py` and associated workflows/tests | VERIFIED-CODE file presence from PR diffs only; original run artifacts and ON/OFF deltas **not yet checked in this pass** |

**Important correction to issue-only audit:** source-language matching and safe missing-translation display are not merely proposals; current `main` contains the relevant code. This still does not close #130/#139 without acceptance tests and actual six-direction evidence.

## Translation-provider glossary consumption cross-check (2026-10-22)

Read the actual `main` provider source, not just PR file lists:

- `apps/server/langtextflow/translation/ollama.py` and `openai_compatible.py` import and call `relevant_glossary` in their translation paths. **VERIFIED-CODE: glossary hints are used by these prompt-oriented providers**; output adherence and model behavior still require tests.
- `apps/server/langtextflow/translation/m2m100_ct2.py` explicitly does **`del context`** in `translate` with the comment that glossary must be proven before enabling. Its runtime inference uses **beam_size=1** and returns a single candidate. **VERIFIED-CODE: production M2M100 CT2 provider does NOT consume the glossary**. Do not describe #135 as a shipped runtime glossary feature.
- `scripts/compare_multilingual_m2m100_cpu.py` runs an **experimental** glossary-aware beam-4 candidate selection using `choose_candidate`, compares baseline/assisted, and records snapshot SHA, matched terms and intervention cost. This is **benchmark-only**, not the above runtime adapter.
- `docs/GLOSSARY_CPU_QUALIFICATION.md` explicitly calls this a draft, says the reranker is not guaranteed, and leaves real bilingual samples, operator snapshot, ASR hotword-vs-MT intervention and production promotion unchecked.
- `docs/MULTILINGUAL_MISSIONARY_INTERPRETATION.md` defines 12 synthetic samples × 6 directions (72 translations) plus 3 FLEURS utterances per source language, and warns these do not establish live streaming, human-reviewed translation, or 30–90 minute stability. **Documentation is a test design/description, not independently verified run output**.

**Actionable acceptance gap:** #139 must compare actual runtime or an explicitly matched prototype across all six directions with glossary OFF/ON, including EN→KO and JA→KO. The experimental reranker cannot be promoted based solely on lexical match counts; verify semantic quality and overhead. Original workflow artifact numbers were not fetched in this pass.

## Runtime, glossary and reliability issue audit: #175–199 (2026-10-22)

Fetched **all 25 issue records individually**; each currently reports CLOSED. This is issue-state verification, **not** proof that every acceptance condition is met. The records include stacked/overlapping fixes; do not count each as an independent feature.

| Issues | Closed implementation scope | Remaining evidence boundary |
|---|---|---|
| #175, #178, #187, #191 | Operator metrics: postprocess drops/failures, WebSocket evictions, persistence failures, bounded latency tracking | Verify metric visibility and representative load behavior |
| #176 | Heavy automatic model DAG paused during feature stabilization | Closed CI policy task does **not** imply benchmarks have run or passed |
| #177, #179, #189, #190, #193, #194 | CT2 native cancellation capacity; overloaded/failed/stopped segment finalization; provider preflight; bounded SQLite stop drain | Stress cancellation/restart, deadline, persistent-storage failure on target runner |
| #180, #186, #196–#199 | ASR capability discovery, timestamp bounding, replay memory and callback fencing | Test real ASR provider behavior, delayed callbacks and long sessions; #197/#198 overlap with consolidated #199 |
| #181 | Optional EN/JA missionary sermon terminology seed | Default KO glossary remains unchanged; inspect actual seed and UI import behavior |
| #182, #184, #188 | Per-project glossary and session snapshots; deterministic overlays; stale project fetch fencing | Implementation tasks CLOSED while parent #123 remains OPEN; inspect DB schema and runtime snapshot before parent closure |
| #183, #192, #195 | Socket session isolation, failed-start safety, typed operator start request | E2E verify cross-session isolation and operator UX |
| #185 | History summary staleness and explicit language/provider selection | Parent #81 remains OPEN; this fix is not the entire history UI |

**Direct current-main spot check:** `apps/server/langtextflow/runtime.py` contains a 1024-entry bounded persistence queue, a configurable drain timeout and a counter for dropped persistence events, plus active generation tracking. This supports a **VERIFIED-CODE** label for those mechanisms, not long-duration qualification. A quick text search in `glossary_repository.py` and `session_repository.py` did not locate the project overlay/snapshot identifiers; **do not infer absence** because implementations may live in other modules. Locate the exact storage and snapshot code before upgrading #182/#184 evidence to VERIFIED-CODE.

**Priority:** validate #123 project snapshot isolation, #139 glossary ON/OFF, and #155–#158 runtime stress acceptance before claiming P0 feature completion. Issue closure alone is not a substitute for end-to-end evidence.

## Reuse gate before opening a model or pipeline experiment

1. Search this matrix and the prior-work ledger for model checkpoint, language direction, fixture hash, runner, and issue/PR.
2. Record **baseline SHA, artifact ID, model revision, data hash, CPU threads/RAM, decoding window, quality and latency definitions**.
3. If a matching failure exists, isolate its cause first; do not repeat unchanged conditions without a falsifiable hypothesis.
4. Use one matched-fixture comparison; keep offline replay, actual streaming, model-only inference and end-to-end wall time separate.
5. Treat generic CI green as infrastructure only; a failed model workflow or `field_ready=false` blocks promotion.

## Audit completeness and next passes

- Repository-wide discovery previously found 178 PR records and 58 issue records using partitioned GitHub searches. **Discovery is not line-by-line review**; this matrix groups only established workstreams.
- Still needed: per-issue comment/linked-run audit; per-PR implementation and merge disposition; missing model runs and negative results; current main runtime and glossary behavior; current Wiki navigation and lint.
- Never describe this matrix as an exhaustive inventory of experiments or as certification for church deployment.

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
| Glossary and sermon terms | #124, #125, #129, #138, #140, #181, #182 | Merged glossary features and church seeds; source-language constraints researched | Compare actual operator UI, ASR hotwords, translation enforcement and six-direction quality |
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

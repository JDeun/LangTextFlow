# Release Roadmap & Repository Hygiene
2026-11-03 · Tracking #224 · **Milestone inventory verified through the repository API; field/release acceptance remains open.**

The latest implementation checkpoint below supersedes the earlier dated inventories.
Open issue totals include research, umbrella and qualification work; they are not
counts of missing user-facing features.

## Initial inventory (2026-11-03, before the completion work)

`GET /repos/JDeun/LangTextFlow/milestones?state=all&per_page=100` verified:

| Number | Exact title |
|---|---|
| 1 | M0 - Repository Hygiene & Baseline |
| 2 | M1 - Runtime Reliability |
| 3 | M2 - Multilingual Quality |
| 4 | M3 - Hardware & Field Qualification |
| 5 | M4 - Product Completion |
| 6 | M5 - v1.0 Stable Release |

The open inventory contains **52 issues and 16 PRs**. No issues were closed by this inventory pass. Previous claims that milestone titles or M5 could not be verified are historical connector limitations, resolved by this read-only GitHub CLI/API check. Human owners, independent bilingual review, consented event audio, physical 4/8GB devices and signing/installer acceptance remain unverified.

Local baseline at `39e8635`: 774 backend tests and 73 frontend tests passed, along with frontend typecheck/build, Ruff, Markdown links, wiki structure, UI localization and CSS gates. These are code checks, not field evidence.

## Completion integration checkpoint (2026-11-03)

At `0859f94d761dc5720a5308861863e1b85953329d`, implementation PRs #350–#368
are merged following their latest-head CI and CodeQL checks. The initial 16 open PRs
were reconciled against existing or replacement implementations: #119, #202, #206,
#264, #265, #267, #269, #270, #271, #272, #273, #304, #330, #346, #347 and #348
are closed. This includes superseded branches; it does not mean every old branch was merged.
New follow-up work can still open PRs.

The refreshed repository API inventory has **51 open issues**, all with verified
milestones and classification labels. #254 closed after merged audience metrics #352.
The previously unclassified research issues #349 and #341 now belong to M2/ID 3;
optional native-helper audit #344 belongs to M0/ID 1. These three carry P2 research
labels; they do not become default engines or bypass their license/quality gates.
All 51 issues remain without an assigned human owner. Issue counts include umbrella
and field-acceptance tickets and are not counts of unimplemented features.

| Stage | Open issues at this checkpoint |
|---|---:|
| M0 / ID 1 | 11 |
| M1 / ID 2 | 8 |
| M2 / ID 3 | 20 |
| M3 / ID 4 | 5 |
| M4 / ID 5 | 7 |

Verified integration includes raw ASR preservation, review/approval locking, opt-in
audience latency metrics, bounded summary jobs, post-event transcript versions and
explicit summary evidence selection, citation navigation, operator lifecycle extraction,
source-language model defaults and per-provider overrides. #365 adds summary cancellation
and reviewed-text/Markdown/WAV downloads. #364 is a compiling, cross-platform-tested
**experimental** Rust/Python IPC scaffold; production providers do not use it yet.

Desktop verification includes actual frozen startup, synthetic captions, archive/export,
shutdown and multiprocessing resource-tracker termination. The missing package data
failure (#354), stdin watchdog shutdown abort (#359) and frozen helper diversion bug
(#366) were reproduced and fixed. Full unsigned Windows/macOS packaging passed in
[run 41583831188](https://github.com/JDeun/LangTextFlow/actions/runs/41583831188) and
[run 41588818150](https://github.com/JDeun/LangTextFlow/actions/runs/41588818150).
A cached small model also completed a public FLEURS clip through the actual frozen
child process; this is not a signed-installer or field acceptance result.

Research dependency remediation #367 passed standard CI/CodeQL, installed-package
audits and actual Linux CPU model checks. The FunASR CPU-wheel mismatch was retained
as a failure before the corrected
[run 41589694247](https://github.com/JDeun/LangTextFlow/actions/runs/41589694247)
succeeded. M2M100 conversion, six text directions and real KO/EN/JA speech combinations
completed in [run 41589177929](https://github.com/JDeun/LangTextFlow/actions/runs/41589177929)
at its recorded source; the later FunASR-only fix did not change those tested files.
Build/import success is separate from language adequacy or hardware qualification.

The ASR bounded recovery in #363 preserves already-valid small-model outputs and
records original timestamp failures before a single retry. Some tiny/base read-speech
CER remains high. The OPUS big checkpoint remains a negative result despite the
audited research harness #361; no model is promoted. Exact hashes, runs and limits are
in [real-speech evidence](REAL_SPEECH_EVALUATION.md),
[CPU model combinations](CHURCH_CPU_MODEL_COMBINATIONS.md) and
[church quality baseline](CHURCH_QUALITY_BASELINE.md).

Operator reconnect restoration, long-source summary context and full-runtime local-MT
benchmark coverage were the next implementation slices at this snapshot; the
subsequent checkpoint below records their integration. Licensed field audio and
independent reviews, physical 4/8GB tests, 30/60/90-minute native event soak, human
accessibility acceptance, accountable release owners, signing/notarization and installer
upgrade/rollback remain open. M0–M5 and v1.0 are **not** declared complete.

## Runtime and archive follow-up checkpoint (2026-11-03)

At `33b9b2c4fe67cf5951124216bd89822105cd1dad`, #369 and #371–#377 are
also merged after their latest-head CI/CodeQL checks. Reconnect restoration now
hydrates the real operator state without acquiring a microphone or restoring
recording/metrics consent. Delayed stop requests and audio WebSocket handshakes
are bound to their acknowledged session IDs. #372 connects approved offline
transcript citations to their matched managed recording without inferring live
timestamp alignment or starting playback automatically.

Long-source summary chunks preserve IDs, timestamps, speakers and metadata (#369).
Unknown transcript-selector fields are rejected, interrupted-session warnings
survive generation and remote Ollama requires explicit transmission approval
before a job/provider call (#375). Missing or invalid endpoint-policy metadata
disables generation until refresh succeeds. #80 is closed for service
implementation acceptance; semantic entailment and field/dual-pass acceptance
remain under #78 and #104. The integrated local suite passed 955 backend and
86 frontend tests plus build, Ruff, CSS/i18n and link checks. Final summary head
`d56086c7d8ca8283827197491e9c04ceb309929f` passed
[CI41602141666](https://github.com/JDeun/LangTextFlow/actions/runs/41602141666)
and [CodeQL41602141349](https://github.com/JDeun/LangTextFlow/actions/runs/41602141349).

The bounded opt-in runtime benchmark (#374) completed actual KO→EN and EN→KO
ASR/M2M100 inference with serialized audience-hub delivery in
[run41596721553](https://github.com/JDeun/LangTextFlow/actions/runs/41596721553).
Both directions drained and delivered 2/2 translated committed captions; one KO
timestamp required recovery and remains recorded. Measurement source and final
report-rule validation are distinguished in [real-speech evidence](REAL_SPEECH_EVALUATION.md).
These are in-process hub observations, not microphone/network/browser timing.
Independent target references are unavailable, so chrF remains null and #100
stays open for its remaining quality scope.

Pinned Notesoop/LFM3/Hex source/license triage (#373) is now documented in the
[optional ASR report](research/optional-asr-upstream-triage-2026-11.md). No candidate
weights, adapter, catalog admission or default was added; these optional research
tracks do not block shipping the existing provider stack.

Model preparation, preflight, live ASR and offline transcription now share the
same local Whisper cache lookup (#376), including existing legacy snapshots
without the optional preprocessor configuration. Frozen preparation has a
dedicated worker rather than starting a server through Python `-c` arguments.
Cached small-model reuse passed source and frozen offline checks without new
weights; unsigned Windows/macOS packaging passed at earlier head `ac8451a` in
[run41598509422](https://github.com/JDeun/LangTextFlow/actions/runs/41598509422).
Final integration head `b707aeff2d0b1fd9cbc6282578e00cfa5964e5f6` passed
[CI41602484443](https://github.com/JDeun/LangTextFlow/actions/runs/41602484443)
and [CodeQL41602484447](https://github.com/JDeun/LangTextFlow/actions/runs/41602484447).
An earlier browser mock-start timeout remains unexplained; the unchanged strict
suite and 500 HTTP/WebSocket lifecycle cycles subsequently passed. Browser smoke
cleanup now bounds server termination and preserves the original failure/logs;
this does not claim a confirmed fix for that intermittent timeout.

The later checkpoint below supersedes this intermediate issue count. Field
audio/independent reviews, physical 4/8GB devices, native
30/60/90-minute event soak, human accessibility acceptance, accountable owners,
production signing/notarization and installer upgrade/rollback remain open.
This checkpoint does not declare M0–M5 or v1.0 complete.

## Operator, privacy and media integration checkpoint (2026-11-03)

At `64e04b0fc44452f112106c0c275d5c735021e964`, #378–#388 are merged
after latest-head CI/CodeQL checks. Delayed history edits cannot reopen or mutate
another selected session (#378); incomplete M2M100 caches fail preflight and
native initialization degrades safely (#379). Provider JSON responses and model
pull progress use bounded streaming reads, reject unexpected compression and
redirects, and redact transport errors (#380/#384). A truncated model-pull stream
cannot report completion. Provider initialization has bounded async deadlines;
cancelled native work retains its capacity lease until the native call finishes
(#383). This bounds retries, but cannot forcibly interrupt an in-process native
call. Invalid session restarts preserve the current audio connection (#385).
Configured Ollama correction is labelled accurately for local or remote endpoints
in all three UI languages (#386).

The Impeccable-guided UI changes (#381/#382) align typography, control sizes,
spacing, contrast, mobile navigation and section hierarchy. A measured header
height keeps sticky sidebars and navigation targets visible at narrow desktop
widths and 200% text size. EN/KO/JA checks at 320/390/861/1024/1440 pixels,
keyboard navigation, browser accessibility checks and 87 frontend policy tests
passed. These automated checks do not replace human screen-reader acceptance.

Real runtime/SQLite/WAV regressions (#387) verify sample-clock behavior across
uneven PCM buffers, sender pauses, invalid frames, recording overflow and ASR
rejection. Captured audio can include an ASR-rejected tail; native word timing
and real microphone alignment remain unqualified. The browser acceptance suite
now creates live and approved-offline summaries through the actual UI, API,
background job, bounded provider HTTP and SQLite flow (#388), then reviews,
approves, exports and seeks their citations. Its synthetic local provider is a
deterministic service fixture, not a model-quality measurement. #81 is closed
for this automated UI/service scope; semantic and field acceptance remain open.

#130 is closed for glossary data/UI/routing implementation, including explicit
source languages, conservative legacy migration, exports, session snapshots and
provider scoping. Six-direction semantic quality, native bias and code-switching
acceptance remain separate gates. At this checkpoint the issue API has **48 open
issues**, all classified and assigned to verified milestones, with no human
assignees: M0 11, M1 8, M2 19, M3 5, M4 5. Signing, release ownership,
physical-device and field gates above remain open; v1.0 is not declared complete.

The follow-up browser run caught a real generation-to-review race: a running
summary that completed with the same ID left its editor empty. #390 refreshes
the editor when persisted content arrives while preserving unsaved edits on an
unchanged archive refresh. An actual React negative control failed before the
fix and passed afterward; all 87 policy tests, build, final CI41619705079 and
CodeQL41619705080 passed.

At `ff6937d121879278361771327687e072e0a97313`, #389 is also merged after
[CI41620013552](https://github.com/JDeun/LangTextFlow/actions/runs/41620013552)
and [CodeQL41620013631](https://github.com/JDeun/LangTextFlow/actions/runs/41620013631)
on final head `e74b911e66ad3816bd04ffede581f0fff3ad851d`. A durable
recording journal preserves ownership between completed private WAV flush,
rename and SQLite registration, including imported files and cancellation.
Bounded recovery verifies known files before linking or removing them;
unknown, changed or ambiguous files are preserved. The full backend suite
passed 1,101 tests, and independent review ran 33 related cases successfully.
An additional local Mac mini M4/16GB offline native smoke at source `2d67efb` reused cached
faster-whisper small and public FLEURS KO clip 1883 (12.48 seconds): three
committed captions, sample-exact recorded PCM, verified file hash/journal
linkage, and unchanged assets after runtime reinitialization (6.805 seconds
total). No new weights or external speech transmission were used. This does
not qualify word timing, physical microphone capture, secure erasure or
universal power-loss recovery. #79 stays open for its shipping privacy/retention
review; production real-media and legal/privacy acceptance remain under #78.

#391 subsequently closes a microphone lifecycle race: stop/unmount or a newer
start fences delayed permission, socket configuration, worklet loading, context
resume and device recovery. Late streams are stopped and stale callbacks cannot
send into or shut down a newer capture. Fifteen browser cases exercise the real
controller with platform API fixtures and resource-cleanup assertions, including
a pre-fix negative control. Final head `55822f3` passed
[CI41620230654](https://github.com/JDeun/LangTextFlow/actions/runs/41620230654)
and [CodeQL41620230839](https://github.com/JDeun/LangTextFlow/actions/runs/41620230839)
before merge `0039077518445c1ffee8b397ec2acdcaad84c32a`. Physical microphone
and OS permission-dialog acceptance remain separate.

## Latest implementation checkpoint (2026-11-03)

#393 consolidates original ZIP evidence for five real ASR candidates, OPUS,
TranslateGemma, M2M100 and FunASR in the [model issue audit](research/model-issue-evidence-coverage-2026-11.md).
Failures, missing hotword-bearing references, actual versus declared checkout
SHA, and rights-blocked candidates remain explicit. Three public speech clips
and unlike MT corpora cannot establish an optimal deployment combination.

#394 fixes a deterministically reproduced Python 3.11 socket-send/cancellation
race that could leave session shutdown awaiting its caption writer. The same
production-hub probe failed before and passed after the fix; final
[CI41622809313](https://github.com/JDeun/LangTextFlow/actions/runs/41622809313)
and [CodeQL41622809298](https://github.com/JDeun/LangTextFlow/actions/runs/41622809298)
passed. Earlier intermittent browser-start failures lack coroutine stacks, so
they are retained rather than all attributed to this race. The unchanged strict
browser suite subsequently passed for #392 and the later integrations.

#392/#395/#396 complete the missing #123 implementation: project scope cannot
silently add global recommendations; global < matching preset < project < session
overrides resolve deterministically; disabled entries suppress inherited values;
scope-aware imports preserve IDs and roll back overlaps. The operator can review
the resolved snapshot before starting. Its optional canonical digest rejects a
changed glossary before touching current audio, and the final union is validated
against the 500-entry session limit. #399 verifies all four tiers and inherited
reverts through real SQLite and the resolve/start API boundary. Issue #123 is
closed for this implementation scope, not model adherence or terminology quality.

At `31dbced46dd741df3cbc889c8ed10eb6783995a4`, #397 also replaces the
non-preemptible M2M100 native thread with one bounded, killable child process.
Timeout/cancel/stop retain ownership until termination/reaping; offline cache,
six-pair and glossary-failure contracts remain intact. Independent review
reproduced and corrected error-exit and late-spawn cleanup races. Final head
`e9eb7c4` passed [CI41625752762](https://github.com/JDeun/LangTextFlow/actions/runs/41625752762)
and [CodeQL41625752563](https://github.com/JDeun/LangTextFlow/actions/runs/41625752563),
including actual Windows/macOS child-process regressions.
[Run41624773060](https://github.com/JDeun/LangTextFlow/actions/runs/41624773060)
also performed real public KO→EN/EN→KO ASR+MT at production source `ab345776`:
2/2 translated committed captions per direction, queue drain and shutdown passed.
Aligned translation gold remains unavailable; parent-only RSS excludes the new
child and must not be compared as total model memory. The recorded converted
model binary differs from the older experiment, precluding a pure code-speed A/B.

#398 executes the owned Operator/Glossary REA observation using isolated local
synthetic data and publishes [sanitized evidence](research/evidence/rea-owned-ui-2026-11.json).
It closes the first-party-tool execution checkbox in #137, not Cuckoo parity,
model quality, native audio or human accessibility acceptance.

Through #400/#402/#403 at `900d211a0165bc334103b0bf291a61436f8e8472`,
mixed-target/reconnect browser acceptance closes #161; event/provider/timestamp
provenance persists through SQLite, export and reconnect; benchmark memory now
separately reports sampled child-inclusive RSS with partial/error accounting.
The integrated local core checkpoint passed **1176 backend / 91 frontend tests**
and build/lint gates. OS-process access required running the real-child RSS test
outside the sandbox; that test was not weakened or skipped.

Current-source [native run41628932696](https://github.com/JDeun/LangTextFlow/actions/runs/41628932696)
completed actual base ASR + M2M100 KO→EN/EN→KO with 2/2 translated committed
captions per direction, drained queues and child cleanup. Its eight-second decode
window produced first captions around 8.4 seconds; sampled process-tree RSS peaked
at 2906.33 / 3588.45 MiB. One KO timestamp was recovered and a wrong year remains.
These are short in-process-hub observations, not audience-paint latency, physical
4GB qualification, semantic acceptance or a long soak. See the
[original-artifact checkpoint](REAL_SPEECH_EVALUATION.md#current-source-subprocess-and-memory-checkpoint-2026-11-03).
Unsigned Windows/macOS packaging and frozen-process smoke passed at the same source
in [run41628928751](https://github.com/JDeun/LangTextFlow/actions/runs/41628928751).
Signing, notarization and actual installer upgrade/rollback remain unverified.

At main `e60365c35adfffe7dd76009de0e89b32f21808bc`, the full API inventory
contained **65 issues: initially 46 open / 19 closed**, excluding PRs. After
original-scope acceptance checks, #101/#128 closed completed and #159 reopened
for its unfulfilled explicit acceptance. The resulting inventory is **45 open /
20 closed**: M0 11, M1 8, M2 17, M3 4, M4 5; all 45 open issues have
labels/milestones and no assignee.
The [full issue audit](M0_ISSUE_AUDIT.md) distinguishes unfinished implementation,
research, quality evidence and external acceptance. **Completion is not reduced
to model selection.** In particular #86's project AI digest/review and restorable
archive are missing; the
[recorded user-approved Rust-core target](https://github.com/JDeun/LangTextFlow/issues/344#issuecomment-6536557339)
in #344 remains an implementation obligation. The echo-only scaffold does not
meet real-worker, cancellation/restart, authoritative ownership or cutover gates.
Earlier descriptions of all #344 work as optional research were incorrect;
optional adoption of Hex code/models is a separate decision.

#405/#406 add and run one same-host, same-16-case KO→EN CPU text comparison;
[inspected results](CACHED_MT_TEXT_COMPARISON.md#first-common-corpus-result-2026-11-03)
favor OPUS-small's speed/quality tradeoff in that limited scope. The matched
[eight-clip ASR results](REAL_SPEECH_EVALUATION.md#eight-clip-four-second-basesmall-comparison-2026-11-03)
retain substantial recognition errors and outliers. No optimal deployed model,
six-direction quality acceptance or v1.0 release is approved.

Subsequent fixes #407/#408 are merged after exact-head CI/CodeQL and independent
focused tests: existing native MT is accessible to the long-run CLI and failed MT
cannot report source-only success; deterministic aliases preserve protected
numeric/negation tokens, and lexical adherence uses source-aware word boundaries.
These fix concrete wiring/safety defects, not the unexecuted long-run or semantic
qualification. Project digests, native setup UX and supervised real-model Rust
worker slices are now separate active implementation tracks.

## Historical planning and audits (2026-10-22–26)

The dated inventories and pending statuses below preserve the original handoff. Use the current inventory above for milestone existence and titles; do not treat historical open-PR counts or unverified-milestone statements as current state.

## Scope and existing backlog
Open issue search showed architecture #154–#161; multilingual #117–#139; ASR #100–#115; post-event #78–#86; new competitor research #209–#222. Research documents from PR #215 and #223 have been merged into `main`. Runtime PRs #199 (ASR fallback), #200 (glossary capacity), and #201 (WebSocket caption projection) have also been merged. These changes do **not** establish field accuracy or hardware acceptance.

## Existing planned milestone ladder (issue assignment IDs 1–5 observed)
| Milestone | Exit gate | Representative issues |
|---|---|---|
| M0 Repository Hygiene & Baseline | docs/PR index coherent, duplicates cross-linked, CI green, benchmark baseline captured | #224, #209, #214 |
| M1 Runtime Reliability | bounded post-ASR pipeline, isolation, per-language status, partial/stable contracts, VAD/commit regressions | #154–#158, #161, #210, #211 |
| M2 Multilingual Quality | ko/en/ja six directions; glossary ON/OFF; ASR/MT model qualification and code-switch tests | #111–#115, #117–#130, #139, #160, #212, #213 |
| M3 Hardware & Field Qualification | reproducible actual-audio CER/WER/latency and 30/60/90-min soak; 4/8GB CPU constraints assessed | #100–#107, #127–#128, #214 |
| M4 Product Completion | archive UX, project knowledge, operator workflow, signed installer and privacy/security review | #78–#81, #86, #137 |
| M5 v1.0 Stable Release | all blocking gates met, changelog, signed deliverables, upgrade/rollback & support | #224 |

These are **completion gates, not calendar commitments**. Issues are already assigned to numeric GitHub milestone IDs 1–5. Exact milestone titles and M5 existence require the milestone-list API; do not create duplicates. See [M0 issue audit](M0_ISSUE_AUDIT.md).

## Hygiene conventions
- Issue title: `[area/P0|P1|P2] Verb + measurable outcome` or `research:` for exploratory work.
- Single canonical implementation issue per feature. Research tasks link implementation issues instead of duplicating scope.
- Close only with linked PR/test/benchmark evidence; mark duplicates with original reference.
- Labels: `area:asr`, `area:mt`, `area:runtime`, `area:docs`, `area:ux`, `type:research`, `type:bug`, `type:feature`, `type:test`, `priority:P0/P1/P2` (create labels before use).
- PR template: purpose, linked issue, before/after, tests, docs, licensing, risk/rollback.
- Docs canonical: README (user overview); docs/ARCHITECTURE.md (contracts); docs/MODEL_GUIDE.md (providers); docs/BENCHMARK.md (gates); docs/research/* (evidence + hypotheses).
- Do not claim real hardware results from synthetic CI; keep field acceptance status explicit.
- Avoid copying code from external repos before LICENSE/dependency/model weight audit.

## Immediate PR/document hygiene
1. PR #215 and #223 were merged; review their distinct competitor-vs-open-source findings and maintain reciprocal links.
2. Add or verify the research index and ensure no dead links remain on main.
3. Review #209–#222 against older ASR, glossary and runtime issues; use 'related' rather than automatically 'duplicate'.
4. Review architecture #154–#158 dependency ordering before refactors.
5. Collect CI/lint/test status and preserve all existing issue history.

## Open release blockers
- Actual multilingual quality measurements and real-world audio benchmarks.
- Low-spec hardware qualification and 30/60/90-min soak.
- Signing/notarization and real installer lifecycle.
- Operator/field acceptance, privacy/legal review.

## Status
This is a git-managed roadmap document. Individual issue fetches verified numeric milestone IDs 1–5 for 47 issues on 2026-10-22; #254 and #274 were newly assigned to IDs 4 and 3 on 2026-10-26. The connector still lacks a milestone-list action, so **exact titles, due dates, aggregate completion, and M5 existence remain unverified**. Do not create or rename milestones based on numeric guesses. See #224 for implementation and assignment tracking.

## Verified integration evidence (2026-10-22)
- PR #199: ASR fallback session/candidate fencing and dependency security; merged as `cd4acb0`; exact-head CI, CodeQL, Windows/macOS desktop packaging passed.
- PR #200: project glossary 500-entry capacity contract; merged as `2f7c4b3`; exact-head CI, CodeQL, desktop packaging passed.
- PR #201: disconnect/reconnect caption projection reset; merged as `9331dba`; exact-head CI, CodeQL, desktop packaging passed.
- PR #215: proprietary competitor research; merged as `9e4d3ae`.
- PR #223: seven open-source dictation project comparison; merged as `9d6eda9`.
- PR #119 remains open; its model-quality conclusions must not be treated as validated.
- CI and packaging prove build/test integrity, **not** real sermon CER/WER, six-direction translation quality, 4/8GB memory qualification, or 30/60/90-minute field soak.

## Pre-development readiness checklist (tracking #224)
| Preparation task | Status | Evidence / remaining action |
|---|---|---|
| Repository baseline | Partial | #199–#201 green before merge; main branch post-merge aggregate CI not independently audited here. |
| Existing PR triage | Partial | #119 remains open; #215/#223 merged. Re-review any newly opened PR before scheduling. |
| Canonical docs | Partial | README, ARCHITECTURE, MODEL_GUIDE, BENCHMARK and research documents identified; cross-link and stale-claim audit still needed. |
| Actual GitHub milestones M0–M5 | **Unverified** | #224/#214 expose milestone number 1, but milestone titles and all six milestone records are not confirmed; requires milestone listing/creation permission. |
| Issue-to-milestone assignments | **Incomplete/unverified** | Planning annotations in issue bodies do not imply actual assignment. Check every open issue after milestone records are confirmed. |
| Labels and issue conventions | **Unverified** | Label taxonomy is proposed, not confirmed created; audit available labels before bulk assignment. |
| Duplicate/overlap triage | Partial | #214 vs #100 and #209 research issues explicitly related; no premature closure. |
| Benchmark protocol | Defined, evidence pending | #214; no reproducible real-speech baseline, operator hardware inventory or audio fixture rights documented as accepted. |
| Release owners, risk/rollback | Pending | Assign responsible maintainers and establish approval/rollback checklist before v1.0 release. |

**Readiness verdict:** Development preparation is **not complete**. The checklist is an explicit blocker register, not a claim of GitHub configuration or model qualification. Do not mark M0 complete until all preparation rows are verified.

## M0 preparation handoff (2026-10-22)
### Decision register
- **Ready for implementation planning:** scoped P0/P1 backlog, canonical documentation roles, merge discipline and automated test gates are defined. This is not release or field acceptance.
- **Not ready to claim GitHub milestone completion:** only milestone numeric ID `1` was observed on issues #224 and #214. Its title and M0–M5 creation cannot be inspected using the available connector. Do not bulk reassign issues to guessed numeric IDs.
- **Not ready to close #224:** GitHub milestone and label inventories, issue assignment coverage and human owners remain unverified.
- **PR #119:** research-only CPU OPUS-MT comparison is open, its model conversion results are not qualified. Do not merge as a housekeeping shortcut.
- **Merged research:** PR #215 and #223 are on main; `docs/research/OPEN_SOURCE_DICTATION_COMPARISON.md` is present. Do not treat research notes as verified model benchmarks.

### Recommended GitHub configuration (requires repository settings/API access)
1. List all existing milestones, preserving titles, numbers, descriptions and issue assignments; only create missing M0–M5 entries. Do not overwrite existing milestones.
2. List existing labels before creating missing `priority:P0`, `priority:P1`, `priority:P2`, `area:asr`, `area:mt`, `area:runtime`, `area:docs`, `area:ux`, `type:research`, `type:bug`, `type:feature`, `type:test`.
3. Assign open issues to verified milestone IDs by the table above, respecting cross-stage dependencies and existing assignments; log exceptions.
4. Audit the remaining open PR #119 separately against its real-model qualification criteria.
5. Require maintainers to name a release owner, field-validation operator, licensing reviewer and rollback approver before M5 exit.

### Benchmark input readiness checklist (no inference runs)
- [ ] Licensed/consented, hash-pinned ko/en/ja sermon or lecture WAV files, 16kHz mono PCM16.
- [ ] Independent reviewed transcripts, source/target language metadata and glossary terminology references.
- [ ] Hardware inventory (CPU/RAM/OS), cold/warm protocol, repeat count >=3, CPU/RSS measurement.
- [ ] Measurable pass/fail thresholds for CER/WER, RTF, first partial/stable/commit latency, translation p95, dropped segments and term recall.
- [ ] 30/60/90-minute soak, reconnection, cancellation and queue-overflow acceptance on target 4/8GB machines.
- [ ] Artifact retention policy, privacy/consent and reproducible command/results paths.

**Handoff:** M0 documentation and checklist are prepared. Actual GitHub milestone/label setup and field fixtures remain external or unverified prerequisites; never mark those complete without evidence.

## Completed issue classification audit
The original 47 open issues were individually checked for milestone numeric ID and taxonomy labels on 2026-10-22; see [M0 issue audit](M0_ISSUE_AUDIT.md) for exact issue numbers and stage totals. GitHub milestone titles and M5 creation remain unverified, as do human owner assignments.

## 2026-10-26 M0 reconciliation

- Open issue inventory: **49**; 47 previously audited plus #254 (M3 / numeric ID 4) and #274 (M2 / numeric ID 3), both assigned and labelled today.
- Open PRs at snapshot: 13 before audit PR #275; **14 including #275**. Functional PR #264 relates to #254; CPU experiment PR #119 is not field-qualified. Remaining 11 were dependency/CI updates.
- Audit change tracked in PR #275; do not close #224 before milestone inventory, CI verification, and benchmark evidence are confirmed.
- [M0 audit](M0_ISSUE_AUDIT.md) is the canonical issue-number breakdown; this roadmap remains the gate definition.

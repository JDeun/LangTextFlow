# Release Roadmap & Repository Hygiene
2026-10-22 · Tracking #224 · **proposed milestone plan, not yet created GitHub Milestones**

## Scope and existing backlog
Open issue search showed architecture #154–#161; multilingual #117–#139; ASR #100–#115; post-event #78–#86; new competitor research #209–#222. Research docs are pending review in PR #215 and #223, **not on main**.

## Proposed sequential milestones
| Milestone | Exit gate | Representative issues |
|---|---|---|
| M0 Repository Hygiene & Baseline | docs/PR index coherent, duplicates cross-linked, CI green, benchmark baseline captured | #224, #209, #214 |
| M1 Runtime Reliability | bounded post-ASR pipeline, isolation, per-language status, partial/stable contracts, VAD/commit regressions | #154–#158, #161, #210, #211 |
| M2 Multilingual Quality | ko/en/ja six directions; glossary ON/OFF; ASR/MT model qualification and code-switch tests | #111–#115, #117–#130, #139, #160, #212, #213 |
| M3 Hardware & Field Qualification | reproducible actual-audio CER/WER/latency and 30/60/90-min soak; 4/8GB CPU constraints assessed | #100–#107, #127–#128, #214 |
| M4 Product Completion | archive UX, project knowledge, operator workflow, signed installer and privacy/security review | #78–#81, #86, #137 |
| M5 v1.0 Stable Release | all blocking gates met, changelog, signed deliverables, upgrade/rollback & support | #224 |

These are **completion gates, not calendar commitments**. Assign issues to actual GitHub Milestones after they are created.

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
1. Review PR #215 and #223 for duplicated research content; maintain separate competitor-vs-open-source comparison docs with reciprocal links.
2. Add index linking both after merge, avoid dead links on main.
3. Review #209–#222 against older ASR, glossary and runtime issues; use 'related' rather than automatically 'duplicate'.
4. Review architecture #154–#158 dependency ordering before refactors.
5. Collect CI/lint/test status and preserve all existing issue history.

## Open release blockers
- Actual multilingual quality measurements and real-world audio benchmarks.
- Low-spec hardware qualification and 30/60/90-min soak.
- Signing/notarization and real installer lifecycle.
- Operator/field acceptance, privacy/legal review.

## Status
This is a git-managed roadmap document. The GitHub connector currently exposed no create/list milestone action, so **no milestone records or issue-to-milestone assignments are claimed**. See #224 for implementation and assignment tracking.

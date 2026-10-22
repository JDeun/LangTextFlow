# M0 GitHub issue and milestone assignment audit

2026-10-22 · tracking #224 · verified by individual `fetch_issue` calls for all **47** currently open issues.

| Planned stage | GitHub milestone numeric ID observed | Open issue numbers | Count |
|---|---:|---|---:|
| M0 | 1 | #224, #222, #221, #220, #219, #218, #217, #216, #214, #209 | 10 |
| M1 | 2 | #210, #211, #161, #158, #157, #156, #155, #154 | 8 |
| M2 | 3 | #213, #212, #160, #139, #130, #128, #126, #123, #122, #121, #118, #117, #115, #114, #113, #112, #111 | 17 |
| M3 | 4 | #127, #107, #106, #101, #100 | 5 |
| M4 | 5 | #137, #104, #86, #81, #80, #79, #78 | 7 |

**Results:** All 47 open issues have a non-null milestone number matching the stage plan above. All 47 also have at least one of the new `priority:*`, `area:*` or `type:*` labels. Priority labels are intentionally omitted on exploratory tasks until ranking; do not interpret that as no triage. Draft PR #119 remains open and is separately assigned milestone 3 (its issue/PR number is not included in the 47 issue count).

**Scope limits:** The connector exposes issue milestone numeric IDs but not a repository milestone listing; milestone *titles, descriptions, due dates and M5 (numeric ID 6)* are not verified. A complete repository label inventory and release-owner assignments are also not verified. Do not manufacture an M5 milestone number or close #224 prematurely.

**No code or runtime acceptance is implied.** This audit covers issue classification, not real-world CER/WER, latency, hardware, consented audio fixture provenance or installer signing.

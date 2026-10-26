# M0 GitHub issue and milestone assignment audit

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

# Research: <specific question>

- **Status:** proposed | investigating | evidence collected | decision pending | archived
- **Date / researcher:** YYYY-MM-DD / @handle
- **Tracking issue:** #...
- **Scope:** ASR | MT | glossary | runtime | UX | platform | security

## Hypothesis and decision to unblock
State what choice this research will inform, and the conditions under which the hypothesis would be rejected.

## External evidence (not reproduced)
| Source and permalink | Retrieved | Claim | Verification gap |
|---|---|---|---|
| ... | YYYY-MM-DD | ... | ... |

## License and data rights
Separate runtime, model weights, training data, dependencies and benchmark fixture permissions. Mark unknowns explicitly; do not infer redistribution rights.

## Reproduction protocol (if executed)
- Code commit, model revision/hash, OS/CPU/RAM and inference backend:
- Input audio language, consent/license, fixture SHA256 and reviewed transcript:
- Exact commands, cold/warm repeats, environment and artifact path:
- Metrics: CER/WER, RTF, first partial/stable/commit latency, translation p50/p95, RSS, glossary term recall, error rates (as applicable):

## Results
Distinguish **measured**, **source-reported**, and **not tested**. Include counterexamples and failure cases.

## Recommendation and follow-up
- Decision: adopt | trial | defer | reject | no decision
- Rationale and uncertainty:
- Linked implementation issue / [ADR](../decisions/README.md) / re-evaluation trigger:

# LangTextFlow — Product North Star & Engineering Principles

**Status:** Proposed product charter (not a field-readiness claim).  
**Last reviewed:** 2026-10-22

## North Star

**Break language barriers at live spoken events through trustworthy, low-latency, locally operable multilingual captions, without requiring a human interpreter for routine access.**

The founding use case is **English/Japanese missionaries speaking to a Korean congregation**, requiring Korean captions. Korean speakers must likewise be accessible to English/Japanese audiences. Extend this architecture to conferences, lectures and other events.

## Non-negotiable principles

1. **All directions, not Korean-outbound only.** Validate KO↔EN, KO↔JA and EN↔JA separately. EN→KO and JA→KO are primary P0 missionary acceptance paths; source-language captions remain available.
2. **Live utility over offline scores.** First useful caption, p50/p95/p99 end-to-end lag, revisions, backlog, drop rate, recovery and 30/60/90-minute operation matter more than isolated ASR/MT accuracy.
3. **Meaning before terminology cosmetics.** Protect negation, numbers, biblical names, doctrinal meaning and speaker intent. Human bilingual review is mandatory before claims of reliable interpretation.
4. **Local-first and privacy-aware.** Audio, transcripts and glossary stay local by default. Remote APIs require deliberate opt-in; never silently fall back to cloud.
5. **Failure must be honest and bounded.** Preserve source captions when translation fails, show unavailable/pending status, and bound queues, CPU inference and memory.
6. **Replaceable model adapters.** Stable versioned caption events and explicit ASR/correction/MT interfaces; choose models based on pinned reproducible measurements, licensing and resource use.
7. **Operator control, simple audience experience.** Project-scoped glossary and immutable session context, language choice, QR/read-only captions, clear status and auditability.
8. **Open and reproducible.** Initial CPU qualification hardware is the GitHub Actions hosted Linux runner. Separate physical 4/8 GB qualification; do not silently equate hardware profiles.

## Decision trade-offs

- Do not sacrifice semantic fidelity for lexical glossary hits.
- Do not block source caption delivery waiting for every target language.
- Do not label synthetic fixtures, green CI or short clips as field certification.
- Do not automatically switch input languages until language-ID/code-switching has measured safety evidence.
- Prefer an honest, usable MVP over an unverified “commercial grade” claim.

## Acceptance and evidence levels

| Level | Evidence | Allowed statement |
|---|---|---|
| Implemented | Reviewed code and tests | Feature path exists |
| Reproduced | Pinned fixture/model/runner, original artifacts and failures | Measured on those conditions |
| End-to-end qualified | Six directed ASR→MT paths, glossary ON/OFF, human bilingual meaning review, latency and overload/restart | Qualified only for named conditions |
| Field-ready | Consented continuous real speech; 30/60/90-min live operator/audience acceptance; installation/recovery | Ready only for tested scenario/hardware |

Numeric thresholds belong in a versioned acceptance plan and must be declared **before** testing. Never conceal a failing language direction inside a favorable average.

## Current gaps (2026-10-22)

- M2M100 CT2 runtime does **not** consume glossary context; beam-4 glossary reranking is experimental (#139).
- Project glossary precedence, snapshot and SQLite persistence are present in code; full UX and executed integration acceptance for parent #123 remain open.
- Multilingual streaming, six-direction meaning quality and sustained field operation remain separately gated (#126–#128, #139).
- The public README calls the project **pre-field alpha / code-complete candidate**.

## New-work checklist

Identify the North Star outcome, language directions, operator impact, license/privacy/CPU budget, pinned comparison baseline, success/failure criteria, degraded behavior, and existing issues/PRs before starting work.

## Canonical implementation and qualification documents

- [Product Requirements (PRD)](PRD.md)
- [Quality & Field Acceptance Plan](QUALITY_ACCEPTANCE_PLAN.md)
- [Model Selection & Engineering Decision Policy](MODEL_SELECTION_POLICY.md)

## References

- [README](../README.md) — public scope and maturity.
- [Original missionary use case](MULTILINGUAL_MISSIONARY_INTERPRETATION.md) — source/target priority and experiment limitations.
- [Architecture](ARCHITECTURE.md) — caption state machine and invariants.
- [Roadmap](ROADMAP.md) — delivery plan; checkboxes are not quality certification.
- [Evidence audit](knowledge/wiki/audit-coverage-matrix.md) — verified code, artifacts and gaps.
- [Architecture decisions](decisions/README.md) — accepted ADRs.

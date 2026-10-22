# LangTextFlow — Product Requirements Document (PRD v1)

**Status:** Baseline requirements for implementation and qualification; release is **not** approved.  
**Date:** 2026-10-22  
**Authority:** [North Star](PRODUCT_NORTH_STAR.md) → this PRD → [qualification](QUALITY_ACCEPTANCE_PLAN.md) → [model decisions](MODEL_SELECTION_POLICY.md).  
**Evidence boundary:** A feature marked implemented in the roadmap does not imply field acceptance.

## 1. Problem and users

**Origin:** A visiting English- or Japanese-speaking missionary gives a sermon to Korean congregants. They need Korean captions without requiring a live interpreter. Korean sermons likewise need EN/JA captions. The same product can support conferences, lectures and meetings.

- **Operator:** prepares audio device, source language, target languages, glossary, model and audience link; supervises failure and ends/exports sessions.
- **Speaker:** speaks naturally, without an app-specific script or requirement to pause after each sentence.
- **Audience:** scans a QR code, selects an available language and reads captions without edit privileges.
- **Administrator:** installs/updates a local app, controls privacy, models and storage.

## 2. P0 product acceptance requirements

| ID | Requirement | Verifiable acceptance | Tracking |
|---|---|---|---|
| FR-01 | Source KO/EN/JA, target any other supported language | All six directed pairs EN→KO, JA→KO, KO→EN, KO→JA, EN→JA, JA→EN pass separately; source track preserved | #126 #128 |
| FR-02 | Live ASR and revisions | Real microphone/continuous audio yields timestamped, versioned partial/stable/final events; no stage regression or stale overwrite | #127 #158 |
| FR-03 | Simultaneous audience targets | A slow/failed target does not block other targets or source captions; per-target status observable | #154 #155 #156 |
| FR-04 | Operator/session lifecycle | Start, stop, reconnect, project assignment, interruption recovery, read-only QR/projector/OBS delivery and history/export tested | ROADMAP |
| FR-05 | Terminology control | Source-scoped global/preset/project/session glossary, deterministic precedence, scoped import/export, immutable persisted session snapshot, safe edit/revert | #123 #130 |
| FR-06 | **Effective** glossary | Matched real speech/text glossary ON/OFF; ASR hotword and MT influence measured independently; no unacceptable false insertion/semantic regression | #139 |
| FR-07 | Local-first operation | Offline local ASR/MT where qualified; no silent remote calls; explicit opt-in for network providers; source caption fallback | #154 #157 |
| FR-08 | Operator transparency | Current engine, language pair, lag, queue, degraded/unavailable status and model readiness visible; no fabricated success | #154–#158 |
| FR-09 | Sustained use | Consented real speech, 30/60/90-minute session, memory/backlog, reconnect and failure recovery pass per [quality plan](QUALITY_ACCEPTANCE_PLAN.md) | ROADMAP |
| FR-10 | Distribution and accessibility | Tested signed desktop install/update/uninstall on target OS, keyboard/screen-reader human acceptance, legal/privacy review | ROADMAP |

**P0 priority:** EN→KO and JA→KO are **mandatory founding-use-case gates**, not optional after KO-outbound work. All six pairs must be qualified before claiming full KO/EN/JA bidirectional support. Each pair is scored separately.

## 3. Functional journey

1. Operator chooses source language (manual until automatic LID is qualified), target languages, project/glossary, local model and microphone; readiness check must fail clearly if resources are missing.
2. Start snapshots the resolved glossary and context into persistent session history.
3. ASR emits source captions promptly. Correction and independent MT tasks update versioned captions without losing the original.
4. Audience joins through QR/read-only URL, chooses source or target track and receives stable readable captions.
5. Translation failure must not suppress source or successful target tracks; show accurate degraded status.
6. End/export retains transcript, context snapshot and relevant diagnostics; privacy controls govern retention.

## 4. Nonfunctional constraints

- **Target benchmark:** GitHub Actions hosted Linux CPU; record CPU, memory, thread count and peak RSS. Later test actual Windows/macOS and constrained 4/8GB hardware independently.
- **Performance:** use preregistered provisional thresholds and metrics in [quality plan](QUALITY_ACCEPTANCE_PLAN.md); never equate model-only RTF with audio→audience latency.
- **Security:** operator-only mutation, audience read-only, project isolation, local-first data, no raw private audio in public artifacts without permission.
- **Licensing:** pin model revision and verify code, weights and dataset licenses before distribution.
- **Reliability:** bounded queues, cancellation, target isolation, explicit recovery and observability.

## 5. Out of scope for initial acceptance

Automatic multilingual code-switching, speaker diarization, perfect simultaneous interpreting, unrestricted mobile/offline hardware parity, guaranteed 100% semantic correctness, and automatic post-event recording are not implied by this PRD. They require their own measured acceptance.

## 6. Release decision

Implementation checkboxes → pinned reproducible CPU experiments → six-direction human-reviewed ASR→MT → 30/60/90-minute real-event acceptance → installation/security/accessibility review. **Any P0 blocker prevents a field-ready claim** for that scope. See [roadmap](ROADMAP.md) and [evidence audit](knowledge/wiki/audit-coverage-matrix.md).

## 7. Change control

Requirements IDs are stable. Changes affecting language directions, privacy, meaning fidelity, local-first behavior or failure isolation require PR review, evidence impact analysis and an ADR where architectural. Never silently lower thresholds after observing results.

# External Benchmark & Commercial Service Evidence — 2026-10-22

**Purpose:** Ground LangTextFlow quality targets in published independent evaluation and documented commercial product capabilities. **Not** a measured head-to-head benchmark of LangTextFlow. Do not treat non-comparable latency definitions as interchangeable.

## Research findings and primary sources

| Source | Published evidence | Transferability / caveat |
|---|---|---|
| [IWSLT 2025 simultaneous track](https://iwslt.org/2025/simultaneous) | Non-computation-aware **StreamLAAL** EN→JA low 0–3.5s, high 3.5–5s; EN→DE and CS→EN low 0–2s, high 2–4s; EN→ZH low 0–2.5s, high 2.5–4s | **Translation lag metric**, not microphone→browser p95; EN→JA is relevant but no EN/JA→KO target published here |
| [IWSLT 2024 simultaneous track](https://iwslt.org/2024/simultaneous) | Speech-to-text simultaneous translation **Average Lagging** constraint 2s | Different metric/evaluation setting; not end-to-end wall-clock |
| [IWSLT 2025 findings](https://publikationen.bibliothek.kit.edu/1000185469/167698135) | EN→DE low-latency systems' reported non-computation-aware test StreamLAAL about 1.47–1.99s; high latency about 2.90–3.71s in cited table | Benchmark test set and direction, not KO/JA real sermons; compute-aware values differ |
| [Papi et al., TACL 2025](https://aclanthology.org/2025.tacl-1.14/) | Review of 110 papers flags segmented-speech evaluation and terminology inconsistencies as barriers to real-world continuous streaming claims | Methodology evidence supporting unsegmented/continuous field tests |
| [Xu et al., NAACL 2025](https://aclanthology.org/2025.findings-naacl.393/) | Documents pitfalls in computation-aware simultaneous translation latency measurement | Must log physical audio→audience timing and computation-aware latency separately |
| [Microsoft Teams live captions](https://support.microsoft.com/en-gb/teams/meetings/use-live-captions-in-microsoft-teams-meetings) | Commercial live translated captions; supports Korean/Japanese/English and attendee-selected language; meeting/event license and language constraints | **Capability benchmark only**: official support page does not provide comparable p95 latency or theological fidelity SLA |
| [Google Meet translated captions](https://support.google.com/meet/answer/10964115?hl=en) | Commercial translated captions including KO/EN/JA; audience controls and caption appearance | **Capability benchmark only**: no published comparable p95 CPU/local SLA |
| [Microsoft Teams Interpreter](https://support.microsoft.com/en-gb/teams/copilot/interpreter-in-microsoft-teams-meetings-and-calls) | Warns rapid exchanges, interruptions and overlapping speech may delay translation | Failure-mode/UX comparison; not numeric threshold evidence |

## Target derivation: what can and cannot be justified

- **Evidence-based benchmark reference:** track IWSLT 2025 **EN→JA StreamLAAL ≤3.5s low regime / 3.5–5s high regime** as an external **research comparison**, explicitly *not* a LangTextFlow browser end-to-end SLO. Use other IWSLT directions only for methodological context.
- **Commercial functional parity:** audience language selection, readable caption UI, clear status, support for KO/EN/JA; evaluate locally without claiming identical cloud service latency.
- **Research-backed methodology:** continuous unsegmented speech, compute-aware and actual end-to-end wall-clock traces, p50/p95/p99, semantic review, interruptions and recovery.
- **Not externally established:** LangTextFlow source p95≤3s, stable p95≤5s, translated p95≤8s/p99≤12s, queue-age≤10s. These remain **unapproved internal design hypotheses**. Neither IWSLT lag nor commercial feature pages validate those numbers.
- **Not externally established:** any universal acceptable CER/WER, doctrinal error tolerance or glossary improvement threshold. Require a consented multilingual sermon corpus, bilingual review rubric and user tests.

## Recommended two-layer scorecard

1. **Research comparability:** six directed pairs; for EN→JA report StreamLAAL (computation-aware and non-aware) with precise IWSLT protocol if reproducible, plus BLEU/COMET and human adequacy. Label missing KO/JA→KO external comparators **not available**, not assumed equivalent.
2. **Product usability:** audio timestamp→first source caption, segment end→stable/translated audience visibility p50/p95/p99; full ASR→MT correctness, source continuity, queue age and 30/60/90-min reliability. Decide release thresholds **after** collecting matched-run baseline and user-reviewed acceptability.

## Evidence integrity

The source pages were reviewed 2026-10-22. Product support matrices can change; recheck before release. IWSLT research latency values cannot be subtracted from or substituted for measured local end-to-end latency. No competitor was directly tested, and no public official Teams/Meet p95 latency guarantees were identified in the reviewed support pages. This document is a literature/feature review, not an empirical LangTextFlow benchmark.

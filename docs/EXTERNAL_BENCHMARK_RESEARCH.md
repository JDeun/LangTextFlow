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


## Specialist commercial services: primary-source capability comparison (2026-10-22)

| Service | Official claim | Practical product parity to evaluate | Evidence limitation |
|---|---|---|---|
| [Cuckoo AI](https://www.cuckoo.so/ko) and [Cuckoo events](https://www.cuckoo.so/ko/events) | ~20 languages, meeting context documents, terminology glossary, automatic language recognition; event QR captions, projector/screen share, desktop overlay and OBS | Project/document context, glossary operator UX, QR/audience language selection, projector and OBS workflows | No independently audited per-pair CER/semantic score or comparable p95 latency in reviewed pages; product marketing is not an SLO |
| [KUDO venues](https://kudo.ai/solutions/live-speech-translation-for-venues-and-rooms/) | 70+ languages, custom glossary, auto detection, QR browser captions/audio, venues including houses of worship; vendor **claims 4.1s average latency** and 4.1/5 fluency, 4.25/5 accuracy | Church/event deployment and audio interface, audience self-service, domain glossary, separate operator controls | **Vendor-reported mean**, not independently reproduced, not p95/p99, unclear per-language/CPU/definition; cloud/network solution |
| [KUDO meeting setup](https://support.kudoway.com/hc/en-us/articles/40522205896087-Scheduling-KUDO-AI-Meetings) | Caption-only mode positioned as lower latency than speech+caption; operator/participant/viewer/mobile links, adjustable target languages | Caption-only product mode and audience roles, live target language controls | No numeric caption-only latency or KO/JA→KO fidelity SLA on this page |
| [sotonis](https://sotonis.com/) | Church-specific Worship Mode, glossary, scripture/liturgy text preservation, QR audience, 82 languages, vendor claim **within 2s of first word** | Church preset, protected canonical liturgy, source/target views, operator console, accessibility | Vendor claim is not independently measured and may refer to **first output**, not complete translated utterance or p95 |
| [Mission21 LiveCaption](https://mission21.com/livecaption/) | Worship/event-focused desktop captions, 13 languages including KO/EN/JA, mic and OBS/projector; vendor claim ~1s delay **after sentence ends** | Microphone/device selection and overlay integration, church-specific deployment | Sentence-end-relative vendor claim, not first-word lag or p95; not independently reproduced |

**Metric incompatibility:** KUDO's claimed **mean 4.1s**, sotonis's **first-word-relative 2s**, and Mission21's **sentence-end-relative ~1s** measure different events (and may have different output modalities). They must **not** be combined into one target percentile or compared as equivalent to IWSLT StreamLAAL. The proper action is a matched-input, timestamp-instrumented external user study with vendor permission/terms and documented account/device conditions.

**Prioritization impact:** Cuckoo and KUDO establish a strong **feature parity target** for QR audience language selection, glossary/document context, operator controls and projector/OBS delivery. Specialist church services suggest **domain-specific liturgy/scripture handling** should be evaluated, but do not prove that forcing canonical text is always semantically safe. Local/offline CPU and no silent cloud fallback remain LangTextFlow differentiators; do not infer competing providers are locally runnable.

## IWSLT 2026 update

The [IWSLT 2026 simultaneous track](https://iwslt.org/2026/simultaneous) now emphasizes **unsegmented long-form speech** and an **extra-context** track, but its listed directions are EN→DE/ZH/IT and CS→EN, **not** EN/JA→KO. Its main Docker track uses an H100 80GB GPU, so its measured system latency is not a like-for-like GitHub Actions Linux CPU baseline. Use this as methodology/architecture evidence, not an attainable CPU p95 promise.

## IWSLT 2026 official protocol: important correction and new actionable targets

**Official source:** https://iwslt.org/2026/simultaneous (checked 2026-10-22).

- IWSLT 2026 main simultaneous **low** and **high** regimes are **non-computation-aware LongYAAL 0–2 s and 2–4 s**, respectively, **shared across listed language pairs**. The 2025 EN→JA StreamLAAL 0–3.5/3.5–5 s regimes belong to **2025**, and **must not** be described as current 2026 rules.
- The 2026 primary ranking latency metric is **LongYAAL**; StreamLAAL is reported for historical comparison. The main translation quality metric is **XCOMET-XL**, with secondary chrF/BLEURT and human evaluation of primary submissions.
- Official long-form test recordings are **unsegmented, up to 2.5 hours**. This supplies an evidence-based rationale for long-form endurance **test coverage**, not proof that 90 minutes is a sufficient commercial pass duration.
- Official Docker submission evaluation uses **one H100 80GB GPU**. LangTextFlow's GitHub Actions Linux CPU is not performance-comparable; CPU runs should report both metric protocol compatibility and compute profile.
- **KO is absent from official 2026 simultaneous language pairs**; JA is not a main 2026 simultaneous target either. The 2026 **offline** and **subtitling** tracks introduce Japanese but cannot be treated as KO/JA→KO simultaneous benchmark scores.
- **Actionable protocol:** instrument output token/segment timestamps and source audio offsets so a future LongYAAL/StreamLAAL-compatible log can be produced; retain the product's independent microphone→audience p50/p95/p99 latency.
- **Source:** https://iwslt.org/2026/metrics confirms that automatically segmented speech degrades conventional text-only quality metric reliability and compares quality estimation against human judgments. Therefore prioritize human semantic review and reference-free estimation only as a secondary signal.

**Decision:** do not revise the provisional 3/5/8/12-second *end-to-end* thresholds to 2/4 seconds on the basis of IWSLT 2026. The benchmark metric is a different construct.

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

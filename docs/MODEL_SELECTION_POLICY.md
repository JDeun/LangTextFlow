# LangTextFlow — Model Selection & Engineering Decision Policy v1

**Status:** Proposed policy for review; no checkpoint is approved for field use by this document.  
**Date:** 2026-10-22  
**Related:** [North Star](PRODUCT_NORTH_STAR.md), [PRD](PRD.md), [quality plan](QUALITY_ACCEPTANCE_PLAN.md).

## 1. Scope and decision hierarchy

The product requires source KO/EN/JA and six directed translation pairs, with EN→KO and JA→KO founding P0 priorities. Select **ASR, correction and MT separately**, then qualify the **combined** audio→audience system. Prefer locally runnable, reproducible and permissively distributable options; no model is architecturally permanent.

**Hard gates before scoring:** documented code/weight/data license and distribution rights; exact checkpoint revision/hash; CPU inference feasibility on the specified GitHub Actions Linux profile; actual language-direction support; no silent cloud dependency; bounded memory; output safety checks. Missing evidence = BLOCKED, not zero-score guesswork.

## 2. Fair comparison protocol

1. Search [audit matrix](knowledge/wiki/audit-coverage-matrix.md), [prior work ledger](knowledge/wiki/prior-work-ledger.md) and open/closed issues/PRs before proposing a new experiment.
2. Record hypothesis and failure mode of previous candidates. Do not repeat an identical failed configuration without a falsifiable change.
3. Pin same fixture/audio SHA, reference, runner profile, thread budget, warm/cold regime and decoding policy. Report all timeouts and failures.
4. For ASR compare source CER/WER, first partial, stable latency, revisions, hallucinations, glossary hotword ON/OFF and memory.
5. For MT compare each of six directions, bilingual semantic review, negation/numerals/names, chrF++ as **secondary** proxy, glossary ON/OFF, per-target latency and memory.
6. For the full pipeline compare p50/p95/p99 audience latency, 30/60/90-min backlog, source-caption continuity, independent target failures and recovery.
7. Freeze the decision matrix before running; retain original negative artifacts and explain changes from baseline.

## 3. Decision matrix

**No composite score can override a hard gate.** For candidates passing gates, evaluate: (a) semantic correctness and critical-error risk (highest); (b) live p95/p99 latency and sustained backlog; (c) six-direction coverage; (d) memory and CPU; (e) glossary actual intervention and false insertion; (f) model startup, deployment, license and maintenance. Use per-direction results rather than one aggregate leaderboard. A cheaper model can win if it meets meaning and live gates more reliably.

**Outcome:** `REJECTED` (hard gate fails), `EXPERIMENTAL` (runs but quality insufficient/unverified), `CANDIDATE` (reproducible benchmark passes), `QUALIFIED-SCOPE` (human-reviewed E2E and named hardware), `FIELD-READY-SCOPE` (field/installer/privacy acceptance). Never use `FIELD-READY` without scope.

## 4. Candidate registry: evidence, not endorsements

| Candidate | Intended role | Current limitation / follow-up |
|---|---|---|
| Qwen3-ASR 0.6B / 1.7B | KO/EN/JA ASR comparison | Existing tiny KO-only evidence cannot qualify native multilingual streaming (#127) |
| Fun-ASR-MLT-Nano-2512 | Multilingual ASR with hotwords | 3 FLEURS clips per language; no glossary terms in references; fixed OFF→ON order; research only |
| faster-whisper / VibeVoice | Existing runtime ASR adapters | Verify actual source-language capability, partial latency and timestamps under real audio |
| M2M100 418M CT2 INT8 | Six-direction local MT research | Experimental six-direction comparisons; production runtime currently discards glossary context (#128 #139) |
| OPUS-MT directional | Fast specialized MT baseline | Direction-specific coverage; KO→EN alone cannot solve EN/JA→KO |
| Ollama / OpenAI-compatible | Runtime translation adapters | Prompt glossary can be supplied; local resource and remote privacy/latency must be evaluated independently |
| TranslateGemma 4B | MT research comparison | Prior CPU live timing/regression and `field_ready=false`; not automatically promoted |

All license, memory, speed and quality claims require the pinned run's source evidence; this table is a worklist.

## 5. Adoption, rollback and ADR

An adoption proposal must state: targeted PRD requirement and direction, baseline/alternative and model hashes, original run artifacts, hardware/license, quality/latency tradeoffs, glossary false-insertion controls, fallback, UI exposure, migration and rollback. Architectural selection gets an ADR in [decisions](decisions/README.md). A field regression or license failure revokes qualification for the affected scope; keep a last-known-good model option and visible operator warning.

## External comparator discipline

Use [external benchmark research](EXTERNAL_BENCHMARK_RESEARCH.md) as the reference for IWSLT simultaneous lag regimes and commercial translated-caption feature parity. Do **not** rank models against Microsoft Teams or Google Meet on undocumented p95 latency; no directly comparable public official SLA was established. Report StreamLAAL separately from actual microphone→audience p95/p99, and qualify the EN→KO / JA→KO founding cases on their own measured corpus.

## 6. Open decisions

Which ASR meets **native-streaming** first-caption and bilingual proper-noun targets? Which per-direction MT or multilingual MT achieves semantic fidelity on the actual Linux CPU runner? Can any local provider reliably enforce glossary without doctrinal or latency regressions? Which actual field device profiles can sustain 90 minutes? Answer with measurements, not marketing or synthetic scores.

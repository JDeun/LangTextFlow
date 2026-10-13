# Glossary-aware CPU interpretation qualification (draft)

This document is for the experimental stacked PR based on #129. It is **not**
a field-ready, production CTranslate2 glossary adapter.

## Required contract

Model qualification MUST use both identical unassisted translations and
translations actually affected by the selected glossary, on the **same source
audio/text, model revision, CPU, target language and hardware budget**.
Measuring words after inference alone is not an integration test.

The --glossary-snapshot flag is required in BOTH text and FLEURS audio
experiments. The JSON is GlossaryTransfer's portable operator export shape
from source-language PR #134: a schema_version=1 object with entries[].
Each entry requires an explicit source_language; ambiguous legacy entries
are rejected instead of being silently treated as Korean. Enabled, presets,
source language, aliases, and selected target language are honored. Only
utterance-relevant rows enter the lexical reranking step, capped at 24.

The benchmarks/church_glossary_multilingual.v1.json file is a **small, unreviewed**
export-compatible sample to make the experiment reproducible. Its 9 entries
cover three church concepts with independently scoped KO, EN and JA source
forms. It does **not** stand in for the full 157-entry KO-source dictionary
under PR #124 or the congregation's edited production glossary. A later
operator-exported versioned snapshot must be tested after #124/#134 merge,
including validated English/Japanese source entries and edit preservation.

## What is applied to the translation model?

M2M100 + CTranslate2 do not expose a proven domain-safe hard glossary
constraint in the current implementation. This experiment:

1. Generates a baseline hypothesis at beam 1.
2. Matches source-language-specific canonical terms / aliases from the
   exported glossary to the **actual transcribed source sentence**.
3. Where applicable, generates up to four additional **real MT hypotheses**
   (beam 4) and selects a candidate only if it contains more expected
   terminology, retains previously matched terms, preserves baseline numeral
   surfaces and detectable negation, and stays within 0.70–1.30 of baseline
   output length.
4. Otherwise **keeps the original generated baseline**, records the
   missing_targets and marks glossary_guaranteed=false. There is no
   fabricated translation, hard substitution or prompt-only success claim.
5. Records baseline inference time and total glossary-assisted elapsed time,
   along with per-target baseline/adapted strings, matched/expected counts,
   candidate changes, dataset SHA and glossary SHA.

The digit and negation checks are weak *regression tripwires*, not verified
semantic equivalence. A lexical term hit is **not** proof of faithful theology.
No automatic replacement of scripture citations, biblical names or negations.

## Required quality gates for selecting a model

- [x] Source/target direction, preset and enabled state isolate glossary terms.
- [x] Script and CLI use the portable operator export schema and fail closed
      when source language is missing or malformed.
- [x] Baseline vs assisted text comparisons, and actual glossary-aware path
      after ASR, are wired in the six-direction experiment.
- [x] Offline tests exercise the actual candidate-selection method and
      protection rules; English word boundaries; all six pairs.
- [ ] Exact-head CI, CodeQL and CPU model experiment pass.
- [ ] Independently review at least 20 **real glossary-bearing** samples per
      directed language pair, including negation, names, dates, Bible books,
      scripture chapter/verse preservation and differing denomination labels.
- [ ] Provide licensed long sermon/lecture speech, genuine ASR errors, and
      operator-exported glossary snapshot from #124/#134 after integration.
- [ ] Compare ASR hotword bias and MT glossary intervention **on/off** under
      the same session, not just separately.
- [ ] Measure sustained 10–30+ min p50/p95/p99, queue growth, RTF, failure,
      aggregate RAM, cold-start and reproducibility on GitHub Actions Linux.
- [ ] Compare licensed additional ASR and MT candidates via one shared
      experiment protocol; stop early on licensing or CPU execution blockers.

Model ranking must consider glossary misses and intervention overhead rather
than optimizing chrF++ in isolation. If a candidate never emits required
terms, mark it unsupported/needs adaptation, not glossary supported.
Do not promote an experimental beam reranker to the runtime service without
human evaluation, bounded latency and independent regression gates.

Related: #120, #122, #124, #125, #126, #127, #128, #129, #130, #134.

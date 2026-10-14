# Multidirectional, local live interpretation: the original church use case

## Origin and correct product requirement

LangTextFlow was conceived when visiting overseas missionaries delivered sermons
in a foreign language and a Korean congregation needed **Korean captions without
a human interpreter**. This is NOT a project exclusively translating a Korean
preacher into English or Japanese.

- Primary P0: **English sermon -> Korean captions**.
- Primary P0: **Japanese sermon -> Korean captions**.
- Primary P0: **Korean sermon -> English and/or Japanese captions**.
- Secondary P1: English -> Japanese, Japanese -> English.
- One ASR transcript should fan out to all selected other target languages.
- Manual source-language selection currently exists; automatic language
  detection and switching require explicit measured safety validation.
- At any point the audience can select the appropriate available language.
  The source-language track is not translation.

## Hardware

The first performance target is the actual **GitHub Actions hosted Linux CPU**
runner, capturing vCPU count, host RAM, CPU model and memory peaks. Physical
4GB/8GB Windows/Linux qualification is a *later* resource profile; it must not
arbitrarily eliminate a feasible baseline for the current target.

## Initial model candidates

- Multilingual ASR: Qwen3-ASR 0.6B / 1.7B, native-streaming + offline; plus
  faster-whisper and whisper.cpp as multilingual comparators. Existing Korean
  only Qwen3 0.6B ASR evidence does NOT say anything about its English/Japanese
  accuracy or its native streaming path.
- Multilingual MT: facebook/m2m100_418M, MIT, CT2 INT8, explicit source
  language and forced target token. Evaluate six directed language pairs.
- Directional MT baseline: OPUS-MT Korean->English from #119, **cannot** itself
  solve visiting English/Japanese missionaries -> Korean.
- Specialized MT per direction, bigger multilingual models and source-language
  detection are follow-up A/B candidates; license and GitHub runner availability
  must be recorded for each.

## The experimental evidence in this PR

This workflow is **a smoke baseline, not a production system**:

1. Twelve **synthetic, unreviewed** lecture/sermon sentences, each with an
   authored Korean, English and Japanese version, cross-combined to form
   **72 directional translations** (6 directions x 12 examples). p95, safe
   output presence and unreviewed chrF++ are computed separately per direction.
   Some cases include negations, numbers, model terms and Bible references.
2. Fixed FLEURS CC BY 4.0 test parquet at the existing pinned dataset revision:
   3 Korean, 3 English and 3 Japanese real utterances (real audio hashes
   recorded). One Qwen3-ASR 0.6B INT8 source recognizer loaded, followed by
   the two other language translations **from that same ASR output**. Source
   CER/WER, per-language translation presence, workload inference-only RTF,
   full paced replay RTF, p95 estimated dual-caption lag and co-resident RSS.
3. Model and converted weights hashes, full text/real-speech hypotheses,
   source audio IDs, license, RAM and CPU metadata are GitHub Action artifacts.
4. Any empty, verbatim-source, metadata-JSON or missing language-pair output
   fails the workflow. A non-empty translation is NOT necessarily correct.

The baseline is utterance-at-a-time with a **sequential**, two-target CPU
translator. It does not verify overlapping partial ASR and speculative MT,
interleaved input languages, automatic LID, continuous microphone/soundboard
processing, 30–90min backlog control, or doctrinal integrity. There are **no
independently reviewed bilingual translations for these real audio clips**.

## Next acceptance gates

- Validated actual audio from foreign-language missionary sermons where
  redistribution permission exists, otherwise use consented lecture/
  conference material with long contiguous speech and high-quality human
  text translations. Do not assume synthesized FLEURS references mean
  real-world bilingual translation correctness.
- Side-by-side source recognition (including biblical names) and translation
  meaning by human review; separate glossary adherence (#122) and schema/UI
  work (#120/#121/#123).
- Actual Qwen3-ASR native streaming for English, Japanese, Korean; measure
  first partial, revisions and source-language identification separately.
- Live CaptionRuntime concurrency, audio->caption p50/p95/p99, queue depth,
  stale revision cancellation, 10–30+ minutes sustained CPU throughput;
  compare to Cuckoo product experience, not assumed internal performance.
- Asymmetric primary priority: en->ko and ja->ko MUST pass before we
  report "missionary sermon" feature readiness.

Related work: #126 #127 #128 #119 #122.

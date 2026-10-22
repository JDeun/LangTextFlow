# LangTextFlow research landscape

Current research spans local multilingual ASR, six-direction translation, realtime stability, glossary correctness and CPU-only deployment. This page is an **index-level synthesis**, not a claim of production qualification.

- **Status:** seed synthesis; needs source-by-source ingest
- **Last reviewed:** 2026-10-22
- **Evidence class:** existing reports / unverified field acceptance

## Evidence links
- [Open-source dictation comparison](../../research/OPEN_SOURCE_DICTATION_COMPARISON.md): README-level analysis, not code audit or measured superiority.
- [Model guide](../../MODEL_GUIDE.md): candidate and provider responsibilities.
- [Benchmark protocol](../../BENCHMARK.md) and [translation protocol](../../TRANSLATION_BENCHMARK.md): how measurements should be gathered; protocols are not results.
- [Church quality baseline](../../CHURCH_QUALITY_BASELINE.md): church sermon acceptance context.
- [Research index](../../research/README.md): remaining canonical research and tracking issues.

## Open questions
1. Which ASR and translation combinations meet ko/en/ja six-direction accuracy and latency on actual target CPU hardware?
2. Which glossary enforcement mechanism improves terminology without damaging unrelated speech?
3. What are the real 30/60/90-minute stability and memory measurements on 4GB/8GB machines?

Return to the [knowledge index](index.md). No model promotion or ADR approval is implied.

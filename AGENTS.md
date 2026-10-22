# LangTextFlow agent operating rules

For research, model comparisons, or architecture decisions, **read [knowledge schema](docs/knowledge/schema.md) and [wiki index](docs/knowledge/wiki/index.md) first**. The wiki is an evidence-linked synthesis, not a replacement for source documents, code, or reproducible measurements.

- Preserve immutable source records under `docs/knowledge/raw/`; never silently edit or overwrite an existing record.
- The agent maintains wiki pages and cross-links **through reviewed GitHub PRs**, not direct pushes to main.
- Follow `ingest`, `query`, `lint`, and `promote` protocols in the schema. Record evidence quality and date; flag contradictions rather than inventing resolutions.
- Keep existing `docs/research/` reports and `docs/decisions/` ADRs canonical; link instead of duplicating or rewriting them automatically.
- Never turn external reported metrics into LangTextFlow measurements, and never approve a model or release without actual acceptance evidence.
- Respect licenses, consent, confidential audio, privacy and no external upload of user speech without explicit authorization.
- Wiki maintenance is **agent-assisted, not a deployed autonomous service**; instructions alone do not schedule jobs or run an LLM.

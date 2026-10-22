# LangTextFlow LLM Wiki

This is an **agent-assisted Git-managed knowledge base**, not a separately deployed wiki application. The approach separates immutable [source records](raw/README.md), agent-maintained [wiki synthesis](wiki/index.md), and [operating schema](schema.md).

## Start here
1. Read the [schema](schema.md) and [index](wiki/index.md).
2. Read [AGENTS.md](../../AGENTS.md) for repository-wide agent expectations.
3. Use the existing [research index](../research/README.md) for canonical research and [ADRs](../decisions/README.md) for approved technical decisions.

## Operations
- **Ingest:** curate one source, append immutable provenance, update related wiki pages and index/log in a reviewed PR.
- **Query:** search index first, cite primary sources, optionally file a durable answer **with approval**.
- **Lint:** inspect links, stale claims, missing sources and contradictions; report before modifying scientific claims.
- **Promote:** after measurement, rights review and acceptance, record a separate approved ADR.

The seed wiki is a starting map, not a completed ingestion of all research. No automatic scheduler, LLM runtime, or semantic lint implementation is included yet.

## Offline CLI tools

Register immutable source metadata (manual, no fetching): `uv run python scripts/ingest_knowledge_source.py --help`.

Search existing raw and wiki Markdown locally (Unicode keyword matching; not semantic RAG): `uv run python scripts/query_knowledge.py 'ASR'`.

Structural wiki validation: `uv run python scripts/check_knowledge_wiki.py`.

These commands do not call a hosted LLM or modify approved ADRs. Query only returns matched file paths and excerpts; verify the underlying cited source before making technical decisions.

Create a review-only wiki update plan from a registered source (no edits performed): `uv run python scripts/plan_knowledge_update.py docs/knowledge/raw/<source-record>.md`.

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

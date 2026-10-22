# LangTextFlow LLM Wiki schema (v1)

## Purpose and authority
A lightweight Git-managed adaptation of the raw → LLM-maintained wiki → schema pattern. Human curates sources and approves PRs; agent synthesizes wiki. Canonical technical reports live in [research](../research/README.md), accepted decisions in [ADRs](../decisions/README.md). A wiki summary is never a primary source or approval record.

## Layers
- `raw/`: **append-only provenance records**, not copied copyrighted articles or private audio. Each record gives source URL, accessed date, revision/hash, rights, scope and evidence classification. Never rewrite an existing raw record; corrections are new records linked to the original.
- `wiki/`: agent-maintained summaries, entities, comparisons, open questions, index and append-only log. Every substantive claim cites a raw record or canonical source link. Mark `external-reported`, `reproduced`, `hypothesis`, or `unverified`.
- `schema.md` and repository `AGENTS.md`: operating contract. Modify via PR when conventions change.

## Page conventions
Use kebab-case .md filenames, H1 title, short summary, `Status` and `Last reviewed` fields, source citations, relevant cross-links, and open validation gaps. Relative Markdown links must resolve. Index each new page in `wiki/index.md`. Append timestamped entries to `wiki/log.md`; never rewrite history. Do not manufacture measurement results, model licenses, or research completion.

## Ingest — agent instruction, manually invoked
1. Accept **one curated source** with permalink, date, license/redistribution status and issue reference; add a new immutable raw metadata record.
2. Read `wiki/index.md`, the raw record, and related existing wiki/research/ADR pages before writing.
3. Create/update relevant wiki pages; preserve provenance for each claim, distinguish source-reported vs locally measured, and flag conflicts or superseded statements.
4. Update bidirectional links when relevant, index and append a log entry. Open a PR with changes, validation gaps and follow-up issue. Do not change an accepted ADR automatically.

## Query — agent instruction, manually invoked
1. Read `wiki/index.md` first, then relevant pages and their cited primary records.
2. Answer with traceable links and evidence classification; explicitly identify stale, missing or conflicting evidence.
3. **Only when requested or approved**, file durable synthesis as a wiki page; update index/log via PR. Query alone makes no changes.

## Lint — agent instruction, manually invoked
1. Check missing relative links, index entries, orphan pages, empty content, missing provenance, conflicting metrics, stale dates, and unlinked decisions.
2. Report mechanical vs semantic findings separately. Do not automatically rewrite scientific claims or accepted ADRs; propose corrective PRs and verification tasks.
3. Append lint result to log **only when a PR is actually created**. Do not claim automated semantic lint is deployed.

## Promote — decision gate
A wiki conclusion becomes a recommended engineering decision only after reproducible evidence, license/rights review, acceptance criteria, and reviewer sign-off. Record accepted decisions in `docs/decisions/` through a separate PR. Wiki updates cannot themselves promote a model, change defaults, or close field-validation issues.

## Safety and scale
No private transcripts, credentials, copyrighted full text, or personal information in the public repository. Store references and approved redacted summaries instead. Start with Markdown and existing CI link checks; introduce semantic indexing only if volume warrants it. No scheduled ingestion or autonomous external fetch is configured by this schema.

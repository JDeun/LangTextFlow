# Operator glossary workspace (Korean, English and Japanese)

The **Glossary** is a persistent SQLite-backed term dictionary, not the free-form
ASR hotword textarea or session reference documents. It is shared between
sessions and filtered by preset. Operator access is required by the API.

## Access and editing

Use the glossary navigation item (at `/#/glossary`, compatible with packaged desktop assets) in the operator's top bar or the shortcut
under Setup → Context. The dedicated workspace displays all saved terms,
imports/exports and suggestions. Returning to the live view via the in-app
button does not reload the React application or interrupt an active session.

For every term the editor manages:

- Canonical term with explicit spoken/source-language metadata and same-language ASR aliases
- **Korean (ko)**, **English (en)** and **Japanese (ja)** target translations
- Category, relevance boost (0–20), enabled flag
- Global scope or one/multiple preset scopes (`general`, `church`,
  `conference`, `lecture`)

Select **Edit** to modify an existing record. PUT keeps the database ID and
creation time. Editing KO/EN/JA target fields preserves all *other* stored language
translations; clearing one field deletes only that target mapping. The **Cancel**
button discards the draft; deleting remains an explicit separate operation.
JSON and CSV round-trip the full set of mapped languages and presets.

During a live session the glossary can be viewed, but editing is disabled:
the server snapshots the active preset's glossary at session start. Existing
live sessions are not retroactively altered by a saved term change.

## Where the glossary actually applies

1. At `POST /api/v1/session/start`, enabled global and matching preset
   records are copied into `SessionContext.glossary`.
2. Supported ASR engines receive the term and aliases as hotword hints.
3. Deterministic correction can replace known aliases with canonical terms.
4. Ollama and OpenAI-compatible translators select glossary mappings for
   the current output language and include them in the prompt.

**This does not guarantee exact translation adherence.** Prompt-based glossary
mappings may be ignored. In particular, the CTranslate2/OPUS-MT candidate
benchmarks in #119 do not yet read `SessionContext.glossary` for constrained
decoding or dictionary-aware scoring. Follow-up #122 handles provider-neutral
terminology adherence; #123 tracks project-specific scopes. Never silently
rewrite arbitrary Japanese/English substrings, negation, dates or Bible
chapter/verse numbers.

## Church default seed

The church seed contains **157 editable Korean-source terms**, each with EN and JA
equivalents. These are starter values, not a certified
denominational translation: an editor may change them. Reimporting the seed
does not replace existing matching canonical term entries.

## What is measured separately

Benchmarking Korean lecture, speech or worship audio may use glossary-scoped
terms but should also report ordinary semantic translation fidelity and
source ASR errors. The initial hardware target for model ranking is the
**GitHub Actions Linux CPU runner**, not 4GB/8GB physical machines; long
continuous soundboard tests are a later acceptance gate.

## Per-term language identity (source-language migration, #130)

The operator selects a **source/spoken language** for each canonical term.
English sermon "grace" → Korean "은혜" is a separate glossary row from
Korean sermon "은혜" → English "grace" or Japanese sermon "恵み" → Korean "은혜".
Aliases apply only to their source language.

The existing SQLite table gains `source_language TEXT NOT NULL DEFAULT 'ko'`
via an idempotent migration. Legacy records retain their IDs, timestamps and
translations and are assigned **Korean as a conservative compatibility default**.
Review old imported technical/general English terms and explicitly change their
source language; the migration **does not infer** source identity from spelling.

At session creation, only rows matching the selected input language and preset
are snapshotted to `SessionContext.glossary`. The session does not change when
an operator edits a glossary record. JSON/CSV exports now include
`source_language`; v1 legacy files without it import as Korean, and duplicate
terms across different source languages are permitted.

Caution: manually selecting a source language is currently necessary; automatic
language identification, code-switching and constrained decoding have not been
certified for actual missionary sermons. Language-tagged terminology fixes
cross-language ASR hints but cannot establish semantic MT fidelity on its own.

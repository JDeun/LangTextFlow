# Operator glossary workspace (English and Japanese)

The **Glossary** is a persistent SQLite-backed term dictionary, not the free-form
ASR hotword textarea or session reference documents. It is shared between
sessions and filtered by preset. Operator access is required by the API.

## Access and editing

Use the glossary navigation item in the operator's top bar or the shortcut
under Setup → Context. The dedicated workspace displays all saved terms,
imports/exports and suggestions. Returning to the live view via the in-app
button does not reload the React application or interrupt an active session.

For every term the editor manages:

- Korean source/canonical term and ASR misrecognition aliases
- Both **English (en)** and **Japanese (ja)** translations
- Category, relevance boost (0–20), enabled flag
- Global scope or one/multiple preset scopes (`general`, `church`,
  `conference`, `lecture`)

Select **Edit** to modify an existing record. PUT keeps the database ID and
creation time. Editing either target language preserves all *other* existing
translation languages; emptying en/ja deletes only that mapping. The **Cancel**
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

The existing church seed now includes human-readable starter **en and ja**
equivalents for eight terms. These are starter values, not a certified
denominational translation: an editor may change them. Reimporting the seed
does not replace existing matching canonical term entries.

## What is measured separately

Benchmarking Korean lecture, speech or worship audio may use glossary-scoped
terms but should also report ordinary semantic translation fidelity and
source ASR errors. The initial hardware target for model ranking is the
**GitHub Actions Linux CPU runner**, not 4GB/8GB physical machines; long
continuous soundboard tests are a later acceptance gate.

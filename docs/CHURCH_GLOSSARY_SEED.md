# Built-in Korean sermon glossary (v1)

Use **Operator → Glossary (`/#/glossary`) → Import church preset terms**
(`POST /api/v1/glossary/presets/church`) once to populate the **editable**
church glossary. Nothing is silently seeded into general, lecture or
conference sessions. Importing the preset again inserts missing terms but
does **not** overwrite edited records. You can disable, delete or edit each
entry, or import/export the complete data as JSON/CSV.

## Coverage

- **66 biblical books** (39 OT + 27 NT) in Korean canonical forms, standard
  English titles and Japanese book names from Japan Bible Society.
- **74 common sermon concepts**: gospel, salvation, faith, grace, atonement,
  Scripture, worship, baptism, communion, scripture references, etc.
- **17 common names and places**: Abraham, Moses, David, Mary, Peter, Paul,
  Galilee, Jerusalem, Bethlehem and others.

Total **157 entries** (66 books, 74 concepts, 17 names/places); two
ambiguous Korean terms were omitted rather than hard-coding risky meanings.
Edit theological terms before deployment:
`baptism` (`침례` versus `세례`), `Communion/Lord's Supper`, God/lord names,
and word choices may vary by denomination, Japanese Bible edition, and
speaker style. Korean canonical terms are **not an authoritative cross-church
terminology standard**. Do not automatically replace Bible verse numbers,
negation or whole sentences based on a term table.

The Japanese **66 book names** follow Japan Bible Society
([official title list](https://www.bible.or.jp/read/vers_search/titlename.html)).
Some congregations use forms such as `使徒の働き`, `伝道者の書`, or `詩篇`.
These alternate spellings are not automatically declared equivalent to all
Japanese speech recognition/translation systems. Japanese aliases require explicitly selected Japanese source-language
records rather than being automatically derived from Korean translations.

## Important caveats

The canonical **input term is currently Korean**, with EN/JA translations.
Source-language tagging is implemented under #130. The bundled entries are
explicitly Korean-source records, so they do NOT automatically provide English
or Japanese ASR hints. Add independently reviewed English/Japanese canonical
terms and aliases as separate source-language records.

A dictionary is not a translation quality guarantee; model-specific EN/JA/KO
fidelity remains under #122/#126/#129. Broad 100+ entry dictionaries also
now use **relevance-limited prompts** for Ollama and OpenAI-compatible
translators (maximum 24 matches per utterance, matched against canonical terms
and aliases). Compare prompt/ASR hotword latency on GitHub Actions Linux CPU.
The full seed is still offered to ASR hint paths; field throughput is not certified.

Data source and distribution: `apps/server/langtextflow/data/church_glossary_seed.v1.json`
contains **short terminology records only**, no copyrighted Bible verse
passages, and is distributed as source under the project's license.

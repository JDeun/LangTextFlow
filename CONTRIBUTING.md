# Contributing to LangTextFlow

LangTextFlow is a realtime captioning system. Changes are reviewed not only for feature correctness but also for latency, failure containment, security boundaries, and reproducibility.

## Development setup

Backend:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
```

Frontend:

```bash
cd apps/web
npm ci
npm run build
```

Optional ASR/benchmark dependencies are documented in [README.md](README.md) and [docs/README.md](docs/README.md).

## Required checks

Before opening a pull request, run the checks that apply to your change:

```bash
ruff check apps/server scripts
pytest --strict-config --strict-markers --cov=apps/server/langtextflow --cov-report=term-missing
python scripts/repo_hygiene.py
python scripts/check_hf_snapshot_calls.py
python scripts/check_markdown_links.py
```

Frontend changes must also pass:

```bash
cd apps/web
npm ci
npm audit --audit-level=high
npm run build
```

GitHub Actions additionally runs `pip check`, `pip-audit`, Bandit, dependency audits, and CodeQL.

## Realtime safety rules

Changes to ASR, WebSocket, audio, persistence, correction, or translation paths must preserve these invariants:

- one slow or broken audience client must not block other clients;
- external provider failures must not stop the original-language caption path when degraded operation is possible;
- untrusted transcript, glossary, and reference-document text must remain data, not model instructions;
- audio and document parsing must enforce size and structural limits before expensive work;
- persistence failures must be isolated from live caption delivery;
- replay/failover must not regress segment ordering or duplicate suppression.

See [docs/ADVERSARIAL_VALIDATION.md](docs/ADVERSARIAL_VALIDATION.md) and [docs/SECURITY_MODEL.md](docs/SECURITY_MODEL.md).

## Tests for bug fixes

A bug fix should include a regression test that fails before the fix whenever practical. Security- or reliability-sensitive regressions should be placed in an adversarial or lifecycle test module so the intent remains visible.

## Documentation

Public behavior changes must update the relevant documentation. Local Markdown links are validated in CI; do not add links to files that are not committed.

## Pull requests

Keep pull requests focused. Describe:

1. the user-visible or operational change;
2. failure/degraded behavior;
3. security or data-boundary impact;
4. validation performed;
5. any physical or field validation still required.

Do not mark hardware soak, live-room audio quality, signed installers, or model-quality measurements as complete without actual evidence.

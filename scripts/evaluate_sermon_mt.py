"""Opt-in local M2M100 sermon-corpus inference; never downloads model assets.

Example:
  python scripts/evaluate_sermon_mt.py --model-dir data/model-cache/m2m100-ct2 \\
      --tokenizer-dir data/model-cache/m2m100-tokenizer --output results/sermon-mt.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "server"))
sys.path.insert(0, str(ROOT / "scripts"))

from validate_sermon_mt_eval import CORPUS, validate  # noqa: E402

from langtextflow.models import SessionContext  # noqa: E402
from langtextflow.translation.m2m100_ct2 import M2M100CT2Translator  # noqa: E402


async def evaluate(
    model_dir: str, tokenizer_dir: str, cpu_threads: int, *,
    corpus: Path = CORPUS,
) -> dict[str, object]:
    """Run every corpus record and retain failures instead of aborting a batch."""
    validation = validate(corpus)
    provider = M2M100CT2Translator(
        model_dir=model_dir, tokenizer_dir=tokenizer_dir, cpu_threads=cpu_threads,
    )
    await provider.prepare()
    records: list[dict[str, object]] = []
    try:
        for raw in corpus.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            item = json.loads(raw)
            started = perf_counter()
            output = None
            error = None
            try:
                output = await provider.translate(
                    item["source"],
                    source_language=item["source_language"],
                    target_language=item["target_language"],
                    context=SessionContext(),
                )
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            records.append({
                **item,
                "output": output,
                "error": error,
                "latency_ms": round((perf_counter() - started) * 1000, 1),
                "exact_match": output == item["reference"] if error is None else None,
            })
    finally:
        await provider.close()
    return {
        "model_dir": model_dir,
        "tokenizer_dir": tokenizer_dir,
        "cpu_threads": cpu_threads,
        "validation": validation,
        "success_count": sum(row["error"] is None for row in records),
        "failure_count": sum(row["error"] is not None for row in records),
        "records": records,
        "quality_review_required": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", default="data/model-cache/m2m100-ct2")
    parser.add_argument("--tokenizer-dir", default="data/model-cache/m2m100-tokenizer")
    parser.add_argument("--cpu-threads", type=int, default=2)
    parser.add_argument("--corpus", type=Path, default=CORPUS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(evaluate(
        args.model_dir, args.tokenizer_dir, args.cpu_threads, corpus=args.corpus,
    ))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(f"Evaluated {len(report['records'])} samples: "
          f"{report['success_count']} succeeded, {report['failure_count']} failed")
    return 1 if report["failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Opt-in real M2M100 CPU six-direction smoke; no implicit downloads.

Usage: python scripts/verify_m2m100_six_pairs.py
Run after manually provisioning the pinned local model/tokenizer cache.
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

from langtextflow.models import SessionContext  # noqa: E402
from langtextflow.translation.m2m100_ct2 import M2M100CT2Translator  # noqa: E402

SAMPLES = {
    "ko": "오늘 우리는 함께 새로운 가능성을 배웁니다.",
    "en": "Today we learn about new possibilities together.",
    "ja": "今日は一緒に新しい可能性について学びます。",
}


async def verify(model_dir: str, tokenizer_dir: str, cpu_threads: int) -> dict:
    provider = M2M100CT2Translator(
        model_dir=model_dir, tokenizer_dir=tokenizer_dir, cpu_threads=cpu_threads,
    )
    await provider.prepare()
    results = []
    try:
        for source in ("ko", "en", "ja"):
            for target in ("ko", "en", "ja"):
                if source == target:
                    continue
                started = perf_counter()
                try:
                    result = await provider.translate(
                        SAMPLES[source], source_language=source,
                        target_language=target, context=SessionContext(),
                    )
                    results.append({
                        "source": source, "target": target,
                        "status": "passed", "output": result,
                        "elapsed_ms": round((perf_counter() - started) * 1000, 1),
                    })
                except Exception as exc:
                    results.append({
                        "source": source, "target": target,
                        "status": "failed", "error": type(exc).__name__,
                        "elapsed_ms": round((perf_counter() - started) * 1000, 1),
                    })
    finally:
        await provider.close()
    return {"model_dir": model_dir, "tokenizer_dir": tokenizer_dir, "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", default="data/model-cache/m2m100-ct2")
    parser.add_argument("--tokenizer-dir", default="data/model-cache/m2m100-tokenizer")
    parser.add_argument("--cpu-threads", type=int, default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(verify(args.model_dir, args.tokenizer_dir, args.cpu_threads))
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if all(item["status"] == "passed" for item in report["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())

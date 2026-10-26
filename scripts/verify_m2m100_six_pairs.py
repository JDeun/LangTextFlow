"""Opt-in real M2M100 CPU six-direction smoke; no implicit downloads.

Usage: python scripts/verify_m2m100_six_pairs.py
Run after manually provisioning the pinned local model/tokenizer cache.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
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


def _linux_memory_kib() -> dict[str, int] | None:
    """Linux-only process RSS and high-water mark from /proc/self/status."""
    try:
        status = Path("/proc/self/status").read_text(encoding="utf-8")
    except OSError:
        return None
    values: dict[str, int] = {}
    for line in status.splitlines():
        if line.startswith(("VmRSS:", "VmHWM:")):
            parts = line.split()
            if len(parts) == 3 and parts[2] == "kB" and parts[1].isdigit():
                values[parts[0].rstrip(":")] = int(parts[1])
    if not all(key in values for key in ("VmRSS", "VmHWM")):
        return None
    return {"rss_kib": values["VmRSS"], "peak_rss_kib": values["VmHWM"]}


def _percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile; conservative for small benchmark samples."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


async def verify(
    model_dir: str, tokenizer_dir: str, cpu_threads: int, *, repetitions: int = 1,
    warmup_runs: int = 0,
) -> dict:
    if repetitions < 1:
        raise ValueError("repetitions must be at least one")
    if warmup_runs < 0:
        raise ValueError("warmup runs must not be negative")
    memory_before = _linux_memory_kib()
    provider = M2M100CT2Translator(
        model_dir=model_dir, tokenizer_dir=tokenizer_dir, cpu_threads=cpu_threads,
    )
    await provider.prepare()
    memory_after_prepare = _linux_memory_kib()
    results = []
    memory_after_inference = None
    try:
        for source in ("ko", "en", "ja"):
            for target in ("ko", "en", "ja"):
                if source == target:
                    continue
                durations = []
                outputs = []
                errors = []
                warmup_errors = []
                for index in range(warmup_runs + repetitions):
                    started = perf_counter()
                    try:
                        result = await provider.translate(
                            SAMPLES[source], source_language=source,
                            target_language=target, context=SessionContext(),
                        )
                        if index >= warmup_runs:
                            durations.append((perf_counter() - started) * 1000)
                            outputs.append(result)
                    except Exception as exc:
                        if index < warmup_runs:
                            warmup_errors.append(type(exc).__name__)
                        else:
                            errors.append(type(exc).__name__)
                results.append({
                    "source": source, "target": target,
                    "status": "passed" if not errors and not warmup_errors else "failed",
                    "runs": repetitions, "warmup_runs": warmup_runs,
                    "successful_runs": len(durations),
                    "errors": errors, "warmup_errors": warmup_errors,
                    "output": outputs[-1] if outputs else None,
                    "p50_ms": round(statistics.median(durations), 1) if durations else None,
                    "p95_ms": round(_percentile(durations, 0.95), 1) if durations else None,
                })
    finally:
        memory_after_inference = _linux_memory_kib()
        await provider.close()
    return {
        "model_dir": model_dir, "tokenizer_dir": tokenizer_dir,
        "memory_before_prepare": memory_before,
        "memory_after_prepare": memory_after_prepare,
        "memory_after_inference": memory_after_inference,
        "cpu_threads": cpu_threads, "repetitions": repetitions,
        "warmup_runs": warmup_runs,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", default="data/model-cache/m2m100-ct2")
    parser.add_argument("--tokenizer-dir", default="data/model-cache/m2m100-tokenizer")
    parser.add_argument("--cpu-threads", type=int, default=2)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--warmup-runs", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(verify(
        args.model_dir, args.tokenizer_dir, args.cpu_threads,
        repetitions=args.repetitions, warmup_runs=args.warmup_runs,
    ))
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if all(item["status"] == "passed" for item in report["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())

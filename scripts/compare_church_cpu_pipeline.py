"""Measure CTranslate2 Korean→English MT and co-resident CPU ASR+MT candidates.

Research harness only: three FLEURS read-speech clips and synthetic domain text
are insufficient to certify real church interpretation, latency, or reliability.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from langtextflow.benchmark import quality_metrics
from langtextflow.translation_benchmark import terminology_metrics
from scripts.benchmark_cpu_asr_candidates import (
    ResourceSamples,
    create_recognizer,
    decode_sample,
    load_three_pinned_fleurs,
)
from scripts.evaluate_fleurs_asr import FLEURS_REVISION, write_pcm_wav

MODEL_IDS = {
    "opus-small": "Helsinki-NLP/opus-mt-ko-en",
    "opus-big": "Helsinki-NLP/opus-mt-tc-big-ko-en",
}
ASR_CANDIDATES = ("qwen3-0.6b", "zipformer-ko")


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lo = int(position)
    hi = min(lo + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo), 2)


def acceptable_output(source: str, hypothesis: str) -> bool:
    """Never publish an empty/untranslated or leaked metadata envelope."""
    result = hypothesis.strip()
    if not result or result.casefold() == source.strip().casefold():
        return False
    if result.startswith("{") or result.startswith("["):
        return False
    if any(token in result for token in ('"session_title"', '"source_text"', '"context"')):
        return False
    return True


def validate_fixtures(path: Path) -> tuple[list[dict[str, Any]], str]:
    source = path.read_bytes()
    rows = [json.loads(line) for line in source.decode("utf-8").splitlines() if line.strip()]
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)) or len(rows) != 20:
        raise ValueError("expected 20 distinct baseline fixture IDs")
    ko_en = [r for r in rows if r["source_language"] == "ko" and r["target_language"] == "en"]
    if len(ko_en) != 16:
        raise ValueError("expected 16 baseline Korean-to-English fixtures")
    return ko_en, hashlib.sha256(source).hexdigest()


class CTranslateMarian:
    def __init__(self, model_dir: Path, tokenizer_dir: Path, *, threads: int) -> None:
        import ctranslate2
        from transformers import AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(
            str(tokenizer_dir), local_files_only=True, use_fast=False
        )
        self.engine = ctranslate2.Translator(
            str(model_dir), device="cpu", compute_type="int8",
            inter_threads=1, intra_threads=threads,
        )

    def translate(self, text: str) -> str:
        encoded = self.tokenizer.convert_ids_to_tokens(self.tokenizer.encode(text))
        translated = self.engine.translate_batch(
            [encoded], beam_size=1, max_decoding_length=256,
        )[0].hypotheses[0]
        return self.tokenizer.decode(
            self.tokenizer.convert_tokens_to_ids(translated),
            skip_special_tokens=True,
        ).strip()


def text_benchmark(
    translator: CTranslateMarian, fixture_path: Path,
) -> dict[str, Any]:
    from sacrebleu.metrics import CHRF

    rows, corpus_sha = validate_fixtures(fixture_path)
    chrf = CHRF(word_order=2)
    items: list[dict[str, Any]] = []
    with ResourceSamples() as resources:
        for item in rows:
            begin = time.perf_counter()
            hypothesis = translator.translate(item["source_text"])
            ms = (time.perf_counter() - begin) * 1000
            valid = acceptable_output(item["source_text"], hypothesis)
            term = terminology_metrics(tuple(item.get("expected_terms", [])), hypothesis)
            items.append({
                "id": item["id"], "source": item["source_text"],
                "hypothesis": hypothesis, "reference": item["reference_text"],
                "latency_ms": round(ms, 2), "valid": valid,
                "chrf_plus_plus": round(
                    chrf.sentence_score(hypothesis, [item["reference_text"]]).score, 3
                ) if valid else None,
                "critical_terms": term,
                "reference_status": "synthetic_unreviewed",
            })
    scores = [i["chrf_plus_plus"] for i in items if i["chrf_plus_plus"] is not None]
    expected = sum(i["critical_terms"]["expected"] for i in items)
    matched = sum(i["critical_terms"]["matched"] for i in items)
    latencies = [i["latency_ms"] for i in items]
    return {
        "mode": "text", "fixture_sha256": corpus_sha, "cases": len(items),
        "valid_outputs": sum(i["valid"] for i in items),
        "mean_sentence_chrf_plus_plus": (
            round(statistics.fmean(scores), 3) if scores else None
        ),
        "critical_term_accuracy": round(matched / expected, 4) if expected else None,
        "latency_p50_ms": percentile(latencies, 0.50),
        "latency_p95_ms": percentile(latencies, 0.95),
        "peak_co_resident_rss_mb": resources.summary()["rss_peak_mb"],
        "samples": items, "field_ready": False,
    }


def real_audio_benchmark(
    translator: CTranslateMarian, asr_name: str, asr_dir: Path, threads: int,
) -> dict[str, Any]:
    if asr_name not in ASR_CANDIDATES:
        raise ValueError("unsupported ASR combination")
    rows: list[dict[str, Any]] = []
    with ResourceSamples() as resources:
        recognizer = create_recognizer(asr_name, asr_dir, threads)
        experiment_begin = time.perf_counter()
        audio_seconds = 0.0
        with tempfile.TemporaryDirectory(prefix="ltf-ct2-fleurs-") as temp:
            for idx, sample in enumerate(load_three_pinned_fleurs()):
                wav = Path(temp) / f"sample-{idx}.wav"
                seconds, sha = write_pcm_wav(sample["audio"], wav)
                audio_seconds += seconds
                asr = decode_sample(
                    asr_name, recognizer, wav, threads=threads, pace="realtime",
                )
                before_mt = time.perf_counter()
                hypothesis = translator.translate(asr["hypothesis"])
                mt_ms = (time.perf_counter() - before_mt) * 1000
                # Sequential utterances on a single co-resident CPU.
                # This is an intentionally conservative replay proxy, NOT
                # realtime runtime streaming or first-token latency.
                wall_seconds = time.perf_counter() - experiment_begin
                rows.append({
                    "id": sample["id"], "pcm_sha256": sha,
                    "duration_seconds": round(seconds, 3),
                    "reference_ko": sample["text"],
                    "asr_hypothesis_ko": asr["hypothesis"],
                    "source_quality": quality_metrics(sample["text"], asr["hypothesis"]),
                    "translated_en": hypothesis,
                    "translation_valid": acceptable_output(asr["hypothesis"], hypothesis),
                    "mt_inference_ms": round(mt_ms, 2),
                    "asr_inference_seconds": asr["inference_seconds"],
                    "asr_streaming_kind": asr["streaming_kind"],
                    "estimated_translation_lag_ms": round(
                        max(0, wall_seconds - audio_seconds) * 1000, 1
                    ),
                })
        elapsed = time.perf_counter() - experiment_begin
        del recognizer
    return {
        "mode": "real-audio", "dataset": "google/fleurs",
        "revision": FLEURS_REVISION, "source_language": "ko",
        "target_language": "en", "asr_model": asr_name,
        "audio_seconds": round(audio_seconds, 3),
        "experiment_wall_seconds": round(elapsed, 3),
        "sequential_replay_rtf": round(elapsed / audio_seconds, 4),
        "estimated_translation_lag_p95_ms": percentile(
            [x["estimated_translation_lag_ms"] for x in rows], 0.95
        ),
        "translated_coverage": sum(x["translation_valid"] for x in rows),
        "peak_co_resident_rss_mb": resources.summary()["rss_peak_mb"],
        "samples": rows, "human_translation_reference": False,
        "field_ready": False,
        "limitations": (
            "Three isolated read-speech samples, co-resident offline/online "
            "ASR and MarianMT, but NOT live CaptionRuntime or a continuous "
            "30-90min microphone/soundboard test. English semantic accuracy "
            "has no independent human reference."
        ),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", choices=MODEL_IDS, required=True)
    p.add_argument("--ct2-dir", type=Path, required=True)
    p.add_argument("--tokenizer-dir", type=Path, required=True)
    p.add_argument("--mode", choices=["text", "real-audio"], required=True)
    p.add_argument("--fixture", type=Path, default=Path("benchmarks/church_translation_quality.v1.jsonl"))
    p.add_argument("--asr", choices=ASR_CANDIDATES)
    p.add_argument("--asr-dir", type=Path)
    p.add_argument("--threads", type=int, default=2)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if not 1 <= args.threads <= 4:
        p.error("threads must be 1-4")
    if args.mode == "real-audio" and (args.asr is None or args.asr_dir is None):
        p.error("real-audio requires --asr and --asr-dir")
    model = CTranslateMarian(args.ct2_dir, args.tokenizer_dir, threads=args.threads)
    started = time.perf_counter()
    report = (
        text_benchmark(model, args.fixture) if args.mode == "text"
        else real_audio_benchmark(model, args.asr, args.asr_dir, args.threads)
    )
    report.update({
        "schema_version": 1, "model_id": MODEL_IDS[args.model],
        "compute_type": "int8", "cpu_threads": args.threads,
        "total_elapsed_seconds": round(time.perf_counter() - started, 3),
        "quality_certified": False,
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in report.items() if k != "samples"}, ensure_ascii=False))
    if args.mode == "text" and report["valid_outputs"] != report["cases"]:
        raise SystemExit("invalid/untranslated or metadata-exposing output")


if __name__ == "__main__":
    main()

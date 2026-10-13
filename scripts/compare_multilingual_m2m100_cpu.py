"""CPU-only multilingual ASR→M2M100 evaluation for three input/output languages.

Priority en/ja missionary speech→Korean captions; Korean speech→en/ja captions.
Research only: synthetic text references and short FLEURS samples are not
human-certified church interpretation.
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
from scripts.glossary_cpu_contract import (
    choose_candidate,
    load_glossary_snapshot,
    relevant_terms,
)
from scripts.benchmark_cpu_asr_candidates import (
    ResourceSamples,
    create_recognizer,
    decode_sample,
)
from scripts.evaluate_fleurs_asr import FLEURS_REVISION, write_pcm_wav

MODEL = "facebook/m2m100_418M"
LANGUAGES = ("ko", "en", "ja")
FLEURS_CONFIGS = {"ko": "ko_kr", "en": "en_us", "ja": "ja_jp"}
PAIRS = tuple((source, target) for source in LANGUAGES
              for target in LANGUAGES if target != source)


def targets_for_source(source: str) -> tuple[str, ...]:
    if source not in LANGUAGES:
        raise ValueError("source must be ko/en/ja")
    return tuple(target for target in LANGUAGES if target != source)


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lo = int(position)
    hi = min(lo + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo), 2)


def safe_caption(source: str, output: str) -> bool:
    caption = output.strip()
    if not caption or caption.casefold() == source.strip().casefold():
        return False
    if caption.startswith(("{", "[")):
        return False
    return not any(
        field in caption for field in ('"source_text"', '"session_title"', '"context"')
    )


def load_text_fixtures(path: Path) -> tuple[list[dict[str, Any]], str]:
    original = path.read_bytes()
    items = [json.loads(line) for line in original.decode("utf-8").splitlines()
             if line.strip()]
    ids = [item["id"] for item in items]
    if len(items) < 8 or len(ids) != len(set(ids)):
        raise ValueError("at least eight distinct bilingual/multilingual cases required")
    for row in items:
        if not set(LANGUAGES).issubset(row.keys()):
            raise ValueError("each fixture must include ko, en and ja source/reference")
        if not all(isinstance(row[key], str) and row[key].strip()
                   for key in LANGUAGES):
            raise ValueError("reference texts must be nonempty")
    return items, hashlib.sha256(original).hexdigest()


class M2M100CT2:
    """One multilingual MT model with explicit source and target language IDs."""

    def __init__(self, model_dir: Path, tokenizer_dir: Path, threads: int) -> None:
        import ctranslate2
        from transformers import M2M100Tokenizer

        self.tokenizer = M2M100Tokenizer.from_pretrained(
            str(tokenizer_dir), local_files_only=True
        )
        self.translator = ctranslate2.Translator(
            str(model_dir), device="cpu", compute_type="int8",
            inter_threads=1, intra_threads=threads,
        )
        self.target_tokens = {
            language: self.tokenizer.lang_code_to_token[language]
            for language in LANGUAGES
        }

    def translate_candidates(
        self, source_text: str, source: str, target: str,
        *, beam_size: int = 1,
    ) -> list[str]:
        """Return real model hypotheses for bounded terminology-aware reranking."""
        if source not in LANGUAGES or target not in LANGUAGES or source == target:
            raise ValueError("M2M100 direction must be two different supported languages")
        if not 1 <= beam_size <= 4:
            raise ValueError("experimental beam_size must be 1..4")
        self.tokenizer.src_lang = source
        tokens = self.tokenizer.convert_ids_to_tokens(
            self.tokenizer.encode(source_text)
        )
        target_prefix = self.target_tokens[target]
        result = self.translator.translate_batch(
            [tokens], target_prefix=[[target_prefix]], beam_size=beam_size,
            num_hypotheses=beam_size, max_decoding_length=256,
        )[0]
        decoded = []
        for output in result.hypotheses:
            if not output or output[0] != target_prefix:
                raise RuntimeError("M2M100 output violated the requested language prefix")
            ids = self.tokenizer.convert_tokens_to_ids(output[1:])
            decoded.append(self.tokenizer.decode(ids, skip_special_tokens=True).strip())
        if not decoded:
            raise RuntimeError("M2M100 returned no translation hypotheses")
        return decoded

    def translate(self, source_text: str, source: str, target: str) -> str:
        return self.translate_candidates(source_text, source, target)[0]


def translate_to_targets(
    model: M2M100CT2, text: str, source: str,
    *, glossary_entries: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Evaluate baseline and real glossary-assisted model candidates per target.

    The CLI always supplies a versioned snapshot. The optional default only
    keeps standalone smoke fixtures backward compatible.
    """
    translations: dict[str, dict[str, Any]] = {}
    for language in targets_for_source(source):
        mappings = relevant_terms(glossary_entries or [], text, source, language)
        start = time.perf_counter()
        baseline = model.translate(text, source, language)
        baseline_ms = round((time.perf_counter() - start) * 1000, 2)
        candidates: list[str] = []
        if mappings:
            # The glossary actually influences selection from M2M100 hypotheses.
            # Do not hallucinate a required term or silently substitute strings.
            candidates = model.translate_candidates(
                text, source, language, beam_size=4
            )
        evaluated = choose_candidate(
            baseline, candidates, text, mappings, language, safe=safe_caption
        )
        hypothesis = evaluated["hypothesis"]
        translations[language] = {
            "hypothesis": hypothesis,
            "valid": safe_caption(text, hypothesis),
            "baseline_valid": safe_caption(text, baseline),
            "baseline_latency_ms": baseline_ms,
            "latency_ms": round((time.perf_counter() - start) * 1000, 2),
            "glossary": evaluated,
        }
    return translations


def text_evaluation(
    model: M2M100CT2, fixtures: Path, glossary_snapshot: Path,
) -> dict[str, Any]:
    from sacrebleu.metrics import CHRF

    corpus, digest = load_text_fixtures(fixtures)
    glossary_entries, glossary_digest = load_glossary_snapshot(glossary_snapshot)
    scorer = CHRF(word_order=2)
    by_pair: dict[str, list[dict[str, Any]]] = {
        f"{source}->{target}": [] for source, target in PAIRS
    }
    with ResourceSamples() as resources:
        for case in corpus:
            for source in LANGUAGES:
                translated = translate_to_targets(
                    model, case[source], source, glossary_entries=glossary_entries
                )
                for target, value in translated.items():
                    reference = case[target]
                    by_pair[f"{source}->{target}"].append({
                        "id": case["id"], "source": case[source],
                        "reference": reference, **value,
                        "chrf_plus_plus": (
                            round(scorer.sentence_score(
                                value["hypothesis"], [reference]
                            ).score, 3) if value["valid"] else None
                        ),
                    })
    summary = {}
    for direction, rows in by_pair.items():
        scores = [row["chrf_plus_plus"] for row in rows
                  if row["chrf_plus_plus"] is not None]
        summary[direction] = {
            "valid": sum(row["valid"] for row in rows), "cases": len(rows),
            "mean_sentence_chrf_plus_plus": (
                round(statistics.fmean(scores), 3) if scores else None
            ),
            "mt_latency_p95_ms": percentile(
                [row["latency_ms"] for row in rows], 0.95
            ),
            "baseline_latency_p95_ms": percentile(
                [row["baseline_latency_ms"] for row in rows], 0.95
            ),
            "glossary_relevant": sum(row["glossary"]["relevant"] for row in rows),
            "glossary_baseline_matched": sum(
                row["glossary"]["baseline_matched"] for row in rows
            ),
            "glossary_assisted_matched": sum(
                row["glossary"]["matched"] for row in rows
            ),
            "glossary_candidate_changes": sum(
                row["glossary"]["applied"] for row in rows
            ),
        }
    return {
        "mode": "text", "model": MODEL,
        "fixture_sha256": digest, "fixture_review": "synthetic_unreviewed",
        "glossary_snapshot_sha256": glossary_digest,
        "glossary_method": "beam4_safe_lexical_rerank_not_guaranteed",
        "per_direction": summary, "samples": by_pair,
        "peak_resident_rss_mb": resources.summary()["rss_peak_mb"],
        "field_ready": False,
    }


def fleurs_parquet_uri(language: str) -> str:
    if language not in FLEURS_CONFIGS:
        raise ValueError("unsupported speech input language")
    return (
        f"hf://datasets/google/fleurs@{FLEURS_REVISION}/"
        f"{FLEURS_CONFIGS[language]}/test-00000-of-00001.parquet"
    )


def load_fleurs_samples(language: str, *, count: int = 3) -> list[dict[str, Any]]:
    from datasets import Audio, load_dataset

    dataset = load_dataset(
        "parquet", data_files={"test": fleurs_parquet_uri(language)},
        split="test", streaming=True,
    ).cast_column("audio", Audio(decode=False))
    samples: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="ltf-verify-fleurs-") as temp:
        for index, item in enumerate(dataset):
            if len(samples) >= count:
                break
            if index >= 48:
                break
            text = str(
                item.get("raw_transcription") or item.get("transcription") or ""
            ).strip()
            audio = item.get("audio")
            if not text or not isinstance(audio, dict):
                continue
            try:
                write_pcm_wav(audio, Path(temp) / f"probe-{index}.wav")
            except (ValueError, OSError, RuntimeError):
                continue
            samples.append({
                "id": item.get("id"), "text": text, "audio": audio,
            })
    if len(samples) != count:
        raise RuntimeError(
            f"{language}: expected {count} eligible FLEURS speech samples"
        )
    return samples


def audio_evaluation(
    model: M2M100CT2, asr_dir: Path, glossary_snapshot: Path, *, threads: int = 2,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    glossary_entries, glossary_digest = load_glossary_snapshot(glossary_snapshot)
    with ResourceSamples() as resources:
        recognizer = create_recognizer("qwen3-0.6b", asr_dir, threads)
        with tempfile.TemporaryDirectory(prefix="ltf-multi-asr-") as folder:
            for source in LANGUAGES:
                experiment_start = time.perf_counter()
                duration_sum = 0.0
                rows = []
                for index, item in enumerate(load_fleurs_samples(source)):
                    wav = Path(folder) / f"{source}-{index}.wav"
                    seconds, pcm_hash = write_pcm_wav(item["audio"], wav)
                    duration_sum += seconds
                    asr = decode_sample(
                        "qwen3-0.6b", recognizer, wav,
                        threads=threads, pace="realtime",
                    )
                    translated = translate_to_targets(
                        model, asr["hypothesis"], source,
                        glossary_entries=glossary_entries,
                    )
                    rows.append({
                        "id": item["id"], "pcm_sha256": pcm_hash,
                        "duration_seconds": round(seconds, 3),
                        "reference_source": item["text"],
                        "source_hypothesis": asr["hypothesis"],
                        "source_metrics": quality_metrics(
                            item["text"], asr["hypothesis"]
                        ),
                        "asr_inference_seconds": asr["inference_seconds"],
                        "streaming_kind": asr["streaming_kind"],
                        "translations": translated,
                        "estimated_both_targets_lag_ms": round(
                            max(
                                0.0,
                                (time.perf_counter() - experiment_start)
                                - duration_sum,
                            ) * 1000, 2
                        ),
                    })
                wall = time.perf_counter() - experiment_start
                inference = sum(row["asr_inference_seconds"] for row in rows)
                inference += sum(
                    row["translations"][target]["latency_ms"] / 1000
                    for row in rows for target in targets_for_source(source)
                )
                results[source] = {
                    "audio_seconds": round(duration_sum, 3),
                    "wall_seconds": round(wall, 3),
                    "inference_workload_rtf": round(
                        inference / duration_sum, 4
                    ),
                    "paced_sequential_replay_rtf": round(
                        wall / duration_sum, 4
                    ),
                    "estimated_dual_caption_lag_p95_ms": percentile(
                        [row["estimated_both_targets_lag_ms"] for row in rows],
                        0.95,
                    ),
                    "glossary_relevant": {
                        target: sum(
                            row["translations"][target]["glossary"]["relevant"]
                            for row in rows
                        ) for target in targets_for_source(source)
                    },
                    "glossary_assisted_matches": {
                        target: sum(
                            row["translations"][target]["glossary"]["matched"]
                            for row in rows
                        ) for target in targets_for_source(source)
                    },
                    "translated_coverage": {
                        target: sum(
                            row["translations"][target]["valid"]
                            for row in rows
                        ) for target in targets_for_source(source)
                    },
                    "samples": rows,
                }
        del recognizer
    return {
        "mode": "real_audio", "mt_model": MODEL,
        "asr_model": "Qwen3-ASR-0.6B INT8",
        "dataset": "google/fleurs", "revision": FLEURS_REVISION,
        "source_languages": LANGUAGES,
        "glossary_snapshot_sha256": glossary_digest,
        "glossary_method": "beam4_safe_lexical_rerank_not_guaranteed",
        "per_source": results,
        "peak_co_resident_rss_mb": resources.summary()["rss_peak_mb"],
        "speech_translation_reference": False,
        "speaker_language_detection_verified": False,
        "live_runtime_qualification": False, "field_ready": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("text", "real-audio"), required=True)
    parser.add_argument("--ct2-dir", type=Path, required=True)
    parser.add_argument("--tokenizer-dir", type=Path, required=True)
    parser.add_argument("--asr-dir", type=Path)
    parser.add_argument("--glossary-snapshot", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--fixtures", type=Path,
        default=Path("benchmarks/lecture_multilingual_ko_en_ja.v1.jsonl"),
    )
    args = parser.parse_args()
    if not 1 <= args.threads <= 4:
        parser.error("threads must be from 1 to 4")
    if args.mode == "real-audio" and args.asr_dir is None:
        parser.error("real-audio requires --asr-dir")
    model = M2M100CT2(
        model_dir=args.ct2_dir, tokenizer_dir=args.tokenizer_dir,
        threads=args.threads,
    )
    report = (
        text_evaluation(model, args.fixtures, args.glossary_snapshot)
        if args.mode == "text"
        else audio_evaluation(
            model, args.asr_dir, args.glossary_snapshot, threads=args.threads
        )
    )
    report.update({"model_revision_sha256": "see model-revision.json",
                   "compute_type": "int8"})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.mode == "text":
        valid = all(
            row["valid"] == row["cases"] and row["glossary_relevant"] > 0
            for row in report["per_direction"].values()
        )
    else:
        valid = all(
            count == 3 for record in report["per_source"].values()
            for count in record["translated_coverage"].values()
        )
    print(json.dumps(
        {key: value for key, value in report.items()
         if key not in {"samples", "per_source"}},
        ensure_ascii=False,
    ))
    if not valid:
        raise SystemExit("unsafe/missing output for one or more language pairs")


if __name__ == "__main__":
    main()

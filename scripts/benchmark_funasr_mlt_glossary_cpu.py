"""Fun-ASR-MLT-Nano-2512 native hotword ON/OFF CPU speech benchmark.

Research only. Offline utterance inference with official FunASR adapter, not
streaming. FLEURS short samples generally do not contain church terminology,
so success is NOT evidence of improved domain recognition or field readiness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any

from langtextflow.benchmark import quality_metrics
from scripts.benchmark_cpu_asr_candidates import ResourceSamples
from scripts.compare_multilingual_m2m100_cpu import (
    LANGUAGES,
    load_fleurs_samples,
    percentile,
)
from scripts.evaluate_fleurs_asr import FLEURS_REVISION, write_pcm_wav
from scripts.glossary_cpu_contract import load_glossary_snapshot

MODEL_ID = "FunAudioLLM/Fun-ASR-MLT-Nano-2512"
SOURCE_TO_FUNASR = {"ko": "韩文", "en": "英文", "ja": "日文"}
MAX_ASR_HINTS = 24


def asr_hotwords(
    entries: list[dict[str, Any]], source_language: str,
    *, preset: str = "church", limit: int = MAX_ASR_HINTS,
) -> list[str]:
    """Use actual session source glossary, NOT ground-truth text as an oracle."""
    if source_language not in LANGUAGES or not 0 <= limit <= MAX_ASR_HINTS:
        raise ValueError("unsupported source language or ASR hotword limit")
    if limit == 0:
        return []
    seen: set[str] = set()
    terms: list[str] = []
    rows = sorted(
        entries,
        key=lambda r: (-float(r.get("boost", 1.0)), -len(r["term"])),
    )
    for row in rows:
        if not row.get("enabled", True) or row["source_language"] != source_language:
            continue
        if row.get("presets") and preset not in row["presets"]:
            continue
        for value in (row["term"], *row.get("aliases", [])):
            normalized = value.casefold().strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            terms.append(value)
            if len(terms) >= limit:
                return terms
    return terms


def observed_hotwords(text: str, hotwords: list[str]) -> list[str]:
    """Lexical observation only; token boundaries protect English suffixes.

    Do not use reference text to choose hotword prompts. The reference is
    inspected only AFTER decoding to quantify off-target vocabulary insertions.
    """
    normalized_text = unicodedata.normalize("NFKC", text).casefold()
    present: list[str] = []
    for term in hotwords:
        needle = unicodedata.normalize("NFKC", term).casefold().strip()
        if not needle:
            continue
        if re.search(r"[a-z0-9]", needle):
            found = re.search(
                r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])",
                normalized_text,
            ) is not None
        else:
            found = needle in normalized_text
        if found:
            present.append(term)
    return present


def snapshot_hashes(folder: Path) -> dict[str, str]:
    """Small provenance files; never upload copyrighted model weights."""
    hashes: dict[str, str] = {}
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(folder).as_posix()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
        hashes[relative] = digest.hexdigest()
    return hashes


def decode(
    model: Any, wav: Path, source_language: str, *,
    hotwords: list[str],
) -> tuple[str, float]:
    if source_language not in SOURCE_TO_FUNASR:
        raise ValueError("unsupported language")
    start = time.perf_counter()
    result = model.generate(
        input=[str(wav)],
        cache={},
        batch_size=1,
        hotwords=hotwords,
        language=SOURCE_TO_FUNASR[source_language],
        itn=True,
        llm_kwargs={"do_sample": False},
    )
    elapsed = time.perf_counter() - start
    if not isinstance(result, list) or not result or not isinstance(result[0], dict):
        raise RuntimeError("FunASR must return a nonempty result list")
    hypothesis = result[0].get("text")
    if not isinstance(hypothesis, str):
        raise RuntimeError("FunASR result is missing text")
    return hypothesis.strip(), round(elapsed, 5)


def evaluate_samples(
    model: Any, samples: list[dict[str, Any]], source_language: str,
    glossary_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Paired samples: identical audio/hash with and without source glossary."""
    if source_language not in LANGUAGES:
        raise ValueError("invalid source language")
    terms = asr_hotwords(glossary_entries, source_language)
    if not terms:
        raise ValueError("glossary must provide spoken-language hotwords")
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="ltf-funasr-wav-") as tmp:
        for index, sample in enumerate(samples):
            wav = Path(tmp) / f"{source_language}-{index}.wav"
            duration, audio_sha = write_pcm_wav(sample["audio"], wav)
            baseline, off_seconds = decode(
                model, wav, source_language, hotwords=[]
            )
            assisted, on_seconds = decode(
                model, wav, source_language, hotwords=terms
            )
            source_text = sample["text"]
            reference_terms = set(observed_hotwords(source_text, terms))
            off_terms = set(observed_hotwords(baseline, terms))
            on_terms = set(observed_hotwords(assisted, terms))
            rows.append({
                "id": sample["id"], "pcm_sha256": audio_sha,
                "audio_seconds": round(duration, 3),
                "reference_source": source_text,
                "baseline_hypothesis": baseline,
                "assisted_hypothesis": assisted,
                "baseline_metrics": quality_metrics(source_text, baseline),
                "assisted_metrics": quality_metrics(source_text, assisted),
                "baseline_inference_seconds": off_seconds,
                "assisted_inference_seconds": on_seconds,
                "source_glossary_terms_used": len(terms),
                "reference_hotwords_present": sorted(reference_terms),
                "baseline_off_target_hotwords": sorted(off_terms - reference_terms),
                "assisted_off_target_hotwords": sorted(on_terms - reference_terms),
                "new_off_target_hotwords": sorted(
                    (on_terms - off_terms) - reference_terms
                ),
                "hypothesis_changed": baseline != assisted,
            })
    if not rows:
        raise ValueError("at least one real speech clip required")
    durations = sum(r["audio_seconds"] for r in rows)
    total_base = sum(r["baseline_inference_seconds"] for r in rows)
    total_assist = sum(r["assisted_inference_seconds"] for r in rows)
    triggered = sum(bool(r["reference_hotwords_present"]) for r in rows)
    return {
        "source_language": source_language,
        "clips": len(rows),
        "total_audio_seconds": round(durations, 3),
        "glossary_hotwords_sent": terms,
        "glossary_context_source": "source_language_scoped_operator_export",
        "reference_clips_with_glossary_terms": triggered,
        "clips_changed_by_hotwords": sum(r["hypothesis_changed"] for r in rows),
        "baseline_off_target_term_count": sum(
            len(r["baseline_off_target_hotwords"]) for r in rows
        ),
        "assisted_off_target_term_count": sum(
            len(r["assisted_off_target_hotwords"]) for r in rows
        ),
        "new_off_target_term_count": sum(
            len(r["new_off_target_hotwords"]) for r in rows
        ),
        "glossary_field_qualification": False,
        "glossary_test_status": (
            "lexical_observation_only" if triggered else "not_exercised"
        ),
        "baseline_mean_cer": round(statistics.fmean(
            r["baseline_metrics"]["cer"] for r in rows
        ), 4),
        "assisted_mean_cer": round(statistics.fmean(
            r["assisted_metrics"]["cer"] for r in rows
        ), 4),
        "baseline_p95_ms": percentile(
            [r["baseline_inference_seconds"] * 1000 for r in rows], 0.95
        ),
        "assisted_p95_ms": percentile(
            [r["assisted_inference_seconds"] * 1000 for r in rows], 0.95
        ),
        "baseline_inference_rtf": round(total_base / durations, 4),
        "assisted_inference_rtf": round(total_assist / durations, 4),
        "speech_transcripts": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--remote-code", type=Path, required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--remote-code-revision", required=True)
    parser.add_argument("--glossary-snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    if args.threads not in (1, 2, 4):
        parser.error("threads must be 1, 2 or 4")
    if not args.remote_code.is_file():
        parser.error("pinned FunASR code path does not exist")
    entries, snapshot_hash = load_glossary_snapshot(args.glossary_snapshot)
    import torch
    from funasr import AutoModel

    torch.set_num_threads(args.threads)
    start = time.perf_counter()
    with ResourceSamples() as resources:
        model = AutoModel(
            model=str(args.model_dir),
            trust_remote_code=True,
            remote_code=str(args.remote_code.resolve()),
            device="cpu",
            hub="hf",
        )
        load_ms = round((time.perf_counter() - start) * 1000, 2)
        measured = {
            source: evaluate_samples(
                model, load_fleurs_samples(source), source, entries,
            )
            for source in ("en", "ja", "ko")
        }
        resource_report = resources.summary()
    final = {
        "model_id": MODEL_ID,
        "model_revision": args.model_revision,
        "remote_code_revision": args.remote_code_revision,
        "model_sha256": snapshot_hashes(args.model_dir),
        "remote_code_sha256": hashlib.sha256(args.remote_code.read_bytes()).hexdigest(),
        "glossary_snapshot_sha256": snapshot_hash,
        "dataset": "google/fleurs",
        "dataset_revision": FLEURS_REVISION,
        "hardware_profile": "GitHub Actions CPU; record hardware.txt",
        "threads": args.threads,
        "model_load_ms": load_ms,
        "runtime": "offline_utterance_funasr_AutoModel_not_native_streaming",
        "source_hotwords_passed_to_model": True,
        "semantic_glossary_certified": False,
        "field_ready": False,
        "per_language": measured,
        "resource_samples": resource_report,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "model_id": MODEL_ID, "field_ready": False,
        "languages": {
            source: {
                k: v for k, v in result.items() if k != "speech_transcripts"
            }
            for source, result in measured.items()
        },
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

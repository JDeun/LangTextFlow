"""Killable, local-only CPU decoder. JSON-lines stdout is a bounded private IPC channel."""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import sys
import threading
import time
import wave
from importlib.metadata import version
from pathlib import Path

from .parent_pipe import read_parent_pipe
from .post_event_transcripts import DECODE_PARAMETERS, MAX_SEGMENTS, validate_segments
from .whisper_cache import resolve_whisper_model


def _emit(value: dict) -> None:
    # Escapes preserve Unicode through Windows' locale-encoded stdout pipes.
    print(json.dumps(value, ensure_ascii=True), flush=True)


def _sha(stream) -> str:
    stream.seek(0)
    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    stream.seek(0)
    return digest.hexdigest()


def window_interval(start: float, end: float, duration: float) -> tuple[float, float, bool]:
    if (not math.isfinite(start) or not math.isfinite(end)
            or start < 0 or end < start or start > duration or end > duration + 0.5):
        raise ValueError("decoder timestamp outside its audio window")
    # Whisper may include a small padded tail, never a timestamp from another window.
    return start, min(end, duration), end > duration


def decode(path: Path, model_name: str, language: str, expected_sha: str,
           cache_dir: str) -> None:
    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio

    started = time.monotonic()
    # Never download weights in an operator job; model setup is a separate explicit action.
    model_path = resolve_whisper_model(model_name, cache_dir)
    with (Path(model_path) / "model.bin").open("rb") as weights:
        model_sha = _sha(weights)
    model = WhisperModel(
        str(model_path), device="cpu", compute_type="int8", cpu_threads=2, num_workers=1,
        local_files_only=True,
    )
    if path.is_symlink():
        raise ValueError("managed recordings cannot be symbolic links")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as audio:
        if _sha(audio) != expected_sha:
            raise ValueError("recording hash changed")
        with wave.open(audio, "rb") as wav:
            rate, channels = wav.getframerate(), wav.getnchannels()
            frames = wav.getnframes()
            if (wav.getsampwidth() != 2 or wav.getcomptype() != "NONE"
                    or channels not in (1, 2) or not 8000 <= rate <= 48000
                    or frames <= 0 or frames / rate > 4 * 3600):
                raise ValueError("invalid managed PCM WAV")
            duration = frames / rate
            segments: list[dict] = []
            quality = {"windows": 0, "empty_windows": 0, "low_confidence_segments": 0,
                       "clipped_segments": 0}
            offset = 0
            while offset < frames:
                count = min(rate * DECODE_PARAMETERS["window_seconds"], frames - offset)
                raw = wav.readframes(count)
                if len(raw) != count * channels * 2:
                    raise ValueError("incomplete recording")
                buffer = io.BytesIO()
                with wave.open(buffer, "wb") as window:
                    window.setnchannels(channels)
                    window.setsampwidth(2)
                    window.setframerate(rate)
                    window.writeframes(raw)
                buffer.seek(0)
                decoded, _ = model.transcribe(
                    decode_audio(buffer), language=language, beam_size=5,
                    condition_on_previous_text=False, vad_filter=False,
                )
                before = len(segments)
                for item in decoded:
                    if not item.text.strip():
                        continue
                    start, end, clipped = window_interval(item.start, item.end, count / rate)
                    quality["clipped_segments"] += int(clipped)
                    segment = {
                        "segment_id": f"offline-{len(segments) + 1:06d}",
                        "start_ms": int((offset / rate + start) * 1000),
                        "end_ms": min(int(duration * 1000), int((offset / rate + end) * 1000)),
                        "text": item.text.strip(),
                    }
                    segments.append(segment)
                    if len(segments) > MAX_SEGMENTS:
                        raise ValueError("too many transcript segments")
                    if item.avg_logprob < -1 or item.no_speech_prob > 0.6:
                        quality["low_confidence_segments"] += 1
                    # Bound incremental output before it reaches IPC or persistent storage.
                    validate_segments(segments, duration)
                    _emit({"segment": segment})
                quality["windows"] += 1
                quality["empty_windows"] += int(before == len(segments))
                offset += count
                _emit({"progress": offset / frames})
        if _sha(audio) != expected_sha:
            raise ValueError("recording changed during transcription")
    elapsed = time.monotonic() - started
    _emit({"complete": {
        "model_sha256": model_sha, "faster_whisper_version": version("faster-whisper"),
        "ctranslate2_version": version("ctranslate2"), "quality_counters": quality,
        "processing_seconds": round(elapsed, 3), "real_time_factor": round(elapsed / duration, 4),
    }})


def main(args: list[str] | None = None) -> None:
    args = sys.argv[1:] if args is None else args
    if (len(args) != 5 or args[1] not in {"tiny", "base", "small"}
            or args[2] not in {"ko", "en", "ja"}):
        raise SystemExit(2)

    parent_pipe = sys.stdin.fileno()

    def parent_watchdog():
        # The parent holds stdin open; a crash/restart closes it on every supported OS.
        # Raw reads avoid holding BufferedReader's lock during interpreter shutdown.
        try:
            read_parent_pipe(1, descriptor=parent_pipe)
        finally:
            os._exit(1)

    threading.Thread(target=parent_watchdog, daemon=True).start()
    try:
        decode(Path(args[0]), args[1], args[2], args[3], args[4])
    except Exception:
        # Provider/runtime exceptions may contain local paths; only the parent emits UI errors.
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

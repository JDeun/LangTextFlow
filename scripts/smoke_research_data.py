"""Check the optional benchmark data stack with synthetic local audio only."""
from __future__ import annotations

import io
import os
import tempfile
import wave
from importlib.metadata import version
from pathlib import Path


def main() -> None:
    # This compatibility check must never download a dataset or contact the Hub.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    import fsspec
    import pyarrow as pa
    import pyarrow.parquet as pq
    import soundfile as sf
    from datasets import Audio, load_dataset

    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 1600)
    encoded = audio.getvalue()
    with tempfile.TemporaryDirectory(prefix="ltf-data-smoke-") as temporary:
        parquet = Path(temporary) / "synthetic.parquet"
        pq.write_table(pa.Table.from_pylist([{
            "audio": {"bytes": encoded, "path": None},
            "raw_transcription": "synthetic compatibility check",
        }]), parquet)
        dataset = load_dataset(
            "parquet", data_files={"test": str(parquet)}, split="test", streaming=True,
        ).cast_column("audio", Audio(decode=False))
        row = next(iter(dataset))
        assert row["audio"]["bytes"] == encoded
        assert row["raw_transcription"] == "synthetic compatibility check"
        samples, sample_rate = sf.read(io.BytesIO(row["audio"]["bytes"]))
        assert sample_rate == 16000 and len(samples) == 1600
        with fsspec.open(parquet, "rb") as source:
            assert source.read(4) == b"PAR1"
    print("Research data import/streaming smoke passed:", {
        name: version(name) for name in ("datasets", "fsspec", "huggingface-hub", "soundfile")
    })


if __name__ == "__main__":
    main()

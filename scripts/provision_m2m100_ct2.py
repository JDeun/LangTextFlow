"""Explicit online provisioning for the opt-in offline M2M100 CPU adapter.

Run manually on a networked machine. The application itself never downloads models.
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

MODEL_ID = "facebook/m2m100_418M"


def provision(*, model_dir: Path, tokenizer_dir: Path, revision: str) -> None:
    """Stage model/tokenizer and install only after conversion succeeds."""
    if not revision or revision.lower() in {"main", "master", "latest"}:
        raise ValueError("Provide an immutable Hugging Face commit revision")
    if model_dir.exists() or tokenizer_dir.exists():
        raise FileExistsError("Target cache already exists; refusing to overwrite")
    if model_dir.resolve() == tokenizer_dir.resolve():
        raise ValueError("Model and tokenizer directories must differ")
    try:
        import ctranslate2
        from huggingface_hub import snapshot_download
        from transformers import M2M100Tokenizer
    except ImportError as exc:
        raise RuntimeError(
            "Install provisioning dependencies: huggingface_hub, "
            "ctranslate2, transformers and sentencepiece"
        ) from exc

    model_dir.parent.mkdir(parents=True, exist_ok=True)
    tokenizer_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ltf-m2m100-") as staging:
        root = Path(staging)
        source = Path(snapshot_download(repo_id=MODEL_ID, revision=revision))
        tokenizer = M2M100Tokenizer.from_pretrained(str(source), local_files_only=True)
        for language in ("ko", "en", "ja"):
            if language not in tokenizer.lang_code_to_token:
                raise ValueError(f"Missing language token: {language}")
        tokenizer.save_pretrained(str(root / "tokenizer"))
        converter = ctranslate2.converters.TransformersConverter(str(source))
        converter.convert(str(root / "model"), quantization="int8")
        if not all((root / "model" / name).is_file()
                   for name in ("model.bin", "config.json")):
            raise RuntimeError("CTranslate2 conversion did not produce required files")
        (root / "model" / "provisioning.json").write_text(
            json.dumps({"model_id": MODEL_ID, "revision": revision,
                        "quantization": "int8"}, indent=2) + "\n",
            encoding="utf-8",
        )
        shutil.move(str(root / "tokenizer"), str(tokenizer_dir))
        try:
            shutil.move(str(root / "model"), str(model_dir))
        except BaseException:
            shutil.rmtree(tokenizer_dir)
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True,
                        help="Immutable upstream model commit SHA")
    parser.add_argument("--model-dir", type=Path,
                        default=Path("data/model-cache/m2m100-ct2"))
    parser.add_argument("--tokenizer-dir", type=Path,
                        default=Path("data/model-cache/m2m100-tokenizer"))
    parser.add_argument("--allow-network", action="store_true",
                        help="Explicitly authorize a Hugging Face download")
    args = parser.parse_args()
    if not args.allow_network:
        parser.error("Explicit --allow-network is required; no implicit downloads")
    provision(model_dir=args.model_dir, tokenizer_dir=args.tokenizer_dir,
              revision=args.revision)
    print("Provisioned M2M100 CT2 INT8 model and tokenizer for offline runtime")


if __name__ == "__main__":
    main()

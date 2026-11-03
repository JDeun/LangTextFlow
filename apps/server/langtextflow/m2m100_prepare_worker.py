"""Explicit, fixed-hash download and native validation; never publishes a cache."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from .storage_guard import ensure_storage_capacity

MODEL_REVISION = "55c2e61bbf05dfb8d7abccdc3fae6fc8512fd636"
TRANSPORT_REVISION = "18e406c615ef2991fa74d53734bf66b0a6b10cb4"
MODEL_ID = "facebook/m2m100_418M"
PREPARE_TIMEOUT_SECONDS = 1200.0


@dataclass(frozen=True)
class ModelFile:
    relative: str
    repository: str
    revision: str
    sha256: str
    maximum_bytes: int

    @property
    def url(self) -> str:
        return (f"https://huggingface.co/{self.repository}/resolve/{self.revision}/"
                f"{Path(self.relative).name}")


# Trust anchors: first-party runtime 41628932696 and official-source receipt
# 41637393208 (docs/local-m2m100-provisioning.md); transport metadata is not evidence.
FILES = (
    ModelFile("model/model.bin", "gn64/M2M100_418M_CTranslate2", TRANSPORT_REVISION,
              "a1826980fc5c037e69c7ac94fcb56c03001a66f380eb71863cc0a3879e71421b", 490667752),
    ModelFile("model/config.json", "gn64/M2M100_418M_CTranslate2", TRANSPORT_REVISION,
              "8f6496adfc930cbfecbe8281112197705c488fab47d34b4829b06d7f478909af", 4096),
    ModelFile("model/shared_vocabulary.json", "gn64/M2M100_418M_CTranslate2", TRANSPORT_REVISION,
              "7eb5d0ff184c6095c7c10f9911c0aea492250abd12854f9c3d787c64b1c6397e", 4000000),
    ModelFile("tokenizer/tokenizer_config.json", MODEL_ID, MODEL_REVISION,
              "a53e6aa83da0b82565ed90c3849056307a9453843322ac5b8439ec4b9497fe48", 4096),
    ModelFile("tokenizer/special_tokens_map.json", MODEL_ID, MODEL_REVISION,
              "c1a4f86c3874d279ae1b2a05162858db5dd6c61665d84223ed886cbcff08fda6", 4096),
    ModelFile("tokenizer/sentencepiece.bpe.model", MODEL_ID, MODEL_REVISION,
              "d8f7c76ed2a5e0822be39f0a4f95a55eb19c78f4593ce609e2edbc2aea4d380a", 2423393),
    ModelFile("tokenizer/vocab.json", MODEL_ID, MODEL_REVISION,
              "b6e77e474aeea8f441363aca7614317c06381f3eacfe10fb9856d5081d1074cc", 4000000),
)
DOWNLOAD_BYTES = sum(item.maximum_bytes for item in FILES)


def _phase(directory: Path, stage: str, filename: str | None = None) -> None:
    temporary = directory / ".phase.json"
    temporary.write_text(json.dumps({"phase": stage, "file": filename, "pid": os.getpid()}))
    os.replace(temporary, directory / "phase.json")


def preparation_progress(directory: Path) -> dict:
    """Return only fixed public enums/file keys and a bounded byte count, never raw JSON."""
    try:
        path = directory / "phase.json"
        if path.is_symlink() or path.stat().st_size > 512:
            return {}
        value = json.loads(path.read_bytes())
        if (not isinstance(value, dict) or not isinstance(value.get("phase"), str)
                or value["phase"] not in {"checking_cache", "downloading", "native_validation"}):
            return {}
        result = {"phase": value["phase"]}
        item = next((item for item in FILES if item.relative == value.get("file")), None)
        if item is not None:
            result["file"] = item.relative
            file_path = directory / item.relative
            if file_path.is_file() and not file_path.is_symlink():
                result["file_bytes"] = min(item.maximum_bytes, file_path.stat().st_size)
        return result
    except (OSError, ValueError):
        return {}


def _allowed_url(url: str) -> bool:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    return (parsed.scheme == "https" and parsed.port in (None, 443)
            and parsed.username is None and parsed.password is None
            and not parsed.fragment
            and (host == "huggingface.co" or host.endswith(".huggingface.co")
                 or host.endswith(".hf.co")))


def _write_verified(blocks, output: Path, item: ModelFile, reserve: int) -> None:
    digest = hashlib.sha256()
    count = 0
    with output.open("xb") as target:
        for block in blocks:
            count += len(block)
            if count > item.maximum_bytes:
                raise ValueError("model file exceeds its fixed size limit")
            ensure_storage_capacity(output.parent, required_bytes=len(block),
                                    reserve_bytes=reserve, operation="prepare local translation")
            target.write(block)
            digest.update(block)
        target.flush()
        os.fsync(target.fileno())
    if not count or digest.hexdigest() != item.sha256:
        raise ValueError("model file checksum mismatch")


def download_file(client: httpx.Client, item: ModelFile, output: Path, reserve: int) -> None:
    url = item.url
    for _ in range(6):
        if not _allowed_url(url):
            raise ValueError("model download destination is not allowed")
        # No auth/proxy environment; signed redirect URLs are never logged or returned.
        with client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers.get("location", ""))
                continue
            response.raise_for_status()
            length = response.headers.get("content-length")
            if length is not None and int(length) > item.maximum_bytes:
                raise ValueError("model file exceeds its fixed size limit")
            if response.headers.get("content-encoding", "identity") != "identity":
                raise ValueError("model download must use unencoded bytes")
            _write_verified(response.iter_raw(1024 * 1024), output, item, reserve)
            return
    raise ValueError("model download redirect limit exceeded")


def prepare(directory: Path, reserve: int, existing: tuple[Path, Path]) -> None:
    if (directory.is_symlink() or not directory.is_dir()
            or any(item.name != "owner.json" for item in directory.iterdir())):
        raise ValueError("preparation requires an empty owned directory")
    ensure_storage_capacity(directory, required_bytes=DOWNLOAD_BYTES,
                            reserve_bytes=reserve, operation="prepare local translation")
    (directory / "model").mkdir()
    (directory / "tokenizer").mkdir()
    with httpx.Client(timeout=httpx.Timeout(30, connect=10), trust_env=False,
                      follow_redirects=False) as client:
        for item in FILES:
            _phase(directory, "checking_cache", item.relative)
            output = directory / item.relative
            source = existing[0 if item.relative.startswith("model/") else 1] / output.name
            try:
                if (source.is_symlink() or source.parent.is_symlink() or not source.is_file()
                        or source.stat().st_size > item.maximum_bytes):
                    raise ValueError("cache file must be regular")
                with source.open("rb") as cached:
                    _write_verified(iter(lambda: cached.read(1024 * 1024), b""),
                                    output, item, reserve)
            except (OSError, ValueError):
                output.unlink(missing_ok=True)
                _phase(directory, "downloading", item.relative)
                download_file(client, item, output, reserve)
    # Native/tokenizer initialization is offline, inside this killable child only.
    from .translation.m2m100_ct2 import M2M100CT2Translator

    _phase(directory, "native_validation")
    M2M100CT2Translator(model_dir=str(directory / "model"),
                        tokenizer_dir=str(directory / "tokenizer"), cpu_threads=2)._prepare_sync()


def main(args: list[str] | None = None) -> None:
    args = sys.argv[1:] if args is None else args
    if args == ["--check-runtime"]:
        # Frozen packaging smoke imports the real tokenizer/native runtime without any weights.
        with open(os.devnull, "w") as silent:
            os.dup2(silent.fileno(), sys.stdout.fileno())
            os.dup2(silent.fileno(), sys.stderr.fileno())
        import ctranslate2
        import sentencepiece
        from transformers import M2M100Tokenizer

        from .translation.m2m100_ct2 import M2M100CT2Translator

        assert ctranslate2 and sentencepiece and M2M100Tokenizer and M2M100CT2Translator
        assert not any(name == "torch" or name.startswith("torch.") for name in sys.modules)
        return
    if len(args) != 4 or not args[1].isdigit():
        raise SystemExit(2)
    # Redirect native output before imports/validation; the parent sees one bounded ACK.
    output = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    with open(os.devnull, "w") as silent:
        os.dup2(silent.fileno(), sys.stdout.fileno())
        os.dup2(silent.fileno(), sys.stderr.fileno())

    def parent_closed() -> None:
        sys.stdin.buffer.read(1)
        os._exit(0)  # EOF stops downloads/native validation even if the parent crashes.

    threading.Thread(target=parent_closed, daemon=True).start()
    try:
        prepare(Path(args[0]), int(args[1]), (Path(args[2]), Path(args[3])))
        print('{"ready": true}', file=output, flush=True)
        threading.Event().wait()  # Parent retains the lease until publication and child reap.
    except Exception:
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

"""Explicit, fixed-hash download and native validation; never publishes a cache."""
from __future__ import annotations

import asyncio
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


def _probe_runtime(order: str, watch: str) -> None:
    """Private, model-free frozen import control; never changes production loading."""
    output = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    with open(os.devnull, "w") as silent:
        os.dup2(silent.fileno(), 1)
        os.dup2(silent.fileno(), 2)
    if watch != "none":
        descriptor = os.dup(0) if watch == "duplicate" else 0
        if watch == "winapi":
            import ctypes
            import msvcrt
            from ctypes import wintypes

            handle = wintypes.HANDLE(msvcrt.get_osfhandle(0))
            read_file = ctypes.WinDLL("kernel32", use_last_error=True).ReadFile
            read_file.argtypes = (wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                                  ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID)
            read_file.restype = wintypes.BOOL
            data = ctypes.create_string_buffer(1)
            count = wintypes.DWORD()
        entered = threading.Event()

        def closed() -> None:
            entered.set()
            if watch == "winapi":
                success = read_file(handle, data, 1, ctypes.byref(count), None)
                os._exit(0 if success or ctypes.get_last_error() == 109 else 1)
            os.read(descriptor, 1)
            os._exit(0)

        threading.Thread(target=closed, daemon=True).start()
        entered.wait()
        # Match actual preparation, where downloading precedes the native imports.
        import time

        time.sleep(.05)

    def mark(phase: str) -> None:
        print(json.dumps({"phase": phase, "pid": os.getpid()}), file=output, flush=True)

    try:
        mark("native_import_ct2")
        import ctranslate2

        if order == "preload-spm":
            mark("native_import_sentencepiece")
            import sentencepiece

            assert sentencepiece
        mark("native_import_tokenizer")
        from transformers import M2M100Tokenizer

        assert ctranslate2 and M2M100Tokenizer
        print(json.dumps({"ready": True, "pid": os.getpid()}), file=output, flush=True)
        if watch == "none":
            os.read(0, 1)
            os._exit(0)
        threading.Event().wait()
    except Exception:
        os._exit(1)


async def _probe_runtime_matrix(output: Path) -> None:
    from .translation.m2m100_ct2 import _M2M100Worker

    results = []
    prefix = ([sys.executable, "--m2m100-prepare-worker"] if getattr(sys, "frozen", False)
              else [sys.executable, "-m", "langtextflow.m2m100_prepare_worker"])
    controls = [(order, watch) for order in ("direct", "preload-spm")
                for watch in ("none", "shared", "duplicate")]
    if os.name == "nt":
        controls.append(("direct", "winapi"))
    for order, watch in controls:
        result = {"order": order, "watch": watch, "ready": False, "timed_out": False}
        worker = _M2M100Worker([*prefix, "--probe-runtime", order, watch])
        try:
            async with asyncio.timeout(15):
                worker.process = await asyncio.shield(worker._launch)
                result["spawn_pid"] = worker.process.pid
                while True:
                    message = await worker._read()
                    pid = message.get("pid")
                    if type(pid) is not int or pid <= 0:
                        raise ValueError("invalid probe identity")
                    result["reported_pid"] = pid
                    if message.get("ready") is True:
                        result["ready"] = True
                        break
                    phase = message.get("phase")
                    if phase not in {"native_import_ct2", "native_import_sentencepiece",
                                     "native_import_tokenizer"}:
                        raise ValueError("invalid probe phase")
                    result["last_phase"] = phase
        except TimeoutError:
            result["timed_out"] = True
        except Exception:
            result["error_category"] = "import_or_protocol_failed"
        finally:
            if worker.process is not None:
                worker.process.stdin.close()
                try:
                    await asyncio.wait_for(worker.process.wait(), 3)
                    result["eof_exit_code"] = worker.process.returncode
                except TimeoutError:
                    result["eof_exit_code"] = None
            await worker.close()
            result["reaped"] = worker.released.is_set()
        results.append(result)
    output.write_text(json.dumps({"weights_downloaded": False, "cases": results}, indent=2))


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
                or value["phase"] not in {
                    "checking_cache", "downloading", "native_validation",
                    "native_load_started", "native_load_returned",
                    "native_unload_started", "native_unload_returned",
                    "native_import_ct2", "native_import_tokenizer", "native_tokenizer_load",
                    "native_engine_load", "native_language_check",
                }):
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

    _phase(directory, "native_load_started")
    validated = M2M100CT2Translator(model_dir=str(directory / "model"),
                                    tokenizer_dir=str(directory / "tokenizer"),
                                    cpu_threads=2)._prepare_sync(
        report_phase=lambda phase: _phase(directory, phase),
    )
    _phase(directory, "native_load_returned")
    _phase(directory, "native_unload_started")
    del validated
    _phase(directory, "native_unload_returned")


def main(args: list[str] | None = None) -> None:
    args = sys.argv[1:] if args is None else args
    if len(args) == 2 and args[0] == "--probe-matrix":
        asyncio.run(_probe_runtime_matrix(Path(args[1])))
        return
    if (len(args) == 3 and args[0] == "--probe-runtime"
            and args[1] in {"direct", "preload-spm"}
            and (args[2] in {"none", "shared", "duplicate"}
                 or args[2] == "winapi" and os.name == "nt")):
        _probe_runtime(args[1], args[2])
        return
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
        os.read(0, 1)
        os._exit(0)  # EOF stops downloads/native validation even if the parent crashes.

    threading.Thread(target=parent_closed, daemon=True).start()
    try:
        prepare(Path(args[0]), int(args[1]), (Path(args[2]), Path(args[3])))
        print('{"ready": true}', file=output, flush=True)
        threading.Event().wait()  # Parent retains the lease until publication and child reap.
    except Exception:
        os._exit(1)  # Do not finalize native state while the parent-pipe watchdog is blocked.


if __name__ == "__main__":
    main()

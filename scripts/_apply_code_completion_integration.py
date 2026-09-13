from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one anchor in {path}, found {count}: {old[:80]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_main() -> None:
    path = ROOT / "apps/server/langtextflow/main.py"
    replace_once(
        path,
        "import asyncio\nfrom contextlib import asynccontextmanager, suppress\n",
        "import asyncio\nimport os\nfrom contextlib import asynccontextmanager, suppress\nfrom pathlib import Path\n",
    )
    replace_once(
        path,
        "from .glossary_repository import GlossaryRepository\n",
        "from .glossary_recommendations import GlossaryRecommendation, recommend_glossary_terms\n"
        "from .glossary_repository import GlossaryRepository\n",
    )
    replace_once(
        path,
        "from .mdns import MdnsPublisher\nfrom .model_setup import ModelSetupJob, ModelSetupManager, ModelSetupRequest\n",
        "from .mdns import MdnsPublisher\n"
        "from .model_cache import CacheInventory, ModelCacheManager\n"
        "from .model_setup import ModelSetupJob, ModelSetupManager, ModelSetupRequest\n",
    )
    replace_once(
        path,
        "from .runtime import CaptionRuntime\n",
        "from .runtime import CaptionRuntime\n"
        "from .runtime_install import (\n"
        "    RuntimeKind,\n"
        "    RuntimeProvisionJob,\n"
        "    RuntimeProvisionManager,\n"
        "    RuntimeStatus,\n"
        ")\n",
    )
    replace_once(
        path,
        "settings = get_settings()\nruntime = CaptionRuntime()\nglossary_repository = GlossaryRepository(settings.database_path)\nmodel_setup_manager = ModelSetupManager(settings)\n",
        "settings = get_settings()\n"
        "model_cache_root = Path(settings.model_cache_dir).expanduser().resolve()\n"
        "model_cache_root.mkdir(parents=True, exist_ok=True)\n"
        "os.environ.setdefault(\"HF_HOME\", str(model_cache_root / \"huggingface\"))\n"
        "runtime = CaptionRuntime()\n"
        "glossary_repository = GlossaryRepository(settings.database_path)\n"
        "model_setup_manager = ModelSetupManager(settings)\n"
        "model_cache_manager = ModelCacheManager(settings)\n"
        "runtime_provision_manager = RuntimeProvisionManager(settings)\n",
    )
    replace_once(
        path,
        "        await vibevoice_lifecycle.shutdown()\n        await model_setup_manager.shutdown()\n",
        "        await vibevoice_lifecycle.shutdown()\n"
        "        await model_setup_manager.shutdown()\n"
        "        await runtime_provision_manager.shutdown()\n",
    )
    replace_once(
        path,
        "@app.get(\"/api/v1/state\", response_model=SessionState)\n",
        "@app.get(\"/api/v1/setup/runtimes\", response_model=list[RuntimeStatus])\n"
        "async def runtime_statuses(request: Request) -> list[RuntimeStatus]:\n"
        "    _require_operator(request)\n"
        "    return await runtime_provision_manager.statuses()\n\n\n"
        "@app.post(\"/api/v1/setup/runtimes/{kind}\", response_model=RuntimeProvisionJob, status_code=202)\n"
        "async def provision_runtime(request: Request, kind: RuntimeKind) -> RuntimeProvisionJob:\n"
        "    _require_operator(request)\n"
        "    if runtime.state.running:\n"
        "        raise HTTPException(status_code=409, detail=\"stop the active caption session before changing runtimes\")\n"
        "    return await runtime_provision_manager.start(kind)\n\n\n"
        "@app.get(\"/api/v1/setup/runtime-jobs\", response_model=list[RuntimeProvisionJob])\n"
        "async def runtime_provision_jobs(request: Request) -> list[RuntimeProvisionJob]:\n"
        "    _require_operator(request)\n"
        "    return await runtime_provision_manager.list_jobs()\n\n\n"
        "@app.get(\"/api/v1/setup/runtime-jobs/{job_id}\", response_model=RuntimeProvisionJob)\n"
        "async def runtime_provision_job(request: Request, job_id: str) -> RuntimeProvisionJob:\n"
        "    _require_operator(request)\n"
        "    job = await runtime_provision_manager.get_job(job_id)\n"
        "    if job is None:\n"
        "        raise HTTPException(status_code=404, detail=\"runtime provision job not found\")\n"
        "    return job\n\n\n"
        "@app.post(\"/api/v1/setup/runtime-jobs/{job_id}/cancel\", response_model=RuntimeProvisionJob)\n"
        "async def cancel_runtime_provision_job(request: Request, job_id: str) -> RuntimeProvisionJob:\n"
        "    _require_operator(request)\n"
        "    job = await runtime_provision_manager.cancel(job_id)\n"
        "    if job is None:\n"
        "        raise HTTPException(status_code=404, detail=\"runtime provision job not found\")\n"
        "    return job\n\n\n"
        "@app.get(\"/api/v1/setup/cache\", response_model=CacheInventory)\n"
        "async def model_cache_inventory(request: Request) -> CacheInventory:\n"
        "    _require_operator(request)\n"
        "    return await model_cache_manager.inventory()\n\n\n"
        "@app.delete(\"/api/v1/setup/cache/{area}\", response_model=CacheInventory)\n"
        "async def clear_model_cache(request: Request, area: str) -> CacheInventory:\n"
        "    _require_operator(request)\n"
        "    if runtime.state.running:\n"
        "        raise HTTPException(status_code=409, detail=\"stop the active caption session before clearing model cache\")\n"
        "    try:\n"
        "        return await model_cache_manager.clear(area)\n"
        "    except ValueError as exc:\n"
        "        raise HTTPException(status_code=400, detail=str(exc)) from exc\n\n\n"
        "@app.get(\"/api/v1/state\", response_model=SessionState)\n",
    )
    replace_once(
        path,
        "@app.get(\"/api/v1/glossary/export\")\n",
        "@app.get(\"/api/v1/glossary/recommendations\", response_model=list[GlossaryRecommendation])\n"
        "async def glossary_recommendations(\n"
        "    request: Request,\n"
        "    session_limit: int = Query(default=20, ge=1, le=100),\n"
        "    limit: int = Query(default=30, ge=1, le=100),\n"
        "    min_occurrences: int = Query(default=2, ge=2, le=50),\n"
        ") -> list[GlossaryRecommendation]:\n"
        "    _require_operator(request)\n"
        "    return await asyncio.to_thread(\n"
        "        recommend_glossary_terms, runtime.history, glossary_repository,\n"
        "        session_limit=session_limit, limit=limit, min_occurrences=min_occurrences,\n"
        "    )\n\n\n"
        "@app.get(\"/api/v1/glossary/export\")\n",
    )


def patch_model_setup() -> None:
    path = ROOT / "apps/server/langtextflow/model_setup.py"
    replace_once(path, "import json\nimport re\nimport sys\n", "import json\nimport os\nimport re\nimport sys\nfrom pathlib import Path\n")
    replace_once(
        path,
        "        code = (\n            \"import json,sys; \"\n            \"from faster_whisper.utils import download_model; \"\n            \"path=download_model(sys.argv[1]); \"\n            \"print(json.dumps({'path': path}))\"\n        )\n",
        "        cache_dir = (Path(self.settings.model_cache_dir).expanduser().resolve() / \"huggingface\")\n"
        "        cache_dir.mkdir(parents=True, exist_ok=True)\n"
        "        code = (\n"
        "            \"import json,sys; \"\n"
        "            \"from faster_whisper.utils import download_model; \"\n"
        "            \"path=download_model(sys.argv[1], cache_dir=sys.argv[2]); \"\n"
        "            \"print(json.dumps({'path': path}))\"\n"
        "        )\n",
    )
    replace_once(
        path,
        "                job.model,\n                stdout=asyncio.subprocess.PIPE,\n",
        "                job.model,\n                str(cache_dir),\n"
        "                stdout=asyncio.subprocess.PIPE,\n"
        "                env={**os.environ, \"HF_HOME\": str(cache_dir)},\n",
    )


def patch_vibevoice_lifecycle() -> None:
    path = ROOT / "apps/server/langtextflow/vibevoice_lifecycle.py"
    helper = '''    def _repo_path(self) -> Path:\n        if self.settings.vibevoice_repo_path:\n            return Path(self.settings.vibevoice_repo_path).expanduser().resolve()\n        return Path(self.settings.managed_runtime_dir).expanduser().resolve() / "vibevoice"\n\n    def _model_path(self) -> Path:\n        if self.settings.vibevoice_model_path:\n            return Path(self.settings.vibevoice_model_path).expanduser().resolve()\n        return (\n            Path(self.settings.model_cache_dir).expanduser().resolve()\n            / "vibevoice"\n            / self.settings.vibevoice_model_revision\n        )\n\n    def _python_path(self) -> str:\n        if self.settings.vibevoice_python.strip():\n            return self.settings.vibevoice_python.strip()\n        repo = self._repo_path()\n        candidate = repo / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")\n        return str(candidate)\n\n'''
    replace_once(path, "    def _configuration_error(self) -> str | None:\n", helper + "    def _configuration_error(self) -> str | None:\n")
    old = '''        repo = (\n            Path(self.settings.vibevoice_repo_path).expanduser()\n            if self.settings.vibevoice_repo_path\n            else None\n        )\n        if repo is None or not repo.is_dir():\n            return "VibeVoice repository 경로를 설정하고 먼저 runtime을 준비하세요."\n'''
    new = '''        repo = self._repo_path()\n        if not repo.is_dir():\n            return "VibeVoice runtime을 먼저 준비하세요."\n'''
    replace_once(path, old, new)
    old = '''        model = (\n            Path(self.settings.vibevoice_model_path).expanduser()\n            if self.settings.vibevoice_model_path\n            else None\n        )\n        if model is None or not model.is_dir():\n            return "준비된 VibeVoice streaming checkpoint의 로컬 경로를 설정하세요."\n'''
    new = '''        model = self._model_path()\n        if not model.is_dir():\n            return "준비된 VibeVoice streaming checkpoint를 찾지 못했습니다."\n'''
    replace_once(path, old, new)
    replace_once(
        path,
        "        python = self.settings.vibevoice_python.strip() or sys.executable\n",
        "        python = self._python_path()\n",
    )
    replace_once(
        path,
        "        repo = str(Path(self.settings.vibevoice_repo_path).expanduser().resolve())\n        model = str(Path(self.settings.vibevoice_model_path).expanduser().resolve())\n        python = self.settings.vibevoice_python.strip() or sys.executable\n",
        "        repo = str(self._repo_path())\n        model = str(self._model_path())\n        python = self._python_path()\n",
    )
    replace_once(
        path,
        "            repo_path=self.settings.vibevoice_repo_path or None,\n            model_path=self.settings.vibevoice_model_path or None,\n",
        "            repo_path=str(self._repo_path()),\n            model_path=str(self._model_path()),\n",
    )


def patch_runtime_install() -> None:
    path = ROOT / "apps/server/langtextflow/runtime_install.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace('VIBEVOICE_MODEL_REVISION = "c0b4d1571323d98254b7a743e7fe7e543b792caa"\n\n\n', '')
    text = text.replace('self.cache_root / "vibevoice" / VIBEVOICE_MODEL_REVISION', 'self.cache_root / "vibevoice" / self.settings.vibevoice_model_revision')
    text = text.replace('self.settings.vibevoice_model_id, VIBEVOICE_MODEL_REVISION, str(model_dir)', 'self.settings.vibevoice_model_id, self.settings.vibevoice_model_revision, str(model_dir)')
    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_main()
    patch_model_setup()
    patch_vibevoice_lifecycle()
    patch_runtime_install()


if __name__ == "__main__":
    main()

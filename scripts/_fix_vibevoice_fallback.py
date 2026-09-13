from pathlib import Path

path = Path("apps/server/langtextflow/vibevoice_lifecycle.py")
text = path.read_text(encoding="utf-8")
old = '''    def _python_path(self) -> str:\n        if self.settings.vibevoice_python.strip():\n            return self.settings.vibevoice_python.strip()\n        repo = self._repo_path()\n        candidate = (\n            repo / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")\n        )\n        return str(candidate)\n'''
new = '''    def _python_path(self) -> str:\n        if self.settings.vibevoice_python.strip():\n            return self.settings.vibevoice_python.strip()\n        repo = self._repo_path()\n        candidate = (\n            repo / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")\n        )\n        if candidate.is_file():\n            return str(candidate)\n        return sys.executable\n'''
if text.count(old) != 1:
    raise RuntimeError(f"expected one _python_path block, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")

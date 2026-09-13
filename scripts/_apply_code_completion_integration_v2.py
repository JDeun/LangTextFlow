from __future__ import annotations

import _apply_code_completion_integration as patch


AMBIGUOUS_PYTHON_ANCHOR = (
    "        python = self.settings.vibevoice_python.strip() or sys.executable\n"
)


def guarded_replace(path, old: str, new: str) -> None:
    if old != AMBIGUOUS_PYTHON_ANCHOR:
        return patch._original_replace_once(path, old, new)  # type: ignore[attr-defined]
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 2:
        raise RuntimeError(f"expected two VibeVoice Python anchors, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


patch._original_replace_once = patch.replace_once  # type: ignore[attr-defined]
patch.replace_once = guarded_replace
patch.main()

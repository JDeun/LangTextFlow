"""Explicit model preparation entrypoint shared by Python and frozen executables."""
from __future__ import annotations

import json
import sys

from .whisper_cache import prepare_whisper_model


def main(args: list[str] | None = None) -> None:
    args = sys.argv[1:] if args is None else args
    if len(args) != 2:
        raise SystemExit(1)
    try:
        path = prepare_whisper_model(args[0], args[1])
    except Exception:
        raise SystemExit(1) from None
    print(json.dumps({"path": str(path)}), flush=True)


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "apps" / "desktop" / "src-tauri" / "tauri.release.conf.json"


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"missing required release setting: {name}")
    return value


def main() -> None:
    endpoint = _required("TAURI_UPDATER_ENDPOINT")
    public_key = _required("TAURI_SIGNING_PUBLIC_KEY")
    payload = {
        "bundle": {"createUpdaterArtifacts": True},
        "plugins": {
            "updater": {
                "pubkey": public_key,
                "endpoints": [endpoint],
            }
        },
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUTPUT.relative_to(ROOT))


if __name__ == "__main__":
    main()

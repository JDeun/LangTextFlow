from pathlib import Path

path = Path("apps/web/src/App.tsx")
text = path.read_text(encoding="utf-8")
old = "              setHistoryRefreshToken((value) => value + 1);\n"
if old not in text:
    raise SystemExit("expected history refresh call not found")
path.write_text(text.replace(old, "", 1), encoding="utf-8")
Path(__file__).unlink()

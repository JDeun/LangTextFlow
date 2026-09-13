from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / "apps/server/langtextflow/main.py"
text = path.read_text(encoding="utf-8")
old = "from contextlib import asynccontextmanager\n"
new = "from contextlib import asynccontextmanager, suppress\n"
if text.count(old) != 1:
    raise RuntimeError("main.py contextlib import changed unexpectedly")
text = text.replace(old, new, 1)
old = "    try:\n        await asyncio.wait_for(websocket.close(code=1012, reason=reason), timeout=1.0)\n    except Exception:\n        pass\n"
new = "    with suppress(Exception):\n        await asyncio.wait_for(websocket.close(code=1012, reason=reason), timeout=1.0)\n"
if text.count(old) != 1:
    raise RuntimeError("main.py audio close block changed unexpectedly")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
Path(__file__).unlink()

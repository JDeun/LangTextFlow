import asyncio
import threading

import pytest

from langtextflow.native_work import run_native_work


@pytest.mark.asyncio
async def test_cancelled_native_work_keeps_capacity_until_thread_finishes():
    entered, release = threading.Event(), threading.Event()
    failures = []
    loop = asyncio.get_running_loop()
    old_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: failures.append(context))

    def slow_work():
        entered.set()
        assert release.wait(3)
        raise ValueError("discarded old model load")

    pending = asyncio.create_task(run_native_work("whisper", slow_work))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        for _ in range(4):
            with pytest.raises(RuntimeError, match="still busy"):
                await run_native_work("whisper", lambda: pytest.fail("queued extra work"))
        assert await run_native_work("m2m100-load", lambda: "independent provider")
    finally:
        release.set()
        # The native completion releases admission independently of its cancelled waiter.
        for _ in range(100):
            try:
                assert await run_native_work("whisper", lambda: "fresh") == "fresh"
                break
            except RuntimeError:
                await asyncio.sleep(.01)
        else:
            pytest.fail("native capacity was never released")
        await asyncio.sleep(0)
        loop.set_exception_handler(old_handler)
    assert not failures


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["whisper", "m2m100-load"])
async def test_late_model_load_after_cancel_does_not_attach_to_closed_provider(
    tmp_path, monkeypatch, provider,
):
    from langtextflow.asr.faster_whisper import FasterWhisperStreamingAsrEngine
    from langtextflow.models import StartSessionRequest
    from langtextflow.translation.m2m100_ct2 import M2M100CT2Translator

    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    model = object()

    async def publish(event):
        pytest.fail("cancelled load published a caption")

    def slow_load():
        entered.set()
        try:
            assert release.wait(3)
            return model if provider == "whisper" else (object(), model)
        finally:
            finished.set()

    if provider == "whisper":
        engine = FasterWhisperStreamingAsrEngine(publish)
        monkeypatch.setattr(engine, "_load_model_sync", slow_load)
        pending = asyncio.create_task(engine.start(StartSessionRequest(engine="faster-whisper")))
        close = engine.stop
        def attached():
            return engine._model
    else:
        for name in ("model.bin", "config.json", "tokenizer_config.json",
                     "sentencepiece.bpe.model", "vocab.json"):
            (tmp_path / name).touch()
        translator = M2M100CT2Translator(model_dir=str(tmp_path), tokenizer_dir=str(tmp_path))
        monkeypatch.setattr(translator, "_prepare_sync", slow_load)
        pending = asyncio.create_task(translator.prepare())
        close = translator.close
        def attached():
            return translator._engine
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await close()
        release.set()
        assert await asyncio.to_thread(finished.wait, 1)
        await asyncio.sleep(.01)
        assert attached() is None
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await close()

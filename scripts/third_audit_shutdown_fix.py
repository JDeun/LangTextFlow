from pathlib import Path

root = Path(__file__).resolve().parents[1]


def replace(path: str, old: str, new: str, *, count: int = 1) -> None:
    target = root / path
    text = target.read_text(encoding="utf-8")
    actual = text.count(old)
    if actual != count:
        raise RuntimeError(f"{path}: expected {count} occurrence(s), found {actual}")
    target.write_text(text.replace(old, new, count), encoding="utf-8")


def append_once(path: str, marker: str, content: str) -> None:
    target = root / path
    text = target.read_text(encoding="utf-8")
    if marker not in text:
        target.write_text(text.rstrip() + "\n\n\n" + content.strip() + "\n", encoding="utf-8")


# Session stop must not drain an arbitrarily large/slow post-processing backlog.
replace(
    "apps/server/langtextflow/pipeline.py",
    "    async def stop(self) -> None:\n"
    "        if self._worker_task is not None:\n"
    "            await self._queue.put(None)\n"
    "            await self._worker_task\n"
    "            self._worker_task = None\n"
    "        if self.llm_corrector is not None:\n"
    "            await self.llm_corrector.close()\n"
    "        if self.translator is not None:\n"
    "            await self.translator.close()\n"
    "        self.llm_corrector = None\n"
    "        self.translator = None\n"
    "        self.request = None\n",
    "    async def stop(self) -> None:\n"
    "        first_error: Exception | None = None\n"
    "        task = self._worker_task\n"
    "        self._worker_task = None\n"
    "        if task is not None:\n"
    "            if not task.done():\n"
    "                task.cancel()\n"
    "            try:\n"
    "                await task\n"
    "            except asyncio.CancelledError:\n"
    "                pass\n"
    "            except Exception as exc:\n"
    "                first_error = exc\n"
    "        self._queue = asyncio.Queue(maxsize=128)\n\n"
    "        corrector = self.llm_corrector\n"
    "        translator = self.translator\n"
    "        self.llm_corrector = None\n"
    "        self.translator = None\n"
    "        self.request = None\n"
    "        for provider in (corrector, translator):\n"
    "            if provider is None:\n"
    "                continue\n"
    "            try:\n"
    "                await provider.close()\n"
    "            except Exception as exc:\n"
    "                if first_error is None:\n"
    "                    first_error = exc\n"
    "        if first_error is not None:\n"
    "            raise first_error\n",
)

# A full ASR queue must not block end-of-stream signalling forever.
replace(
    "apps/server/langtextflow/asr/faster_whisper.py",
    "    async def end_audio(self) -> None:\n"
    "        if self._queue is None or self._ended or self._failure is not None:\n"
    "            return\n"
    "        self._ended = True\n"
    "        await self._queue.put(None)\n\n"
    "    async def stop(self) -> None:\n"
    "        await self.end_audio()\n"
    "        if self._worker_task is not None:\n"
    "            with suppress(asyncio.CancelledError):\n"
    "                await self._worker_task\n"
    "        self._worker_task = None\n"
    "        self._queue = None\n"
    "        self._request = None\n"
    "        self._model = None\n",
    "    async def end_audio(self) -> None:\n"
    "        if self._queue is None or self._ended or self._failure is not None:\n"
    "            return\n"
    "        self._ended = True\n"
    "        try:\n"
    "            self._queue.put_nowait(None)\n"
    "        except asyncio.QueueFull:\n"
    "            # The worker observes _ended and exits once the existing backlog drains.\n"
    "            pass\n\n"
    "    async def stop(self) -> None:\n"
    "        await self.end_audio()\n"
    "        task = self._worker_task\n"
    "        if task is not None:\n"
    "            if self._failure is not None and not task.done():\n"
    "                task.cancel()\n"
    "            try:\n"
    "                await asyncio.wait_for(task, timeout=10.0)\n"
    "            except (asyncio.CancelledError, TimeoutError):\n"
    "                pass\n"
    "        self._worker_task = None\n"
    "        self._queue = None\n"
    "        self._request = None\n"
    "        self._model = None\n",
)
replace(
    "apps/server/langtextflow/asr/faster_whisper.py",
    "                finally:\n"
    "                    self._queue.task_done()\n"
    "        except asyncio.CancelledError:\n",
    "                finally:\n"
    "                    self._queue.task_done()\n"
    "                if self._ended and self._queue.empty():\n"
    "                    if buffer:\n"
    "                        await self._process_chunk(bytes(buffer))\n"
    "                    return\n"
    "        except asyncio.CancelledError:\n",
)

replace(
    "apps/server/langtextflow/asr/vibevoice.py",
    "    async def end_audio(self) -> None:\n"
    "        if self._queue is None or self._ended or self._failure is not None:\n"
    "            return\n"
    "        self._ended = True\n"
    "        await self._queue.put(None)\n",
    "    async def end_audio(self) -> None:\n"
    "        if self._queue is None or self._ended or self._failure is not None:\n"
    "            return\n"
    "        self._ended = True\n"
    "        try:\n"
    "            self._queue.put_nowait(None)\n"
    "        except asyncio.QueueFull:\n"
    "            # The sender observes _ended and emits the end marker after draining.\n"
    "            pass\n",
)
replace(
    "apps/server/langtextflow/asr/vibevoice.py",
    "                finally:\n"
    "                    self._queue.task_done()\n"
    "        except asyncio.CancelledError:\n",
    "                finally:\n"
    "                    self._queue.task_done()\n"
    "                if self._ended and self._queue.empty():\n"
    "                    await self._ws.send(\"end\")\n"
    "                    return\n"
    "        except asyncio.CancelledError:\n",
)

# Once persistence stores one latest row per segment, exports must retain unfinished tail segments.
replace(
    "apps/server/langtextflow/main.py",
    "def _best_export_segments(segments: list[TranscriptEvent]) -> list[TranscriptEvent]:\n"
    "    committed = [segment for segment in segments if segment.committed]\n"
    "    return committed or segments\n",
    "def _best_export_segments(segments: list[TranscriptEvent]) -> list[TranscriptEvent]:\n"
    "    # SessionRepository and CaptionStore already retain only the latest version per\n"
    "    # segment. Filtering globally to committed rows would drop a valid stable tail\n"
    "    # whenever an earlier segment had already committed.\n"
    "    return segments\n",
)
replace(
    "apps/server/langtextflow/main.py",
    "        await model_setup_manager.shutdown()\n"
    "        await asyncio.to_thread(mdns_publisher.stop)\n",
    "        await model_setup_manager.shutdown()\n"
    "        await asyncio.to_thread(mdns_publisher.stop)\n"
    "        _allow_audio_socket_acceptance()\n",
)

append_once(
    "apps/server/tests/test_pipeline.py",
    "test_pipeline_stop_cancels_blocked_postprocessing",
    '''
class BlockingCorrector(ConstrainedCorrector):
    provider = "blocking"
    model = "blocking-corrector"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.never = asyncio.Event()

    async def correct(
        self,
        text: str,
        *,
        source_language: str,
        context: SessionContext,
    ) -> str:
        del text, source_language, context
        self.started.set()
        await self.never.wait()
        raise AssertionError("unreachable")


@pytest.mark.asyncio
async def test_pipeline_stop_cancels_blocked_postprocessing() -> None:
    published: list[TranscriptEvent] = []

    async def publish(event: TranscriptEvent) -> None:
        published.append(event)

    corrector = BlockingCorrector()
    pipeline = PipelineWithCorrector(publish, Settings(), corrector)
    await pipeline.start(
        StartSessionRequest(
            source_language="ko",
            target_languages=["en"],
            correction_provider="fake",
            translation_provider="none",
        )
    )
    await pipeline.ingest(
        TranscriptEvent(
            segment_id="blocked",
            version=1,
            stage=CaptionStage.STABLE,
            source_language="ko",
            text="blocked correction",
            start_ms=0,
            end_ms=100,
        )
    )
    await asyncio.wait_for(corrector.started.wait(), timeout=1.0)
    await asyncio.wait_for(pipeline.stop(), timeout=0.2)

    assert pipeline._worker_task is None
    assert pipeline.queue_depth == 0
    assert [event.stage for event in published] == [CaptionStage.STABLE]
''',
)

append_once(
    "apps/server/tests/test_faster_whisper.py",
    "test_faster_whisper_end_audio_does_not_block_on_full_queue",
    '''
@pytest.mark.asyncio
async def test_faster_whisper_end_audio_does_not_block_on_full_queue() -> None:
    async def publish(event) -> None:
        del event

    engine = FasterWhisperStreamingAsrEngine(publish, queue_chunks=1)
    engine._queue = asyncio.Queue(maxsize=1)
    engine._queue.put_nowait(b"\\x00\\x00\\x00\\x00")

    await asyncio.wait_for(engine.end_audio(), timeout=0.1)
    assert engine._ended is True
''',
)

append_once(
    "apps/server/tests/test_vibevoice.py",
    "test_vibevoice_end_audio_does_not_block_on_full_queue",
    '''
@pytest.mark.asyncio
async def test_vibevoice_end_audio_does_not_block_on_full_queue() -> None:
    async def publish(event) -> None:
        del event

    engine = VibeVoiceStreamingAsrEngine(
        publish,
        base_url="http://127.0.0.1:8001",
        queue_chunks=1,
    )
    engine._queue = asyncio.Queue(maxsize=1)
    engine._queue.put_nowait(b"\\x00\\x00\\x00\\x00")

    await asyncio.wait_for(engine.end_audio(), timeout=0.1)
    assert engine._ended is True
''',
)

replace(
    "apps/server/tests/test_api_boundaries.py",
    "from langtextflow.models import AudioStreamInfo, SessionContext, SessionState\n",
    "from langtextflow.models import (\n"
    "    AudioStreamInfo,\n"
    "    CaptionStage,\n"
    "    SessionContext,\n"
    "    SessionState,\n"
    "    TranscriptEvent,\n"
    ")\n",
)
append_once(
    "apps/server/tests/test_api_boundaries.py",
    "test_export_selection_keeps_uncommitted_tail_segment",
    '''
def test_export_selection_keeps_uncommitted_tail_segment() -> None:
    committed = TranscriptEvent(
        segment_id="one",
        version=2,
        stage=CaptionStage.COMMITTED,
        source_language="ko",
        text="first",
        start_ms=0,
        end_ms=100,
        committed=True,
    )
    stable_tail = TranscriptEvent(
        segment_id="two",
        version=1,
        stage=CaptionStage.STABLE,
        source_language="ko",
        text="tail",
        start_ms=100,
        end_ms=200,
    )

    assert main_module._best_export_segments([committed, stable_tail]) == [
        committed,
        stable_tail,
    ]
''',
)

Path(__file__).unlink()

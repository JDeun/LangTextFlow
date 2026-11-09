"""Owned lexical controls; these do not establish complete semantic safety."""
import asyncio
import json

import httpx
import pytest

from langtextflow.config import Settings
from langtextflow.correction import DeterministicCorrector
from langtextflow.correction_safety import protected_tokens
from langtextflow.llm_correction import CorrectionError, validate_constrained_candidate
from langtextflow.models import (
    CaptionStage,
    GlossaryEntry,
    SessionContext,
    StartSessionRequest,
    TranscriptEvent,
)
from langtextflow.pipeline import CaptionPipeline
from langtextflow.store import CaptionStore

_KO = "오늘 배운 내용을 깊이 새기고 이웃을 사랑하며 어려운 사람을 돕는 이 약속을 "
_EN = "We will review the lesson carefully and help our neighbors every day because we "
_JA = "今日学んだ内容を大切にして、周りの人を助けながら毎日の約束を守ります。私たちは"
_CASES = [
    ("ko", _KO + "잊지 마라.", _KO + "잊어라."),
    ("ko", _KO + "포기하지 마세요.", _KO + "포기하세요."),
    ("ko", _KO + "잊어라.", _KO + "잊지마라."),
    ("ko", _KO + "잊지 마라. 포기하지 마세요.", _KO + "잊지 마라. 포기하세요."),
    ("en", _EN + "do not forget this promise.", _EN + "do forget this promise."),
    ("en", _EN + "remember this promise.", _EN + "never remember this promise."),
    ("en", _EN + "do not forget and never give up.", _EN + "do forget and never give up."),
    ("ja", _JA + "忘れない。", _JA + "忘れる。"),
    ("ja", _JA + "忘れます。", _JA + "忘れません。"),
    ("ja", _JA + "忘れないし諦めません。", _JA + "忘れるし諦めません。"),
]


@pytest.mark.parametrize("language,source,candidate", _CASES, ids=[
    "ko-remove-mara", "ko-remove-maseyo", "ko-insert", "ko-count",
    "en-remove", "en-insert", "en-count", "ja-remove", "ja-insert", "ja-count",
])
def test_shared_llm_guard_rejects_detectable_negation_changes(language, source, candidate):
    with pytest.raises(CorrectionError, match="negation"):
        validate_constrained_candidate(source, candidate, source_language=language)


@pytest.mark.parametrize("alias,canonical", [
    ("잊지 마라", "잊어라"), ("포기하지 마세요", "포기하세요"), ("잊어라", "잊지마라"),
])
def test_deterministic_alias_cannot_change_the_same_korean_prohibitions(alias, canonical):
    source = _KO + alias + "."
    context = SessionContext(glossary=[GlossaryEntry(term=canonical, aliases=[alias])])
    assert DeterministicCorrector().correct(source, context, source_language="ko") == source


@pytest.mark.parametrize("language,source,candidate", [
    ("ko", "마음과 마라톤에 대해 이야기하며 요한 보금을 읽습니다.",
     "마음과 마라톤에 대해 이야기하며 요한복음을 읽습니다."),
    ("ko", "이 약속을 잊지 마라", "이 약속을 잊지 마라."),
    ("en", "Knowledge and notable progress help our neighbors every day.",
     "Knowledge and notable progress help our neighbors every day!"),
    ("en", "We do not give up this promise.", "We do not give up this promise!"),
    ("ja", "私たちは約束を忘れない", "私たちは約束を忘れない。"),
], ids=["ko-word-controls", "ko-safe-punctuation", "en-substrings", "en-safe", "ja-safe"])
def test_safe_small_edits_and_word_controls_are_preserved(language, source, candidate):
    assert validate_constrained_candidate(source, candidate, source_language=language) == candidate


def test_prohibition_markers_require_the_ji_form_and_a_complete_word_ending():
    assert protected_tokens("마음 마라톤 저지 마라톤 바지 마음", "ko") == ([], 0)
    assert protected_tokens("잊지 마라. 하지 마세요. 잊지마.", "ko") == ([], 3)


@pytest.mark.parametrize("language,source,candidate", [_CASES[0], _CASES[4], _CASES[7]],
                         ids=["ko", "en", "ja"])
async def test_existing_pipeline_real_provider_guard_falls_back_and_keeps_raw_source(
    monkeypatch, tmp_path, language, source, candidate,
):
    native_client = httpx.AsyncClient
    calls = []

    def respond(request):
        if request.method == "GET":
            return httpx.Response(200, json={"models": [{"name": "test-model"}]})
        calls.append(request)
        return httpx.Response(200, json={"message": {
            "content": json.dumps({"corrected_text": candidate}),
        }})

    monkeypatch.setattr("langtextflow.llm_correction.httpx.AsyncClient", lambda **options:
        native_client(transport=httpx.MockTransport(respond), **options))
    store = CaptionStore()

    async def publish(event):
        store.apply(event)

    pipeline = CaptionPipeline(publish, Settings(database_path=str(tmp_path / "test.db")))
    await pipeline.start(StartSessionRequest(
        engine="mock", source_language=language,
        target_languages=["ko" if language == "en" else "en"],
        correction_provider="ollama", correction_model="test-model", translation_provider="none",
    ))
    try:
        await pipeline.ingest(TranscriptEvent(
            segment_id="synthetic-source", version=1, stage=CaptionStage.STABLE,
            source_language=language, text=source, start_ms=0, end_ms=1000,
        ))
        await asyncio.wait_for(pipeline._queue.join(), 2)
        final = store.snapshot()[0]
        assert len(calls) == 1 and final.committed
        assert final.text == final.original_text == source
        assert final.correction.method == "fallback" and final.correction.llm_attempted
        assert not final.correction.llm_applied
        assert final.correction.fallback_reason == (
            "correction provider failed; using deterministic result"
        )
    finally:
        await pipeline.stop()

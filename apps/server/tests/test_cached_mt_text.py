"""Same production scorer, synthetic providers only; no weights or new dependencies."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

from scripts import compare_cached_mt_text as cli


@pytest.fixture
def args(tmp_path):
    return SimpleNamespace(
        candidate="m2m100", fixture=cli.FIXTURE, output=tmp_path / "report.json",
        ct2_dir=None, tokenizer_dir=None, threads=2, timeout=.03, total_timeout=.1,
        model="translategemma:4b", requested_revision="a" * 40,
        expected_digest="sha256:" + "b" * 64, ollama_url="http://127.0.0.1:11434",
    )


@pytest.fixture
def scoring_dependencies(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    class CHRF:
        def __init__(self, *, word_order):
            assert word_order == 2

        def sentence_score(self, hypothesis, references):
            return SimpleNamespace(score=100 if hypothesis == references[0] else 0)

    class Resources:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def summary(self):
            return {"rss_peak_mb": None}

    monkeypatch.setitem(sys.modules, "sacrebleu.metrics", SimpleNamespace(CHRF=CHRF))
    monkeypatch.setattr("scripts.compare_church_cpu_pipeline.ResourceSamples", Resources)


class FakeProvider:
    def __init__(self, *, failure=None, cleanup_failure=False):
        self.rows, _ = cli.validate_fixtures(cli.FIXTURE)
        self.inputs = []
        self.loops = []
        self.closed = False
        self.failure = failure
        self.cleanup_failure = cleanup_failure

    async def prepare(self):
        self.loops.append(asyncio.get_running_loop())
        if self.failure == "prepare":
            raise FileNotFoundError("/private/cache?secret=private-token")

    async def translate(self, text, *, source_language, target_language, context):
        self.loops.append(asyncio.get_running_loop())
        assert (source_language, target_language) == ("ko", "en")
        assert context.title == "Synthetic church service evaluation" and not context.glossary
        assert not context.hotwords and not context.reference_documents
        self.inputs.append(text)
        if len(self.inputs) == 1:
            if self.failure == "timeout":
                await asyncio.Event().wait()
            if self.failure == "runtime":
                raise RuntimeError("http://user:secret@private-host/checkpoint")
            if self.failure == "invalid":
                return "다른 한국어 답변"
        return next(row["reference_text"] for row in self.rows if row["source_text"] == text)

    async def close(self):
        self.loops.append(asyncio.get_running_loop())
        self.closed = True
        if self.cleanup_failure:
            raise RuntimeError("private endpoint")


@pytest.mark.parametrize("candidate", ["opus-small", "m2m100", "ollama"])
def test_all_adapters_feed_identical_order_hash_direction_and_glossary_off(
    args, scoring_dependencies, monkeypatch, candidate,
):
    provider = FakeProvider()
    args.candidate = candidate
    if candidate == "opus-small":
        native = object.__new__(cli.CTranslateMarian)

        def translate(text):
            provider.inputs.append(text)
            return next(row["reference_text"] for row in provider.rows
                        if row["source_text"] == text)

        native.translate = translate
        monkeypatch.setattr(cli, "build_provider", lambda _: native)
    else:
        monkeypatch.setattr(cli, "build_provider", lambda _: provider)
    report = cli.evaluate(args)
    rows, digest = cli.validate_fixtures(cli.FIXTURE)
    assert provider.inputs == [row["source_text"] for row in rows]
    assert [row["id"] for row in report["samples"]] == [row["id"] for row in rows]
    assert report["fixture_sha256"] == digest == cli.FIXTURE_SHA256
    assert report["valid_outputs"] == report["recorded_cases"] == report["cases"] == 16
    assert report["status"] == "measured" and report["comparison_eligible"]
    if candidate != "opus-small":
        assert provider.closed and len({id(loop) for loop in provider.loops}) == 1
    assert report["verified_model"] is None and report["model_revision"] is None
    assert report["requested_revision"] == "a" * 40
    assert not report["field_ready"] and not report["quality_certified"]


@pytest.mark.parametrize("failure,status", [
    ("runtime", "failed"), ("timeout", "timed_out"), ("invalid", "invalid_outputs"),
    ("prepare", "unavailable"),
])
def test_failures_cannot_rank_partial_candidate_or_leak_private_provider_details(
    args, scoring_dependencies, monkeypatch, failure, status,
):
    provider = FakeProvider(failure=failure)
    monkeypatch.setattr(cli, "build_provider", lambda _: provider)
    report = cli.evaluate(args)
    assert report["status"] == status and not report["comparison_eligible"]
    assert provider.closed
    assert "private" not in json.dumps(report) and "secret" not in json.dumps(report)
    assert len(report["inputs"]) == 16
    if failure != "prepare":
        assert report["valid_outputs"] == 15
        assert report["recorded_cases"] == 16 and report["unobserved_cases"] == 0
        assert report["samples"][0]["chrf_plus_plus"] is None
        assert report["samples"][0]["critical_terms"]["matched"] == 0
        assert report["mean_sentence_chrf_plus_plus"] == 100
        assert report["case_outcomes"][0]["status"] == (
            "timed_out" if failure == "timeout" else
            "failed" if failure == "runtime" else "measured"
        )


def test_cleanup_failure_revokes_comparison_eligibility(args, scoring_dependencies, monkeypatch):
    provider = FakeProvider(cleanup_failure=True)
    monkeypatch.setattr(cli, "build_provider", lambda _: provider)
    report = cli.evaluate(args)
    assert report["valid_outputs"] == 16
    assert report["cleanup"] == "failed" and not report["comparison_eligible"]


def test_ollama_requires_exact_cached_digest_cpu_options_and_product_prompt(args):
    provider = cli.CachedTranslateGemma(args)
    requests = []

    async def request(method, route, **kwargs):
        requests.append((method, route, kwargs.get("json")))
        if route == "/api/tags":
            return {"models": [{"name": args.model, "digest": "b" * 64}]}
        if route == "/api/generate":
            return {"done": True}
        return {"message": {"content": "The Lord lives."}}

    provider._request = request
    with asyncio.Runner() as runner:
        runner.run(provider.prepare())
        output = runner.run(provider.translate(
            "주님이 살아계십니다.", source_language="ko", target_language="en", context=cli.CONTEXT,
        ))
        runner.run(provider.close())
    assert output == "The Lord lives."
    assert requests[1][2] == {"model": args.model, "prompt": "", "stream": False,
                              "options": provider.options, "keep_alive": "10m"}
    payload = requests[2][2]
    assert payload["messages"] == cli.OllamaTranslator.build_translategemma_messages(
        "주님이 살아계십니다.", source_language="ko", target_language="en", context=cli.CONTEXT,
    )
    assert payload["options"] == {"num_gpu": 0, "num_thread": 2, "seed": 0,
                                  "temperature": 0, "num_predict": 256}
    assert [request[1] for request in requests] == [
        "/api/tags", "/api/generate", "/api/chat", "/api/generate",
    ]
    assert requests[-1][2] == {"model": args.model, "keep_alive": 0}


@pytest.mark.parametrize("name,digest", [
    ("translategemma:12b", "b" * 64), ("translategemma:4b", "c" * 64),
])
def test_missing_exact_ollama_cache_never_loads_or_pulls_a_family_substitute(args, name, digest):
    provider = cli.CachedTranslateGemma(args)
    requests = []

    async def request(method, route, **kwargs):
        requests.append((method, route))
        return {"models": [{"name": name, "digest": digest}]}

    provider._request = request
    with asyncio.Runner() as runner:
        with pytest.raises(FileNotFoundError):
            runner.run(provider.prepare())
        runner.run(provider.close())
    assert requests == [("GET", "/api/tags")]


def test_owned_loopback_http_does_not_route_through_environment_proxy(args, monkeypatch):
    import httpx

    from langtextflow import provider_http

    options = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    def make_client(**kwargs):
        options.append(kwargs)
        return Client()

    async def bounded_request(client, method, url, **kwargs):
        assert url == "http://127.0.0.1:11434/api/tags"
        return {"models": []}

    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    monkeypatch.setattr(provider_http, "bounded_json_request", bounded_request)
    with asyncio.Runner() as runner:
        runner.run(cli.CachedTranslateGemma(args)._request("GET", "/api/tags"))
    assert options == [{"timeout": args.timeout, "trust_env": False}]


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only evaluation supervisor")
def test_real_child_timeout_is_preserved_and_reaped(args, monkeypatch):
    processes = []
    popen = cli.subprocess.Popen

    def create(*a, **kwargs):
        process = popen(*a, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(cli, "worker_command", lambda _: [
        sys.executable, "-c", "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "time.sleep(30)",
    ])
    monkeypatch.setattr(cli.subprocess, "Popen", create)
    report = cli.supervise(args)
    assert report["status"] == "timed_out" and not report["comparison_eligible"]
    assert processes[0].returncode is not None
    assert json.loads(args.output.read_text())["error_type"] == "CandidateProcessTimeout"


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only evaluation supervisor")
def test_child_crash_retains_all_input_identities_and_cannot_rank(args, monkeypatch):
    monkeypatch.setattr(cli, "worker_command", lambda _: [
        sys.executable, "-c", "raise SystemExit(7)",
    ])
    report = cli.supervise(args)
    assert report["status"] == "failed" and report["process_returncode"] == 7
    assert len(report["inputs"]) == len(report["case_outcomes"]) == 16
    assert report["recorded_cases"] == 0 and report["unobserved_cases"] == 16
    assert not report["comparison_eligible"]


def test_changed_fixture_cannot_silently_change_comparison_population(args, tmp_path):
    args.fixture = tmp_path / "changed.jsonl"
    args.fixture.write_bytes(cli.FIXTURE.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="fixture bytes differ"):
        cli.evaluate(args)

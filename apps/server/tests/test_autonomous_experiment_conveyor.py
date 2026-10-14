"""Idempotence, dependency gating, frozen SHA and retry decisions."""
from __future__ import annotations

import base64
import json

from scripts.autonomous_experiment_conveyor import (
    advance,
    frozen_preregistration,
    sources,
)

FROZEN = "a" * 40
PLAN = {
    "schema_version": 1,
    "stages": [
        {"id": "catalog", "workflow": "catalog.yml", "deps": [],
         "artifact": None},
        {"id": "m2m", "workflow": "m2m.yml", "deps": ["catalog"],
         "artifact": "m2m-canonical"},
        {"id": "apple", "workflow": "apple.yml", "deps": ["catalog"],
         "artifact": "apple-status"},
        {"id": "church_quality", "workflow": "quality.yml", "deps": ["m2m"],
         "artifact": "quality-report"},
    ],
    "infrastructure_recovery": {"max_run_attempts": 3},
    "blocked_candidates": [{"model": "NLLB", "reason": "noncommercial"}],
}


class FakeAPI:
    def __init__(self, states=None):
        self.repo = "JDeun/LangTextFlow"
        self.states = states or {}
        self.dispatched = []
        self.retried = []

    def runs(self, stage):
        return self.states.get(stage["id"], [])

    def dispatch(self, stage, sha):
        self.dispatched.append((stage["id"], sha))

    def retry(self, run):
        self.retried.append(run["id"])

    def artifact_ok(self, stage, run):
        return run.get("artifact_good", False)


def run(stage, status="completed", conclusion="success", attempt=1,
        artifact_good=True):
    return {
        "id": len(stage) * 10, "created_at": "2026-10-14T00:00:00Z",
        "display_title": f"ltf-stage-{stage} source={FROZEN}",
        "status": status, "conclusion": conclusion, "run_attempt": attempt,
        "event": "workflow_dispatch", "head_branch": "main",
        "status": "completed",
        "head_repository": {"full_name": "JDeun/LangTextFlow"},
        "artifact_good": artifact_good,
    }


def test_push_triggers_new_frozen_experiment():
    api = FakeAPI()
    assert sources(api, "push", {"after": FROZEN, "ref": "refs/heads/main"}, PLAN) == [FROZEN]
    assert sources(api, "push", {"after": "main", "ref": "refs/heads/main"}, PLAN) == []
    assert sources(api, "push", {"after": FROZEN, "ref": "refs/heads/other"}, PLAN) == []


def test_first_advance_only_dispatches_catalog():
    api = FakeAPI()
    report = advance(api, FROZEN, PLAN, execute=False)
    assert report["actions"] == ["dispatch:catalog"]
    assert report["stages"]["m2m"] == "waiting_on_dependency"
    assert report["field_ready"] is False


def test_successful_gate_dispatches_ready_siblings_without_duplicates():
    api = FakeAPI({"catalog": [run("catalog")],
                   "m2m": [run("m2m", status="in_progress")]})
    report = advance(api, FROZEN, PLAN, execute=False)
    assert report["stages"]["catalog"] == "success"
    assert report["stages"]["m2m"] == "running_or_queued"
    assert "dispatch:apple" in report["actions"]
    assert "dispatch:m2m" not in report["actions"]
    assert report["stages"]["church_quality"] == "waiting_on_dependency"


def test_missing_artifact_fails_closed_not_counted_as_scientific_success():
    api = FakeAPI({"catalog": [run("catalog")],
                   "m2m": [run("m2m", artifact_good=False)]})
    report = advance(api, FROZEN, PLAN, execute=False)
    assert report["stages"]["m2m"] == "blocked_missing_canonical_artifact"
    assert report["stages"]["church_quality"] == "waiting_on_dependency"


def test_infrastructure_retried_with_limit_but_negative_artifacts_preserved():
    api = FakeAPI({"catalog": [run("catalog")],
                   "m2m": [run("m2m", conclusion="failure",
                               attempt=1, artifact_good=False)]})
    report = advance(api, FROZEN, PLAN, execute=True)
    assert report["stages"]["m2m"] == "retrying_infrastructure"
    assert api.retried == [30]
    api.states["m2m"] = [run("m2m", conclusion="failure",
                              attempt=3, artifact_good=False)]
    report = advance(api, FROZEN, PLAN, execute=False)
    assert report["stages"]["m2m"] == "blocked_retry_limit"
    api.states["m2m"] = [run("m2m", conclusion="failure",
                              attempt=1, artifact_good=True)]
    report = advance(api, FROZEN, PLAN, execute=False)
    assert report["stages"]["m2m"] == "blocked_scientific_or_semantic_failure"


def test_workflow_run_resumes_same_sha_and_hourly_never_creates_unseeded_run():
    api = FakeAPI({"catalog": [run("catalog")]})
    event = {"workflow_run": {
        "display_title": f"ltf-stage-m2m source={FROZEN}",
        "event": "workflow_dispatch", "head_branch": "main",
    }}
    assert sources(api, "workflow_run", event, PLAN) == [FROZEN]
    assert sources(api, "schedule", {}, PLAN) == [FROZEN]
    assert sources(FakeAPI(), "schedule", {}, PLAN) == []


def test_frozen_sha_rejects_branch_name():
    try:
        advance(FakeAPI(), "main", PLAN, execute=False)
        raise AssertionError("expected frozen SHA rejection")
    except ValueError:
        pass


def test_preregistration_is_loaded_from_frozen_source_not_mutable_main():
    class FrozenGitHub:
        def api(self, method, path):
            assert method == "GET"
            assert path.endswith("?ref=" + FROZEN)
            return {
                "encoding": "base64",
                "content": base64.b64encode(
                    json.dumps(PLAN).encode()
                ).decode(),
            }

    assert frozen_preregistration(FrozenGitHub(), FROZEN) == PLAN


def test_pull_request_smoke_never_launches_unattended_scientific_cohort():
    pr_run = run("catalog")
    pr_run["event"] = "pull_request"
    pr_run["head_branch"] = "ci/experiment-conveyor-20261014"
    api = FakeAPI({"catalog": [pr_run]})
    assert sources(api, "schedule", {}, PLAN) == []
    assert sources(api, "workflow_run", {"workflow_run": pr_run}, PLAN) == []



def test_workflow_run_rejects_forked_or_incomplete_marked_stages():
    for override in (
        {"event": "pull_request"},
        {"status": "in_progress"},
        {"head_branch": "other"},
        {"head_repository": {"full_name": "outsider/LangTextFlow"}},
        {"head_repository": {}},
    ):
        event = run("funasr")
        event.update(override)
        assert sources(
            FakeAPI(), "workflow_run", {"workflow_run": event}, PLAN
        ) == []


def test_repository_preregistration_advances_native_funasr_independently():
    """Real model stage must run after catalog without waiting for M2M100."""
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[3]
        / "benchmarks/translation-conveyor-preregistration.json"
    )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    stages = {s["id"]: s for s in manifest["stages"]}
    assert set(stages) == {
        "catalog", "m2m", "apple", "funasr",
        "church_quality", "church_live",
    }
    assert stages["funasr"]["deps"] == ["catalog"]
    assert stages["funasr"]["artifact"] == "funasr-mlt-cpu-glossary-ab"
    assert stages["church_quality"]["deps"] == ["m2m"]
    assert not any(
        "Fun-ASR" in m["model"] for m in manifest["blocked_candidates"]
    )
    assert manifest["scientific_integrity"]["field_ready_after_ci"] is False


def test_funasr_negative_scientific_result_does_not_disappear_on_retry():
    """A measured failed experiment remains visible, not an infrastructure retry."""
    import copy

    manifest = copy.deepcopy(PLAN)
    manifest["stages"].insert(
        3,
        {"id": "funasr", "workflow": "funasr.yml", "deps": ["catalog"],
         "artifact": "funasr-mlt-cpu-glossary-ab"},
    )
    api = FakeAPI({
        "catalog": [run("catalog")],
        "funasr": [run("funasr", conclusion="failure", artifact_good=True)],
    })
    report = advance(api, FROZEN, manifest, execute=False)
    assert report["stages"]["funasr"] == "blocked_scientific_or_semantic_failure"
    assert report["stages"]["m2m"] == "dispatched_not_yet_completed"
    assert api.retried == []

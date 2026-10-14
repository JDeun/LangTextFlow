"""Idempotence, dependency gating, frozen SHA and retry decisions."""
from __future__ import annotations

import base64
import json

from scripts.autonomous_experiment_conveyor import (
    advance,
    automatic_experiments_paused,
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
    event = {"workflow_run": run("m2m")}
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


def test_repository_dispatch_callback_resumes_only_acknowledged_main_study() -> None:
    event = {
        "action": "ltf-stage-finished",
        "client_payload": {"source_sha": FROZEN, "stage": "catalog"},
    }
    api = FakeAPI({"catalog": [run("catalog")]})
    assert sources(api, "repository_dispatch", event, PLAN) == [FROZEN]

    # Neither a forged stage nor an unknown run can seed a costly study.
    assert sources(FakeAPI(), "repository_dispatch", event, PLAN) == []
    bad = {"action": "ltf-stage-finished", "client_payload": {
        "source_sha": FROZEN, "stage": "not_a_stage",
    }}
    assert sources(api, "repository_dispatch", bad, PLAN) == []

    pr_run = run("catalog")
    pr_run["head_branch"] = "not-main"
    assert sources(
        FakeAPI({"catalog": [pr_run]}), "repository_dispatch", event, PLAN
    ) == []


def test_repository_dispatch_does_not_accept_unknown_event_or_sha() -> None:
    api = FakeAPI({"catalog": [run("catalog")]})
    for action, source in (
        ("other-event", FROZEN),
        ("ltf-stage-finished", "main"),
        ("ltf-stage-finished", "1" * 41),
    ):
        event = {
            "action": action,
            "client_payload": {"source_sha": source, "stage": "catalog"},
        }
        assert sources(api, "repository_dispatch", event, PLAN) == []


def test_real_preregistered_dag_advances_without_manual_intervention() -> None:
    """Real six-stage manifest advances all ready branches on a callback."""
    from pathlib import Path

    manifest_path = (
        Path(__file__).resolve().parents[3]
        / "benchmarks/translation-conveyor-preregistration.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    api = FakeAPI({"catalog": [run("catalog")]})

    first = advance(api, FROZEN, manifest, execute=False)
    assert set(first["actions"]) == {
        "dispatch:m2m", "dispatch:apple", "dispatch:funasr",
    }
    assert first["stages"]["church_quality"] == "waiting_on_dependency"
    assert first["stages"]["church_live"] == "waiting_on_dependency"

    api.states["m2m"] = [run("m2m")]
    second = advance(api, FROZEN, manifest, execute=False)
    assert "dispatch:church_quality" in second["actions"]
    assert "dispatch:church_live" not in second["actions"]

    api.states["church_quality"] = [run("church_quality")]
    third = advance(api, FROZEN, manifest, execute=False)
    assert "dispatch:church_live" in third["actions"]

    api.states["apple"] = [run("apple")]
    api.states["funasr"] = [run("funasr")]
    api.states["church_live"] = [run("church_live")]
    done = advance(api, FROZEN, manifest, execute=False)
    assert done["execution_complete"]
    assert not done["field_ready"]
    assert not done["translation_quality_certified"]


def test_all_registered_scientific_stage_names_match_workflow_and_callback() -> None:
    """Manifest stage ids must agree with GitHub's actual run-name marker.

    The controller uses an exact `ltf-stage-{id} source={sha}` match when
    looking for completed runs. A hyphen/underscore mismatch silently causes
    unlimited redispatches and prevents downstream stages from advancing.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    plan = json.loads(
        (root / "benchmarks/translation-conveyor-preregistration.json")
        .read_text(encoding="utf-8")
    )
    for stage in plan["stages"]:
        workflow = (root / ".github/workflows" / stage["workflow"]).read_text(
            encoding="utf-8"
        )
        stage_id = stage["id"]
        assert (
            f"run-name: ltf-stage-{stage_id} source=" in workflow
        ), f"{stage['workflow']} run-name does not match {stage_id}"
        assert re.search(
            r"^\s*STAGE:\s*" + re.escape(stage_id) + r"\s*$",
            workflow,
            flags=re.MULTILINE,
        ), f"{stage['workflow']} callback stage does not match {stage_id}"


def test_automatic_experiment_pause_marker_is_explicit_and_reversible(tmp_path):
    marker = tmp_path / ".pause-autonomous-model-experiments"
    assert automatic_experiments_paused(None) is False
    assert automatic_experiments_paused(marker) is False
    marker.write_text("Paused for code stabilization", encoding="utf-8")
    assert automatic_experiments_paused(marker) is True
    marker.unlink()
    assert automatic_experiments_paused(marker) is False

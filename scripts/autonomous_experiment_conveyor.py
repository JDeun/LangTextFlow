"""Outcome-blind autonomous research DAG controller, adapted from SchemaRouter 0.14."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{40}")
TITLE = re.compile(r"ltf-stage-([a-z0-9_]+) source=([0-9a-f]{40})")
FAILURES = {"failure", "timed_out", "startup_failure"}


class GitHub:
    def __init__(self, repo: str):
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+", repo):
            raise ValueError("invalid repo")
        self.repo = repo

    def api(self, method: str, path: str, data: dict | None = None):
        cmd = ["gh", "api", "--method", method,
               f"/repos/{self.repo}{path}"]
        if data is not None:
            cmd += ["--input", "-"]
        result = subprocess.run(
            cmd, input=json.dumps(data) if data is not None else None,
            capture_output=True, text=True, timeout=40, check=False,
        )
        if result.returncode:
            raise RuntimeError(f"GitHub API {method} failed for stage endpoint")
        return json.loads(result.stdout) if result.stdout.strip() else None

    def runs(self, stage: dict) -> list[dict]:
        response = self.api(
            "GET", f"/actions/workflows/{stage['workflow']}/runs?per_page=100"
        )
        return response["workflow_runs"]

    def artifact_ok(self, stage: dict, run: dict) -> bool:
        if stage["artifact"] is None:
            return True
        data = self.api("GET", f"/actions/runs/{run['id']}/artifacts?per_page=100")
        found = [a for a in data["artifacts"] if
                 a.get("name") == stage["artifact"]
                 and not a.get("expired")
                 and str(a.get("digest") or "").startswith("sha256:")]
        return len(found) == 1

    def dispatch(self, stage: dict, sha: str) -> None:
        self.api("POST", f"/actions/workflows/{stage['workflow']}/dispatches",
                 {"ref": "main", "inputs": {"source_sha": sha}})

    def retry(self, run: dict) -> None:
        self.api("POST", f"/actions/runs/{run['id']}/rerun-failed-jobs")


def matching(api: GitHub, stage: dict, sha: str) -> list[dict]:
    title = f"ltf-stage-{stage['id']} source={sha}"
    runs = [r for r in api.runs(stage) if
            r.get("event") == "workflow_dispatch"
            and r.get("display_title") == title]
    return sorted(runs, key=lambda x: (x["created_at"], x["id"]), reverse=True)


def advance(api: GitHub, sha: str, manifest: dict, execute: bool) -> dict:
    if not SHA.fullmatch(sha):
        raise ValueError("source SHA must be frozen")
    states: dict[str, str] = {}
    actions: list[str] = []
    for stage in manifest["stages"]:
        name = stage["id"]
        if any(states.get(parent) != "success" for parent in stage["deps"]):
            states[name] = "waiting_on_dependency"
            continue
        runs = matching(api, stage, sha)
        if not runs:
            if execute:
                api.dispatch(stage, sha)
                # A 204 dispatch is not positive acknowledgement. Resolve the
                # run marker before any later controller can consider it absent.
                for _ in range(12):
                    time.sleep(1)
                    if matching(api, stage, sha):
                        break
            states[name] = "dispatched_not_yet_completed"
            actions.append(f"dispatch:{name}")
            continue
        run = runs[0]
        if run["status"] != "completed":
            states[name] = "running_or_queued"
        elif run["conclusion"] == "success":
            states[name] = (
                "success" if api.artifact_ok(stage, run)
                else "blocked_missing_canonical_artifact"
            )
        elif run["conclusion"] in FAILURES:
            # Preserve any scientific negative result with a real artifact.
            if stage["artifact"] and api.artifact_ok(stage, run):
                states[name] = "blocked_scientific_or_semantic_failure"
            elif int(run.get("run_attempt") or 1) < manifest[
                    "infrastructure_recovery"]["max_run_attempts"]:
                if execute:
                    api.retry(run)
                states[name] = "retrying_infrastructure"
                actions.append(f"rerun_failed_jobs:{name}:{run['id']}")
            else:
                states[name] = "blocked_retry_limit"
        else:
            states[name] = f"blocked_{run['conclusion']}"
    return {
        "frozen_source_sha": sha, "stages": states, "actions": actions,
        "execution_complete": all(s == "success" for s in states.values()),
        "unavailable_model_tracks": manifest["blocked_candidates"],
        "translation_quality_certified": False,
        "glossary_quality_certified": False,
        "apple_real_translation_executed": False,
        "field_ready": False,
    }


def sources(api: GitHub, event_name: str, event: dict, manifest: dict) -> list[str]:
    if event_name == "push":
        sha = str(event.get("after") or "")
        return [sha] if SHA.fullmatch(sha) else []
    if event_name == "workflow_run":
        title = str((event.get("workflow_run") or {}).get("display_title") or "")
        m = TITLE.fullmatch(title)
        return [m.group(2)] if m else []
    if event_name == "schedule":
        found: list[str] = []
        for row in api.runs(manifest["stages"][0]):
            m = TITLE.fullmatch(str(row.get("display_title") or ""))
            if m and m.group(1) == "catalog" and m.group(2) not in found:
                found.append(m.group(2))
            if len(found) == 3:
                break
        return found
    return []


def frozen_preregistration(api: GitHub, sha: str) -> dict:
    """Use exact source's study plan, never the latest mutable main plan."""
    if not SHA.fullmatch(sha):
        raise ValueError("invalid source SHA for frozen preregistration")
    entry = api.api(
        "GET",
        "/contents/benchmarks/translation-conveyor-preregistration.json"
        + "?ref=" + sha,
    )
    if entry.get("encoding") != "base64":
        raise ValueError("GitHub returned unexpected preregistration encoding")
    payload = base64.b64decode(entry["content"], validate=False)
    plan = json.loads(payload)
    if plan.get("schema_version") != 1:
        raise ValueError("unsupported frozen experiment manifest schema")
    return plan


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--event-path", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--state-out", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    plan = json.loads(args.manifest.read_text())
    api = GitHub(args.repository)
    event = json.loads(args.event_path.read_text())
    reports = []
    for sha in sources(api, args.event_name, event, plan):
        source_plan = frozen_preregistration(api, sha)
        item = advance(api, sha, source_plan, args.execute)
        item["preregistration_sha256"] = hashlib.sha256(
            json.dumps(source_plan, sort_keys=True).encode()
        ).hexdigest()
        reports.append(item)
    report = {"event": args.event_name, "results": reports}
    args.state_out.parent.mkdir(parents=True, exist_ok=True)
    args.state_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()

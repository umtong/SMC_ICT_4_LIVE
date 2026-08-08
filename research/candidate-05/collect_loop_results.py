#!/usr/bin/env python3
"""Collect latest completed Candidate 05 workflow decisions from GitHub Actions.

The collector is intentionally independent of local backtest logic. It queries
GitHub's authoritative run metadata, downloads immutable artifacts, parses only
predefined decision files, and writes one branch report. Missing or incomplete
runs remain WAIT rather than being guessed from logs.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from typing import Any


WORKFLOWS = {
    "v55_dev": "candidate-05-v55-spot-led-30d.yml",
    "v55_loop": "candidate-05-v55-diagnostic-loop.yml",
    "v56_dev": "candidate-05-v56-early-flow-91d.yml",
    "v56_oos": "candidate-05-v56-oos-comparison.yml",
    "v58_dev": "candidate-05-v58-forced-basis-91d.yml",
    "v58_oos": "candidate-05-v58-oos-comparison.yml",
    "v47_replay": "candidate-05-v47-replay-fixed.yml",
    "v48b_replay": "candidate-05-v48b-replay-fixed.yml",
}
PREFERRED_DECISIONS = {
    "v55_dev": "v55_decision.json",
    "v55_loop": "loop_decision.json",
    "v56_dev": "v56_decision.json",
    "v56_oos": "paired_decision.json",
    "v58_dev": "v58_decision.json",
    "v58_oos": "paired_decision.json",
    "v47_replay": "replay_decision.json",
    "v48b_replay": "replay_decision.json",
}
DECISION_NAMES = frozenset(PREFERRED_DECISIONS.values())
TERMINAL_CONCLUSIONS = frozenset(
    {"success", "failure", "cancelled", "timed_out", "action_required", "neutral"},
)


class GitHubApi:
    def __init__(self, *, repository: str, token: str) -> None:
        self.repository = repository
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "candidate-05-loop-collector",
        }

    def json(self, url: str) -> dict[str, Any]:
        request = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.load(response)

    def bytes(self, url: str, destination: Path) -> None:
        request = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(request, timeout=240) as response, destination.open("wb") as stream:
            shutil.copyfileobj(response, stream)


def latest_completed_run(
    *,
    api: GitHubApi,
    workflow: str,
    branch: str,
) -> dict[str, Any] | None:
    encoded_branch = urllib.parse.quote(branch, safe="")
    url = (
        f"https://api.github.com/repos/{api.repository}/actions/workflows/{workflow}/runs"
        f"?branch={encoded_branch}&status=completed&per_page=20"
    )
    runs = api.json(url).get("workflow_runs", [])
    return runs[0] if runs else None


def download_artifacts(
    *,
    api: GitHubApi,
    run: dict[str, Any],
    destination: Path,
) -> list[dict[str, Any]]:
    destination.mkdir(parents=True, exist_ok=True)
    values: list[dict[str, Any]] = []
    for artifact in api.json(run["artifacts_url"]).get("artifacts", []):
        if artifact.get("expired"):
            continue
        archive = destination / f"artifact-{artifact['id']}.zip"
        api.bytes(artifact["archive_download_url"], archive)
        extract = destination / f"artifact-{artifact['id']}"
        extract.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(extract)
        archive.unlink()
        values.append(
            {
                "id": artifact["id"],
                "name": artifact["name"],
                "digest": artifact.get("digest"),
                "extract": str(extract),
            },
        )
    return values


def load_decision(
    *,
    key: str,
    run_dir: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    candidates = sorted(
        path
        for path in run_dir.rglob("*.json")
        if path.name in DECISION_NAMES
    )
    if not candidates:
        return None, None
    preferred = PREFERRED_DECISIONS[key]
    selected = next((path for path in candidates if path.name == preferred), candidates[0])
    try:
        return json.loads(selected.read_text()), str(selected)
    except Exception as exc:
        return {
            "classification": "DECISION_PARSE_FAILURE",
            "error": f"{type(exc).__name__}: {exc}",
        }, str(selected)


def decision_classification(value: dict[str, Any] | None) -> str | None:
    return None if value is None else value.get("classification")


def replay_positive(value: dict[str, Any] | None) -> bool:
    """Conservative generic screen for repaired legacy experiments.

    A legacy replay is not promoted to composition here because its strategy
    contract differs. This flag only says market logic executed with positive,
    non-liquidated metrics and therefore deserves a dedicated paired OOS run.
    """
    if not value or not value.get("metrics"):
        return False
    for item in value["metrics"]:
        metrics = item.get("value") or {}
        try:
            integrity = (
                int(metrics.get("liquidations", 0)) == 0
                and int(metrics.get("order_rejections", 0)) == 0
                and int(metrics.get("order_denials", 0)) == 0
                and float(metrics.get("min_equity", 0.0)) > 0.0
            )
            positive = (
                float(metrics.get("total_return", 0.0)) > 0.0
                and int(metrics.get("trades", 0)) >= 3
            )
        except (TypeError, ValueError):
            continue
        if integrity and positive:
            return True
    return False


def collect(
    *,
    repository: str,
    branch: str,
    token: str,
    scratch: Path,
    report_dir: Path,
) -> dict[str, Any]:
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    api = GitHubApi(repository=repository, token=token)

    inventory: dict[str, Any] = {}
    decisions: dict[str, Any] = {}
    for key, workflow in WORKFLOWS.items():
        try:
            run = latest_completed_run(api=api, workflow=workflow, branch=branch)
        except urllib.error.HTTPError as exc:
            inventory[key] = {
                "workflow": workflow,
                "available": False,
                "error": f"HTTP_{exc.code}",
            }
            decisions[key] = None
            continue
        if run is None:
            inventory[key] = {
                "workflow": workflow,
                "available": False,
                "error": "NO_COMPLETED_RUN",
            }
            decisions[key] = None
            continue
        run_dir = scratch / key
        try:
            artifacts = download_artifacts(api=api, run=run, destination=run_dir)
            decision, decision_path = load_decision(key=key, run_dir=run_dir)
        except Exception as exc:
            artifacts = []
            decision = {
                "classification": "ARTIFACT_COLLECTION_FAILURE",
                "error": f"{type(exc).__name__}: {exc}",
            }
            decision_path = None
        inventory[key] = {
            "workflow": workflow,
            "available": bool(artifacts),
            "run_id": run["id"],
            "run_number": run["run_number"],
            "head_sha": run["head_sha"],
            "conclusion": run["conclusion"],
            "created_at": run["created_at"],
            "updated_at": run["updated_at"],
            "artifacts": artifacts,
            "decision_path": decision_path,
        }
        decisions[key] = decision

    v55 = decisions.get("v55_loop") or decisions.get("v55_dev")
    v56 = decisions.get("v56_oos")
    v58 = decisions.get("v58_oos")
    promotion = {
        "v55_spot_price_discovery": (
            decision_classification(v55)
            == "V55_PRICE_DISCOVERY_FAMILY_PASSED_DEV_OOS_AND_CONTINUOUS"
        ),
        "v56_early_flow_core": (
            decision_classification(v56)
            == "V56_FROZEN_PHASE_GATE_REPLICATED_OUT_OF_SAMPLE"
        ),
        "v58_forced_basis_reversion": (
            decision_classification(v58)
            == "V58_FORCED_BASIS_EDGE_REPLICATED_OUT_OF_SAMPLE"
        ),
    }
    promoted = [name for name, passed in promotion.items() if passed]
    completed = all(
        inventory.get(key, {}).get("conclusion") in TERMINAL_CONCLUSIONS
        and inventory.get(key, {}).get("run_id")
        for key in WORKFLOWS
    )
    legacy_followups = {
        "v47_relative_value_needs_paired_oos": replay_positive(decisions.get("v47_replay")),
        "v48b_session_value_needs_paired_oos": replay_positive(decisions.get("v48b_replay")),
    }
    summary = {
        "schema": "candidate-05-loop-results-v2",
        "repository": repository,
        "branch": branch,
        "inventory": inventory,
        "decisions": decisions,
        "promotion": promotion,
        "promoted_components": promoted,
        "legacy_followups": legacy_followups,
        "all_expected_complete": completed,
        "next_action": (
            "RUN_PROMOTED_COMPOSITE_ON_FINAL_UNTOUCHED_PATH"
            if promoted
            else "RUN_POSITIVE_LEGACY_PAIRED_OOS"
            if any(legacy_followups.values())
            else "NO_COMPONENT_PASSED_OOS_CONTINUE_NEW_CAUSAL_FAMILIES"
            if completed
            else "WAIT_FOR_REMAINING_COMPONENT_RUNS"
        ),
    }
    (report_dir / "inventory.json").write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n",
    )
    (report_dir / "latest.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=True) + "\n",
    )

    lines = [
        "# Candidate 05 loop results",
        "",
        f"All expected runs complete: `{completed}`",
        f"Next action: `{summary['next_action']}`",
        "",
        "## Promotion state",
        "",
    ]
    for name, passed in promotion.items():
        lines.append(f"- `{name}`: **{'PASS' if passed else 'FAIL/WAIT'}**")
    lines.extend(["", "## Legacy paired-OOS follow-ups", ""])
    for name, needed in legacy_followups.items():
        lines.append(f"- `{name}`: **{'YES' if needed else 'NO/WAIT'}**")
    lines.extend(["", "## Latest classifications", ""])
    for key in sorted(decisions):
        lines.append(f"- `{key}`: `{decision_classification(decisions[key])}`")
    lines.extend(["", "## Runs", ""])
    for key in sorted(inventory):
        item = inventory[key]
        lines.append(
            f"- `{key}`: run `{item.get('run_id')}`, conclusion `{item.get('conclusion')}`, "
            f"sha `{str(item.get('head_sha', ''))[:12]}`",
        )
    (report_dir / "latest.md").write_text("\n".join(lines) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--token-env", default="GH_TOKEN")
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    token = os.environ.get(args.token_env)
    if not token:
        raise RuntimeError(f"missing token environment variable: {args.token_env}")
    result = collect(
        repository=args.repository,
        branch=args.branch,
        token=token,
        scratch=args.scratch,
        report_dir=args.report_dir,
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=True))


if __name__ == "__main__":
    main()

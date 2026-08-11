#!/usr/bin/env python3
"""Collect compact JSON evidence from named GitHub Actions workflows.

The research environment intentionally keeps large market artifacts out of Git.
This helper downloads the latest Actions artifact for each requested workflow
and commits only run metadata, comparison.json, and per-variant metrics.json.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile


def api_json(url: str, token: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "candidate-easychart-evidence-collector",
        },
    )
    with urlopen(request, timeout=60) as response:
        return json.load(response)


def api_bytes(url: str, token: str) -> bytes:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "candidate-easychart-evidence-collector",
        },
    )
    with urlopen(request, timeout=180) as response:
        return response.read()


def latest_completed_run(repo: str, workflow: str, branch: str, token: str) -> dict[str, Any] | None:
    url = (
        f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/runs"
        f"?branch={branch.replace('/', '%2F')}&status=completed&per_page=10"
    )
    payload = api_json(url, token)
    runs = payload.get("workflow_runs", [])
    return runs[0] if runs else None


def wait_for_run(
    repo: str,
    workflow: str,
    branch: str,
    token: str,
    *,
    timeout_seconds: int,
    poll_seconds: int,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        run = latest_completed_run(repo, workflow, branch, token)
        if run is not None:
            return run
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll_seconds)


def copy_compact_json(root: Path, destination: Path) -> list[str]:
    copied: list[str] = []
    candidates = sorted(root.rglob("comparison.json"))
    for index, source in enumerate(candidates):
        relative = Path("comparison.json") if index == 0 else Path(f"comparison-{index}.json")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(str(relative))
    for source in sorted(root.rglob("metrics.json")):
        # Keep the variant directory name plus one parent if needed to avoid
        # collisions from nested artifact roots.
        variant = source.parent.name
        relative = Path("variants") / variant / "metrics.json"
        target = destination / relative
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(str(relative))
    return copied


def collect_one(
    *,
    repo: str,
    workflow: str,
    branch: str,
    token: str,
    output_root: Path,
    timeout_seconds: int,
    poll_seconds: int,
) -> dict[str, Any]:
    stem = Path(workflow).stem
    destination = output_root / stem
    destination.mkdir(parents=True, exist_ok=True)
    run = wait_for_run(
        repo,
        workflow,
        branch,
        token,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )
    if run is None:
        result = {"workflow": workflow, "status": "NO_COMPLETED_RUN_BEFORE_TIMEOUT"}
        (destination / "run.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        return result

    result: dict[str, Any] = {
        "workflow": workflow,
        "run_id": run["id"],
        "run_number": run.get("run_number"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "head_sha": run.get("head_sha"),
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "html_url": run.get("html_url"),
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": [],
    }
    artifacts_payload = api_json(
        f"https://api.github.com/repos/{repo}/actions/runs/{run['id']}/artifacts?per_page=100",
        token,
    )
    for artifact in artifacts_payload.get("artifacts", []):
        if artifact.get("expired"):
            continue
        artifact_result = {
            "id": artifact["id"],
            "name": artifact["name"],
            "size_in_bytes": artifact.get("size_in_bytes"),
            "created_at": artifact.get("created_at"),
        }
        try:
            raw = api_bytes(
                f"https://api.github.com/repos/{repo}/actions/artifacts/{artifact['id']}/zip",
                token,
            )
            with tempfile.TemporaryDirectory() as temporary:
                temp_root = Path(temporary)
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    archive.extractall(temp_root)
                artifact_destination = destination / "artifact" / artifact["name"]
                artifact_destination.mkdir(parents=True, exist_ok=True)
                artifact_result["copied_files"] = copy_compact_json(temp_root, artifact_destination)
        except (HTTPError, zipfile.BadZipFile, OSError) as exc:
            artifact_result["download_error"] = f"{type(exc).__name__}: {exc}"
        result["artifacts"].append(artifact_result)
    (destination / "run.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workflow", action="append", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=2700)
    parser.add_argument("--poll-seconds", type=int, default=30)
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("GITHUB_TOKEN is required")
    args.output.mkdir(parents=True, exist_ok=True)
    results = [
        collect_one(
            repo=args.repo,
            workflow=workflow,
            branch=args.branch,
            token=token,
            output_root=args.output,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
        )
        for workflow in args.workflow
    ]
    (args.output / "manifest.json").write_text(
        json.dumps({"workflows": results}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"workflows": results}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Run frozen V18 staged native evidence in the pinned job container.

All mutable work is isolated under /tmp. The controller verifies frozen V18
source identities, runs causal opportunity preflight, then uses the native
NautilusTrader path only. A valid weekly failure receives exactly one
state-removal ablation on the largest negative scenario contribution. Evidence
is committed atomically through GitHub's Git Data API before the workflow exits.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path
from typing import Any

REPOSITORY = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GITHUB_TOKEN"]
BRANCH = "research/candidate-03"
WORK_ROOT = Path("/tmp/candidate03-v18-staged")
REPO_ROOT = WORK_ROOT / "repo"
CONFIG = Path("research/candidate-03/nt_lvcfr_v18_config.json")
FROZEN_BLOBS = {
    "research/candidate-03/derive_nt_lvcfr_v18_signals.py": "aea81e9d9d5ce2f1d7d37484352ef0b90d2cb0df",
    "research/candidate-03/prepare_nt_lvcfr_v18.py": "162d22d37365513424611f7e66eb07d95fc16102",
    "research/candidate-03/rebuild_nt_lvcfr_bookticker_catalog.py": "27a18072a5a0d21b8d8b81cf0384a2b7336e4f70",
    "research/candidate-03/nt_lvcfr_v18_config.json": "6a15641340aa81ecd03d186ed478c50da6ee0294",
    "research/candidate-03/test_nt_lvcfr_v18.py": "4a0e096329d1bfca28abacfe384410d906eff5e9",
    "research/candidate-03/nt_lvcfr_strategy.py": "e4d00ae0c6fa1d24198c846bccb247baacdc0456",
    "research/candidate-03/run_nt_lvcfr.py": "74bb02f1b69ee31ce32ddfa47497bdd9770ac00b",
}
NEW_V18_STATES = {
    "L1_VACUUM_CONTINUATION",
    "L1_ABSORPTION_FULL_RANGE_CHOCH_REVERSAL",
}


def github_api(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "candidate-03-v18-staged-container",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        raw = response.read()
    return json.loads(raw.decode()) if raw else None


def fetch_branch() -> None:
    shutil.rmtree(WORK_ROOT, ignore_errors=True)
    WORK_ROOT.mkdir(parents=True)
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/tarball/{BRANCH}",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "candidate-03-v18-staged-container",
        },
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        archive_bytes = response.read()
    extract_root = WORK_ROOT / "extract"
    extract_root.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        members = archive.getmembers()
        roots = {Path(member.name).parts[0] for member in members if Path(member.name).parts}
        if len(roots) != 1:
            raise RuntimeError(f"unexpected archive roots: {roots}")
        for member in members:
            target = Path(member.name)
            if target.is_absolute() or ".." in target.parts:
                raise RuntimeError(f"unsafe archive member: {member.name}")
        archive.extractall(extract_root, filter="data")
    shutil.move(str(extract_root / next(iter(roots))), REPO_ROOT)


def git_blob_sha(path: Path) -> str:
    payload = path.read_bytes()
    return hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest()


def verify_frozen_sources() -> None:
    for relative, expected in FROZEN_BLOBS.items():
        actual = git_blob_sha(REPO_ROOT / relative)
        if actual != expected:
            raise RuntimeError(f"frozen source mismatch: {relative}: {actual} != {expected}")


def environment() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = f"{REPO_ROOT / 'src'}:{REPO_ROOT / 'research/candidate-03'}"
    return env


def run(command: list[str], *, log_path: Path | None = None, check: bool = True) -> int:
    print("+", " ".join(command), flush=True)
    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="", flush=True)
        lines.append(line)
    status = process.wait()
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("".join(lines), encoding="utf-8")
    if check and status != 0:
        raise subprocess.CalledProcessError(status, command)
    return status


def verify_runtime() -> None:
    run(["smc4", "doctor"])
    for test in (
        "test_nt_lvcfr.py",
        "test_nt_lvcfr_trade_proxy.py",
        "test_nt_lvcfr_v13.py",
        "test_nt_lvcfr_v18.py",
    ):
        run([sys.executable, f"research/candidate-03/{test}"])


def prepare_schedule(week: str, prepared: Path, output: Path) -> int:
    prepared.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    run(
        [
            sys.executable,
            "research/candidate-03/prepare_nt_lvcfr_v18.py",
            "--week-start",
            week,
            "--output",
            str(prepared),
            "--config",
            str(CONFIG),
        ],
        log_path=output / "preparation.log",
    )
    shutil.copy2(prepared / "signals.json", prepared / "signals-v1.json")
    run(
        [
            sys.executable,
            "research/candidate-03/derive_nt_lvcfr_v18_signals.py",
            "--prepared-root",
            str(prepared),
            "--output-manifest",
            str(output / "v18_signal_manifest.json"),
        ],
        log_path=output / "derivation.log",
    )
    shutil.copy2(prepared / "signals.json", output / "signals.json")
    shutil.copy2(prepared / "data_manifest.json", output / "data_manifest.json")
    signals = json.loads((prepared / "signals.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "v18_signal_manifest.json").read_text(encoding="utf-8"))
    preflight = {
        "candidate": manifest["candidate"],
        "week_start": week,
        "engine_status": "causal_opportunity_preflight_only_no_backtest",
        "derived_signal_count": len(signals),
        "minimum_required_episodes": 8,
        "opportunity_gate_reachable": len(signals) >= 8,
        "state_counts": manifest["state_counts"],
        "l1_routing_counts": manifest["l1_routing_counts"],
        "threshold_policy": manifest["threshold_policy"],
        "performance_metrics_calculated": False,
    }
    (output / "preflight_summary.json").write_text(
        json.dumps(preflight, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(preflight, indent=2, sort_keys=True))
    return len(signals)


def run_native(week: str, prepared: Path, output: Path) -> int:
    run(
        [
            sys.executable,
            "research/candidate-03/rebuild_nt_lvcfr_bookticker_catalog.py",
            "--prepared-root",
            str(prepared),
            "--config",
            str(CONFIG),
        ],
        log_path=output / "catalog_rebuild.log",
    )
    run(
        [
            sys.executable,
            "research/candidate-03/run_nt_lvcfr.py",
            "--week-start",
            week,
            "--prepared-root",
            str(prepared),
            "--output",
            str(output),
            "--config",
            str(CONFIG),
        ],
        log_path=output / "backtest.log",
    )
    gate_status = run(
        [
            sys.executable,
            "research/candidate-03/gate_nt_lvcfr.py",
            str(output / "metrics.json"),
            "--config",
            str(CONFIG),
        ],
        log_path=output / "gate.json",
        check=False,
    )
    (output / "gate_status.txt").write_text(f"{gate_status}\n", encoding="utf-8")
    run(
        [
            sys.executable,
            "research/candidate-03/summarize_nt_lvcfr_states.py",
            "--metrics",
            str(output / "metrics.json"),
            "--episodes",
            str(output / "episodes.csv"),
            "--signals",
            str(output / "signals.json"),
            "--output",
            str(output / "state_attribution.json"),
        ]
    )
    return gate_status


def select_ablation_state(output: Path) -> str | None:
    summary = json.loads((output / "state_attribution.json").read_text(encoding="utf-8"))
    rows = summary["by_scenario_kind"]
    negative_novel = [
        (state, float(row["native_account_pnl"]))
        for state, row in rows.items()
        if state in NEW_V18_STATES and float(row["native_account_pnl"]) < 0.0
    ]
    if negative_novel:
        return min(negative_novel, key=lambda item: item[1])[0]
    negative_all = [
        (state, float(row["native_account_pnl"]))
        for state, row in rows.items()
        if float(row["native_account_pnl"]) < 0.0
    ]
    return min(negative_all, key=lambda item: item[1])[0] if negative_all else None


def run_ablation(
    week: str,
    prepared: Path,
    full_output: Path,
    ablation_output: Path,
    removed_state: str,
) -> int:
    ablation_output.mkdir(parents=True, exist_ok=True)
    full_signals = json.loads((full_output / "signals.json").read_text(encoding="utf-8"))
    kept = [
        signal
        for signal in full_signals
        if str(signal.get("scenario_kind")) != removed_state
    ]
    shutil.copy2(prepared / "signals.json", prepared / "signals-v18-full.json")
    (prepared / "signals.json").write_text(
        json.dumps(kept, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (ablation_output / "signals.json").write_text(
        json.dumps(kept, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v18-state-ablation",
        "week_start": week,
        "ablation": "REMOVE_ONE_SCENARIO_KIND",
        "removed_scenario_kind": removed_state,
        "full_signal_count": len(full_signals),
        "kept_signal_count": len(kept),
        "diagnostic_only": True,
    }
    (ablation_output / "ablation_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not kept:
        return 1
    return run_native(week, prepared, ablation_output)


def copy_results(source: Path, destination: Path) -> None:
    if not source.is_dir():
        return
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True)
    allowed = {".json", ".jsonl", ".csv", ".log", ".txt"}
    for path in source.iterdir():
        if path.is_file() and path.suffix in allowed:
            shutil.copy2(path, destination / path.name)


def commit_results(status: dict[str, Any]) -> str:
    result_root = REPO_ROOT / "research/candidate-03/results"
    (result_root / "V18_STAGED_STATUS.json").write_text(
        json.dumps(status, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    ref = github_api("GET", f"/git/ref/heads/{BRANCH}")
    parent = ref["object"]["sha"]
    parent_commit = github_api("GET", f"/git/commits/{parent}")
    entries: list[dict[str, str]] = []
    for path in sorted(item for item in result_root.rglob("*") if item.is_file()):
        blob = github_api(
            "POST",
            "/git/blobs",
            {
                "content": base64.b64encode(path.read_bytes()).decode(),
                "encoding": "base64",
            },
        )
        entries.append(
            {
                "path": path.relative_to(REPO_ROOT).as_posix(),
                "mode": "100644",
                "type": "blob",
                "sha": blob["sha"],
            }
        )
    tree = github_api(
        "POST",
        "/git/trees",
        {"base_tree": parent_commit["tree"]["sha"], "tree": entries},
    )
    commit = github_api(
        "POST",
        "/git/commits",
        {
            "message": "candidate-03: record staged V18 native evidence",
            "tree": tree["sha"],
            "parents": [parent],
        },
    )
    github_api(
        "PATCH",
        f"/git/refs/heads/{BRANCH}",
        {"sha": commit["sha"], "force": False},
    )
    print(json.dumps({"committed_sha": commit["sha"], **status}, indent=2, sort_keys=True))
    return commit["sha"]


def execute_week(week: str, name: str) -> tuple[int, int, Path, Path]:
    stage = REPO_ROOT / f"artifacts/candidate-03/v18-staged/{name}"
    prepared = stage / "prepared"
    output = stage / "output"
    signal_count = prepare_schedule(week, prepared, output)
    if signal_count < 8:
        (output / "gate_status.txt").write_text("1\n", encoding="utf-8")
        return signal_count, 1, prepared, output
    gate_status = run_native(week, prepared, output)
    return signal_count, gate_status, prepared, output


def main() -> int:
    fetch_branch()
    verify_frozen_sources()
    verify_runtime()

    status: dict[str, Any] = {
        "candidate": "candidate-03-nt-lvcfr-v18-order-book-resilience-router",
        "frozen_source_blobs": FROZEN_BLOBS,
        "completed_stages": [],
    }
    final_status = 1
    failure_output: Path | None = None
    failure_prepared: Path | None = None
    failure_week: str | None = None
    failure_name: str | None = None

    for week, name in (
        ("2024-01-08", "development-1"),
        ("2025-06-23", "development-2"),
        ("2022-05-16", "validation-3"),
    ):
        signal_count, gate_status, prepared, output = execute_week(week, name)
        status[f"{name}_signal_count"] = signal_count
        status[f"{name}_passed"] = gate_status == 0
        status["completed_stages"].append(name)
        if gate_status != 0:
            failure_output = output
            failure_prepared = prepared
            failure_week = week
            failure_name = name
            final_status = 1
            break
        final_status = 0

    if failure_output is not None and (failure_output / "state_attribution.json").exists():
        removed = select_ablation_state(failure_output)
        status["ablation_removed_state"] = removed
        if removed is not None and failure_prepared is not None and failure_week is not None:
            ablation_output = failure_output.parent / "ablation-output"
            ablation_status = run_ablation(
                failure_week,
                failure_prepared,
                failure_output,
                ablation_output,
                removed,
            )
            status["ablation_passed"] = ablation_status == 0
            status["ablation_stage"] = failure_name

    results = REPO_ROOT / "research/candidate-03/results"
    stage_root = REPO_ROOT / "artifacts/candidate-03/v18-staged"
    for name in ("development-1", "development-2", "validation-3"):
        output = stage_root / name / "output"
        if output.exists():
            copy_results(output, results / f"v18-{name}")
        ablation = stage_root / name / "ablation-output"
        if ablation.exists():
            copy_results(ablation, results / f"v18-{name}-ablation")
    commit_results(status)
    return final_status


if __name__ == "__main__":
    raise SystemExit(main())

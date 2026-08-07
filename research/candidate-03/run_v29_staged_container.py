#!/usr/bin/env python3
"""Run V29 cross-asset consensus catch-up on frozen unseen BTC weeks."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import shutil
import types
import urllib.request
from pathlib import Path
from typing import Any

TOKEN = os.environ["GITHUB_TOKEN"]
REPOSITORY = os.environ["GITHUB_REPOSITORY"]
REF = os.environ.get("GITHUB_SHA", "research/candidate-03")
V24_CONTROLLER = "research/candidate-03/run_v24_staged_container.py"
CONFIG = Path("research/candidate-03/nt_lvcfr_v29_config.json")
WEEKS = (
    ("2023-06-12", "unseen-1"),
    ("2024-09-16", "unseen-2"),
    ("2025-01-20", "unseen-3"),
)


def fetch_raw(path: str) -> str:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/contents/{path}?ref={REF}",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github.raw+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "candidate-03-v29-cross-asset",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read().decode("utf-8")


def load_v24_module() -> types.ModuleType:
    source = fetch_raw(V24_CONTROLLER)
    terminal = "raise SystemExit(main())"
    if terminal not in source:
        raise RuntimeError("V24 terminal call not found")
    module = types.ModuleType("candidate03_v24_controller")
    module.__file__ = V24_CONTROLLER
    exec(compile(source.rsplit(terminal, 1)[0], V24_CONTROLLER, "exec"), module.__dict__)
    return module


v24 = load_v24_module()
base = v24.base
v24.CONFIG = CONFIG
base.CONFIG = CONFIG


def verify_runtime() -> str:
    base.run(["smc4", "doctor"])
    base.run(
        [
            base.sys.executable,
            "research/candidate-03/apply_nt_lvcfr_cost_viability_patch.py",
            "research/candidate-03/nt_lvcfr_strategy.py",
        ]
    )
    for test in (
        "test_nt_lvcfr.py",
        "test_nt_lvcfr_trade_proxy.py",
        "test_nt_lvcfr_cost_viability.py",
        "test_nt_lvcfr_v29.py",
    ):
        base.run([base.sys.executable, f"research/candidate-03/{test}"])
    return hashlib.sha256(
        (base.REPO_ROOT / "research/candidate-03/nt_lvcfr_strategy.py").read_bytes()
    ).hexdigest()


def prepare_schedule(week: str, prepared: Path, output: Path) -> int:
    prepared.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    base.run(
        [
            base.sys.executable,
            "research/candidate-03/prepare_nt_lvcfr_v19.py",
            "--week-start",
            week,
            "--output",
            str(prepared),
            "--config",
            str(CONFIG),
        ],
        log_path=output / "preparation.log",
    )
    base.run(
        [
            base.sys.executable,
            "research/candidate-03/derive_nt_lvcfr_v29_signals.py",
            "--week-start",
            week,
            "--prepared-root",
            str(prepared),
            "--output-manifest",
            str(output / "v29_signal_manifest.json"),
        ],
        log_path=output / "v29_derivation.log",
    )
    signals = json.loads((prepared / "signals.json").read_text(encoding="utf-8"))
    (output / "signals.json").write_text(
        json.dumps(signals, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = json.loads((output / "v29_signal_manifest.json").read_text(encoding="utf-8"))
    preflight = {
        "candidate": "candidate-03-nt-lvcfr-v29-cross-asset-consensus-catchup",
        "week_start": week,
        "warmup_start": manifest["warmup_start"],
        "v29_signal_count": len(signals),
        "signals_per_day": manifest["signals_per_day"],
        "state_counts": manifest["state_counts"],
        "no_trade_reasons": manifest["no_trade_reasons"],
        "minimum_required_episodes": 8,
        "opportunity_gate_reachable": len(signals) >= 8,
        "symbols": manifest["symbols"],
        "selection_policy": manifest["selection_policy"],
        "performance_metrics_calculated": False,
    }
    (output / "preflight_summary.json").write_text(
        json.dumps(preflight, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(preflight, indent=2, sort_keys=True))
    return len(signals)


def copy_essential(source: Path, destination: Path) -> list[Path]:
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for name in (
        "metrics.json", "gate.json", "episodes.csv", "positions.csv",
        "state_attribution.json", "preflight_summary.json", "v29_signal_manifest.json",
    ):
        path = source / name
        if path.exists():
            target = destination / name
            shutil.copy2(path, target)
            copied.append(target)
    return copied


def result_row(
    *, week: str, stage: str, signals: int, output: Path, gate_status: int | None
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "week_start": week,
        "stage": stage,
        "signals": signals,
        "official_gate_status": gate_status,
        "official_gate_passed": gate_status == 0,
    }
    metrics_path = output / "metrics.json"
    if metrics_path.exists():
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        for key in (
            "initial_nav", "final_nav", "net_return", "daily_geometric_growth",
            "max_drawdown", "independent_episodes", "wins", "losses",
            "win_rate", "mean_episode_pnl", "native_orders", "native_positions",
            "entry_rejections", "incomplete_at_end", "target_met",
        ):
            row[key] = metrics.get(key)
    gate_path = output / "gate.json"
    if gate_path.exists():
        row["official_gate"] = json.loads(gate_path.read_text(encoding="utf-8"))
    return row


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [row for row in rows if row.get("final_nav") is not None]
    if not completed:
        return {"completed_week_count": 0}
    log_growth = sum(
        math.log(float(row["final_nav"]) / float(row["initial_nav"]))
        for row in completed
    )
    episodes = sum(int(row.get("independent_episodes") or 0) for row in completed)
    wins = sum(int(row.get("wins") or 0) for row in completed)
    days = len(completed) * 7
    return {
        "completed_week_count": len(completed),
        "aggregate_nav_multiple": math.exp(log_growth),
        "aggregate_daily_geometric_growth": math.exp(log_growth / days) - 1.0,
        "pooled_episodes": episodes,
        "pooled_wins": wins,
        "pooled_win_rate": wins / episodes if episodes else 0.0,
        "worst_week_max_drawdown": max(float(row["max_drawdown"]) for row in completed),
    }


def commit_results(status: dict[str, Any], files: list[Path]) -> str:
    root = base.REPO_ROOT / "research/candidate-03/results"
    root.mkdir(parents=True, exist_ok=True)
    status_path = root / "V29_STABILITY_STATUS.json"
    status_path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    files = [status_path, *files]
    ref = base.github_api("GET", f"/git/ref/heads/{base.BRANCH}")
    parent = ref["object"]["sha"]
    parent_commit = base.github_api("GET", f"/git/commits/{parent}")
    entries = []
    for path in files:
        blob = base.github_api(
            "POST", "/git/blobs",
            {"content": base64.b64encode(path.read_bytes()).decode(), "encoding": "base64"},
        )
        entries.append({
            "path": path.relative_to(base.REPO_ROOT).as_posix(),
            "mode": "100644", "type": "blob", "sha": blob["sha"],
        })
    tree = base.github_api(
        "POST", "/git/trees",
        {"base_tree": parent_commit["tree"]["sha"], "tree": entries},
    )
    commit = base.github_api(
        "POST", "/git/commits",
        {"message": "candidate-03: record V29 unseen-week evidence", "tree": tree["sha"], "parents": [parent]},
    )
    base.github_api(
        "PATCH", f"/git/refs/heads/{base.BRANCH}",
        {"sha": commit["sha"], "force": False},
    )
    return str(commit["sha"])


def main() -> int:
    base.fetch_branch()
    base.verify_frozen_sources()
    patched_sha = verify_runtime()
    stage_root = base.REPO_ROOT / "artifacts/candidate-03/v29-cross-asset-catchup"
    result_root = base.REPO_ROOT / "research/candidate-03/results"
    status: dict[str, Any] = {
        "candidate": "candidate-03-nt-lvcfr-v29-cross-asset-consensus-catchup",
        "risk_fraction": 0.03,
        "official_gate_unchanged": True,
        "patched_strategy_sha256": patched_sha,
        "week_selection_salt": "candidate-03|V29|cross-asset-consensus|unseen-freeze",
        "weeks": [],
    }
    files: list[Path] = []
    for week, stage in WEEKS:
        prepared = stage_root / stage / "prepared"
        output = stage_root / stage / "output"
        count = prepare_schedule(week, prepared, output)
        gate_status: int | None = None
        if count >= 8:
            gate_status = v24.run_native(week, prepared, output)
        row = result_row(
            week=week, stage=stage, signals=count,
            output=output, gate_status=gate_status,
        )
        status["weeks"].append(row)
        files.extend(copy_essential(output, result_root / f"v29-{stage}"))
        if gate_status != 0:
            break
    status["aggregate"] = aggregate(status["weeks"])
    promote = (
        len(status["weeks"]) == len(WEEKS)
        and all(bool(row["official_gate_passed"]) for row in status["weeks"])
    )
    status["decision"] = "PROMOTE_TO_LONG_HORIZON" if promote else "REJECT_OR_REDESIGN_V29"
    status["failure_interpretation"] = (
        None if promote else
        "V29 is a single-state cross-asset catch-up hypothesis; removing it leaves no tradable state."
    )
    commit_sha = commit_results(status, files)
    print(json.dumps({"committed_sha": commit_sha, **status}, indent=2, sort_keys=True))
    return 0


raise SystemExit(main())

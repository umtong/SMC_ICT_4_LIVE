#!/usr/bin/env python3
"""Run V26 failed-reversal-trap continuation on frozen BTC weeks.

The controller first derives the already-tested V24 reversal opportunity but
never trades it. V26 waits for that thesis to fail causally, then requires a
failed-gap retest and completed continuation rejection before native entry.
Only NautilusTrader performs orders, fills, costs, funding, positions and NAV.
"""
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
CONFIG = Path("research/candidate-03/nt_lvcfr_v26_config.json")
WEEKS = (
    ("2024-01-08", "development-1"),
    ("2025-06-23", "development-2"),
    ("2022-05-16", "validation-3"),
)


def fetch_raw(path: str) -> str:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/contents/{path}?ref={REF}",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github.raw+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "candidate-03-v26-failed-reversal-trap",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read().decode("utf-8")


def load_v24_module() -> types.ModuleType:
    source = fetch_raw(V24_CONTROLLER)
    terminal = "raise SystemExit(main())"
    if terminal not in source:
        raise RuntimeError("V24 controller terminal call not found")
    source = source.rsplit(terminal, 1)[0]
    module = types.ModuleType("candidate03_v24_controller")
    module.__file__ = V24_CONTROLLER
    exec(compile(source, V24_CONTROLLER, "exec"), module.__dict__)
    return module


v24 = load_v24_module()
base = v24.base
v24.CONFIG = CONFIG
base.CONFIG = CONFIG


def verify_runtime() -> str:
    patched_sha = v24.verify_runtime()
    base.run(
        [
            base.sys.executable,
            "research/candidate-03/test_nt_lvcfr_v26.py",
        ]
    )
    return patched_sha


def prepare_v26_schedule(week: str, prepared: Path, output: Path) -> int:
    v24.prepare_v24_schedule(week, prepared, output)
    source = prepared / "signals-v24.json"
    shutil.copy2(prepared / "signals.json", source)
    base.run(
        [
            base.sys.executable,
            "research/candidate-03/derive_nt_lvcfr_v26_signals.py",
            "--prepared-root",
            str(prepared),
            "--source-signals",
            str(source),
            "--output-signals",
            str(prepared / "signals.json"),
            "--output-manifest",
            str(output / "v26_signal_manifest.json"),
        ],
        log_path=output / "v26_derivation.log",
    )
    signals = json.loads((prepared / "signals.json").read_text(encoding="utf-8"))
    (output / "signals.json").write_text(
        json.dumps(signals, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = json.loads(
        (output / "v26_signal_manifest.json").read_text(encoding="utf-8")
    )
    preflight = {
        "candidate": "candidate-03-nt-lvcfr-v26-failed-reversal-trap-continuation",
        "week_start": week,
        "engine_status": "causal_failed-reversal-trap_preflight_no_backtest",
        "source_v24_signal_count": manifest["source_signal_count"],
        "v26_signal_count": len(signals),
        "minimum_required_episodes": 8,
        "opportunity_gate_reachable": len(signals) >= 8,
        "source_state_counts": manifest["source_state_counts"],
        "output_state_counts": manifest["output_state_counts"],
        "no_trade_reasons": manifest["no_trade_reasons"],
        "minimum_gross_structural_rr": manifest["minimum_gross_structural_rr"],
        "median_gross_structural_rr": manifest["median_gross_structural_rr"],
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
        "metrics.json",
        "gate.json",
        "episodes.csv",
        "positions.csv",
        "state_attribution.json",
        "preflight_summary.json",
        "v26_signal_manifest.json",
    ):
        path = source / name
        if path.exists():
            target = destination / name
            shutil.copy2(path, target)
            copied.append(target)
    return copied


def compact_week_result(
    *,
    week: str,
    stage: str,
    signal_count: int,
    gate_status: int | None,
    output: Path,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "week_start": week,
        "stage": stage,
        "v26_signal_count": signal_count,
        "official_gate_status": gate_status,
        "official_gate_passed": gate_status == 0,
    }
    metrics_path = output / "metrics.json"
    if metrics_path.exists():
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        for key in (
            "initial_nav",
            "final_nav",
            "net_return",
            "daily_geometric_growth",
            "max_drawdown",
            "independent_episodes",
            "wins",
            "losses",
            "win_rate",
            "mean_episode_pnl",
            "native_orders",
            "native_positions",
            "entry_rejections",
            "incomplete_at_end",
            "target_met",
        ):
            result[key] = metrics.get(key)
    gate_path = output / "gate.json"
    if gate_path.exists():
        result["official_gate"] = json.loads(gate_path.read_text(encoding="utf-8"))
    manifest_path = output / "v26_signal_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        result["source_state_counts"] = manifest["source_state_counts"]
        result["output_state_counts"] = manifest["output_state_counts"]
        result["no_trade_reasons"] = manifest["no_trade_reasons"]
        result["median_gross_structural_rr"] = manifest["median_gross_structural_rr"]
    return result


def aggregate_results(weeks: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [row for row in weeks if row.get("final_nav") is not None]
    if not completed:
        return {"completed_week_count": 0}
    log_growth = sum(
        math.log(float(row["final_nav"]) / float(row["initial_nav"]))
        for row in completed
    )
    episodes = sum(int(row.get("independent_episodes") or 0) for row in completed)
    wins = sum(int(row.get("wins") or 0) for row in completed)
    total_days = len(completed) * 7
    return {
        "completed_week_count": len(completed),
        "total_evaluation_days": total_days,
        "aggregate_nav_multiple": math.exp(log_growth),
        "aggregate_daily_geometric_growth": math.exp(log_growth / total_days) - 1.0,
        "pooled_signals": sum(int(row.get("v26_signal_count") or 0) for row in completed),
        "pooled_episodes": episodes,
        "pooled_wins": wins,
        "pooled_win_rate": wins / episodes if episodes else 0.0,
        "worst_week_max_drawdown": max(float(row["max_drawdown"]) for row in completed),
        "all_completed_weeks_passed": all(bool(row["official_gate_passed"]) for row in completed),
    }


def commit_minimal_results(status: dict[str, Any], files: list[Path]) -> str:
    result_root = base.REPO_ROOT / "research/candidate-03/results"
    result_root.mkdir(parents=True, exist_ok=True)
    status_path = result_root / "V26_STABILITY_STATUS.json"
    status_path.write_text(
        json.dumps(status, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    files = [status_path, *files]
    ref = base.github_api("GET", f"/git/ref/heads/{base.BRANCH}")
    parent = ref["object"]["sha"]
    parent_commit = base.github_api("GET", f"/git/commits/{parent}")
    entries: list[dict[str, str]] = []
    for path in files:
        blob = base.github_api(
            "POST",
            "/git/blobs",
            {
                "content": base64.b64encode(path.read_bytes()).decode(),
                "encoding": "base64",
            },
        )
        entries.append(
            {
                "path": path.relative_to(base.REPO_ROOT).as_posix(),
                "mode": "100644",
                "type": "blob",
                "sha": blob["sha"],
            }
        )
    tree = base.github_api(
        "POST",
        "/git/trees",
        {"base_tree": parent_commit["tree"]["sha"], "tree": entries},
    )
    commit = base.github_api(
        "POST",
        "/git/commits",
        {
            "message": "candidate-03: record V26 failed-reversal-trap evidence",
            "tree": tree["sha"],
            "parents": [parent],
        },
    )
    base.github_api(
        "PATCH",
        f"/git/refs/heads/{base.BRANCH}",
        {"sha": commit["sha"], "force": False},
    )
    return str(commit["sha"])


def main() -> int:
    base.fetch_branch()
    base.verify_frozen_sources()
    patched_strategy_sha256 = verify_runtime()
    stage_root = base.REPO_ROOT / "artifacts/candidate-03/v26-failed-reversal-trap"
    result_root = base.REPO_ROOT / "research/candidate-03/results"
    status: dict[str, Any] = {
        "candidate": "candidate-03-nt-lvcfr-v26-failed-reversal-trap-continuation",
        "official_gate_unchanged": True,
        "risk_fraction": 0.03,
        "patched_strategy_sha256": patched_strategy_sha256,
        "entry_logic": (
            "V24 reversal opportunity without entry -> defended FVG far-edge failure with "
            "futures/spot flow -> failed-gap retest and completed continuation rejection -> native entry"
        ),
        "weeks": [],
    }
    committed_files: list[Path] = []
    for week, stage in WEEKS:
        prepared = stage_root / stage / "prepared"
        output = stage_root / stage / "output"
        signal_count = prepare_v26_schedule(week, prepared, output)
        gate_status: int | None = None
        if signal_count >= 8:
            gate_status = v24.run_native(week, prepared, output)
        row = compact_week_result(
            week=week,
            stage=stage,
            signal_count=signal_count,
            gate_status=gate_status,
            output=output,
        )
        status["weeks"].append(row)
        committed_files.extend(copy_essential(output, result_root / f"v26-{stage}"))
        if signal_count < 8 or gate_status != 0:
            break

    status["aggregate"] = aggregate_results(status["weeks"])
    all_three = len(status["weeks"]) == len(WEEKS)
    promote = all_three and all(bool(row["official_gate_passed"]) for row in status["weeks"])
    status["decision"] = (
        "PROMOTE_TO_LONG_HORIZON"
        if promote
        else "REJECT_OR_REDESIGN_V26"
    )
    status["failure_interpretation"] = (
        None
        if promote
        else "V26 is a single-state candidate; removing its only state is the required one-variable ablation and leaves no tradable hypothesis."
    )
    commit_sha = commit_minimal_results(status, committed_files)
    print(json.dumps({"committed_sha": commit_sha, **status}, indent=2, sort_keys=True))
    return 0


raise SystemExit(main())

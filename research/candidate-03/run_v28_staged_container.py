#!/usr/bin/env python3
"""Run V28 structural-state retest evaluation on unseen BTC weeks."""
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
CONFIG = Path("research/candidate-03/nt_lvcfr_v28_config.json")
WEEKS = (
    ("2022-11-21", "unseen-1"),
    ("2024-05-13", "unseen-2"),
    ("2021-08-16", "unseen-3"),
)


def fetch_raw(path: str) -> str:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/contents/{path}?ref={REF}",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github.raw+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "candidate-03-v28-structural-state",
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
    base.run(
        [
            base.sys.executable,
            "research/candidate-03/patch_v27_parser_v2.py",
            "research/candidate-03/derive_nt_lvcfr_v27_signals.py",
        ]
    )
    for test in (
        "test_nt_lvcfr.py",
        "test_nt_lvcfr_trade_proxy.py",
        "test_nt_lvcfr_cost_viability.py",
        "test_nt_lvcfr_v28.py",
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
            "research/candidate-03/derive_nt_lvcfr_v28_signals.py",
            "--week-start",
            week,
            "--prepared-root",
            str(prepared),
            "--output-manifest",
            str(output / "v28_signal_manifest.json"),
        ],
        log_path=output / "v28_derivation.log",
    )
    signals = json.loads((prepared / "signals.json").read_text(encoding="utf-8"))
    (prepared / "signals-v28-full.json").write_text(
        json.dumps(signals, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output / "signals.json").write_text(
        json.dumps(signals, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = json.loads((output / "v28_signal_manifest.json").read_text(encoding="utf-8"))
    preflight = {
        "candidate": "candidate-03-nt-lvcfr-v28-structural-state-retest",
        "week_start": week,
        "training_start": manifest["training_start"],
        "training_end": manifest["training_end"],
        "training_events": manifest["training_events"],
        "training_label_counts": manifest["training_label_counts"],
        "evaluation_events": manifest["evaluation_events"],
        "v28_signal_count": len(signals),
        "state_counts": manifest["state_counts"],
        "no_trade_reasons": manifest["no_trade_reasons"],
        "continuation_model": manifest["continuation_model"],
        "reversal_model": manifest["reversal_model"],
        "minimum_required_episodes": 8,
        "opportunity_gate_reachable": len(signals) >= 8,
        "performance_metrics_calculated": False,
    }
    (output / "preflight_summary.json").write_text(
        json.dumps(preflight, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(preflight, indent=2, sort_keys=True))
    return len(signals)


def filter_schedule(prepared: Path, excluded_state: str | None) -> int:
    full = json.loads((prepared / "signals-v28-full.json").read_text(encoding="utf-8"))
    selected = [
        signal
        for signal in full
        if excluded_state is None or str(signal.get("scenario_kind")) != excluded_state
    ]
    (prepared / "signals.json").write_text(
        json.dumps(selected, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return len(selected)


def run_native(week: str, prepared: Path, output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(prepared / "signals.json", output / "signals.json")
    return v24.run_native(week, prepared, output)


def worst_negative_state(output: Path) -> str | None:
    path = output / "state_attribution.json"
    if not path.exists():
        return None
    states = json.loads(path.read_text(encoding="utf-8")).get("by_scenario_kind", {})
    if len(states) < 2:
        return None
    losses = [
        (float(values.get("native_account_pnl") or 0.0), str(state))
        for state, values in states.items()
        if float(values.get("native_account_pnl") or 0.0) < 0.0
    ]
    return min(losses)[1] if losses else None


def row_from_output(
    *, week: str, stage: str, output: Path, signals: int,
    gate_status: int | None, excluded_state: str | None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "week_start": week,
        "stage": stage,
        "signals": signals,
        "excluded_state": excluded_state,
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


def copy_essential(source: Path, destination: Path) -> list[Path]:
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for name in (
        "metrics.json", "gate.json", "episodes.csv", "positions.csv",
        "state_attribution.json", "preflight_summary.json", "v28_signal_manifest.json",
    ):
        path = source / name
        if path.exists():
            target = destination / name
            shutil.copy2(path, target)
            copied.append(target)
    return copied


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
    status_path = root / "V28_STABILITY_STATUS.json"
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
        {"message": "candidate-03: record V28 unseen-week evidence", "tree": tree["sha"], "parents": [parent]},
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
    stage_root = base.REPO_ROOT / "artifacts/candidate-03/v28-structural-state-retest"
    result_root = base.REPO_ROOT / "research/candidate-03/results"
    status: dict[str, Any] = {
        "candidate": "candidate-03-nt-lvcfr-v28-structural-state-retest",
        "risk_fraction": 0.03,
        "official_gate_unchanged": True,
        "patched_strategy_sha256": patched_sha,
        "week_selection_salt": "candidate-03|V28|structural-state-retest|unseen-freeze",
        "weeks": [],
    }
    files: list[Path] = []
    excluded_state: str | None = None
    for index, (week, stage) in enumerate(WEEKS):
        prepared = stage_root / stage / "prepared"
        baseline = stage_root / stage / "baseline"
        full_count = prepare_schedule(week, prepared, baseline)
        selected_count = filter_schedule(prepared, excluded_state)
        gate_status: int | None = None
        selected_output = baseline
        if selected_count >= 8:
            gate_status = run_native(week, prepared, baseline)
        if index == 0 and gate_status != 0 and selected_count >= 8:
            worst = worst_negative_state(baseline)
            if worst is not None:
                remaining = filter_schedule(prepared, worst)
                if remaining >= 8:
                    ablation = stage_root / stage / "ablation"
                    gate_status = run_native(week, prepared, ablation)
                    excluded_state = worst if gate_status == 0 else None
                    selected_count = remaining
                    selected_output = ablation
        row = row_from_output(
            week=week, stage=stage, output=selected_output,
            signals=selected_count, gate_status=gate_status,
            excluded_state=excluded_state,
        )
        row["full_signal_count"] = full_count
        status["weeks"].append(row)
        files.extend(copy_essential(selected_output, result_root / f"v28-{stage}"))
        if gate_status != 0:
            break
    status["aggregate"] = aggregate(status["weeks"])
    promote = (
        len(status["weeks"]) == len(WEEKS)
        and all(bool(row["official_gate_passed"]) for row in status["weeks"])
    )
    status["excluded_state"] = excluded_state
    status["decision"] = "PROMOTE_TO_LONG_HORIZON" if promote else "REJECT_OR_REDESIGN_V28"
    status["selection_contract"] = (
        "One worst negative state ablation is allowed only on unseen week 1; "
        "the resulting state grammar is frozen before weeks 2 and 3."
    )
    commit_sha = commit_results(status, files)
    print(json.dumps({"committed_sha": commit_sha, **status}, indent=2, sort_keys=True))
    return 0


raise SystemExit(main())

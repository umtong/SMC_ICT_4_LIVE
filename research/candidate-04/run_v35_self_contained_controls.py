#!/usr/bin/env python3
"""Run V31 execution-contract controls without marketplace GitHub actions.

The script restores the exact seven validated-core signal artifacts, replays
three controlled variants through NautilusTrader, verifies attribution and the
3% current-NAV loss contract, and emits one machine-readable comparison.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import urllib.request
import zipfile

import pandas as pd
from pandas.errors import EmptyDataError

from nt_expected_fill_risk_sizing import FILL_EXPECTATION_CONTRACT


REPOSITORY = "umtong/SMC_ICT_4_LIVE"
WEEKS = (
    {
        "name": "low_activity",
        "artifact_id": 8973026625,
        "build_start": "2023-08-02",
        "build_end": "2023-08-10",
        "evaluation_start": "2023-08-04",
        "evaluation_end": "2023-08-10",
    },
    {
        "name": "normal_basis",
        "artifact_id": 8973026320,
        "build_start": "2023-12-16",
        "build_end": "2023-12-24",
        "evaluation_start": "2023-12-18",
        "evaluation_end": "2023-12-24",
    },
    {
        "name": "stress_a",
        "artifact_id": 8973022801,
        "build_start": "2024-08-05",
        "build_end": "2024-08-13",
        "evaluation_start": "2024-08-07",
        "evaluation_end": "2024-08-13",
    },
    {
        "name": "year_end",
        "artifact_id": 8973022682,
        "build_start": "2024-12-25",
        "build_end": "2025-01-02",
        "evaluation_start": "2024-12-27",
        "evaluation_end": "2025-01-02",
    },
    {
        "name": "autumn",
        "artifact_id": 8973033157,
        "build_start": "2025-10-18",
        "build_end": "2025-10-26",
        "evaluation_start": "2025-10-20",
        "evaluation_end": "2025-10-26",
    },
    {
        "name": "may_2024",
        "artifact_id": 8973024256,
        "build_start": "2024-05-25",
        "build_end": "2024-06-02",
        "evaluation_start": "2024-05-27",
        "evaluation_end": "2024-06-02",
    },
    {
        "name": "failed_untouched_2025_03",
        "artifact_id": 8973036804,
        "build_start": "2025-03-22",
        "build_end": "2025-03-30",
        "evaluation_start": "2025-03-24",
        "evaluation_end": "2025-03-30",
    },
)
VARIANTS = {
    "exact_target": {
        "runner": "nt_backtest_v31_exact_causal_target.py",
        "target_contract": "exact_pre_signal_causal_reference",
        "fill_contract": None,
        "changed_variables": 1,
    },
    "expected_fill_only": {
        "runner": "nt_backtest_v34_expected_fill_only.py",
        "target_contract": "validated_v31_capped_reference",
        "fill_contract": FILL_EXPECTATION_CONTRACT,
        "changed_variables": 1,
    },
    "combined": {
        "runner": "nt_backtest_v34_expected_fill.py",
        "target_contract": "exact_pre_signal_causal_reference",
        "fill_contract": FILL_EXPECTATION_CONTRACT,
        "changed_variables": 2,
    },
}


def _download_artifact(
    artifact_id: int,
    destination: Path,
    token: str,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination.with_suffix(".zip")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/actions/artifacts/"
        f"{artifact_id}/zip",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "candidate-04-v35-control",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        archive.write_bytes(response.read())
    with zipfile.ZipFile(archive) as source:
        source.extractall(destination)
    archive.unlink()
    for required in (
        destination / "ablated/signals/signals.json",
        destination / "comparison.json",
    ):
        if not required.is_file() or required.stat().st_size <= 0:
            raise RuntimeError(f"restored artifact missing {required}")


def _run_backtest(
    repository: Path,
    output_root: Path,
    cache_root: Path,
    source: Path,
    week: dict[str, object],
    variant: str,
    contract: dict[str, object],
) -> None:
    candidate_dir = repository / "research/candidate-04"
    output = output_root / variant / str(week["name"]) / "nautilus"
    output.mkdir(parents=True, exist_ok=True)
    console = output.parent / "console.json"
    command = [
        sys.executable,
        str(candidate_dir / str(contract["runner"])),
        "--config",
        str(candidate_dir / "nt_liquidity_config.json"),
        "--build-start",
        str(week["build_start"]),
        "--build-end",
        str(week["build_end"]),
        "--evaluation-start",
        str(week["evaluation_start"]),
        "--evaluation-end",
        str(week["evaluation_end"]),
        "--cache",
        str(cache_root / str(week["name"]) / "raw"),
        "--output",
        str(output),
    ]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(candidate_dir)
    environment["C04_SIGNALS_PATH"] = str(
        (source / "ablated/signals/signals.json").resolve()
    )
    completed = subprocess.run(
        command,
        cwd=repository,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    console.write_text(completed.stdout, encoding="utf-8")
    if completed.returncode != 0:
        tail = "\n".join(completed.stdout.splitlines()[-80:])
        raise RuntimeError(
            f"{variant}/{week['name']} Nautilus execution failed:\n{tail}"
        )
    for required in (
        output / "metrics.json",
        output / "positions.csv",
        output / "strategy_events.json",
    ):
        if not required.exists():
            raise RuntimeError(f"missing Nautilus output {required}")


def _pnl_number(value: object) -> float:
    return float(str(value).split()[0].replace("_", "").replace(",", ""))


def _week_result(
    output_root: Path,
    source: Path,
    week: dict[str, object],
    variant: str,
    contract: dict[str, object],
) -> dict[str, object]:
    base = output_root / variant / str(week["name"]) / "nautilus"
    metrics = json.loads((base / "metrics.json").read_text(encoding="utf-8"))
    events = json.loads(
        (base / "strategy_events.json").read_text(encoding="utf-8")
    )
    comparison = json.loads(
        (source / "comparison.json").read_text(encoding="utf-8")
    )
    try:
        positions = pd.read_csv(base / "positions.csv")
    except EmptyDataError:
        positions = pd.DataFrame()
    entries = [
        event for event in events
        if event.get("event_type") == "ENTRY_SUBMITTED"
    ]
    if len(entries) != len(positions.index):
        raise RuntimeError(
            f"{variant}/{week['name']}: entry-position mismatch "
            f"{len(entries)} != {len(positions.index)}"
        )

    losses: list[float] = []
    planned_ratios: list[float] = []
    quantities: list[float] = []
    risk_budget_utilizations: list[float] = []
    exact_identity_checked = 0
    for position, entry in zip(positions.to_dict("records"), entries):
        details = dict(entry["details"])
        if details.get("target_contract") != contract["target_contract"]:
            raise RuntimeError(
                f"{variant}/{week['name']}: target contract drifted"
            )
        observed_fill_contract = details.get("fill_expectation")
        if observed_fill_contract != contract["fill_contract"]:
            raise RuntimeError(
                f"{variant}/{week['name']}: fill contract drifted: "
                f"{observed_fill_contract!r}"
            )
        if contract["target_contract"] == "exact_pre_signal_causal_reference":
            causal = details.get("causal_target")
            if causal is not None:
                exact_identity_checked += 1
                if abs(float(details["target"]) - float(causal)) > 0.11:
                    raise RuntimeError(
                        f"{variant}/{week['name']}: target differs from causal "
                        "reference"
                    )
        pnl = _pnl_number(position["realized_pnl"])
        equity = float(details["equity"])
        if pnl < 0.0:
            losses.append(abs(pnl) / equity)
        execution_loss = float(details["execution_planned_loss_per_unit"])
        signal_loss = float(details["signal_planned_loss_per_unit"])
        quantity = float(details["quantity"])
        planned_ratios.append(execution_loss / signal_loss)
        quantities.append(quantity)
        risk_budget_utilizations.append(
            execution_loss * quantity / float(details["risk_budget"])
        )

    maximum_loss = max(losses, default=0.0)
    return {
        "matrix": str(week["name"]),
        "evaluation_start": str(week["evaluation_start"]),
        "evaluation_end": str(week["evaluation_end"]),
        "trades": metrics.get("trades"),
        "wins": metrics.get("wins"),
        "win_rate": metrics.get("win_rate"),
        "total_return": metrics.get("total_return"),
        "geometric_daily_growth": metrics.get("geometric_daily_growth"),
        "active_days": metrics.get("active_days"),
        "max_drawdown": metrics.get("max_drawdown"),
        "largest_winner_share": metrics.get("largest_winner_share"),
        "scenario_metrics": metrics.get("scenario_metrics"),
        "maximum_realized_loss_fraction": maximum_loss,
        "risk_pass": maximum_loss <= 0.0301,
        "mean_execution_to_signal_planned_loss": (
            sum(planned_ratios) / len(planned_ratios)
            if planned_ratios else None
        ),
        "mean_quantity": (
            sum(quantities) / len(quantities) if quantities else None
        ),
        "minimum_planned_risk_budget_utilization": (
            min(risk_budget_utilizations) if risk_budget_utilizations else None
        ),
        "maximum_planned_risk_budget_utilization": (
            max(risk_budget_utilizations) if risk_budget_utilizations else None
        ),
        "exact_target_identity_checks": exact_identity_checked,
        "baseline": comparison["ablated"],
    }


def _aggregate(
    weeks: list[dict[str, object]],
    changed_variables: int,
) -> dict[str, object]:
    returns = [float(week["total_return"]) for week in weeks]
    compounded = math.prod(1.0 + value for value in returns) - 1.0
    daily = (1.0 + compounded) ** (1.0 / 49.0) - 1.0
    trades = sum(int(week["trades"]) for week in weeks)
    wins = sum(int(week["wins"]) for week in weeks)
    all_positive = all(value > 0.0 for value in returns)
    all_risk_pass = all(bool(week["risk_pass"]) for week in weeks)
    return {
        "changed_variables": changed_variables,
        "weeks": weeks,
        "calendar_days": 49,
        "all_weeks_positive": all_positive,
        "all_weeks_risk_pass": all_risk_pass,
        "compounded_return": compounded,
        "geometric_daily_growth": daily,
        "trades": trades,
        "wins": wins,
        "win_rate": wins / trades if trades else 0.0,
        "maximum_realized_loss_fraction": max(
            float(week["maximum_realized_loss_fraction"])
            for week in weeks
        ),
        "development_target_reached": (
            all_positive and all_risk_pass and daily >= 0.01
        ),
        "final_validation_completed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument(
        "--source-root", type=Path, default=Path("sources-v35")
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("artifacts/candidate-04-v35"),
    )
    parser.add_argument(
        "--cache-root", type=Path, default=Path(".cache/candidate-04-v35")
    )
    args = parser.parse_args()

    token = os.environ.get("GH_TOKEN")
    if not token:
        raise RuntimeError("GH_TOKEN is required")
    repository = args.repository.resolve()
    source_root = args.source_root.resolve()
    output_root = args.output_root.resolve()
    cache_root = args.cache_root.resolve()
    source_root.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)

    sources: dict[str, Path] = {}
    for week in WEEKS:
        name = str(week["name"])
        destination = source_root / name
        _download_artifact(int(week["artifact_id"]), destination, token)
        sources[name] = destination

    for week in WEEKS:
        name = str(week["name"])
        for variant, contract in VARIANTS.items():
            print(f"RUNNING {variant}/{name}", flush=True)
            _run_backtest(
                repository,
                output_root,
                cache_root,
                sources[name],
                week,
                variant,
                contract,
            )

    result: dict[str, object] = {
        "source_commit": os.environ.get("GITHUB_SHA"),
        "engine": "NautilusTrader 1.230.0 BacktestNode",
        "source_signal_set": "validated V31 core after stress-continuation ablation",
        "controls": {
            "exact_target": "target relation only",
            "expected_fill_only": "fill expectation only",
            "combined": "only after both independent controls are observable",
        },
        "variants": {},
    }
    variant_results: dict[str, object] = {}
    for variant, contract in VARIANTS.items():
        weeks = [
            _week_result(
                output_root,
                sources[str(week["name"])],
                week,
                variant,
                contract,
            )
            for week in WEEKS
        ]
        variant_results[variant] = _aggregate(
            weeks,
            int(contract["changed_variables"]),
        )
    result["variants"] = variant_results

    destination = output_root / "control_comparison.json"
    destination.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("CONTROL_RESULTS_JSON_BEGIN")
    print(json.dumps(result, indent=2, sort_keys=True))
    print("CONTROL_RESULTS_JSON_END")


if __name__ == "__main__":
    main()

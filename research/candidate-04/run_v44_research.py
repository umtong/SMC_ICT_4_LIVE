#!/usr/bin/env python3
"""Sequential V44 target-registry research through NautilusTrader.

This module is orchestration only. It freezes the V31/V34 signal rows, enriches
only those without an executable strict target, invokes the existing
NautilusTrader runner, applies staged BTC gates, and writes reproducible
implementation-versus-logic evidence. It does not calculate fills, positions,
PnL or NAV itself.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
C04 = ROOT / "research/candidate-04"


@dataclass(frozen=True, slots=True)
class Week:
    name: str
    build_start: str
    build_end: str
    evaluation_start: str
    evaluation_end: str


WEEKS = (
    Week("low_activity", "2023-08-02", "2023-08-10", "2023-08-04", "2023-08-10"),
    Week("normal_basis", "2023-12-16", "2023-12-24", "2023-12-18", "2023-12-24"),
    Week("stress_a", "2024-08-05", "2024-08-13", "2024-08-07", "2024-08-13"),
    Week("year_end", "2024-12-25", "2025-01-02", "2024-12-27", "2025-01-02"),
    Week("autumn", "2025-10-18", "2025-10-26", "2025-10-20", "2025-10-26"),
    Week("may_2024", "2024-05-25", "2024-06-02", "2024-05-27", "2024-06-02"),
    Week(
        "failed_untouched_2025_03",
        "2025-03-22",
        "2025-03-30",
        "2025-03-24",
        "2025-03-30",
    ),
)

STRICT_BASELINE = {
    "calendar_days": 49,
    "compounded_return": 0.2996350171931126,
    "geometric_daily_growth": 0.005362971748631429,
    "trades": 9,
    "wins": 8,
    "active_days": 9,
}


class ResearchRuntimeFailure(RuntimeError):
    def __init__(self, stage: str, return_code: int) -> None:
        super().__init__(f"{stage} failed with return code {return_code}")
        self.stage = stage
        self.return_code = return_code


def run(command: list[str], *, env: dict[str, str], log: Path, stage: str) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as stream:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    if completed.returncode != 0:
        raise ResearchRuntimeFailure(stage, completed.returncode)


def find_source(sources: Path, week: Week) -> Path:
    matches = sorted(
        path
        for path in sources.glob(f"candidate-04-v31-ablation-{week.name}-*")
        if path.is_dir()
    )
    if not matches:
        raise FileNotFoundError(f"missing V31 source for {week.name}")
    source = matches[0]
    required = (
        source / "ablated/signals/signals.json",
        source / "original/source/rich/data_manifest.json",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing V31 evidence inputs: " + ", ".join(missing))
    return source


def run_week(
    week: Week,
    *,
    sources: Path,
    output_root: Path,
    cache_root: Path,
    env: dict[str, str],
) -> dict[str, Any]:
    source = find_source(sources, week)
    output = output_root / week.name
    output.mkdir(parents=True, exist_ok=True)
    signals = source / "ablated/signals/signals.json"
    rich_dir = source / "original/source/rich"
    enriched = output / "signals"
    nautilus = output / "nautilus"

    run(
        [
            sys.executable,
            str(C04 / "causal_target_registry_enricher.py"),
            "--signals",
            str(signals),
            "--base-config",
            str(C04 / "inventory_transfer_config.json"),
            "--rich-dir",
            str(rich_dir),
            "--kline-dir",
            str(cache_root / "raw" / week.name),
            "--build-start",
            week.build_start,
            "--build-end",
            week.build_end,
            "--output-dir",
            str(enriched),
            "--download-klines",
            "--cost-rate",
            "0.00075",
            "--minimum-net-r",
            "1.20",
        ],
        env=env,
        log=output / "enrichment_console.log",
        stage=f"{week.name}:target_enrichment",
    )
    execution_env = dict(env)
    execution_env["C04_SIGNALS_PATH"] = str(
        (enriched / "signals.json").resolve()
    )
    run(
        [
            sys.executable,
            str(C04 / "nt_backtest_v34_expected_fill_only.py"),
            "--config",
            str(C04 / "nt_liquidity_config.json"),
            "--build-start",
            week.build_start,
            "--build-end",
            week.build_end,
            "--evaluation-start",
            week.evaluation_start,
            "--evaluation-end",
            week.evaluation_end,
            "--cache",
            str(cache_root / "nautilus" / week.name),
            "--output",
            str(nautilus),
        ],
        env=execution_env,
        log=output / "nautilus_console.log",
        stage=f"{week.name}:nautilus",
    )
    run(
        [
            sys.executable,
            str(C04 / "summarize_candidate_week.py"),
            "--root",
            str(output),
            "--candidate",
            "candidate-04-v44-causal-target-registry",
            "--stage",
            week.name,
            "--min-trades",
            "2",
            "--min-active-days",
            "2",
            "--min-win-rate",
            "0.50",
            "--min-geometric-daily",
            "0.0",
            "--output",
            str(output / "summary.json"),
        ],
        env=env,
        log=output / "summary_console.log",
        stage=f"{week.name}:summary",
    )
    summary = json.loads((output / "summary.json").read_text())
    enrichment = json.loads((enriched / "summary.json").read_text())
    events = json.loads((nautilus / "strategy_events.json").read_text())
    counts = Counter(str(event.get("event_type")) for event in events)
    return {
        "week": week.name,
        **summary,
        "enrichment": enrichment,
        "execution_events": dict(counts),
    }


def first_week_viable(row: dict[str, Any]) -> bool:
    counts = (row.get("enrichment") or {}).get("counts") or {}
    return bool(
        row.get("risk_pass")
        and float(row.get("total_return") or 0.0) > 0.0
        and int(row.get("trades") or 0) >= 2
        and int(row.get("active_days") or 0) >= 2
        and float(row.get("win_rate") or 0.0) >= 0.50
        and int(counts.get("new_declared_target") or 0) >= 1
    )


def three_week_viable(rows: list[dict[str, Any]]) -> bool:
    returns = [float(row.get("total_return") or 0.0) for row in rows]
    compounded = math.prod(1.0 + value for value in returns) - 1.0
    trades = sum(int(row.get("trades") or 0) for row in rows)
    wins = sum(int(row.get("wins") or 0) for row in rows)
    active = sum(int(row.get("active_days") or 0) for row in rows)
    return bool(
        len(rows) == 3
        and all(row.get("risk_pass") for row in rows)
        and compounded > 0.0
        and trades >= 6
        and active >= 5
        and sum(value > 0.0 for value in returns) >= 2
        and (wins / trades if trades else 0.0) >= 0.50
    )


def aggregate(
    rows: list[dict[str, Any]],
    *,
    runtime_failures: list[dict[str, Any]],
    source_commit: str | None,
    workflow_run_id: str | None,
) -> dict[str, Any]:
    returns = [float(row.get("total_return") or 0.0) for row in rows]
    compounded = math.prod(1.0 + value for value in returns) - 1.0 if rows else 0.0
    calendar_days = 7 * len(rows)
    daily = (
        (1.0 + compounded) ** (1.0 / calendar_days) - 1.0
        if calendar_days and 1.0 + compounded > 0.0
        else 0.0
    )
    trades = sum(int(row.get("trades") or 0) for row in rows)
    wins = sum(int(row.get("wins") or 0) for row in rows)
    active = sum(int(row.get("active_days") or 0) for row in rows)
    declared = sum(
        int(
            (((row.get("enrichment") or {}).get("counts") or {}).get(
                "new_declared_target"
            ))
            or 0
        )
        for row in rows
    )
    full_matrix = len(rows) == len(WEEKS)
    improved = bool(
        full_matrix
        and compounded > STRICT_BASELINE["compounded_return"]
        and trades > STRICT_BASELINE["trades"]
        and active > STRICT_BASELINE["active_days"]
        and all(row.get("risk_pass") for row in rows)
    )
    target_rate = bool(full_matrix and daily >= 0.01)

    if runtime_failures:
        classification = "implementation_failure_requires_identical_week_rerun"
        decision = "repair_implementation_and_rerun_identical_week"
    elif not rows:
        classification = "implementation_failure_no_results"
        decision = "repair_implementation_and_rerun_identical_week"
    elif not first_week_viable(rows[0]):
        classification = "logic_failure_no_first_week_frequency_path"
        decision = "discard_v44_first_week_target_registry_did_not_create_frequency_path"
    elif len(rows) < 3 or not three_week_viable(rows[:3]):
        classification = "first_week_survivor_failed_two_week_confirmation"
        decision = "discard_v44_confirmation_gate_failed"
    elif not full_matrix:
        classification = "implementation_failure_after_three_week_gate"
        decision = "repair_implementation_and_resume_frozen_matrix"
    elif target_rate:
        classification = "development_target_rate_survivor"
        decision = "freeze_v44_and_open_long_plus_integrated_four_asset_validation"
    elif improved:
        classification = "strict_baseline_improved_but_below_target"
        decision = "retain_v44_as_strict_improved_baseline_and_continue_independent_scenario_discovery"
    else:
        classification = "logic_failure_no_strict_baseline_improvement"
        decision = "discard_v44_no_strict_baseline_improvement"

    return {
        "candidate": "candidate-04-v44-causal-target-registry-enrichment",
        "source_commit": source_commit,
        "workflow_run_id": workflow_run_id,
        "engine": "NautilusTrader 1.230.0 BacktestNode",
        "controlled_change": (
            "frozen V31/V34 entries, sides, stops, scenarios and timing; only "
            "no-target signals may receive active pre-signal liquidity declarations"
        ),
        "weeks": rows,
        "runtime_failures": runtime_failures,
        "calendar_days": calendar_days,
        "compounded_return": compounded,
        "geometric_daily_growth": daily,
        "trades": trades,
        "wins": wins,
        "win_rate": wins / trades if trades else 0.0,
        "active_days": active,
        "positive_weeks": sum(value > 0.0 for value in returns),
        "new_declared_targets": declared,
        "strict_v34_baseline": STRICT_BASELINE,
        "strict_baseline_improved": improved,
        "development_target_rate_reached": target_rate,
        "classification": classification,
        "decision": decision,
        "project_target_reached": False,
        "final_validation_completed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()

    sources = args.sources.resolve()
    output_root = args.output_root.resolve()
    cache_root = args.cache_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(C04)
    rows: list[dict[str, Any]] = []
    runtime_failures: list[dict[str, Any]] = []

    stages = ((WEEKS[0],), WEEKS[1:3], WEEKS[3:])
    for stage_index, stage in enumerate(stages):
        if stage_index == 1 and not first_week_viable(rows[0]):
            break
        if stage_index == 2 and not three_week_viable(rows[:3]):
            break
        for week in stage:
            try:
                rows.append(
                    run_week(
                        week,
                        sources=sources,
                        output_root=output_root,
                        cache_root=cache_root,
                        env=env,
                    )
                )
            except Exception as exc:
                runtime_failures.append(
                    {
                        "week": week.name,
                        "stage": getattr(exc, "stage", type(exc).__name__),
                        "return_code": getattr(exc, "return_code", None),
                        "error": str(exc),
                    }
                )
                break
        if runtime_failures:
            break

    evidence = aggregate(
        rows,
        runtime_failures=runtime_failures,
        source_commit=os.environ.get("V44_SOURCE_COMMIT"),
        workflow_run_id=os.environ.get("GITHUB_RUN_ID"),
    )
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    (output_root / "aggregate.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

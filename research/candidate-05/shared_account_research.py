#!/usr/bin/env python3
"""Evidence-driven one-account four-symbol NautilusTrader research.

Every stage launches :mod:`shared_account_backtest`; independent symbol-account
returns are never added.  Short ranges are promotion screens.  Project success
requires the complete 2024-01-01 through 2026-06-30 shared-NAV evaluation.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent
SHARED_BACKTEST = ROOT / "shared_account_backtest.py"


@dataclass(frozen=True, slots=True)
class StageRange:
    name: str
    build_start: str
    build_end: str
    evaluation_start: str
    evaluation_end: str
    calendar_days: int


# Frozen short screens.  They are not individually required to earn 1% per day;
# the combined shared NAV path is the three-week screen.
WEEKS = (
    StageRange("week-3-weak", "2023-09-06", "2023-09-14", "2023-09-08", "2023-09-14", 7),
    StageRange("week-1", "2023-07-07", "2023-07-15", "2023-07-09", "2023-07-15", 7),
    StageRange("week-2", "2024-01-13", "2024-01-21", "2024-01-15", "2024-01-21", 7),
)
CONTINUOUS_30D = StageRange(
    "continuous-30d",
    "2024-02-28",
    "2024-03-30",
    "2024-03-01",
    "2024-03-30",
    30,
)
CONTINUOUS_91D = StageRange(
    "continuous-91d",
    "2024-02-28",
    "2024-05-30",
    "2024-03-01",
    "2024-05-30",
    91,
)
LONG_2024_2026H1 = StageRange(
    "long-2024-2026h1",
    "2023-12-30",
    "2026-06-30",
    "2024-01-01",
    "2026-06-30",
    912,
)

# The long-horizon opportunity requirements preserve the existing 91-day
# minimum densities instead of inventing easier long-range thresholds.
LONG_MIN_TRADES = math.ceil(45 * LONG_2024_2026H1.calendar_days / CONTINUOUS_91D.calendar_days)
LONG_MIN_WINS = math.ceil(15 * LONG_2024_2026H1.calendar_days / CONTINUOUS_91D.calendar_days)
LONG_MIN_ACTIVE_DAYS = math.ceil(30 * LONG_2024_2026H1.calendar_days / CONTINUOUS_91D.calendar_days)
LONG_MIN_POSITIVE_MONTHS = 18  # 60% of the 30 calendar months.
LONG_MONTH_COUNT = 30


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _number(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _integer(value: Any, default: int = 0) -> int:
    number = _number(value, float(default))
    return int(number)


def _monthly_returns(daily_returns: Any) -> dict[str, float]:
    if not isinstance(daily_returns, dict):
        return {}
    multiples: dict[str, float] = {}
    for day, raw_return in sorted(daily_returns.items()):
        text = str(day)
        if len(text) < 7:
            continue
        value = _number(raw_return, float("nan"))
        if not math.isfinite(value) or value <= -1.0:
            return {}
        month = text[:7]
        multiples[month] = multiples.get(month, 1.0) * (1.0 + value)
    return {month: multiple - 1.0 for month, multiple in multiples.items()}


def run_stage(
    *,
    stage: StageRange,
    winner_evidence: Path,
    output_root: Path,
    cache_root: Path,
    python: str,
) -> dict[str, Any]:
    output = output_root / stage.name
    output.mkdir(parents=True, exist_ok=True)
    command = [
        python,
        str(SHARED_BACKTEST),
        "--winner-evidence",
        str(winner_evidence),
        "--build-start",
        stage.build_start,
        "--build-end",
        stage.build_end,
        "--evaluation-start",
        stage.evaluation_start,
        "--evaluation-end",
        stage.evaluation_end,
        "--cache",
        str(cache_root / stage.name),
        "--output",
        str(output),
    ]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT)
    environment["PYTHONUNBUFFERED"] = "1"
    completed = subprocess.run(
        command,
        cwd=ROOT.parent.parent,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    (output / "research_console.log").write_text(completed.stdout or "", encoding="utf-8")
    metrics_path = output / "metrics.json"
    if completed.returncode != 0 or not metrics_path.exists():
        return {
            "available": False,
            "stage": asdict(stage),
            "return_code": completed.returncode,
            "classification": "IMPLEMENTATION_OR_EVIDENCE_ERROR",
            "error_log_tail": (completed.stdout or "").splitlines()[-260:],
            "artifact_directory": str(output),
        }
    try:
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "available": False,
            "stage": asdict(stage),
            "return_code": completed.returncode,
            "classification": "IMPLEMENTATION_OR_EVIDENCE_ERROR",
            "error": repr(exc),
            "artifact_directory": str(output),
        }
    return {
        "available": True,
        "stage": asdict(stage),
        "return_code": completed.returncode,
        "integrity_pass": bool(metrics.get("integrity_pass", False)),
        "integrity_checks": metrics.get("integrity_checks", {}),
        "strategy": metrics.get("strategy"),
        "starting_nav": metrics.get("starting_nav"),
        "ending_nav": metrics.get("ending_nav"),
        "total_return": metrics.get("total_return"),
        "geometric_daily_growth": metrics.get("geometric_daily_growth"),
        "max_drawdown": metrics.get("max_drawdown"),
        "min_equity": metrics.get("min_equity"),
        "trades": metrics.get("trades"),
        "wins": metrics.get("wins"),
        "losses": metrics.get("losses"),
        "win_rate": metrics.get("win_rate"),
        "profit_factor": metrics.get("profit_factor"),
        "expectancy_usdt": metrics.get("expectancy_usdt"),
        "active_days": metrics.get("active_days"),
        "largest_winner_share": metrics.get("largest_winner_share"),
        "daily_returns": metrics.get("daily_returns", {}),
        "scenario_metrics": metrics.get("scenario_metrics", {}),
        "symbol_metrics": metrics.get("symbol_metrics", {}),
        "global_slot_audit": metrics.get("global_slot_audit", {}),
        "artifact_directory": str(output),
    }


def run_valid(run: dict[str, Any]) -> bool:
    return bool(run.get("available")) and bool(run.get("integrity_pass"))


def classify_three_weeks(runs: list[dict[str, Any]]) -> dict[str, Any]:
    if len(runs) != 3 or not all(run_valid(run) for run in runs):
        return {
            "classification": "IMPLEMENTATION_OR_EVIDENCE_ERROR_THREE_WEEK_SHARED_ACCOUNT",
            "passed": False,
        }
    total_days = sum(int(run["stage"]["calendar_days"]) for run in runs)
    multiple = math.prod(1.0 + _number(run.get("total_return"), -1.0) for run in runs)
    growth = multiple ** (1.0 / total_days) - 1.0 if multiple > 0.0 else -1.0
    trades = sum(_integer(run.get("trades")) for run in runs)
    wins = sum(_integer(run.get("wins")) for run in runs)
    active_days = sum(_integer(run.get("active_days")) for run in runs)
    losses = sum(_integer(run.get("losses")) for run in runs)
    passed = multiple > 1.0 and trades >= 6 and wins >= 3 and active_days >= 6
    return {
        "classification": (
            "SHARED_ACCOUNT_THREE_WEEK_SCREEN_PASSED"
            if passed
            else "LOGIC_OR_ROBUSTNESS_FAILURE_SHARED_THREE_WEEKS"
        ),
        "passed": passed,
        "calendar_days": total_days,
        "account_multiple": multiple,
        "geometric_daily_growth": growth,
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "active_days": active_days,
        "requirements": {
            "positive_compounded_nav": multiple > 1.0,
            "min_trades_6": trades >= 6,
            "min_wins_3": wins >= 3,
            "min_active_days_6": active_days >= 6,
            "no_per_week_one_percent_gate": True,
        },
    }


def classify_30d(run: dict[str, Any]) -> dict[str, Any]:
    if not run_valid(run):
        return {"classification": "IMPLEMENTATION_OR_EVIDENCE_ERROR_SHARED_30D", "passed": False}
    checks = {
        "geometric_daily_growth": _number(run.get("geometric_daily_growth"), -1.0) >= 0.01,
        "trades": _integer(run.get("trades")) >= 15,
        "wins": _integer(run.get("wins")) >= 5,
        "active_days": _integer(run.get("active_days")) >= 10,
        "largest_winner_share": _number(run.get("largest_winner_share"), 1.0) <= 0.35,
        "positive_min_equity": _number(run.get("min_equity"), 0.0) > 0.0,
    }
    passed = all(checks.values())
    return {
        "classification": (
            "SHARED_ACCOUNT_30D_PROMOTION_PASSED"
            if passed
            else "LOGIC_OR_ROBUSTNESS_FAILURE_SHARED_30D"
        ),
        "passed": passed,
        "checks": checks,
    }


def classify_91d(run: dict[str, Any]) -> dict[str, Any]:
    """A promotion screen; never a substitute for the 912-day project gate."""
    if not run_valid(run):
        return {"classification": "IMPLEMENTATION_OR_EVIDENCE_ERROR_SHARED_91D", "passed": False}
    checks = {
        "geometric_daily_growth": _number(run.get("geometric_daily_growth"), -1.0) >= 0.01,
        "trades": _integer(run.get("trades")) >= 45,
        "wins": _integer(run.get("wins")) >= 15,
        "win_rate": _number(run.get("win_rate"), 0.0) >= 0.30,
        "active_days": _integer(run.get("active_days")) >= 30,
        "largest_winner_share": _number(run.get("largest_winner_share"), 1.0) <= 0.25,
        "max_drawdown_recoverable": _number(run.get("max_drawdown"), 1.0) <= 0.35,
        "positive_min_equity": _number(run.get("min_equity"), 0.0) > 0.0,
        "global_slot_audit": bool(run.get("global_slot_audit", {}).get("audit_pass", False)),
    }
    passed = all(checks.values())
    return {
        "classification": (
            "SHARED_ACCOUNT_91D_PROMOTION_PASSED"
            if passed
            else "LOGIC_OR_ROBUSTNESS_FAILURE_SHARED_91D"
        ),
        "passed": passed,
        "checks": checks,
        "project_goal_not_yet_reached": True,
    }


def classify_long(run: dict[str, Any]) -> dict[str, Any]:
    """Final project gate for 2024-01-01 through 2026-06-30."""
    if not run_valid(run):
        return {
            "classification": "IMPLEMENTATION_OR_EVIDENCE_ERROR_SHARED_LONG_2024_2026H1",
            "passed": False,
        }
    monthly = _monthly_returns(run.get("daily_returns"))
    positive_months = sum(value > 0.0 for value in monthly.values())
    checks = {
        "exact_evaluation_start": run.get("stage", {}).get("evaluation_start") == "2024-01-01",
        "exact_evaluation_end": run.get("stage", {}).get("evaluation_end") == "2026-06-30",
        "calendar_days_912": _integer(run.get("stage", {}).get("calendar_days")) == 912,
        "geometric_daily_growth": _number(run.get("geometric_daily_growth"), -1.0) >= 0.01,
        "trades_at_91d_density": _integer(run.get("trades")) >= LONG_MIN_TRADES,
        "wins_at_91d_density": _integer(run.get("wins")) >= LONG_MIN_WINS,
        "win_rate": _number(run.get("win_rate"), 0.0) >= 0.30,
        "active_days_at_91d_density": _integer(run.get("active_days")) >= LONG_MIN_ACTIVE_DAYS,
        "largest_winner_share": _number(run.get("largest_winner_share"), 1.0) <= 0.10,
        "max_drawdown_recoverable": _number(run.get("max_drawdown"), 1.0) <= 0.35,
        "positive_min_equity": _number(run.get("min_equity"), 0.0) > 0.0,
        "global_slot_audit": bool(run.get("global_slot_audit", {}).get("audit_pass", False)),
        "all_30_months_present": len(monthly) == LONG_MONTH_COUNT,
        "at_least_18_positive_months": positive_months >= LONG_MIN_POSITIVE_MONTHS,
    }
    passed = all(checks.values())
    return {
        "classification": (
            "PROJECT_ONE_ACCOUNT_FOUR_SYMBOL_LONG_2024_2026H1_GATE_PASSED"
            if passed
            else "LOGIC_OR_ROBUSTNESS_FAILURE_SHARED_LONG_2024_2026H1"
        ),
        "passed": passed,
        "checks": checks,
        "monthly_returns": monthly,
        "positive_months": positive_months,
        "required_minimums": {
            "trades": LONG_MIN_TRADES,
            "wins": LONG_MIN_WINS,
            "active_days": LONG_MIN_ACTIVE_DAYS,
            "positive_months": LONG_MIN_POSITIVE_MONTHS,
        },
    }


def run_research(
    *,
    winner_evidence: Path,
    output_root: Path,
    cache_root: Path,
    summary_path: Path,
    python: str,
) -> dict[str, Any]:
    output_root = output_root.resolve()
    cache_root = cache_root.resolve()
    summary_path = summary_path.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)

    winner_payload = json.loads(winner_evidence.read_text(encoding="utf-8")) if winner_evidence.exists() else {}
    summary: dict[str, Any] = {
        "schema": "candidate-05-shared-account-research-v2",
        "source_commit": os.environ.get("GITHUB_SHA"),
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
        "validated_winner_evidence": winner_payload,
        "one_account": True,
        "one_backtest_node_per_stage": True,
        "four_symbols_together_per_stage": True,
        "independent_results_are_not_summed": True,
        "fixed_ranges": [
            asdict(item)
            for item in (*WEEKS, CONTINUOUS_30D, CONTINUOUS_91D, LONG_2024_2026H1)
        ],
        "runs": {},
        "winner": None,
    }

    if winner_payload.get("classification") != "VALIDATED_BTC_WINNER_RESOLVED":
        summary["classification"] = "NOT_RUN_NO_VALIDATED_BTC_WINNER"
        summary["next_action"] = "Continue exact-control BTC alpha research; shared-account validation is not authorized."
        atomic_json(summary_path, summary)
        return summary

    weekly_runs: list[dict[str, Any]] = []
    for stage in WEEKS:
        run = run_stage(
            stage=stage,
            winner_evidence=winner_evidence.resolve(),
            output_root=output_root,
            cache_root=cache_root,
            python=python,
        )
        summary["runs"][stage.name] = run
        weekly_runs.append(run)
        if not run_valid(run):
            summary["classification"] = "IMPLEMENTATION_OR_EVIDENCE_ERROR_SHARED_WEEK"
            summary["failed_stage"] = stage.name
            summary["next_action"] = (
                "Repair the shared-account implementation without changing strategy logic, then rerun the identical week."
            )
            atomic_json(summary_path, summary)
            return summary

    three_week = classify_three_weeks(weekly_runs)
    summary["three_week_decision"] = three_week
    if not three_week["passed"]:
        summary["classification"] = three_week["classification"]
        summary["next_action"] = (
            "Analyze symbol and slot attribution, perform one core-variable adaptation or discard; do not run continuous ranges."
        )
        atomic_json(summary_path, summary)
        return summary

    run_30d = run_stage(
        stage=CONTINUOUS_30D,
        winner_evidence=winner_evidence.resolve(),
        output_root=output_root,
        cache_root=cache_root,
        python=python,
    )
    summary["runs"][CONTINUOUS_30D.name] = run_30d
    decision_30d = classify_30d(run_30d)
    summary["continuous_30d_decision"] = decision_30d
    if not decision_30d["passed"]:
        summary["classification"] = decision_30d["classification"]
        summary["next_action"] = (
            "Separate implementation failure from cross-symbol logic failure; do not spend time on 91-day or long validation."
        )
        atomic_json(summary_path, summary)
        return summary

    run_91d = run_stage(
        stage=CONTINUOUS_91D,
        winner_evidence=winner_evidence.resolve(),
        output_root=output_root,
        cache_root=cache_root,
        python=python,
    )
    summary["runs"][CONTINUOUS_91D.name] = run_91d
    decision_91d = classify_91d(run_91d)
    summary["continuous_91d_decision"] = decision_91d
    if not decision_91d["passed"]:
        summary["classification"] = decision_91d["classification"]
        summary["next_action"] = (
            "Record 91-day failure attribution; apply at most one structural adaptation before discarding the shared system."
        )
        atomic_json(summary_path, summary)
        atomic_json(output_root / "shared_account_research_summary.json", summary)
        return summary

    run_long = run_stage(
        stage=LONG_2024_2026H1,
        winner_evidence=winner_evidence.resolve(),
        output_root=output_root,
        cache_root=cache_root,
        python=python,
    )
    summary["runs"][LONG_2024_2026H1.name] = run_long
    decision_long = classify_long(run_long)
    summary["long_2024_2026h1_decision"] = decision_long
    summary["classification"] = decision_long["classification"]
    if decision_long["passed"]:
        summary["winner"] = winner_payload.get("winner")
        summary["next_action"] = (
            "Freeze the 912-day one-account four-symbol logic and proceed to live-adapter and paper-trading implementation without changing signal or risk logic."
        )
    else:
        summary["next_action"] = (
            "Record the 912-day regime, symbol, scenario and slot attribution. A new candidate requires a structurally different cause, not threshold fitting."
        )
    atomic_json(summary_path, summary)
    atomic_json(output_root / "shared_account_research_summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--winner-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    summary = run_research(
        winner_evidence=args.winner_evidence.resolve(),
        output_root=args.output,
        cache_root=args.cache,
        summary_path=args.summary,
        python=args.python,
    )
    print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=True))


if __name__ == "__main__":
    main()

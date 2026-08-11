#!/usr/bin/env python3
"""Candidate 60 source-faithful ZaratustraV15 development campaign."""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from trade_ledger_forensics import analyze as analyze_trades

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
C51 = ROOT / "research" / "candidate-51"
WORK = ROOT / ".work" / "candidate-60-zaratustra-v15-v1"
ARTIFACTS = ROOT / "artifacts" / "candidate-60-zaratustra-v15-v1"
EVIDENCE = HERE / "evidence" / "zaratustra-v15-v1"
CACHE = ROOT / ".cache" / "candidate-60-zaratustra-v15-v1"
FREEZE = HERE / "ZARATUSTRA_V15_V1_FREEZE.md"
WARMUP_DAYS = 3
RUNOFF_DAYS = 3


@dataclass(frozen=True)
class Stage:
    name: str
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


DEVELOPMENT = Stage("development", date(2025, 9, 1), date(2025, 9, 14))
POLICY_FRESH = Stage("policy_fresh", date(2025, 11, 3), date(2025, 11, 16))
CASES: dict[str, tuple[str, str]] = {
    "source_combined": ("combined", "source"),
    "edge_combined": ("combined", "edge"),
    "edge_bb": ("bb", "edge"),
    "edge_di": ("di", "edge"),
}
FEATURE_KEYS = (
    "source_selected_branch",
    "source_di_long",
    "source_di_short",
    "source_bb_long",
    "source_bb_short",
    "source_adx_5m",
    "source_dx_5m",
    "source_plus_di_5m",
    "source_minus_di_5m",
    "source_atr_5m",
    "source_mfi_5m",
    "source_bb_upper_5m",
    "source_bb_lower_5m",
)


def safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (date, Path)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(item) for item in value]
    return value


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(safe(value), indent=2, sort_keys=True, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def boundary_ns(day: date, *, end: bool) -> int:
    moment = datetime.combine(day, time.min, tzinfo=timezone.utc)
    if end:
        moment += timedelta(days=1)
        moment -= timedelta(microseconds=1)
    return int(moment.timestamp() * 1_000_000_000)


def stage_record(stage: Stage) -> dict[str, Any]:
    return asdict(stage) | {
        "days": stage.days,
        "data_start": str(stage.start - timedelta(days=WARMUP_DAYS)),
        "data_end": str(stage.end + timedelta(days=RUNOFF_DAYS)),
        "warmup_days": WARMUP_DAYS,
        "runoff_days": RUNOFF_DAYS,
    }


def build_config(stage: Stage, case: str) -> Path:
    family, trigger = CASES[case]
    payload = copy.deepcopy(
        json.loads((C51 / "config.json").read_text(encoding="utf-8"))
    )
    strategy = payload["strategy"]
    for key in (
        "sma_offset_low",
        "sma_offset_high",
        "sma_stop_min_fraction",
        "sma_stop_max_fraction",
        "sma_stop_atr_buffer",
    ):
        strategy.pop(key, None)
    strategy.update(
        {
            "cooldown_minutes": 0,
            "max_hold_minutes": 2_880,
            "funding_flatten_minute": 60,
            "funding_blackout_before_minutes": -1,
            "funding_blackout_after_minutes": -1,
            "picasso_bucket_minutes": 5,
            "picasso_precedence_mode": "corrected_level",
            "picasso_source_effective_leverage": 1.0,
            "picasso_source_stoploss": 0.15,
            "picasso_trailing_positive": 0.0012,
            "picasso_trailing_offset": 0.0107,
            "picasso_emergency_target_fraction": 0.20,
            "picasso_roi_0": 100.0,
            "picasso_roi_416": 100.0,
            "picasso_roi_933": 100.0,
            "picasso_roi_1982": 100.0,
            "z15_family": family,
            "z15_trigger_mode": trigger,
            "z15_dmi_period": 14,
            "z15_mfi_period": 14,
            "z15_atr_period": 14,
            "z15_bb_period": 20,
            "z15_bb_stds": 2.0,
            "z15_mfi_midpoint": 50.0,
            "z15_atr_absolute_max": 0.2,
            "z15_stop_fraction": 0.015,
            "z15_emergency_objective_fraction": 0.20,
            "c60_signal_start_ns": boundary_ns(stage.start, end=False),
            "c60_signal_end_ns": boundary_ns(stage.end, end=True),
            "c60_history_minutes": 6_000,
        }
    )
    path = WORK / "configs" / stage.name / f"{case}.json"
    dump(path, payload)
    return path


def run_case(stage: Stage, case: str) -> dict[str, Any]:
    family, trigger = CASES[case]
    output = ARTIFACTS / stage.name / case
    workspace = WORK / "workspace" / stage.name / case
    for path in (output, workspace):
        if path.exists():
            shutil.rmtree(path)
    output.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            sys.executable,
            str(C51 / "launch.py"),
            "--config",
            str(build_config(stage, case)),
            "--start",
            (stage.start - timedelta(days=WARMUP_DAYS)).isoformat(),
            "--end",
            (stage.end + timedelta(days=RUNOFF_DAYS)).isoformat(),
            "--cache",
            str(CACHE),
            "--output",
            str(output),
            "--workspace",
            str(workspace),
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                (str(C51), str(ROOT / "research" / "candidate-57"), str(HERE))
            ),
        },
        check=False,
    )
    metrics_path = output / "metrics.json"
    diagnostics_path = output / "strategy_diagnostics.json"
    if completed.returncode != 0 or not metrics_path.is_file() or not diagnostics_path.is_file():
        row = {
            "stage": stage_record(stage),
            "case": case,
            "family": family,
            "trigger": trigger,
            "produced": False,
            "returncode": int(completed.returncode),
        }
        dump(EVIDENCE / "cases" / stage.name / f"{case}.json", row)
        return row

    raw = json.loads(metrics_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    starting = number(raw.get("starting_nav"))
    ending = number(raw.get("ending_nav"))
    scored_geo = (
        (ending / starting) ** (1.0 / stage.days) - 1.0
        if starting > 0.0 and ending > 0.0
        else math.nan
    )
    metrics = {
        key: raw.get(key)
        for key in (
            "starting_nav",
            "ending_nav",
            "total_return",
            "max_drawdown",
            "min_equity",
            "trades",
            "wins",
            "losses",
            "win_rate",
            "profit_factor",
            "expectancy_usdt",
            "active_days",
            "largest_winner_share",
            "position_counts_by_symbol",
            "open_position_rows_at_end",
            "active_order_rows_at_end",
            "gate_checks",
        )
    }
    metrics["geometric_daily_growth_scored_window"] = scored_geo
    diagnostic_keys = (
        "source_signals_before_execution_filters",
        "entry_submissions",
        "entry_expirations",
        "selected_symbols",
        "route_counts",
        "unresolved_reason_counts",
        "actionable_family_counts",
        "picasso_trailing_activations",
        "picasso_trailing_exits",
        "picasso_roi_exits",
        "picasso_source_signal_exits",
        "exchange_max_quantity_bounds",
        "max_open_positions_observed",
        "max_simultaneous_entry_intents",
        "global_position_violations",
        "order_rejections",
        "c60_outside_signal_minutes",
        "z15_policy_changed_risk_or_costs",
    )
    row = {
        "stage": stage_record(stage),
        "case": case,
        "family": family,
        "trigger": trigger,
        "produced": True,
        "returncode": 0,
        "independence_claim": (
            "false_to_true_combined_source_episode"
            if trigger == "edge"
            else "raw_source_level_reentries_not_claimed_independent"
        ),
        "metrics": metrics,
        "diagnostics": {key: diagnostics.get(key) for key in diagnostic_keys},
        "trade_forensics": analyze_trades(
            output, int(raw.get("trades") or 0), FEATURE_KEYS
        ),
    }
    dump(EVIDENCE / "cases" / stage.name / f"{case}.json", row)
    return row


def mechanics(row: dict[str, Any]) -> bool:
    if not row.get("produced"):
        return False
    metrics = row.get("metrics") or {}
    diagnostics = row.get("diagnostics") or {}
    checks = metrics.get("gate_checks") or {}
    return (
        int(diagnostics.get("global_position_violations") or 0) == 0
        and int(diagnostics.get("order_rejections") or 0) == 0
        and int(diagnostics.get("max_open_positions_observed") or 0) <= 1
        and int(diagnostics.get("max_simultaneous_entry_intents") or 0) <= 1
        and int(metrics.get("open_position_rows_at_end") or 0) == 0
        and int(metrics.get("active_order_rows_at_end") or 0) == 0
        and bool(checks.get("no_liquidation", True))
        and bool(checks.get("risk_fraction_exactly_three_percent", True))
        and bool((row.get("trade_forensics") or {}).get("ledger_matches_metrics"))
        and int(diagnostics.get("z15_policy_changed_risk_or_costs") or 0) == 0
    )


def positive_primary(row: dict[str, Any], stage: Stage) -> bool:
    metrics = row.get("metrics") or {}
    return (
        mechanics(row)
        and int(metrics.get("trades") or 0) >= max(7, stage.days // 2)
        and number(metrics.get("total_return")) > 0.0
        and number(metrics.get("geometric_daily_growth_scored_window")) > 0.0
        and number(metrics.get("expectancy_usdt")) > 0.0
        and number(metrics.get("profit_factor")) > 1.0
        and number(metrics.get("max_drawdown"), 1.0) <= 0.20
        and number(metrics.get("largest_winner_share"), 1.0) <= 0.75
    )


def summarize_trade_branches(row: dict[str, Any]) -> dict[str, Any]:
    ledger = (row.get("trade_forensics") or {}).get("trade_ledger") or []
    result: dict[str, dict[str, float | int]] = {}
    for trade in ledger:
        branch = str(trade.get("source_selected_branch") or "UNKNOWN")
        item = result.setdefault(branch, {"trades": 0, "wins": 0, "sum_r": 0.0})
        item["trades"] = int(item["trades"]) + 1
        actual_r = number(trade.get("actual_r"))
        item["wins"] = int(item["wins"]) + int(actual_r > 0.0)
        item["sum_r"] = number(item["sum_r"]) + actual_r
    return result


def render(result: dict[str, Any]) -> None:
    lines = [
        "# Candidate 60 — ZaratustraV15 source diagnostic",
        "",
        "Only edge cells make an independent-opportunity claim. Source-combined is retained to show the raw public level behavior.",
        "",
    ]
    for stage_name in ("development", "policy_fresh"):
        rows = result.get(stage_name) or {}
        lines += [f"## {stage_name}", ""]
        if not rows:
            lines += ["Not consumed.", ""]
            continue
        lines += [
            "| cell | trades | W/L | PF | geo/day scored | return | MDD | expectancy |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for case, row in rows.items():
            metrics = row.get("metrics") or {}
            lines.append(
                f"| {case} | {metrics.get('trades')} | {metrics.get('wins')}/{metrics.get('losses')} | "
                f"{metrics.get('profit_factor')} | {metrics.get('geometric_daily_growth_scored_window')} | "
                f"{metrics.get('total_return')} | {metrics.get('max_drawdown')} | "
                f"{metrics.get('expectancy_usdt')} |"
            )
        lines.append("")
    lines += [
        "## Decision",
        "",
        f"- development primary positive: **{result.get('development_primary_positive')}**",
        f"- policy-fresh consumed: **{result.get('policy_fresh_consumed')}**",
        f"- policy-fresh primary positive: **{result.get('policy_fresh_primary_positive')}**",
        f"- decision: **{result.get('decision')}**",
        "",
        "A positive result grants component status only. Long validation and integration remain unauthorized until actual one-slot overlap and conflict are evaluated.",
    ]
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if not FREEZE.is_file():
        raise RuntimeError("ZARATUSTRA_V15_V1 freeze is missing")
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    development = {case: run_case(DEVELOPMENT, case) for case in CASES}
    branch_diagnostics = {
        case: summarize_trade_branches(row)
        for case, row in development.items()
    }
    dev_primary_positive = positive_primary(
        development["edge_combined"], DEVELOPMENT
    )
    policy_fresh: dict[str, dict[str, Any]] = {}
    fresh_positive = False
    if dev_primary_positive:
        policy_fresh["edge_combined"] = run_case(POLICY_FRESH, "edge_combined")
        fresh_positive = positive_primary(
            policy_fresh["edge_combined"], POLICY_FRESH
        )
        decision = (
            "Z15_EDGE_COMBINED_COMPONENT_SUPPORTED_OVERLAP_DIAGNOSTIC_NEXT"
            if fresh_positive
            else "Z15_EDGE_COMBINED_POLICY_FRESH_REJECTED_NO_RETUNING"
        )
    else:
        decision = "Z15_EDGE_COMBINED_DEVELOPMENT_REJECTED_FRESH_UNTOUCHED"

    result = {
        "frozen_before_results": True,
        "threshold_search_used": False,
        "development": development,
        "development_branch_diagnostics": branch_diagnostics,
        "development_primary_positive": dev_primary_positive,
        "policy_fresh_consumed": bool(policy_fresh),
        "policy_fresh": policy_fresh,
        "policy_fresh_primary_positive": fresh_positive,
        "decision": decision,
        "long_evaluation_authorized": False,
        "integration_authorized": fresh_positive,
    }
    dump(EVIDENCE / "comparison.json", result)
    render(result)
    all_rows = list(development.values()) + list(policy_fresh.values())
    return 0 if all(mechanics(row) for row in all_rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())

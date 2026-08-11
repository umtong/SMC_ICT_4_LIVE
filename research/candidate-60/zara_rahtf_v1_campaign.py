#!/usr/bin/env python3
"""Candidate 60 ZaratustraV5 × RAHTF clean-state causal campaign."""
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
WORK = ROOT / ".work" / "candidate-60-zara-rahtf-v1"
ARTIFACTS = ROOT / "artifacts" / "candidate-60-zara-rahtf-v1"
EVIDENCE = HERE / "evidence" / "zara-rahtf-v1"
CACHE = ROOT / ".cache" / "candidate-60-zara-rahtf-v1"
FREEZE = HERE / "ZARA_RAHTF_V1_FREEZE.md"
WARMUP_DAYS = 10
RUNOFF_DAYS = 2


@dataclass(frozen=True)
class Stage:
    name: str
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


DEVELOPMENT = Stage("development", date(2025, 8, 4), date(2025, 8, 17))
POLICY_FRESH = Stage("policy_fresh", date(2025, 10, 6), date(2025, 10, 19))

# source trigger semantics, state gate
DEVELOPMENT_CASES: dict[str, tuple[str, str]] = {
    "edge_control": ("edge", "control"),
    "edge_rahtf": ("edge", "rahtf_clean"),
    "level_control": ("level", "control"),
    "level_rahtf": ("level", "rahtf_clean"),
}
FRESH_CASES = ("edge_control", "edge_rahtf")
FEATURE_KEYS = (
    "rsi_5m",
    "rsi_15m",
    "rsi_30m",
    "plus_di_5m",
    "plus_di_15m",
    "plus_di_30m",
    "minus_di_5m",
    "minus_di_15m",
    "minus_di_30m",
    "source_score",
    "source_stop_fraction",
    "rahtf_confirmed_label_code",
    "rahtf_slow_eff",
    "c60_rahtf_clean_state_pass",
    "c60_rahtf_label_pass",
    "c60_rahtf_slow_drift_pass",
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
    trigger, _ = DEVELOPMENT_CASES[case]
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
            "max_hold_minutes": 480,
            "funding_flatten_minute": 60,
            "funding_blackout_before_minutes": -1,
            "funding_blackout_after_minutes": -1,
            "picasso_bucket_minutes": 5,
            "picasso_precedence_mode": "corrected_level",
            "picasso_source_effective_leverage": 1.0,
            "picasso_source_stoploss": 0.0296,
            "picasso_trailing_positive": 0.0013,
            "picasso_trailing_offset": 0.0071,
            "picasso_emergency_target_fraction": 0.20,
            "picasso_roi_0": 100.0,
            "picasso_roi_416": 100.0,
            "picasso_roi_933": 100.0,
            "picasso_roi_1982": 100.0,
            "zara_trigger_mode": trigger,
            "zara_side_mode": "both",
            "zara_risk_mode": "source_fraction",
            "zara_rsi_period": 14,
            "zara_di_period": 14,
            "zara_bb_period": 20,
            "zara_rsi_threshold": 50.0,
            "zara_di_threshold": 25.0,
            "zara_source_stop_fraction": 0.0296,
            "zara_target_fraction": 0.20,
            "zara_structural_lookback_5m": 8,
            "zara_atr_period_5m": 14,
            "zara_stop_atr_buffer": 0.25,
            "zara_min_stop_fraction": 0.0015,
            "c60_signal_start_ns": boundary_ns(stage.start, end=False),
            "c60_signal_end_ns": boundary_ns(stage.end, end=True),
            "c60_history_minutes": 16_000,
        }
    )
    path = WORK / "configs" / stage.name / f"{case}.json"
    dump(path, payload)
    return path


def run_case(stage: Stage, case: str) -> dict[str, Any]:
    trigger, state = DEVELOPMENT_CASES[case]
    output = ARTIFACTS / stage.name / case
    workspace = WORK / "workspace" / stage.name / case
    for path in (output, workspace):
        if path.exists():
            shutil.rmtree(path)
    output.mkdir(parents=True, exist_ok=True)
    data_start = stage.start - timedelta(days=WARMUP_DAYS)
    data_end = stage.end + timedelta(days=RUNOFF_DAYS)
    completed = subprocess.run(
        [
            sys.executable,
            str(C51 / "launch.py"),
            "--config",
            str(build_config(stage, case)),
            "--start",
            data_start.isoformat(),
            "--end",
            data_end.isoformat(),
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
            "C60_ZARA_RAHTF_MODE": state,
        },
        check=False,
    )
    metrics_path = output / "metrics.json"
    diagnostics_path = output / "strategy_diagnostics.json"
    if (
        completed.returncode != 0
        or not metrics_path.is_file()
        or not diagnostics_path.is_file()
    ):
        row = {
            "stage": stage_record(stage),
            "case": case,
            "source_trigger": trigger,
            "state_gate": state,
            "produced": False,
            "returncode": int(completed.returncode),
        }
        dump(EVIDENCE / "cases" / stage.name / f"{case}.json", row)
        return row

    raw_metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    starting = number(raw_metrics.get("starting_nav"))
    ending = number(raw_metrics.get("ending_nav"))
    signal_geo = (
        (ending / starting) ** (1.0 / stage.days) - 1.0
        if starting > 0.0 and ending > 0.0
        else math.nan
    )
    metric_keys = (
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
    diagnostic_keys = (
        "source_signals_before_execution_filters",
        "entry_submissions",
        "entry_expirations",
        "selected_symbols",
        "route_counts",
        "unresolved_reason_counts",
        "picasso_trailing_activations",
        "picasso_trailing_exits",
        "picasso_roi_exits",
        "picasso_source_signal_exits",
        "zara_final_entry_blackouts",
        "exchange_max_quantity_bounds",
        "max_open_positions_observed",
        "max_simultaneous_entry_intents",
        "global_position_violations",
        "order_rejections",
        "c60_outside_signal_minutes",
        "c60_policy_changed_execution_or_risk",
    )
    metrics = {key: raw_metrics.get(key) for key in metric_keys}
    metrics["geometric_daily_growth_signal_window"] = signal_geo
    expected = int(raw_metrics.get("trades") or 0)
    row = {
        "stage": stage_record(stage),
        "case": case,
        "source_trigger": trigger,
        "state_gate": state,
        "produced": True,
        "returncode": 0,
        "independence_claim": (
            "one_false_to_true_source_transition_per_trade"
            if trigger == "edge"
            else "raw_level_reentries_not_claimed_as_independent"
        ),
        "metrics": metrics,
        "diagnostics": {key: diagnostics.get(key) for key in diagnostic_keys},
        "trade_forensics": analyze_trades(output, expected, FEATURE_KEYS),
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
        and int(diagnostics.get("c60_policy_changed_execution_or_risk") or 0) == 0
    )


def trade_key(row: dict[str, Any]) -> tuple[Any, Any, Any]:
    return row.get("episode_ts"), row.get("symbol"), row.get("side")


def is_positive_trailing(row: dict[str, Any]) -> bool:
    return (
        "TRAILING" in str(row.get("exit_reason") or "")
        and number(row.get("actual_r")) > 0.0
    )


def paired_effect(control: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    control_rows = (control.get("trade_forensics") or {}).get("trade_ledger") or []
    candidate_rows = (candidate.get("trade_forensics") or {}).get("trade_ledger") or []
    left = {trade_key(row): row for row in control_rows}
    right = {trade_key(row): row for row in candidate_rows}
    shared_keys = set(left) & set(right)
    removed = [left[key] for key in sorted(set(left) - set(right), key=str)]
    added = [right[key] for key in sorted(set(right) - set(left), key=str)]
    control_trailing = [row for row in control_rows if is_positive_trailing(row)]
    preserved_trailing = [
        left[key]
        for key in shared_keys
        if is_positive_trailing(left[key]) and number(right[key].get("actual_r")) > 0.0
    ]
    best = max(control_rows, key=lambda row: number(row.get("actual_r"), -math.inf), default=None)
    best_key = trade_key(best) if best is not None else None
    return {
        "shared_trade_keys": len(shared_keys),
        "removed_control_trades": len(removed),
        "added_candidate_trades": len(added),
        "removed_negative_trades": sum(number(row.get("actual_r")) < 0.0 for row in removed),
        "removed_positive_trades": sum(number(row.get("actual_r")) > 0.0 for row in removed),
        "removed_sum_r": sum(number(row.get("actual_r")) for row in removed),
        "added_sum_r": sum(number(row.get("actual_r")) for row in added),
        "control_positive_trailing_count": len(control_trailing),
        "preserved_positive_trailing_count": len(preserved_trailing),
        "positive_trailing_preservation_share": (
            len(preserved_trailing) / len(control_trailing)
            if control_trailing
            else None
        ),
        "best_control_trade_preserved_positive": (
            best_key in right and number(right[best_key].get("actual_r")) > 0.0
            if best_key is not None
            else False
        ),
        "best_control_trade_r": best.get("actual_r") if best is not None else None,
        "removed_examples": removed[:20],
        "added_examples": added[:20],
    }


def state_rejections(row: dict[str, Any]) -> int:
    reasons = (row.get("diagnostics") or {}).get("unresolved_reason_counts") or {}
    return sum(
        int(reasons.get(name) or 0)
        for name in (
            "C60_RAHTF_CONTEXT_NOT_READY",
            "C60_RAHTF_CONFIRMED_LABEL_REJECTED",
            "C60_RAHTF_SLOW_DRIFT_REJECTED",
        )
    )


def positive_component(row: dict[str, Any], stage: Stage) -> bool:
    metrics = row.get("metrics") or {}
    return (
        mechanics(row)
        and int(metrics.get("trades") or 0) >= max(7, stage.days // 2)
        and number(metrics.get("total_return")) > 0.0
        and number(metrics.get("geometric_daily_growth_signal_window")) > 0.0
        and number(metrics.get("expectancy_usdt")) > 0.0
        and number(metrics.get("profit_factor")) > 1.0
        and number(metrics.get("max_drawdown"), 1.0) <= 0.20
        and number(metrics.get("largest_winner_share"), 1.0) <= 0.75
    )


def compare_pair(
    control: dict[str, Any], candidate: dict[str, Any], stage: Stage
) -> dict[str, Any]:
    left = control.get("metrics") or {}
    right = candidate.get("metrics") or {}
    effect = paired_effect(control, candidate)
    preservation = effect.get("positive_trailing_preservation_share")
    causal_support = (
        state_rejections(candidate) > 0
        and int(effect.get("removed_negative_trades") or 0)
        > int(effect.get("removed_positive_trades") or 0)
        and (preservation is None or number(preservation) >= 0.50)
        and bool(effect.get("best_control_trade_preserved_positive"))
    )
    improved = (
        number(right.get("total_return")) > number(left.get("total_return"))
        and number(right.get("expectancy_usdt")) > number(left.get("expectancy_usdt"))
        and number(right.get("profit_factor")) > number(left.get("profit_factor"))
    )
    return {
        "mechanically_valid": mechanics(control) and mechanics(candidate),
        "state_rejections": state_rejections(candidate),
        "metric_delta": {
            key: number(right.get(key)) - number(left.get(key))
            for key in (
                "total_return",
                "geometric_daily_growth_signal_window",
                "max_drawdown",
                "expectancy_usdt",
                "profit_factor",
                "trades",
            )
        },
        "paired_effect": effect,
        "causal_state_hypothesis_supported": causal_support,
        "aggregate_improved": improved,
        "candidate_positive_component": positive_component(candidate, stage),
        "promotion_ready": (
            mechanics(control)
            and mechanics(candidate)
            and causal_support
            and improved
            and positive_component(candidate, stage)
        ),
    }


def render(result: dict[str, Any]) -> None:
    lines = [
        "# Candidate 60 — ZaratustraV5 × RAHTF clean-state result",
        "",
        "This is a causal state-factor study. Level re-entries are reported as raw trades; only rising-edge cells are eligible for an independent-opportunity claim.",
        "",
    ]
    for stage_name in ("development", "policy_fresh"):
        rows = result.get(stage_name) or {}
        lines += [
            f"## {stage_name}",
            "",
        ]
        if not rows:
            lines += ["Not consumed.", ""]
            continue
        lines += [
            "| cell | trades | W/L | PF | geo/day scored | return | MDD | expectancy | state rejects |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for case, row in rows.items():
            metrics = row.get("metrics") or {}
            lines.append(
                f"| {case} | {metrics.get('trades')} | {metrics.get('wins')}/{metrics.get('losses')} | "
                f"{metrics.get('profit_factor')} | {metrics.get('geometric_daily_growth_signal_window')} | "
                f"{metrics.get('total_return')} | {metrics.get('max_drawdown')} | "
                f"{metrics.get('expectancy_usdt')} | {state_rejections(row)} |"
            )
        lines.append("")
    development_comparison = result.get("development_edge_comparison") or {}
    fresh_comparison = result.get("policy_fresh_edge_comparison") or {}
    lines += [
        "## Causal decision",
        "",
        f"- development promotion ready: **{development_comparison.get('promotion_ready')}**",
        f"- development state hypothesis supported: **{development_comparison.get('causal_state_hypothesis_supported')}**",
        f"- policy-fresh consumed: **{result.get('policy_fresh_consumed')}**",
        f"- policy-fresh component supported: **{fresh_comparison.get('promotion_ready', False)}**",
        f"- decision: **{result.get('decision')}**",
        "",
        "No long evaluation or integration is authorized by a development-only improvement. A policy-fresh positive result grants component status only; overlap with the delayed jump specialist must then be evaluated in one continuous one-slot account.",
    ]
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if not FREEZE.is_file():
        raise RuntimeError("candidate-60 ZARA_RAHTF_V1 freeze is missing")
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    development = {
        case: run_case(DEVELOPMENT, case)
        for case in DEVELOPMENT_CASES
    }
    dev_compare = compare_pair(
        development["edge_control"], development["edge_rahtf"], DEVELOPMENT
    )

    policy_fresh: dict[str, dict[str, Any]] = {}
    fresh_compare: dict[str, Any] = {}
    if bool(dev_compare.get("promotion_ready")):
        policy_fresh = {
            case: run_case(POLICY_FRESH, case)
            for case in FRESH_CASES
        }
        fresh_compare = compare_pair(
            policy_fresh["edge_control"],
            policy_fresh["edge_rahtf"],
            POLICY_FRESH,
        )
        decision = (
            "RAHTF_ZARA_COMPONENT_SUPPORTED_INTEGRATION_DIAGNOSTIC_NEXT"
            if bool(fresh_compare.get("promotion_ready"))
            else "RAHTF_ZARA_POLICY_FRESH_REJECTED_NO_RETUNING"
        )
    else:
        decision = "RAHTF_ZARA_DEVELOPMENT_NOT_PROMOTED_FRESH_UNTOUCHED"

    result = {
        "frozen_before_results": True,
        "threshold_search_used": False,
        "development": development,
        "development_edge_comparison": dev_compare,
        "policy_fresh_consumed": bool(policy_fresh),
        "policy_fresh": policy_fresh,
        "policy_fresh_edge_comparison": fresh_compare,
        "decision": decision,
        "long_evaluation_authorized": False,
        "integration_authorized": decision
        == "RAHTF_ZARA_COMPONENT_SUPPORTED_INTEGRATION_DIAGNOSTIC_NEXT",
    }
    dump(EVIDENCE / "comparison.json", result)
    render(result)

    all_rows = list(development.values()) + list(policy_fresh.values())
    return 0 if all(mechanics(row) for row in all_rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())

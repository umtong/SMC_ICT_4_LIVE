#!/usr/bin/env python3
"""Policy-fresh source-control comparison for the Ichi profit buffer."""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from datetime import date
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
WORK = ROOT / ".work" / "candidate-57-ichi-source-exit-profit-buffer-v4"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-ichi-source-exit-profit-buffer-v4"
EVIDENCE = HERE / "evidence" / "ichi-source-exit-profit-buffer-v4"
CACHE = ROOT / ".cache" / "candidate-57-ichi-source-exit-profit-buffer-v4"
FREEZE = HERE / "ICHI_SOURCE_EXIT_PROFIT_BUFFER_V4_FREEZE.md"


@dataclass(frozen=True)
class Stage:
    name: str
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


FRESH = Stage("policy_fresh_2025_02", date(2025, 2, 1), date(2025, 2, 28))
CASES = {
    "source_control": C51 / "strategy_ichi_v2_fast_base.py",
    "profit_buffer": HERE / "strategy_ichi_source_exit_profit_buffer_v4.py",
}
FEATURE_KEYS = (
    "fan_magnitude",
    "fan_magnitude_gain",
    "source_score",
    "cloud_top",
    "cloud_bottom",
    "trend_close_1h",
    "trend_close_8h",
    "profit_buffer_armed",
    "profit_buffer_armed_elapsed_minutes",
    "profit_buffer_armed_after_cost_fraction",
)


def safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
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


def build_config(case: str) -> Path:
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
            "picasso_source_stoploss": 0.040,
            "picasso_trailing_positive": 0.0,
            "picasso_trailing_offset": 0.0,
            "picasso_emergency_target_fraction": 0.080,
            "picasso_roi_0": 0.015,
            "picasso_roi_416": 0.015,
            "picasso_roi_933": 0.015,
            "picasso_roi_1982": 0.015,
            "ichi_trigger_mode": "level",
            "ichi_side_mode": "short",
            "ichi_profile": "report_inferred",
            "ichi_shift_inputs_one_candle": True,
            "ichi_above_cloud_level": 1,
            "ichi_bullish_level": 4,
            "ichi_fan_shift_value": 3,
            "ichi_min_fan_magnitude_gain": 1.0013,
            "ichi_conversion_period": 20,
            "ichi_base_period": 60,
            "ichi_lagging_span_period": 120,
            "ichi_displacement": 30,
            "ichi_stop_fraction": 0.040,
            "ichi_objective_fraction": 0.080,
            "ichi_roi_enabled": True,
            "ichi_ignore_roi_if_entry_signal": True,
            "ichi_roi_0": 0.015,
            "ichi_roi_t1_minutes": 10_000,
            "ichi_roi_t1": 0.015,
            "ichi_roi_t2_minutes": 20_000,
            "ichi_roi_t2": 0.015,
            "ichi_roi_t3_minutes": 30_000,
            "ichi_roi_t3": 0.015,
            "ichi_trailing_enabled": False,
            "ichi_trailing_positive": 0.0,
            "ichi_trailing_offset": 0.0,
            "ichi_trailing_only_offset_is_reached": True,
            "ichi_exit_indicator": "trend_close_1.5h",
        }
    )
    if case == "profit_buffer":
        strategy["ichi_profit_buffer_round_trip_cost_fraction"] = 0.0021
    path = WORK / "configs" / f"{case}.json"
    dump(path, payload)
    return path


def run_case(case: str) -> dict[str, Any]:
    strategy_source = CASES[case]
    if not strategy_source.is_file():
        raise RuntimeError(f"missing assembled strategy source: {strategy_source}")
    shutil.copy2(strategy_source, C51 / "strategy.py")
    output = ARTIFACTS / FRESH.name / case
    workspace = WORK / "workspace" / FRESH.name / case
    for path in (output, workspace):
        if path.exists():
            shutil.rmtree(path)
    output.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(C51 / "launch.py"),
        "--config",
        str(build_config(case)),
        "--start",
        FRESH.start.isoformat(),
        "--end",
        FRESH.end.isoformat(),
        "--cache",
        str(CACHE),
        "--output",
        str(output),
        "--workspace",
        str(workspace),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(C51)},
        check=False,
    )
    metrics_path = output / "metrics.json"
    diagnostics_path = output / "strategy_diagnostics.json"
    if completed.returncode != 0 or not metrics_path.is_file() or not diagnostics_path.is_file():
        row = {
            "case": case,
            "stage": asdict(FRESH) | {"days": FRESH.days},
            "produced": False,
            "returncode": int(completed.returncode),
        }
        dump(EVIDENCE / "cases" / f"{case}.json", row)
        return row

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    metric_keys = (
        "starting_nav",
        "ending_nav",
        "total_return",
        "geometric_daily_growth",
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
        "ichi_roi_exits",
        "ichi_source_signal_exits",
        "ichi_roi_ignored_active_signal_minutes",
        "ichi_profit_buffer_arms",
        "ichi_profit_buffer_disarms",
        "ichi_profit_buffer_immediate_nonpositive_exits",
        "ichi_profit_buffer_break_even_exits",
        "ichi_profit_buffer_confirmed_exits",
        "ichi_profit_buffer_roi_resolutions",
        "ichi_profit_buffer_policy_changed_entries",
        "ichi_profit_buffer_thresholds_searched",
        "max_open_positions_observed",
        "max_simultaneous_entry_intents",
        "global_position_violations",
        "order_rejections",
    )
    expected = int(metrics.get("trades") or 0)
    row = {
        "case": case,
        "stage": asdict(FRESH) | {"days": FRESH.days},
        "produced": True,
        "returncode": 0,
        "metrics": {key: metrics.get(key) for key in metric_keys},
        "diagnostics": {key: diagnostics.get(key) for key in diagnostic_keys},
        "trade_forensics": analyze_trades(output, expected, FEATURE_KEYS),
    }
    dump(EVIDENCE / "cases" / f"{case}.json", row)
    return row


def account_ok(row: dict[str, Any]) -> bool:
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
        and bool((row.get("trade_forensics") or {}).get("ledger_matches_metrics"))
    )


def trade_key(row: dict[str, Any]) -> tuple[Any, Any, Any]:
    return row.get("episode_ts"), row.get("symbol"), row.get("side")


def compare(control: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    control_ledger = (control.get("trade_forensics") or {}).get("trade_ledger") or []
    candidate_ledger = (candidate.get("trade_forensics") or {}).get("trade_ledger") or []
    control_by_key = {trade_key(row): row for row in control_ledger}
    candidate_by_key = {trade_key(row): row for row in candidate_ledger}
    paired: list[dict[str, Any]] = []
    for key in sorted(set(control_by_key) & set(candidate_by_key), key=str):
        left = control_by_key[key]
        right = candidate_by_key[key]
        paired.append(
            {
                "key": list(key),
                "control_exit_reason": left.get("exit_reason"),
                "candidate_exit_reason": right.get("exit_reason"),
                "control_r": left.get("actual_r"),
                "candidate_r": right.get("actual_r"),
                "delta_r": number(right.get("actual_r")) - number(left.get("actual_r")),
                "candidate_profit_buffer_armed": right.get("profit_buffer_armed"),
                "candidate_armed_after_cost_fraction": right.get(
                    "profit_buffer_armed_after_cost_fraction"
                ),
            }
        )
    changed = [
        row
        for row in paired
        if row.get("control_exit_reason") != row.get("candidate_exit_reason")
        or abs(number(row.get("delta_r"))) > 1e-12
    ]
    control_only = [
        row for key, row in control_by_key.items() if key not in candidate_by_key
    ]
    candidate_only = [
        row for key, row in candidate_by_key.items() if key not in control_by_key
    ]
    control_metrics = control.get("metrics") or {}
    candidate_metrics = candidate.get("metrics") or {}
    candidate_diagnostics = candidate.get("diagnostics") or {}
    mechanics = account_ok(control) and account_ok(candidate)
    arms = int(candidate_diagnostics.get("ichi_profit_buffer_arms") or 0)
    source_delta = {
        "total_return": number(candidate_metrics.get("total_return"))
        - number(control_metrics.get("total_return")),
        "geometric_daily_growth": number(
            candidate_metrics.get("geometric_daily_growth")
        )
        - number(control_metrics.get("geometric_daily_growth")),
        "expectancy_usdt": number(candidate_metrics.get("expectancy_usdt"))
        - number(control_metrics.get("expectancy_usdt")),
        "max_drawdown": number(candidate_metrics.get("max_drawdown"))
        - number(control_metrics.get("max_drawdown")),
        "trades": int(candidate_metrics.get("trades") or 0)
        - int(control_metrics.get("trades") or 0),
    }
    pf = candidate_metrics.get("profit_factor")
    positive_pf = (
        number(pf) > 1.0
        if pf is not None
        else int(candidate_metrics.get("wins") or 0) > 0
        and int(candidate_metrics.get("losses") or 0) == 0
    )
    causal_support = (
        mechanics
        and arms > 0
        and source_delta["total_return"] > 0.0
        and source_delta["expectancy_usdt"] > 0.0
        and number(candidate_metrics.get("expectancy_usdt")) > 0.0
        and positive_pf
        and int(candidate_diagnostics.get("ichi_profit_buffer_thresholds_searched") or 0)
        == 0
    )
    if not mechanics:
        decision = "IMPLEMENTATION_ERROR_NO_ALPHA_CONCLUSION"
    elif arms == 0:
        decision = "UNDERINFORMATIVE_NO_PROFITABLE_SOURCE_CROSS"
    elif causal_support:
        decision = "RETAIN_CAUSAL_COMPONENT_NOT_LONG_READY"
    else:
        decision = "POLICY_FRESH_HYPOTHESIS_REJECTED_NO_RETUNING"
    return {
        "mechanically_valid": mechanics,
        "decision": decision,
        "integration_authorized": False,
        "long_evaluation_authorized": False,
        "thresholds_searched": False,
        "candidate_arms": arms,
        "candidate_resolutions": {
            key: candidate_diagnostics.get(key)
            for key in (
                "ichi_profit_buffer_disarms",
                "ichi_profit_buffer_immediate_nonpositive_exits",
                "ichi_profit_buffer_break_even_exits",
                "ichi_profit_buffer_confirmed_exits",
                "ichi_profit_buffer_roi_resolutions",
            )
        },
        "source_control_delta": source_delta,
        "causal_support": causal_support,
        "paired_trades": len(paired),
        "changed_paired_trades": changed,
        "control_only_trades": control_only,
        "candidate_only_trades": candidate_only,
        "control": control,
        "candidate": candidate,
    }


def render(result: dict[str, Any]) -> None:
    control = result.get("control") or {}
    candidate = result.get("candidate") or {}
    cm = control.get("metrics") or {}
    pm = candidate.get("metrics") or {}
    delta = result.get("source_control_delta") or {}
    lines = [
        "# Ichi source-exit profit-buffer v4 policy-fresh result",
        "",
        f"- interval: {FRESH.start} to {FRESH.end}",
        f"- mechanically valid: {result.get('mechanically_valid')}",
        f"- decision: `{result.get('decision')}`",
        f"- thresholds searched: {result.get('thresholds_searched')}",
        f"- integration authorized: {result.get('integration_authorized')}",
        f"- long evaluation authorized: {result.get('long_evaluation_authorized')}",
        "",
        "| case | trades | W/L | PF | expectancy USDT | geo/day | return | MDD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| source control | {cm.get('trades')} | {cm.get('wins')}/{cm.get('losses')} | {cm.get('profit_factor')} | {cm.get('expectancy_usdt')} | {cm.get('geometric_daily_growth')} | {cm.get('total_return')} | {cm.get('max_drawdown')} |",
        f"| profit buffer | {pm.get('trades')} | {pm.get('wins')}/{pm.get('losses')} | {pm.get('profit_factor')} | {pm.get('expectancy_usdt')} | {pm.get('geometric_daily_growth')} | {pm.get('total_return')} | {pm.get('max_drawdown')} |",
        "",
        "## Frozen causal effect",
        "",
        f"- arms: {result.get('candidate_arms')}",
        f"- resolutions: {result.get('candidate_resolutions')}",
        f"- return delta: {delta.get('total_return')}",
        f"- geo/day delta: {delta.get('geometric_daily_growth')}",
        f"- expectancy delta USDT: {delta.get('expectancy_usdt')}",
        f"- MDD delta: {delta.get('max_drawdown')}",
        f"- paired trades: {result.get('paired_trades')}",
        f"- changed paired trades: {len(result.get('changed_paired_trades') or [])}",
        f"- control-only trades: {len(result.get('control_only_trades') or [])}",
        f"- candidate-only trades: {len(result.get('candidate_only_trades') or [])}",
        "",
        "The result is interpreted by the predeclared transaction-level prediction, not by an aggregate pass/fail gate.  A rejection closes this exact lifecycle repair without threshold or hold-time retuning.",
    ]
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if not FREEZE.is_file():
        raise RuntimeError(f"missing freeze: {FREEZE}")
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    cases = {name: run_case(name) for name in ("source_control", "profit_buffer")}
    result = compare(cases["source_control"], cases["profit_buffer"])
    dump(EVIDENCE / "comparison.json", result)
    render(result)
    return 0 if result.get("mechanically_valid") else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Two-regime causal diagnostic for the frozen TrendRider pullback branch."""
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
WORK = ROOT / ".work" / "candidate-57-trendrider-pullback-long-v1"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-trendrider-pullback-long-v1"
EVIDENCE = HERE / "evidence" / "trendrider-pullback-long-v1"
CACHE = ROOT / ".cache" / "candidate-57-trendrider-pullback-long-v1"
FREEZE = HERE / "TRENDRIDER_PULLBACK_LONG_V1_FREEZE.md"


@dataclass(frozen=True)
class Stage:
    name: str
    start: date
    end: date
    expected_state: str

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


STAGES = (
    Stage("bull_expansion_development", date(2024, 11, 1), date(2024, 11, 14), "INTENDED_BULL_CONTINUATION"),
    Stage("contrast_development", date(2025, 2, 1), date(2025, 2, 14), "CONTRAST_STATE"),
)
FEATURE_KEYS = (
    "source_confidence_numeric",
    "source_confidence_raw",
    "rsi",
    "adx",
    "plus_di",
    "minus_di",
    "volume_ratio",
    "btc_rsi_1h",
    "macd_hist",
    "source_is_bull",
    "source_pullback",
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


def build_config() -> Path:
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
            "max_hold_minutes": 1440,
            "funding_flatten_minute": 60,
            "funding_blackout_before_minutes": -1,
            "funding_blackout_after_minutes": -1,
            "picasso_bucket_minutes": 60,
            "picasso_precedence_mode": "corrected_level",
            "picasso_source_effective_leverage": 1.0,
            "picasso_source_stoploss": 0.060,
            "picasso_trailing_positive": 0.030,
            "picasso_trailing_offset": 0.050,
            "picasso_emergency_target_fraction": 0.229,
            "picasso_roi_0": 0.229,
            "picasso_roi_416": 0.044,
            "picasso_roi_933": 0.0,
            "picasso_roi_1982": 0.0,
            "trendrider_ema_fast": 9,
            "trendrider_ema_slow": 16,
            "trendrider_ema_regime_fast": 50,
            "trendrider_ema_regime_slow": 200,
            "trendrider_rsi_period": 16,
            "trendrider_adx_period": 14,
            "trendrider_volume_ema_period": 20,
            "trendrider_obv_ema_period": 20,
            "trendrider_rsi_pullback_low": 30.0,
            "trendrider_rsi_pullback_high": 65.0,
            "trendrider_adx_threshold": 18.0,
            "trendrider_volume_factor": 0.7,
            "trendrider_pullback_tolerance": 0.02,
            "trendrider_min_confidence": 5,
            "trendrider_stop_fraction": 0.06,
            "trendrider_emergency_objective_fraction": 0.229,
            "trendrider_trailing_positive": 0.03,
            "trendrider_trailing_offset": 0.05,
            "trendrider_roi_0": 0.229,
            "trendrider_roi_t1_minutes": 124,
            "trendrider_roi_t1": 0.136,
            "trendrider_roi_t2_minutes": 290,
            "trendrider_roi_t2": 0.044,
            "trendrider_roi_t3_minutes": 764,
            "trendrider_roi_t3": 0.0,
            "trendrider_rsi_exit": 78.0,
            "trendrider_early_loss_2h": -0.015,
            "trendrider_early_loss_4h": 0.0,
            "trendrider_early_loss_8h": 0.005,
            "trendrider_early_loss_16h": 0.010,
            "trendrider_round_trip_cost_fraction": 0.0021,
            "trendrider_history_minutes": 16000,
        }
    )
    path = WORK / "config.json"
    dump(path, payload)
    return path


def run_stage(stage: Stage, config: Path) -> dict[str, Any]:
    output = ARTIFACTS / stage.name
    workspace = WORK / "workspace" / stage.name
    for path in (output, workspace):
        if path.exists():
            shutil.rmtree(path)
    output.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(C51 / "launch.py"),
        "--config",
        str(config),
        "--start",
        stage.start.isoformat(),
        "--end",
        stage.end.isoformat(),
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
            "stage": asdict(stage) | {"days": stage.days},
            "produced": False,
            "returncode": int(completed.returncode),
        }
        dump(EVIDENCE / "cases" / f"{stage.name}.json", row)
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
        "actionable_family_counts",
        "trendrider_roi_exits",
        "trendrider_trailing_activations",
        "trendrider_trailing_exits",
        "trendrider_indicator_exits",
        "trendrider_early_loss_cut_2h",
        "trendrider_early_loss_cut_4h",
        "trendrider_early_loss_cut_8h",
        "trendrider_early_loss_cut_16h",
        "trendrider_time_exit_24h",
        "trendrider_other_public_entry_branches_imported",
        "trendrider_private_layers_used",
        "trendrider_daily_informative_filter_used",
        "trendrider_parameter_grid_used",
        "max_open_positions_observed",
        "max_simultaneous_entry_intents",
        "global_position_violations",
        "order_rejections",
    )
    expected = int(metrics.get("trades") or 0)
    row = {
        "stage": asdict(stage) | {"days": stage.days},
        "produced": True,
        "returncode": 0,
        "metrics": {key: metrics.get(key) for key in metric_keys},
        "diagnostics": {key: diagnostics.get(key) for key in diagnostic_keys},
        "trade_forensics": analyze_trades(output, expected, FEATURE_KEYS),
    }
    dump(EVIDENCE / "cases" / f"{stage.name}.json", row)
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


def classify(rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    bull = rows[STAGES[0].name]
    contrast = rows[STAGES[1].name]
    mechanically_valid = account_ok(bull) and account_ok(contrast)
    bm = bull.get("metrics") or {}
    cm = contrast.get("metrics") or {}
    bull_trades = int(bm.get("trades") or 0)
    contrast_trades = int(cm.get("trades") or 0)
    bull_expectancy = number(bm.get("expectancy_usdt"))
    bull_return = number(bm.get("total_return"))
    bull_pf_raw = bm.get("profit_factor")
    bull_pf = number(bull_pf_raw)
    repeated_opportunity = bull_trades >= 7
    bull_positive = bull_expectancy > 0.0 and bull_return > 0.0 and (
        bull_pf > 1.0
        or (bull_pf_raw is None and int(bm.get("wins") or 0) > 0 and int(bm.get("losses") or 0) == 0)
    )
    contrast_exposure_lower = contrast_trades <= bull_trades
    contrast_damage = number(cm.get("total_return"))
    if not mechanically_valid:
        decision = "IMPLEMENTATION_ERROR_NO_ALPHA_CONCLUSION"
    elif bull_trades == 0:
        decision = "INTENDED_REGIME_FALSE_NEGATIVE_NO_OPPORTUNITY"
    elif bull_positive and repeated_opportunity and contrast_exposure_lower:
        decision = "MECHANISM_PROMISING_POLICY_FRESH_REQUIRED"
    elif bull_positive and not repeated_opportunity:
        decision = "POSITIVE_BUT_TOO_SPARSE_AS_STANDALONE"
    elif bull_positive:
        decision = "POSITIVE_INTENDED_REGIME_BUT_STATE_NOT_SELECTIVE"
    else:
        decision = "TREND_PULLBACK_OR_LIFECYCLE_HYPOTHESIS_FAILED_NO_RETUNING"
    return {
        "mechanically_valid": mechanically_valid,
        "decision": decision,
        "policy_fresh_authorized": decision == "MECHANISM_PROMISING_POLICY_FRESH_REQUIRED",
        "integration_authorized": False,
        "long_evaluation_authorized": False,
        "thresholds_searched": False,
        "bull_repeated_opportunity": repeated_opportunity,
        "bull_positive_after_cost": bull_positive,
        "contrast_exposure_lower_or_equal": contrast_exposure_lower,
        "contrast_total_return": contrast_damage,
        "cases": rows,
    }


def render(result: dict[str, Any]) -> None:
    lines = [
        "# TrendRider pullback-long v1 causal diagnostic",
        "",
        f"- mechanically valid: {result.get('mechanically_valid')}",
        f"- decision: `{result.get('decision')}`",
        f"- thresholds searched: {result.get('thresholds_searched')}",
        f"- policy-fresh authorized: {result.get('policy_fresh_authorized')}",
        f"- integration authorized: {result.get('integration_authorized')}",
        f"- long evaluation authorized: {result.get('long_evaluation_authorized')}",
        "",
        "| stage | expected state | trades | W/L | PF | expectancy USDT | geo/day | return | MDD | signals |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for stage in STAGES:
        row = (result.get("cases") or {}).get(stage.name) or {}
        metrics = row.get("metrics") or {}
        diagnostics = row.get("diagnostics") or {}
        lines.append(
            f"| {stage.name} | {stage.expected_state} | {metrics.get('trades')} | {metrics.get('wins')}/{metrics.get('losses')} | {metrics.get('profit_factor')} | {metrics.get('expectancy_usdt')} | {metrics.get('geometric_daily_growth')} | {metrics.get('total_return')} | {metrics.get('max_drawdown')} | {diagnostics.get('source_signals_before_execution_filters')} |"
        )
    lines.extend(
        [
            "",
            "The two intervals are development diagnostics.  The decision is based on whether the frozen branch behaves as a repeated bull-continuation mechanism and naturally reduces exposure in the contrast regime.  A failure closes this exact branch without an indicator or lifecycle parameter search.",
        ]
    )
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if not FREEZE.is_file():
        raise RuntimeError(f"missing freeze: {FREEZE}")
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    config = build_config()
    rows = {stage.name: run_stage(stage, config) for stage in STAGES}
    result = classify(rows)
    dump(EVIDENCE / "comparison.json", result)
    render(result)
    return 0 if result.get("mechanically_valid") else 2


if __name__ == "__main__":
    raise SystemExit(main())

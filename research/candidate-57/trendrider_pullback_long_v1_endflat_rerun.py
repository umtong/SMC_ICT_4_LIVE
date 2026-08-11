#!/usr/bin/env python3
"""Implementation-only rerun of TrendRider v1 with a two-day close runoff."""
from __future__ import annotations

import copy
from dataclasses import asdict
from datetime import datetime, time, timedelta, timezone
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import trendrider_pullback_long_v1_campaign as base

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
C51 = ROOT / "research" / "candidate-51"
WORK = ROOT / ".work" / "candidate-57-trendrider-pullback-long-v1-endflat"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-trendrider-pullback-long-v1-endflat"
EVIDENCE = HERE / "evidence" / "trendrider-pullback-long-v1-endflat"
CACHE = ROOT / ".cache" / "candidate-57-trendrider-pullback-long-v1-endflat"
RUNOFF_DAYS = 2


def safe(value: Any) -> Any:
    return base.safe(value)


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(safe(value), indent=2, sort_keys=True, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def signal_end_ns(stage: base.Stage) -> int:
    end = datetime.combine(
        stage.end + timedelta(days=1),
        time.min,
        tzinfo=timezone.utc,
    ) - timedelta(microseconds=1)
    return int(end.timestamp() * 1_000_000_000)


def build_config(stage: base.Stage) -> Path:
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
            "trendrider_signal_end_ns": signal_end_ns(stage),
        }
    )
    path = WORK / "configs" / f"{stage.name}.json"
    dump(path, payload)
    return path


def run_stage(stage: base.Stage) -> dict[str, Any]:
    output = ARTIFACTS / stage.name
    workspace = WORK / "workspace" / stage.name
    for path in (output, workspace):
        if path.exists():
            shutil.rmtree(path)
    output.mkdir(parents=True, exist_ok=True)
    data_end = stage.end + timedelta(days=RUNOFF_DAYS)
    command = [
        sys.executable,
        str(C51 / "launch.py"),
        "--config",
        str(build_config(stage)),
        "--start",
        stage.start.isoformat(),
        "--end",
        data_end.isoformat(),
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
            "stage": asdict(stage) | {
                "days": stage.days,
                "data_end": str(data_end),
                "runoff_days": RUNOFF_DAYS,
            },
            "produced": False,
            "returncode": int(completed.returncode),
        }
        dump(EVIDENCE / "cases" / f"{stage.name}.json", row)
        return row

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    starting = float(metrics.get("starting_nav") or 0.0)
    ending = float(metrics.get("ending_nav") or 0.0)
    entry_window_geo = (
        (ending / starting) ** (1.0 / stage.days) - 1.0
        if starting > 0.0 and ending > 0.0
        else math.nan
    )
    metrics["entry_window_calendar_days"] = stage.days
    metrics["runoff_days"] = RUNOFF_DAYS
    metrics["data_end"] = str(data_end)
    metrics["geometric_daily_growth_entry_window"] = entry_window_geo

    metric_keys = (
        "starting_nav",
        "ending_nav",
        "total_return",
        "geometric_daily_growth",
        "geometric_daily_growth_entry_window",
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
        "entry_window_calendar_days",
        "runoff_days",
        "data_end",
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
        "trendrider_runoff_wrapper",
        "trendrider_signal_end_ns",
        "trendrider_post_cutoff_flat_minutes",
        "trendrider_alpha_policy_changed_for_runoff",
        "max_open_positions_observed",
        "max_simultaneous_entry_intents",
        "global_position_violations",
        "order_rejections",
    )
    expected = int(metrics.get("trades") or 0)
    row = {
        "stage": asdict(stage) | {
            "days": stage.days,
            "data_end": str(data_end),
            "runoff_days": RUNOFF_DAYS,
        },
        "produced": True,
        "returncode": 0,
        "metrics": {key: metrics.get(key) for key in metric_keys},
        "diagnostics": {key: diagnostics.get(key) for key in diagnostic_keys},
        "trade_forensics": base.analyze_trades(output, expected, base.FEATURE_KEYS),
    }
    dump(EVIDENCE / "cases" / f"{stage.name}.json", row)
    return row


def render(result: dict[str, Any]) -> None:
    lines = [
        "# TrendRider pullback-long v1 end-flat implementation rerun",
        "",
        "The entry, state, stop, ROI, trailing and lifecycle policy are unchanged.  Only two days of data runoff are supplied while new entries are frozen at the original stage boundary.",
        "",
        f"- mechanically valid: {result.get('mechanically_valid')}",
        f"- decision: `{result.get('decision')}`",
        f"- thresholds searched: {result.get('thresholds_searched')}",
        f"- policy-fresh authorized: {result.get('policy_fresh_authorized')}",
        f"- integration authorized: {result.get('integration_authorized')}",
        f"- long evaluation authorized: {result.get('long_evaluation_authorized')}",
        "",
        "| stage | trades | W/L | PF | expectancy USDT | entry-window geo/day | return | MDD | signals | end open |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for stage in base.STAGES:
        row = (result.get("cases") or {}).get(stage.name) or {}
        metrics = row.get("metrics") or {}
        diagnostics = row.get("diagnostics") or {}
        lines.append(
            f"| {stage.name} | {metrics.get('trades')} | {metrics.get('wins')}/{metrics.get('losses')} | {metrics.get('profit_factor')} | {metrics.get('expectancy_usdt')} | {metrics.get('geometric_daily_growth_entry_window')} | {metrics.get('total_return')} | {metrics.get('max_drawdown')} | {diagnostics.get('source_signals_before_execution_filters')} | {metrics.get('open_position_rows_at_end')} |"
        )
    lines.extend(
        [
            "",
            "A mechanically valid positive development result does not authorize integration or long evaluation.  It authorizes at most one predeclared policy-fresh interval.",
        ]
    )
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    rows = {stage.name: run_stage(stage) for stage in base.STAGES}
    result = base.classify(rows)
    result["implementation_only_change"] = "two-day close runoff with frozen original entry cutoff"
    dump(EVIDENCE / "comparison.json", result)
    render(result)
    return 0 if result.get("mechanically_valid") else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fresh, trade-forensic comparison of Ichi, MBE breadth and their N-to-1 policy."""
from __future__ import annotations

import copy
from datetime import date
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Iterable

from trade_ledger_forensics import analyze as analyze_trades

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
C51 = ROOT / "research" / "candidate-51"
WORK = ROOT / ".work" / "candidate-57-ichi-mbe-n1-fresh-v1"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-ichi-mbe-n1-fresh-v1"
EVIDENCE = HERE / "evidence" / "ichi-mbe-n1-fresh-v1"
CACHE = ROOT / ".cache" / "candidate-57-ichi-mbe-n1-fresh-v1"
FREEZE = HERE / "ICHI_MBE_N1_FRESH_V1_FREEZE.md"

START = date(2026, 4, 1)
END = date(2026, 4, 30)
DAYS = (END - START).days + 1
MODES = ("ichi_only", "mbe_only", "integrated")

METRIC_KEYS = (
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

DIAGNOSTIC_KEYS = (
    "candidate",
    "integration_mode",
    "arbitration_policy",
    "source_logic_changed",
    "outcome_derived_arbitration",
    "family_source_signals",
    "family_actionable_boundaries",
    "family_selected_entries",
    "dual_family_boundaries",
    "mbe_raw_collision_boundaries",
    "mbe_singleton_rejections",
    "mbe_collision_competing_candidates",
    "mbe_roi_exits",
    "ichi_family_horizon_exits",
    "integrated_unresolved_boundaries",
    "family_route_counts",
    "family_unresolved_reasons",
    "source_signals_before_execution_filters",
    "entry_submissions",
    "entry_expirations",
    "selected_symbols",
    "route_counts",
    "unresolved_reason_counts",
    "max_open_positions_observed",
    "max_simultaneous_entry_intents",
    "global_position_violations",
    "order_rejections",
    "order_denials",
    "liquidations",
)

FEATURE_KEYS = (
    "fan_magnitude",
    "fan_magnitude_gain",
    "source_score",
    "cloud_top",
    "cloud_bottom",
    "trend_close_1h",
    "trend_close_8h",
    "rsi",
    "rsi_cross_magnitude",
    "tema_to_middle_bps",
    "tema_slope_bps",
    "bb_width_bps",
    "volume_ratio_20",
    "return_1h_bps",
    "return_4h_bps",
    "return_8h_bps",
    "ema_2h_to_8h_bps",
    "realized_vol_1h_bps",
    "range_1h_bps",
)


def number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def money(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in {"none", "nan", "nat"}:
        return None
    try:
        result = float(text.split()[0])
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


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


def build_config(mode: str) -> Path:
    if mode not in MODES:
        raise ValueError(mode)
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
            # Shared account cadence and funding behavior.
            "cooldown_minutes": 0,
            "max_hold_minutes": 480,
            "funding_flatten_minute": 60,
            "funding_blackout_before_minutes": -1,
            "funding_blackout_after_minutes": -1,
            # Parent public-strategy fields used by the reused shell.
            "picasso_bucket_minutes": 5,
            "picasso_precedence_mode": "short_avg646",
            "picasso_rsi_long_period": 14,
            "picasso_bb_long_period": 9,
            "picasso_bb_short_period": 20,
            "picasso_source_effective_leverage": 6.46,
            "picasso_source_stoploss": 0.22,
            "picasso_trailing_positive": 0.0,
            "picasso_trailing_offset": 0.0,
            "picasso_emergency_target_fraction": 0.50,
            "picasso_roi_0": 0.079,
            "picasso_roi_416": 0.001,
            "picasso_roi_933": 0.001,
            "picasso_roi_1982": 0.001,
            # Frozen Ichi report-short component.
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
            # Frozen N-to-1 account policy.
            "integration_mode": mode,
            "ichi_family_max_hold_minutes": 480,
            "mbe_min_actionable_candidates": 2,
            "mbe_startup_5m_candles": 140,
            "mbe_variant": "short_avg646",
            "mbe_source_leverage": 6.46,
            "mbe_source_stoploss": 0.22,
            "mbe_tema_period": 9,
            "mbe_bb_period": 20,
            "mbe_rsi_period": 14,
            "mbe_roi_0": 0.079,
            "mbe_roi_15": 0.047,
            "mbe_roi_41": 0.032,
            "mbe_roi_114": 0.11,
            "mbe_roi_180": 0.007,
            "mbe_roi_420": 0.001,
            "mbe_emergency_target_fraction": 0.50,
        }
    )
    path = WORK / "configs" / f"{mode}.json"
    dump(path, payload)
    return path


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [float(row["pnl_usdt"]) for row in rows if row.get("pnl_usdt") is not None]
    rs = [float(row["actual_r"]) for row in rows if row.get("actual_r") is not None]
    wins = [value for value in pnls if value > 0.0]
    losses = [-value for value in pnls if value < 0.0]
    positive_r = [value for value in rs if value > 0.0]
    negative_r = [-value for value in rs if value < 0.0]
    return {
        "trades": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(pnls) if pnls else 0.0,
        "sum_pnl_usdt": sum(pnls),
        "mean_pnl_usdt": sum(pnls) / len(pnls) if pnls else None,
        "profit_factor_usdt": (
            sum(wins) / sum(losses)
            if losses and sum(losses) > 0.0
            else (None if wins else 0.0)
        ),
        "sum_r": sum(rs),
        "mean_r": sum(rs) / len(rs) if rs else None,
        "profit_factor_r": (
            sum(positive_r) / sum(negative_r)
            if negative_r and sum(negative_r) > 0.0
            else (None if positive_r else 0.0)
        ),
    }


def scenario_family(record: dict[str, Any]) -> str:
    declared = str(record.get("scenario_family") or "").strip().lower()
    if declared in {"ichi", "mbe"}:
        return declared
    state = str(record.get("state") or "").upper()
    if "MBE2" in state or "RSI_TEMA" in state:
        return "mbe"
    if "ICHI" in state:
        return "ichi"
    return "unknown"


def family_forensics(output: Path) -> dict[str, Any]:
    path = output / "closed_scenarios.json"
    records = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    ledger: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        pnl = money(record.get("realized_pnl"))
        planned = number(record.get("planned_account_loss"), math.nan)
        actual_r = (
            pnl / planned
            if pnl is not None and math.isfinite(planned) and planned > 0.0
            else None
        )
        ledger.append(
            {
                "trade_index": index,
                "scenario_id": record.get("scenario_id"),
                "family": scenario_family(record),
                "state": record.get("state"),
                "symbol": record.get("symbol"),
                "side": record.get("side"),
                "episode_ts": record.get("episode_ts"),
                "close_ts": record.get("ts_event"),
                "pnl_usdt": pnl,
                "planned_account_loss": planned if math.isfinite(planned) else None,
                "actual_r": actual_r,
                "exit_reason": str(
                    record.get("management_exit_reason")
                    or "UNTAGGED_BRACKET_OR_ENGINE"
                ),
                "mbe_actionable_candidates": record.get("mbe_actionable_candidates"),
                "ichi_actionable_candidates": record.get("ichi_actionable_candidates"),
                "dual_family_boundary": record.get("dual_family_boundary"),
            }
        )
    by_family: dict[str, Any] = {}
    by_exit: dict[str, Any] = {}
    for family in sorted({str(row["family"]) for row in ledger}):
        by_family[family] = summarize([row for row in ledger if row["family"] == family])
    for exit_reason in sorted({str(row["exit_reason"]) for row in ledger}):
        by_exit[exit_reason] = summarize(
            [row for row in ledger if row["exit_reason"] == exit_reason]
        )
    episode_keys = sorted(
        {
            (
                str(row["family"]),
                str(row["symbol"]),
                int(row["episode_ts"] or 0),
            )
            for row in ledger
        }
    )
    return {
        "overall": summarize(ledger),
        "by_family": by_family,
        "by_exit_reason": by_exit,
        "episode_keys": episode_keys,
        "independent_episode_count": len(episode_keys),
        "trade_ledger": ledger,
    }


def run_case(mode: str) -> dict[str, Any]:
    output = ARTIFACTS / mode
    workspace = WORK / "workspace" / mode
    for path in (output, workspace):
        if path.exists():
            shutil.rmtree(path)
    output.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(C51 / "launch.py"),
        "--config",
        str(build_config(mode)),
        "--start",
        START.isoformat(),
        "--end",
        END.isoformat(),
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
            "mode": mode,
            "produced": False,
            "returncode": completed.returncode,
            "interval": {"start": START, "end": END, "days": DAYS},
        }
        dump(EVIDENCE / "cases" / f"{mode}.json", row)
        return row

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    expected = int(metrics.get("trades") or 0)
    row = {
        "mode": mode,
        "produced": True,
        "returncode": 0,
        "interval": {"start": START, "end": END, "days": DAYS},
        "metrics": {key: metrics.get(key) for key in METRIC_KEYS},
        "diagnostics": {key: diagnostics.get(key) for key in DIAGNOSTIC_KEYS},
        "trade_forensics": analyze_trades(output, expected, FEATURE_KEYS),
        "family_forensics": family_forensics(output),
    }
    dump(EVIDENCE / "cases" / f"{mode}.json", row)
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
        and int(diagnostics.get("order_denials") or 0) == 0
        and int(diagnostics.get("liquidations") or 0) == 0
        and int(diagnostics.get("max_open_positions_observed") or 0) <= 1
        and int(diagnostics.get("max_simultaneous_entry_intents") or 0) <= 1
        and int(metrics.get("open_position_rows_at_end") or 0) == 0
        and int(metrics.get("active_order_rows_at_end") or 0) == 0
        and bool(checks.get("no_liquidation", True))
        and bool((row.get("trade_forensics") or {}).get("ledger_matches_metrics"))
    )


def metric_delta(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_metrics = left.get("metrics") or {}
    right_metrics = right.get("metrics") or {}
    keys = (
        "ending_nav",
        "total_return",
        "geometric_daily_growth",
        "max_drawdown",
        "trades",
        "wins",
        "losses",
        "win_rate",
        "profit_factor",
        "expectancy_usdt",
        "largest_winner_share",
    )
    return {
        key: number(left_metrics.get(key)) - number(right_metrics.get(key))
        for key in keys
    }


def episode_contrast(integrated: dict[str, Any], ichi: dict[str, Any]) -> dict[str, Any]:
    integrated_keys = {
        tuple(item)
        for item in ((integrated.get("family_forensics") or {}).get("episode_keys") or [])
    }
    ichi_keys = {
        tuple(item)
        for item in ((ichi.get("family_forensics") or {}).get("episode_keys") or [])
    }
    return {
        "integrated_added_episode_keys": sorted(integrated_keys - ichi_keys),
        "ichi_only_omitted_episode_keys": sorted(ichi_keys - integrated_keys),
        "shared_episode_keys": sorted(integrated_keys & ichi_keys),
        "integrated_added_count": len(integrated_keys - ichi_keys),
        "ichi_only_omitted_count": len(ichi_keys - integrated_keys),
        "shared_count": len(integrated_keys & ichi_keys),
    }


def compact(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics") or {}
    diagnostics = row.get("diagnostics") or {}
    return {
        "account_valid": account_ok(row),
        "trades": metrics.get("trades"),
        "wins": metrics.get("wins"),
        "losses": metrics.get("losses"),
        "win_rate": metrics.get("win_rate"),
        "profit_factor": metrics.get("profit_factor"),
        "expectancy_usdt": metrics.get("expectancy_usdt"),
        "geometric_daily_growth": metrics.get("geometric_daily_growth"),
        "total_return": metrics.get("total_return"),
        "max_drawdown": metrics.get("max_drawdown"),
        "largest_winner_share": metrics.get("largest_winner_share"),
        "family_source_signals": diagnostics.get("family_source_signals"),
        "family_actionable_boundaries": diagnostics.get("family_actionable_boundaries"),
        "family_selected_entries": diagnostics.get("family_selected_entries"),
        "mbe_singleton_rejections": diagnostics.get("mbe_singleton_rejections"),
        "mbe_raw_collision_boundaries": diagnostics.get("mbe_raw_collision_boundaries"),
        "dual_family_boundaries": diagnostics.get("dual_family_boundaries"),
        "family_outcomes": (row.get("family_forensics") or {}).get("by_family"),
    }


def project_target(row: dict[str, Any]) -> bool:
    metrics = row.get("metrics") or {}
    pf = metrics.get("profit_factor")
    return (
        account_ok(row)
        and int(metrics.get("trades") or 0) >= DAYS
        and number(metrics.get("geometric_daily_growth")) >= 0.01
        and number(metrics.get("expectancy_usdt")) > 0.0
        and (pf is None or number(pf) > 1.0)
        and number(metrics.get("min_equity")) > 0.0
    )


def render(comparison: dict[str, Any]) -> None:
    lines = [
        "# Ichi/MBE N→1 fresh account comparison",
        "",
        f"Evaluated entries: `{START.isoformat()}` through `{END.isoformat()}` UTC.  Every cell is the same four-symbol, one-position, after-cost NautilusTrader account.",
        "",
        "| mode | trades | W/L | win rate | PF | expectancy | geo/day | return | MDD | MBE collisions | MBE entries |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for mode in MODES:
        row = comparison["cases"][mode]
        metrics = row.get("metrics") or {}
        diagnostics = row.get("diagnostics") or {}
        selected = diagnostics.get("family_selected_entries") or {}
        lines.append(
            f"| {mode} | {metrics.get('trades')} | {metrics.get('wins')}/{metrics.get('losses')} | "
            f"{metrics.get('win_rate')} | {metrics.get('profit_factor')} | "
            f"{metrics.get('expectancy_usdt')} | {metrics.get('geometric_daily_growth')} | "
            f"{metrics.get('total_return')} | {metrics.get('max_drawdown')} | "
            f"{diagnostics.get('mbe_raw_collision_boundaries')} | {selected.get('mbe')} |"
        )
    lines += [
        "",
        "## Causal comparison",
        "",
        f"- integrated minus Ichi: `{json.dumps(comparison['integrated_minus_ichi'], sort_keys=True)}`",
        f"- episode contrast: `{json.dumps(comparison['episode_contrast'], sort_keys=True)}`",
        f"- zero-collision identity expected: `{comparison['zero_collision_identity_expected']}`",
        f"- zero-collision identity observed: `{comparison['zero_collision_identity_observed']}`",
        f"- mechanically valid cells: `{comparison['all_accounts_valid']}`",
        f"- strict project target: `{comparison['strict_project_target']}`",
        "",
        "The result is interpreted trade by trade.  A positive total caused by one outlier, a harmful MBE displacement, or a low-cost accounting difference is not accepted as proof of the integration mechanism.",
    ]
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if not FREEZE.is_file():
        raise RuntimeError("frozen N-to-1 comparison specification missing")
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    cases = {mode: run_case(mode) for mode in MODES}
    integrated = cases["integrated"]
    ichi = cases["ichi_only"]
    mbe = cases["mbe_only"]
    collisions = int(
        ((integrated.get("diagnostics") or {}).get("mbe_raw_collision_boundaries") or 0)
    )
    zero_collision_expected = collisions == 0
    zero_collision_observed = None
    if zero_collision_expected and integrated.get("produced") and ichi.get("produced"):
        zero_collision_observed = (
            (integrated.get("metrics") or {}).get("ending_nav")
            == (ichi.get("metrics") or {}).get("ending_nav")
            and (integrated.get("metrics") or {}).get("trades")
            == (ichi.get("metrics") or {}).get("trades")
            and ((integrated.get("family_forensics") or {}).get("episode_keys") or [])
            == ((ichi.get("family_forensics") or {}).get("episode_keys") or [])
        )

    comparison = {
        "experiment": "candidate-57-ichi-mbe-n1-fresh-v1",
        "policy_frozen_before_interval": True,
        "source_logic_changed": False,
        "interval": {"start": START, "end": END, "days": DAYS},
        "cases": cases,
        "compact": {mode: compact(row) for mode, row in cases.items()},
        "integrated_minus_ichi": metric_delta(integrated, ichi),
        "integrated_minus_mbe": metric_delta(integrated, mbe),
        "episode_contrast": episode_contrast(integrated, ichi),
        "zero_collision_identity_expected": zero_collision_expected,
        "zero_collision_identity_observed": zero_collision_observed,
        "all_accounts_valid": all(account_ok(row) for row in cases.values()),
        "strict_project_target": project_target(integrated),
    }
    dump(EVIDENCE / "comparison.json", comparison)
    render(comparison)
    print(json.dumps(comparison["compact"], indent=2, sort_keys=True, allow_nan=False))
    print(json.dumps(comparison["integrated_minus_ichi"], indent=2, sort_keys=True))

    if any(not row.get("produced") for row in cases.values()):
        return 1
    if not comparison["all_accounts_valid"]:
        return 2
    if zero_collision_expected and zero_collision_observed is False:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fresh one-account comparison of jump, ichi and their frozen N-to-1 policy."""
from __future__ import annotations

import copy
from datetime import date, datetime, time, timedelta, timezone
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from trade_ledger_forensics_v2 import analyze as analyze_trades

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
C51 = ROOT / "research" / "candidate-51"
WORK = ROOT / ".work" / "candidate-57-jump-ichi-n1-fresh-v1"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-jump-ichi-n1-fresh-v1"
EVIDENCE = HERE / "evidence" / "jump-ichi-n1-fresh-v1"
CACHE = ROOT / ".cache" / "candidate-57-jump-ichi-n1-fresh-v1"
FREEZE = HERE / "JUMP_ICHI_N1_FRESH_V1_FREEZE.md"

SIGNAL_START = date(2024, 2, 1)
SIGNAL_END = date(2024, 2, 29)
SIGNAL_DAYS = (SIGNAL_END - SIGNAL_START).days + 1
DATA_START = SIGNAL_START - timedelta(days=20)
DATA_END = SIGNAL_END + timedelta(days=2)
MODES = ("jump_only", "ichi_only", "integrated")
FEATURE_KEYS = (
    "causal_zscore", "absolute_return", "stop_fraction",
    "jump_boundary_candidate_count", "jump_absolute_z",
    "fan_magnitude", "fan_magnitude_gain", "source_score",
    "cloud_top", "cloud_bottom", "trend_close_1h", "trend_close_8h",
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


def ns_start(day: date) -> int:
    return int(datetime.combine(day, time.min, tzinfo=timezone.utc).timestamp() * 1e9)


def ns_end(day: date) -> int:
    return ns_start(day + timedelta(days=1)) - 1


def build_config(mode: str) -> Path:
    if mode not in MODES:
        raise ValueError(mode)
    payload = copy.deepcopy(json.loads((C51 / "config.json").read_text()))
    strategy = payload["strategy"]
    for key in (
        "sma_offset_low", "sma_offset_high", "sma_stop_min_fraction",
        "sma_stop_max_fraction", "sma_stop_atr_buffer",
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
            "ichi_family_max_hold_minutes": 480,
            "jump_timeframe_minutes": 240,
            "jump_threshold_sigma": 2.0,
            "jump_volatility_window": 18,
            "jump_min_absolute_return": 0.0,
            "jump_terminal_atr_period": 14,
            "jump_stop_atr_multiple": 1.0,
            "jump_min_stop_fraction": 0.0015,
            "jump_emergency_target_fraction": 0.20,
            "jump_stop_mode": "impulse",
            "jump_confirmation_minutes": 0,
            "jump_confirmation_bucket_minutes": 5,
            "jump_protection_mode": "transient_be",
            "jump_protection_activation_r": 0.4,
            "jump_protection_floor_r": 0.0,
            "jump_protection_trail_gap_r": 999.0,
            "jump_protection_escape_r": 1.0,
            "integration_mode": mode,
            "signal_start_ns": ns_start(SIGNAL_START),
            "signal_end_ns": ns_end(SIGNAL_END),
        }
    )
    path = WORK / "configs" / f"{mode}.json"
    dump(path, payload)
    return path


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [number(row.get("pnl_usdt"), math.nan) for row in rows]
    pnls = [value for value in pnls if math.isfinite(value)]
    rs = [number(row.get("actual_r"), math.nan) for row in rows]
    rs = [value for value in rs if math.isfinite(value)]
    wins, losses = [v for v in pnls if v > 0], [-v for v in pnls if v < 0]
    pos_r, neg_r = [v for v in rs if v > 0], [-v for v in rs if v < 0]
    return {
        "trades": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(pnls) if pnls else 0.0,
        "sum_pnl_usdt": sum(pnls),
        "mean_pnl_usdt": sum(pnls) / len(pnls) if pnls else None,
        "profit_factor_usdt": sum(wins) / sum(losses) if losses else (None if wins else 0.0),
        "sum_r": sum(rs),
        "mean_r": sum(rs) / len(rs) if rs else None,
        "profit_factor_r": sum(pos_r) / sum(neg_r) if neg_r else (None if pos_r else 0.0),
    }


def family_forensics(output: Path, forensic: dict[str, Any]) -> dict[str, Any]:
    path = output / "closed_scenarios.json"
    records = json.loads(path.read_text()) if path.is_file() else []
    by_id = {str(row.get("scenario_id")): row for row in records}
    ledger = []
    for row in forensic.get("trade_ledger") or []:
        record = by_id.get(str(row.get("scenario_id"))) or {}
        ledger.append(
            {
                **row,
                "family": str(record.get("scenario_family") or "unknown"),
                "state": record.get("state"),
                "dual_family_boundary": record.get("dual_family_boundary"),
                "jump_actionable_candidates": record.get("jump_actionable_candidates"),
                "ichi_actionable_candidates": record.get("ichi_actionable_candidates"),
            }
        )
    families = sorted({str(row["family"]) for row in ledger})
    keys = sorted(
        {(str(r["family"]), str(r.get("symbol")), int(r.get("episode_ts") or 0)) for r in ledger}
    )
    return {
        "overall": summarize(ledger),
        "by_family": {f: summarize([r for r in ledger if r["family"] == f]) for f in families},
        "episode_keys": keys,
        "independent_episode_count": len(keys),
        "trade_ledger": ledger,
    }


def run_case(mode: str) -> dict[str, Any]:
    output, workspace = ARTIFACTS / mode, WORK / "workspace" / mode
    for path in (output, workspace):
        if path.exists():
            shutil.rmtree(path)
    output.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            sys.executable, str(C51 / "launch.py"), "--config", str(build_config(mode)),
            "--start", DATA_START.isoformat(), "--end", DATA_END.isoformat(),
            "--cache", str(CACHE), "--output", str(output), "--workspace", str(workspace),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": f"{C51}:{HERE}"},
        check=False,
    )
    metrics_path, diagnostics_path = output / "metrics.json", output / "strategy_diagnostics.json"
    if completed.returncode != 0 or not metrics_path.is_file() or not diagnostics_path.is_file():
        row = {"mode": mode, "produced": False, "returncode": int(completed.returncode)}
        dump(EVIDENCE / "cases" / f"{mode}.json", row)
        return row
    metrics = json.loads(metrics_path.read_text())
    diagnostics = json.loads(diagnostics_path.read_text())
    starting, ending = number(metrics.get("starting_nav")), number(metrics.get("ending_nav"))
    metrics["geometric_daily_growth_signal_window"] = (
        (ending / starting) ** (1.0 / SIGNAL_DAYS) - 1.0
        if starting > 0 and ending > 0 else math.nan
    )
    forensic = analyze_trades(output, int(metrics.get("trades") or 0), FEATURE_KEYS)
    row = {
        "mode": mode,
        "produced": True,
        "returncode": 0,
        "window": {
            "data_start": DATA_START, "signal_start": SIGNAL_START,
            "signal_end": SIGNAL_END, "data_end": DATA_END, "signal_days": SIGNAL_DAYS,
        },
        "metrics": metrics,
        "diagnostics": diagnostics,
        "trade_forensics": forensic,
        "family_forensics": family_forensics(output, forensic),
    }
    dump(EVIDENCE / "cases" / f"{mode}.json", row)
    return row


def account_ok(row: dict[str, Any]) -> bool:
    if not row.get("produced"):
        return False
    m, d = row.get("metrics") or {}, row.get("diagnostics") or {}
    checks = m.get("gate_checks") or {}
    return (
        int(d.get("global_position_violations") or 0) == 0
        and int(d.get("order_rejections") or 0) == 0
        and int(d.get("max_open_positions_observed") or 0) <= 1
        and int(d.get("max_simultaneous_entry_intents") or 0) <= 1
        and int(m.get("open_position_rows_at_end") or 0) == 0
        and int(m.get("active_order_rows_at_end") or 0) == 0
        and bool(checks.get("no_liquidation", True))
        and bool(checks.get("risk_fraction_exactly_three_percent", False))
        and bool((row.get("trade_forensics") or {}).get("ledger_matches_metrics"))
    )


def family_rows(row: dict[str, Any], family: str) -> list[dict[str, Any]]:
    return [r for r in ((row.get("family_forensics") or {}).get("trade_ledger") or []) if r["family"] == family]


def episode_set(row: dict[str, Any], family: str) -> set[tuple[str, str, int]]:
    return {(family, str(r.get("symbol")), int(r.get("episode_ts") or 0)) for r in family_rows(row, family)}


def jump_preservation(jump: dict[str, Any], integrated: dict[str, Any]) -> dict[str, Any]:
    source = family_rows(jump, "jump")
    combined = {("jump", str(r.get("symbol")), int(r.get("episode_ts") or 0)): r for r in family_rows(integrated, "jump")}
    winners = [r for r in source if number(r.get("actual_r")) > 0]
    preserved = [
        key for r in winners
        if (key := ("jump", str(r.get("symbol")), int(r.get("episode_ts") or 0))) in combined
        and number(combined[key].get("actual_r")) > 0
    ]
    best = max(source, key=lambda r: number(r.get("actual_r"), -math.inf), default=None)
    best_key = ("jump", str(best.get("symbol")), int(best.get("episode_ts") or 0)) if best else None
    return {
        "source_jump_trades": len(source),
        "source_jump_winners": len(winners),
        "preserved_positive_jump_winners": len(preserved),
        "positive_winner_preservation_share": len(preserved) / len(winners) if winners else None,
        "best_jump_key": list(best_key) if best_key else None,
        "best_jump_r": best.get("actual_r") if best else None,
        "best_jump_preserved_positive": bool(best_key in combined and number(combined[best_key].get("actual_r")) > 0) if best_key else False,
    }


def metric_delta(left: dict[str, Any], right: dict[str, Any]) -> dict[str, float]:
    l, r = left.get("metrics") or {}, right.get("metrics") or {}
    keys = (
        "ending_nav", "total_return", "geometric_daily_growth_signal_window",
        "max_drawdown", "trades", "wins", "losses", "win_rate",
        "profit_factor", "expectancy_usdt", "largest_winner_share",
    )
    return {key: number(l.get(key)) - number(r.get(key)) for key in keys}


def strict_target(row: dict[str, Any]) -> bool:
    m = row.get("metrics") or {}
    return (
        account_ok(row)
        and int(m.get("trades") or 0) >= SIGNAL_DAYS
        and number(m.get("geometric_daily_growth_signal_window")) >= 0.01
        and number(m.get("expectancy_usdt")) > 0
        and number(m.get("profit_factor")) > 1
        and number(m.get("max_drawdown"), 1) <= 0.20
        and number(m.get("min_equity")) > 0
    )


def classify(cases: dict[str, dict[str, Any]]) -> dict[str, Any]:
    jump, ichi, integrated = cases["jump_only"], cases["ichi_only"], cases["integrated"]
    valid = all(account_ok(row) for row in cases.values())
    keep = jump_preservation(jump, integrated)
    m = integrated.get("metrics") or {}
    geos = [number((cases[x].get("metrics") or {}).get("geometric_daily_growth_signal_window")) for x in ("jump_only", "ichi_only")]
    trades = [int((cases[x].get("metrics") or {}).get("trades") or 0) for x in ("jump_only", "ichi_only")]
    support = (
        valid and number(m.get("total_return")) > 0 and number(m.get("expectancy_usdt")) > 0
        and number(m.get("profit_factor")) > 1
        and number(m.get("geometric_daily_growth_signal_window")) > max(geos)
        and int(m.get("trades") or 0) > max(trades)
        and number(keep.get("positive_winner_preservation_share"), 0) >= 0.75
        and bool(keep.get("best_jump_preserved_positive"))
        and bool(family_rows(integrated, "ichi"))
    )
    target = strict_target(integrated)
    if not valid:
        decision = "IMPLEMENTATION_ERROR_NO_ALPHA_CONCLUSION"
    elif target:
        decision = "SHORT_FRESH_PROJECT_TARGET_MET_CONTINUOUS_VALIDATION_REQUIRED"
    elif support:
        decision = "JUMP_ICHI_N1_COMPOSITION_SUPPORTED_NEXT_UNTOUCHED_REQUIRED"
    elif number(m.get("total_return")) > 0:
        decision = "POSITIVE_ACCOUNT_BUT_N1_CAUSAL_COMPOSITION_UNRESOLVED"
    else:
        decision = "JUMP_ICHI_N1_COMPOSITION_REJECTED_NO_RETUNING"
    jump_keys, ichi_keys = episode_set(jump, "jump"), episode_set(ichi, "ichi")
    combined_jump, combined_ichi = episode_set(integrated, "jump"), episode_set(integrated, "ichi")
    return {
        "mechanically_valid": valid,
        "decision": decision,
        "strict_project_target": target,
        "composition_causal_support": support,
        "thresholds_searched": False,
        "integration_authorized": target or support,
        "long_evaluation_authorized": False,
        "jump_preservation": keep,
        "integrated_minus_jump": metric_delta(integrated, jump),
        "integrated_minus_ichi": metric_delta(integrated, ichi),
        "episode_comparison": {
            "jump_standalone_count": len(jump_keys),
            "ichi_standalone_count": len(ichi_keys),
            "integrated_jump_count": len(combined_jump),
            "integrated_ichi_count": len(combined_ichi),
            "integrated_omitted_jump": sorted(jump_keys - combined_jump),
            "integrated_omitted_ichi": sorted(ichi_keys - combined_ichi),
            "integrated_added_jump": sorted(combined_jump - jump_keys),
            "integrated_added_ichi": sorted(combined_ichi - ichi_keys),
        },
    }


def render(result: dict[str, Any]) -> None:
    lines = [
        "# Jump + Ichi N→1 fresh one-account comparison",
        "",
        f"Scored entry window: `{SIGNAL_START}` through `{SIGNAL_END}` UTC ({SIGNAL_DAYS} days). Startup and runoff cannot open entries.",
        "",
        f"- mechanically valid: {result['mechanically_valid']}",
        f"- decision: `{result['decision']}`",
        f"- strict project target: {result['strict_project_target']}",
        f"- composition causal support: {result['composition_causal_support']}",
        f"- thresholds searched: {result['thresholds_searched']}",
        "",
        "| mode | trades | W/L | win rate | PF | expectancy | signal geo/day | return | MDD | jump | ichi |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for mode in MODES:
        row, m = result["cases"][mode], result["cases"][mode].get("metrics") or {}
        selected = (row.get("diagnostics") or {}).get("family_selected_entries") or {}
        lines.append(
            f"| {mode} | {m.get('trades')} | {m.get('wins')}/{m.get('losses')} | {m.get('win_rate')} | "
            f"{m.get('profit_factor')} | {m.get('expectancy_usdt')} | {m.get('geometric_daily_growth_signal_window')} | "
            f"{m.get('total_return')} | {m.get('max_drawdown')} | {selected.get('jump', 0)} | {selected.get('ichi', 0)} |"
        )
    lines += [
        "", "## Causal composition", "",
        f"- Jump winner preservation: `{json.dumps(result['jump_preservation'], sort_keys=True)}`",
        f"- Integrated minus jump: `{json.dumps(result['integrated_minus_jump'], sort_keys=True)}`",
        f"- Integrated minus ichi: `{json.dumps(result['integrated_minus_ichi'], sort_keys=True)}`",
        f"- Episode comparison: `{json.dumps(result['episode_comparison'], sort_keys=True)}`",
        "",
        "Positive return alone is insufficient: the integrated account must preserve jump winners, add independent ichi opportunities, and improve both density and geometric growth.",
    ]
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    if not FREEZE.is_file():
        raise RuntimeError("frozen Jump/Ichi N-to-1 specification missing")
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    cases = {mode: run_case(mode) for mode in MODES}
    result = {
        "experiment": "candidate-57-jump-ichi-n1-fresh-v1",
        "policy_frozen_before_signal_window": True,
        "source_logic_changed": False,
        "window": {
            "data_start": DATA_START, "signal_start": SIGNAL_START,
            "signal_end": SIGNAL_END, "data_end": DATA_END, "signal_days": SIGNAL_DAYS,
        },
        "cases": cases,
        **classify(cases),
    }
    dump(EVIDENCE / "comparison.json", result)
    render(result)
    print(json.dumps({mode: cases[mode].get("metrics") for mode in MODES}, indent=2, sort_keys=True, allow_nan=False, default=str))
    if any(not row.get("produced") for row in cases.values()):
        return 1
    return 0 if result["mechanically_valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Parity-gated fresh test of MBE2 cross-asset collision topology."""
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
from typing import Any

from trade_ledger_forensics_v2 import analyze as analyze_trades

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
C51 = ROOT / "research" / "candidate-51"
WORK = ROOT / ".work" / "candidate-57-mbe-collision-topology-fresh-v1"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-mbe-collision-topology-fresh-v1"
EVIDENCE = HERE / "evidence" / "mbe-collision-topology-fresh-v1"
CACHE = ROOT / ".cache" / "candidate-57-mbe-collision-topology-fresh-v1"
FREEZE = HERE / "MBE_COLLISION_TOPOLOGY_FRESH_V1_FREEZE.md"
REFERENCE = HERE / "evidence" / "ichi-mbe-n1-fresh-v1" / "cases" / "mbe_only.json"

PARITY_START = date(2026, 4, 1)
PARITY_END = date(2026, 4, 30)
FRESH_START = date(2024, 3, 1)
FRESH_END = date(2024, 3, 31)
FRESH_DAYS = (FRESH_END - FRESH_START).days + 1
MODES = ("ge2_control", "exact2", "ge3plus")
FEATURE_KEYS = (
    "rsi", "rsi_cross_magnitude", "tema_to_middle_bps", "tema_slope_bps",
    "bb_width_bps", "volume_ratio_20", "return_1h_bps", "return_4h_bps",
    "return_8h_bps", "ema_2h_to_8h_bps", "realized_vol_1h_bps",
    "range_1h_bps", "mbe_raw_actionable_symbols", "mbe_topology_actionable",
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


def build_config(label: str) -> Path:
    payload = copy.deepcopy(json.loads((C51 / "config.json").read_text(encoding="utf-8")))
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
            "integration_mode": "mbe_only",
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
    path = WORK / "configs" / f"{label}.json"
    dump(path, payload)
    return path


def run_case(
    *,
    label: str,
    mode: str,
    start: date,
    end: date,
    destination: Path,
) -> dict[str, Any]:
    output = ARTIFACTS / destination
    workspace = WORK / "workspace" / destination
    for path in (output, workspace):
        if path.exists():
            shutil.rmtree(path)
    output.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            sys.executable, str(C51 / "launch.py"),
            "--config", str(build_config(label)),
            "--start", start.isoformat(), "--end", end.isoformat(),
            "--cache", str(CACHE), "--output", str(output),
            "--workspace", str(workspace),
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": f"{C51}:{HERE}",
            "C57_MBE_TOPOLOGY_MODE": mode,
        },
        check=False,
    )
    metrics_path = output / "metrics.json"
    diagnostics_path = output / "strategy_diagnostics.json"
    if completed.returncode != 0 or not metrics_path.is_file() or not diagnostics_path.is_file():
        row = {
            "label": label, "mode": mode, "produced": False,
            "returncode": int(completed.returncode), "start": start, "end": end,
        }
        dump(EVIDENCE / "cases" / f"{label}.json", row)
        return row
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    expected = int(metrics.get("trades") or 0)
    forensic = analyze_trades(output, expected, FEATURE_KEYS)
    row = {
        "label": label,
        "mode": mode,
        "produced": True,
        "returncode": 0,
        "start": start,
        "end": end,
        "days": (end - start).days + 1,
        "metrics": metrics,
        "diagnostics": diagnostics,
        "trade_forensics": forensic,
    }
    dump(EVIDENCE / "cases" / f"{label}.json", row)
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
        and bool(checks.get("risk_fraction_exactly_three_percent", False))
        and bool((row.get("trade_forensics") or {}).get("ledger_matches_metrics"))
    )


def episode_keys(row: dict[str, Any]) -> list[tuple[int, str, int]]:
    return sorted(
        {
            (
                int(item.get("episode_ts") or 0),
                str(item.get("symbol")),
                int(item.get("side") or 0),
            )
            for item in ((row.get("trade_forensics") or {}).get("trade_ledger") or [])
        }
    )


def parity_check(row: dict[str, Any]) -> dict[str, Any]:
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    reference_keys = sorted(
        {
            (int(item[2]), str(item[1]), -1)
            for item in ((reference.get("family_forensics") or {}).get("episode_keys") or [])
        }
    )
    candidate_keys = episode_keys(row)
    ref_metrics = reference.get("metrics") or {}
    metrics = row.get("metrics") or {}
    return {
        "reference_file": str(REFERENCE.relative_to(ROOT)),
        "account_valid": account_ok(row),
        "episode_keys_identical": candidate_keys == reference_keys,
        "candidate_episode_keys": candidate_keys,
        "reference_episode_keys": reference_keys,
        "candidate_trades": metrics.get("trades"),
        "reference_trades": ref_metrics.get("trades"),
        "trade_count_identical": int(metrics.get("trades") or 0) == int(ref_metrics.get("trades") or 0),
        "ending_nav_delta": number(metrics.get("ending_nav")) - number(ref_metrics.get("ending_nav")),
        "expectancy_delta": number(metrics.get("expectancy_usdt")) - number(ref_metrics.get("expectancy_usdt")),
        "profit_factor_delta": number(metrics.get("profit_factor")) - number(ref_metrics.get("profit_factor")),
        "pass": (
            account_ok(row)
            and candidate_keys == reference_keys
            and int(metrics.get("trades") or 0) == int(ref_metrics.get("trades") or 0)
            and abs(number(metrics.get("ending_nav")) - number(ref_metrics.get("ending_nav"))) <= 0.02
            and abs(number(metrics.get("expectancy_usdt")) - number(ref_metrics.get("expectancy_usdt"))) <= 0.01
        ),
    }


def outcome_summary(row: dict[str, Any]) -> dict[str, Any]:
    ledger = (row.get("trade_forensics") or {}).get("trade_ledger") or []
    roi = [item for item in ledger if "PUBLIC_MBE2_ROI_EXIT" in str(item.get("exit_reason"))]
    stop_like = [item for item in ledger if number(item.get("actual_r")) <= -0.80]
    winners = [item for item in ledger if number(item.get("actual_r")) > 0.0]
    gross = sum(number(item.get("pnl_usdt")) for item in winners)
    largest = max((number(item.get("pnl_usdt")) for item in winners), default=0.0)
    return {
        "roi_exit_trades": len(roi),
        "roi_exit_wins": sum(number(item.get("actual_r")) > 0.0 for item in roi),
        "stop_like_trades": len(stop_like),
        "stop_like_sum_r": sum(number(item.get("actual_r")) for item in stop_like),
        "largest_winner_share": largest / gross if gross > 0.0 else 1.0,
        "episode_keys": episode_keys(row),
    }


def metric_delta(left: dict[str, Any], right: dict[str, Any]) -> dict[str, float]:
    l, r = left.get("metrics") or {}, right.get("metrics") or {}
    keys = (
        "ending_nav", "total_return", "geometric_daily_growth", "max_drawdown",
        "trades", "wins", "losses", "win_rate", "profit_factor",
        "expectancy_usdt", "largest_winner_share",
    )
    return {key: number(l.get(key)) - number(r.get(key)) for key in keys}


def classify(
    parity: dict[str, Any],
    cases: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    control, exact2, ge3 = cases["ge2_control"], cases["exact2"], cases["ge3plus"]
    accounts_valid = all(account_ok(row) for row in cases.values())
    cm, em, gm = control.get("metrics") or {}, exact2.get("metrics") or {}, ge3.get("metrics") or {}
    eo, go = outcome_summary(exact2), outcome_summary(ge3)
    exact2_positive = (
        number(em.get("total_return")) > 0.0
        and number(em.get("expectancy_usdt")) > 0.0
        and number(em.get("profit_factor")) > 1.0
    )
    improved = (
        number(em.get("expectancy_usdt")) > number(cm.get("expectancy_usdt"))
        and number(em.get("profit_factor")) > number(cm.get("profit_factor"))
        and number(em.get("geometric_daily_growth")) > number(cm.get("geometric_daily_growth"))
    )
    contrast = (
        number(em.get("expectancy_usdt")) > number(gm.get("expectancy_usdt"))
        and number(em.get("profit_factor")) > number(gm.get("profit_factor"))
        and int(eo.get("stop_like_trades") or 0) <= int(go.get("stop_like_trades") or 0)
    )
    robust = (
        int(em.get("trades") or 0) >= 10
        and int(eo.get("roi_exit_trades") or 0) > 0
        and number(eo.get("largest_winner_share"), 1.0) <= 0.50
    )
    causal_support = parity.get("pass") and accounts_valid and exact2_positive and improved and contrast and robust
    strict_target = (
        causal_support
        and int(em.get("trades") or 0) >= FRESH_DAYS
        and number(em.get("geometric_daily_growth")) >= 0.01
        and number(em.get("max_drawdown"), 1.0) <= 0.20
        and number(em.get("min_equity")) > 0.0
    )
    if not parity.get("pass") or not accounts_valid:
        decision = "IMPLEMENTATION_ERROR_NO_ALPHA_CONCLUSION"
    elif strict_target:
        decision = "MBE_EXACT2_SHORT_TARGET_MET_INTEGRATION_VALIDATION_REQUIRED"
    elif causal_support:
        decision = "MBE_EXACT2_TOPOLOGY_COMPONENT_SUPPORTED_INTEGRATION_REQUIRED"
    else:
        decision = "MBE_COLLISION_TOPOLOGY_HYPOTHESIS_REJECTED_NO_RETUNING"
    return {
        "mechanically_valid": bool(parity.get("pass") and accounts_valid),
        "decision": decision,
        "strict_project_target": strict_target,
        "causal_support": causal_support,
        "thresholds_searched": False,
        "integration_authorized": causal_support,
        "long_evaluation_authorized": False,
        "predictions": {
            "exact2_positive": exact2_positive,
            "exact2_expectancy_pf_geo_improved_vs_control": improved,
            "ge3plus_contrast_supported": contrast,
            "roi_engine_preserved_and_not_outlier_dominated": robust,
        },
        "outcomes": {mode: outcome_summary(row) for mode, row in cases.items()},
        "exact2_minus_control": metric_delta(exact2, control),
        "exact2_minus_ge3plus": metric_delta(exact2, ge3),
    }


def render(result: dict[str, Any]) -> None:
    lines = [
        "# MBE2 collision topology fresh comparison",
        "",
        f"- parity pass: {result['parity']['pass']}",
        f"- mechanically valid: {result['mechanically_valid']}",
        f"- decision: `{result['decision']}`",
        f"- strict project target: {result['strict_project_target']}",
        f"- causal support: {result['causal_support']}",
        f"- thresholds searched: {result['thresholds_searched']}",
        "",
        f"Fresh interval: `{FRESH_START}` through `{FRESH_END}` UTC ({FRESH_DAYS} days).",
        "",
        "| mode | trades | W/L | win rate | PF | expectancy | geo/day | return | MDD | ROI exits | stop-like |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for mode in MODES:
        row, metrics = result["cases"][mode], result["cases"][mode].get("metrics") or {}
        out = result["outcomes"][mode]
        lines.append(
            f"| {mode} | {metrics.get('trades')} | {metrics.get('wins')}/{metrics.get('losses')} | "
            f"{metrics.get('win_rate')} | {metrics.get('profit_factor')} | {metrics.get('expectancy_usdt')} | "
            f"{metrics.get('geometric_daily_growth')} | {metrics.get('total_return')} | {metrics.get('max_drawdown')} | "
            f"{out.get('roi_exit_trades')} | {out.get('stop_like_trades')} |"
        )
    lines += [
        "", "## Predeclared prediction results", "",
        f"`{json.dumps(result['predictions'], sort_keys=True)}`",
        "",
        "A lower trade count is not evidence by itself. Exact-two is supported only if its per-trade and account growth improve while the three-plus contrast behaves as the hypothesized market-wide state.",
    ]
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if not FREEZE.is_file() or not REFERENCE.is_file():
        raise RuntimeError("frozen specification or April reference evidence missing")
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    parity_case = run_case(
        label="parity_april_2026_ge2_control",
        mode="ge2_control",
        start=PARITY_START,
        end=PARITY_END,
        destination=Path("parity_april_2026_ge2_control"),
    )
    parity = parity_check(parity_case)
    dump(EVIDENCE / "parity.json", parity)
    if not parity.get("pass"):
        result = {
            "experiment": "candidate-57-mbe-collision-topology-fresh-v1",
            "parity": parity,
            "cases": {},
            "mechanically_valid": False,
            "decision": "IMPLEMENTATION_ERROR_NO_ALPHA_CONCLUSION",
            "strict_project_target": False,
            "causal_support": False,
            "thresholds_searched": False,
            "integration_authorized": False,
            "long_evaluation_authorized": False,
            "predictions": {},
            "outcomes": {},
        }
        dump(EVIDENCE / "comparison.json", result)
        (EVIDENCE / "RESULT.md").write_text(
            "# MBE2 collision topology fresh comparison\n\n"
            "Finite-history parity failed. No fresh alpha conclusion is permitted.\n",
            encoding="utf-8",
        )
        return 2

    cases = {
        mode: run_case(
            label=f"fresh_2024_03_{mode}",
            mode=mode,
            start=FRESH_START,
            end=FRESH_END,
            destination=Path("fresh_2024_03") / mode,
        )
        for mode in MODES
    }
    result = {
        "experiment": "candidate-57-mbe-collision-topology-fresh-v1",
        "policy_frozen_before_fresh_interval": True,
        "source_logic_changed": False,
        "parity": parity,
        "fresh_interval": {"start": FRESH_START, "end": FRESH_END, "days": FRESH_DAYS},
        "cases": cases,
        **classify(parity, cases),
    }
    dump(EVIDENCE / "comparison.json", result)
    render(result)
    print(json.dumps({mode: cases[mode].get("metrics") for mode in MODES}, indent=2, sort_keys=True, allow_nan=False, default=str))
    if any(not row.get("produced") for row in cases.values()):
        return 1
    return 0 if result["mechanically_valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Behaviour-identical lifecycle and post-exit audit for public ichiV2 short."""
from __future__ import annotations

import copy
from datetime import date
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
from typing import Any

from trade_ledger_forensics import analyze as analyze_trades

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
C51 = ROOT / "research" / "candidate-51"
WORK = ROOT / ".work" / "candidate-57-ichi-short-lifecycle-v3"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-ichi-short-lifecycle-v3"
EVIDENCE = HERE / "evidence" / "ichi-short-lifecycle-v3"
CACHE = ROOT / ".cache" / "candidate-57-ichi-short-lifecycle-v3"
BASELINE = (
    HERE
    / "evidence"
    / "ichi-v2-fast-v2"
    / "cases"
    / "continuous_30d-report_short_level.json"
)
START = date(2025, 6, 1)
END = date(2025, 6, 30)

FEATURE_KEYS = (
    "fan_magnitude",
    "fan_magnitude_gain",
    "source_score",
    "cloud_top",
    "cloud_bottom",
    "trend_close_1h",
    "trend_close_8h",
    "forensic_elapsed_minutes",
    "forensic_mfe_r",
    "forensic_mae_r",
    "forensic_mfe_r_minute",
    "forensic_mae_r_minute",
    "forensic_time_to_mfe_0p10r",
    "forensic_time_to_mfe_0p25r",
    "forensic_time_to_mae_0p10r",
    "forensic_time_to_mae_0p25r",
    "forensic_time_to_mae_0p50r",
    "forensic_source_state_checks",
    "forensic_active_source_state_checks",
    "forensic_active_source_state_ratio",
    "forensic_first_source_state_loss_minute",
    "forensic_mark_r_at_first_source_state_loss",
    "forensic_mfe_r_at_first_source_state_loss",
    "forensic_mae_r_at_first_source_state_loss",
    "forensic_source_exit_signal_minute",
    "forensic_mark_r_at_source_exit_signal",
    "forensic_mfe_r_at_source_exit_signal",
    "forensic_mae_r_at_source_exit_signal",
    "forensic_source_exit_current_trend",
    "forensic_source_exit_current_indicator",
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


def number(value: Any, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def distribution(values: list[float]) -> dict[str, float | None]:
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        return {"min": None, "q25": None, "median": None, "q75": None, "max": None}

    def q(fraction: float) -> float:
        position = (len(clean) - 1) * fraction
        lo, hi = math.floor(position), math.ceil(position)
        if lo == hi:
            return clean[lo]
        weight = position - lo
        return clean[lo] * (1.0 - weight) + clean[hi] * weight

    return {
        "min": clean[0],
        "q25": q(0.25),
        "median": q(0.50),
        "q75": q(0.75),
        "max": clean[-1],
    }


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
            "ichi_forensic_round_trip_cost_fraction": 0.0021,
        }
    )
    path = WORK / "config.json"
    dump(path, payload)
    return path


def compare_baseline(metrics: dict[str, Any]) -> dict[str, Any]:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    expected = baseline.get("metrics") or {}
    checks: dict[str, bool] = {}
    for key in (
        "trades",
        "wins",
        "losses",
        "total_return",
        "geometric_daily_growth",
        "profit_factor",
        "max_drawdown",
    ):
        if key in {"trades", "wins", "losses"}:
            checks[key] = int(expected.get(key) or 0) == int(metrics.get(key) or 0)
        else:
            checks[key] = (
                abs(number(expected.get(key), 0.0) - number(metrics.get(key), 0.0))
                <= 1e-10
            )
    return {"identical": all(checks.values()), "checks": checks}


def summarize_trades(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups = {
        "roi_exit": [row for row in rows if row.get("exit_reason") == "PUBLIC_ICHI_ROI_EXIT"],
        "source_exit": [
            row
            for row in rows
            if row.get("exit_reason") == "PUBLIC_ICHI_SOURCE_SIGNAL_EXIT"
        ],
    }
    result: dict[str, Any] = {}
    for name, items in groups.items():
        result[name] = {
            "trades": len(items),
            "wins": sum(number(row.get("actual_r"), 0.0) > 0.0 for row in items),
            "losses": sum(number(row.get("actual_r"), 0.0) < 0.0 for row in items),
            "mean_r": (
                statistics.fmean(number(row.get("actual_r"), 0.0) for row in items)
                if items
                else None
            ),
            "mfe_r": distribution([number(row.get("forensic_mfe_r")) for row in items]),
            "mae_r": distribution([number(row.get("forensic_mae_r")) for row in items]),
            "active_source_ratio": distribution(
                [number(row.get("forensic_active_source_state_ratio")) for row in items]
            ),
            "first_state_loss_minute": distribution(
                [number(row.get("forensic_first_source_state_loss_minute")) for row in items]
            ),
            "time_to_mfe_0p25r": distribution(
                [number(row.get("forensic_time_to_mfe_0p25r")) for row in items]
            ),
            "state_loss_before_mfe_0p25r_rate": (
                sum(
                    math.isfinite(number(row.get("forensic_first_source_state_loss_minute")))
                    and (
                        not math.isfinite(number(row.get("forensic_time_to_mfe_0p25r")))
                        or number(row.get("forensic_first_source_state_loss_minute"))
                        <= number(row.get("forensic_time_to_mfe_0p25r"))
                    )
                    for row in items
                )
                / len(items)
                if items
                else None
            ),
        }
    return result


def analyze_shadows(
    rows: list[dict[str, Any]], shadows: list[dict[str, Any]]
) -> dict[str, Any]:
    actual = {str(row.get("scenario_id")): row for row in rows}
    joined: list[dict[str, Any]] = []
    for shadow in shadows:
        row = actual.get(str(shadow.get("scenario_id")))
        if row is None:
            continue
        joined_row = dict(shadow)
        joined_row["actual_exit_reason"] = row.get("exit_reason")
        joined_row["actual_r"] = number(row.get("actual_r"), 0.0)
        joined_row["shadow_minus_actual_r"] = (
            number(shadow.get("post_exit_net_r"), 0.0)
            - number(row.get("actual_r"), 0.0)
        )
        joined.append(joined_row)
    uncensored = [row for row in joined if not bool(row.get("post_exit_censored"))]
    losses = [row for row in uncensored if number(row.get("actual_r"), 0.0) < 0.0]
    recovery = [row for row in losses if row.get("post_exit_resolution") == "ORIGINAL_ROI"]
    delta = [number(row.get("shadow_minus_actual_r")) for row in losses]
    return {
        "started": len(shadows),
        "joined": len(joined),
        "uncensored": len(uncensored),
        "actual_source_exit_losses": len(losses),
        "loss_recovered_to_original_roi": len(recovery),
        "loss_recovery_rate": len(recovery) / len(losses) if losses else None,
        "resolution_counts": {
            reason: sum(row.get("post_exit_resolution") == reason for row in uncensored)
            for reason in ("ORIGINAL_ROI", "ORIGINAL_STOP", "ORIGINAL_HORIZON", "EVALUATION_END")
        },
        "shadow_minus_actual_r": distribution(delta),
        "post_exit_mfe_r": distribution(
            [number(row.get("post_exit_mfe_r")) for row in losses]
        ),
        "post_exit_mae_r": distribution(
            [number(row.get("post_exit_mae_r")) for row in losses]
        ),
        "joined_rows": joined,
    }


def render(report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    trade_groups = report["trade_groups"]
    shadows = report["post_exit_shadows"]
    lines = [
        "# Public ichiV2 short lifecycle forensic v3",
        "",
        "The actual account is behaviour-identical; post-exit shadows never trade.",
        "",
        f"- baseline identical: {report['baseline_identity']['identical']}",
        f"- trades: {metrics.get('trades')}",
        f"- wins/losses: {metrics.get('wins')}/{metrics.get('losses')}",
        f"- PF: {metrics.get('profit_factor')}",
        f"- geometric daily growth: {metrics.get('geometric_daily_growth')}",
        "",
        "## Actual lifecycle",
        "",
        "| exit family | trades | W/L | mean R | median MFE R | median MAE R | median active-source ratio | state loss before +0.25R |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("roi_exit", "source_exit"):
        row = trade_groups[name]
        lines.append(
            f"| {name} | {row.get('trades')} | {row.get('wins')}/{row.get('losses')} | "
            f"{row.get('mean_r')} | {(row.get('mfe_r') or {}).get('median')} | "
            f"{(row.get('mae_r') or {}).get('median')} | "
            f"{(row.get('active_source_ratio') or {}).get('median')} | "
            f"{row.get('state_loss_before_mfe_0p25r_rate')} |"
        )
    lines += [
        "",
        "## Non-trading post-exit shadow",
        "",
        f"- source-exit losses evaluated: {shadows.get('actual_source_exit_losses')}",
        f"- recovered to original ROI: {shadows.get('loss_recovered_to_original_roi')}",
        f"- recovery rate: {shadows.get('loss_recovery_rate')}",
        f"- median shadow minus actual R: {(shadows.get('shadow_minus_actual_r') or {}).get('median')}",
        f"- resolutions: {shadows.get('resolution_counts')}",
        "",
        "## Predeclared interpretation",
        "",
        f"- source-exit-validity hypothesis supported: {report['hypothesis']['supported']}",
        f"- reason: {report['hypothesis']['reason']}",
        "",
        "If the source exit is validated, the next minimal investigation moves to entry-state and cross-asset arbitration. If it is falsified, only an exit-confirmation state is tested, with the recovered trades predeclared before any untouched run.",
    ]
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    if not BASELINE.is_file():
        raise RuntimeError(f"missing baseline: {BASELINE}")
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    output = ARTIFACTS / "continuous_30d"
    workspace = WORK / "workspace"
    for path in (output, workspace):
        if path.exists():
            shutil.rmtree(path)
    output.mkdir(parents=True, exist_ok=True)

    completed = subprocess.run(
        [
            sys.executable,
            str(C51 / "launch.py"),
            "--config",
            str(build_config()),
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
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(C51)},
        check=False,
    )
    metrics_path = output / "metrics.json"
    diagnostics_path = output / "strategy_diagnostics.json"
    if completed.returncode != 0 or not metrics_path.is_file() or not diagnostics_path.is_file():
        dump(EVIDENCE / "failure.json", {"returncode": completed.returncode})
        return completed.returncode or 2

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    forensic = analyze_trades(output, int(metrics.get("trades") or 0), FEATURE_KEYS)
    trade_groups = summarize_trades(forensic["trade_ledger"])
    shadows = list(diagnostics.get("ichi_short_post_exit_shadows_completed") or [])
    shadow_report = analyze_shadows(forensic["trade_ledger"], shadows)
    recovery_rate = number(shadow_report.get("loss_recovery_rate"), 1.0)
    median_delta = number(
        (shadow_report.get("shadow_minus_actual_r") or {}).get("median"), 1.0
    )
    source_mfe = number(
        (trade_groups["source_exit"].get("mfe_r") or {}).get("median"), 1.0
    )
    supported = recovery_rate <= 0.25 and median_delta <= -0.10 and source_mfe <= 0.25
    reason = (
        f"source-exit loss recovery rate={recovery_rate:.3f}; "
        f"median shadow-minus-actual={median_delta:.3f}R; "
        f"source-exit median pre-exit MFE={source_mfe:.3f}R"
    )
    identity = compare_baseline(metrics)
    report = {
        "experiment": "candidate-57-ichi-short-lifecycle-forensic-v3",
        "policy_changed": False,
        "baseline_identity": identity,
        "metrics": metrics,
        "diagnostics": diagnostics,
        "trade_forensics": forensic,
        "trade_groups": trade_groups,
        "post_exit_shadows": shadow_report,
        "hypothesis": {"supported": supported, "reason": reason},
    }
    dump(EVIDENCE / "lifecycle_report.json", report)
    render(report)
    valid = identity["identical"] and forensic.get("ledger_matches_metrics")
    return 0 if valid else 3


if __name__ == "__main__":
    raise SystemExit(main())

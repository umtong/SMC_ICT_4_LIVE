#!/usr/bin/env python3
"""Behaviour-identical lifecycle audit for public ZaratustraV5.

The consumed June-2026 account is reused only to diagnose why 144 winners are
still outweighed by 70 losses. No policy parameter is searched. The wrapper
must reproduce the frozen account before any lifecycle conclusion is accepted.
"""
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

from trade_ledger_forensics import analyze as analyze_trades

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
C51 = ROOT / "research" / "candidate-51"
WORK = ROOT / ".work" / "candidate-57-zara-lifecycle-forensic-v2"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-zara-lifecycle-forensic-v2"
EVIDENCE = HERE / "evidence" / "zara-lifecycle-forensic-v2"
CACHE = ROOT / ".cache" / "candidate-57-zara-lifecycle-forensic-v2"
BASELINE = (
    HERE
    / "evidence"
    / "zaratustra-v5-source-v1"
    / "cases"
    / "continuous_30d-source_level_both.json"
)

START = date(2026, 6, 1)
END = date(2026, 6, 30)
DAYS = (END - START).days + 1

ENTRY_FEATURES = (
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
)
PATH_FEATURES = (
    "forensic_elapsed_minutes",
    "forensic_mfe_r",
    "forensic_mae_r",
    "forensic_mfe_r_minute",
    "forensic_mae_r_minute",
    "forensic_trailing_activation_minute",
    "forensic_first_source_invalidation_minute",
    "forensic_mark_r_at_first_source_invalidation",
    "forensic_mfe_r_at_first_source_invalidation",
    "forensic_mae_r_at_first_source_invalidation",
    "forensic_first_opposite_state_minute",
    "forensic_mark_r_at_first_opposite_state",
    "forensic_same_side_state_ratio",
    "forensic_time_to_mfe_0p10r",
    "forensic_time_to_mfe_0p24r",
    "forensic_time_to_mfe_0p50r",
    "forensic_time_to_mfe_1p00r",
    "forensic_time_to_mae_0p10r",
    "forensic_time_to_mae_0p25r",
    "forensic_time_to_mae_0p50r",
    "forensic_time_to_mae_0p75r",
)
SNAPSHOT_FEATURES = tuple(
    key
    for minute in (5, 15, 30, 60, 120, 240, 360, 480)
    for key in (
        f"forensic_mark_r_{minute}m",
        f"forensic_mfe_r_{minute}m",
        f"forensic_mae_r_{minute}m",
        f"forensic_trail_active_{minute}m",
    )
)
FEATURE_KEYS = ENTRY_FEATURES + PATH_FEATURES + SNAPSHOT_FEATURES


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def number(value: Any, default: float = math.nan) -> float:
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
            "zara_trigger_mode": "level",
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
        }
    )
    path = WORK / "config.json"
    dump(path, payload)
    return path


def quantiles(values: list[float]) -> dict[str, float | None]:
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        return {key: None for key in ("min", "q25", "median", "q75", "max")}

    def q(fraction: float) -> float:
        position = (len(clean) - 1) * fraction
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return clean[lower]
        weight = position - lower
        return clean[lower] * (1.0 - weight) + clean[upper] * weight

    return {
        "min": clean[0],
        "q25": q(0.25),
        "median": q(0.50),
        "q75": q(0.75),
        "max": clean[-1],
    }


def bucket_name(row: dict[str, Any]) -> str:
    result = number(row.get("actual_r"))
    if result > 0.0:
        return "winner"
    if result <= -0.90:
        return "full_stop"
    return "partial_loss"


def lifecycle_order(row: dict[str, Any]) -> str:
    activation = number(row.get("forensic_trailing_activation_minute"))
    if not math.isfinite(activation):
        activation = number(row.get("forensic_time_to_mfe_0p24r"))
    invalidation = number(row.get("forensic_first_source_invalidation_minute"))
    if math.isfinite(activation) and math.isfinite(invalidation):
        return (
            "activation_before_invalidation"
            if activation <= invalidation
            else "invalidation_before_activation"
        )
    if math.isfinite(activation):
        return "activation_without_observed_invalidation"
    if math.isfinite(invalidation):
        return "invalidation_without_activation"
    return "neither_observed"


def summarize_bucket(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "trades": len(rows),
        "sum_r": sum(number(row.get("actual_r"), 0.0) for row in rows),
        "mean_r": (
            sum(number(row.get("actual_r"), 0.0) for row in rows) / len(rows)
            if rows
            else None
        ),
        "activation_rate": (
            sum(
                math.isfinite(
                    number(row.get("forensic_trailing_activation_minute"))
                )
                or math.isfinite(number(row.get("forensic_time_to_mfe_0p24r")))
                for row in rows
            )
            / len(rows)
            if rows
            else None
        ),
        "source_invalidation_rate": (
            sum(
                math.isfinite(
                    number(row.get("forensic_first_source_invalidation_minute"))
                )
                for row in rows
            )
            / len(rows)
            if rows
            else None
        ),
        "order_counts": {},
    }
    for row in rows:
        order = lifecycle_order(row)
        counts = result["order_counts"]
        counts[order] = int(counts.get(order, 0)) + 1
    for key in (
        "forensic_mfe_r",
        "forensic_mae_r",
        "forensic_trailing_activation_minute",
        "forensic_first_source_invalidation_minute",
        "forensic_mark_r_at_first_source_invalidation",
        "forensic_same_side_state_ratio",
        "forensic_mark_r_30m",
        "forensic_mark_r_60m",
        "forensic_mark_r_120m",
        "forensic_mark_r_240m",
    ):
        result[key] = quantiles(
            [
                number(row.get(key))
                for row in rows
                if math.isfinite(number(row.get(key)))
            ]
        )
    return result


def compare_baseline(metrics: dict[str, Any]) -> dict[str, Any]:
    if not BASELINE.is_file():
        return {"available": False, "identical": False}
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    expected = baseline.get("metrics") or {}
    checks = {}
    for key in (
        "trades",
        "wins",
        "losses",
        "total_return",
        "geometric_daily_growth",
        "profit_factor",
        "max_drawdown",
    ):
        left = expected.get(key)
        right = metrics.get(key)
        if key in {"trades", "wins", "losses"}:
            checks[key] = int(left or 0) == int(right or 0)
        else:
            checks[key] = abs(number(left, 0.0) - number(right, 0.0)) <= 1e-10
    return {
        "available": True,
        "identical": all(checks.values()),
        "checks": checks,
        "baseline_metrics": {key: expected.get(key) for key in checks},
        "forensic_metrics": {key: metrics.get(key) for key in checks},
    }


def render(report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    buckets = report["lifecycle_buckets"]
    lines = [
        "# ZaratustraV5 lifecycle forensic v2",
        "",
        "This run is behaviour-identical instrumentation, not a strategy modification.",
        "",
        "## Account identity",
        "",
        f"- baseline identical: {report['baseline_identity']['identical']}",
        f"- trades: {metrics.get('trades')}",
        f"- wins/losses: {metrics.get('wins')}/{metrics.get('losses')}",
        f"- PF: {metrics.get('profit_factor')}",
        f"- geometric daily growth: {metrics.get('geometric_daily_growth')}",
        f"- total return: {metrics.get('total_return')}",
        "",
        "## Lifecycle decomposition",
        "",
        "| outcome | trades | mean R | activation rate | source invalidation rate | dominant temporal order |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for name in ("winner", "partial_loss", "full_stop"):
        row = buckets[name]
        orders = row.get("order_counts") or {}
        dominant = max(orders, key=orders.get) if orders else "none"
        lines.append(
            f"| {name} | {row.get('trades')} | {row.get('mean_r')} | "
            f"{row.get('activation_rate')} | {row.get('source_invalidation_rate')} | "
            f"{dominant} |"
        )
    lines += [
        "",
        "## Predeclared interpretation",
        "",
        "The next policy change is justified only when losing trades usually lose the "
        "same-side source state before reaching the trailing activation, while winners "
        "usually activate first. The minimal next experiment would then preserve entry, "
        "stop, target and trailing and add only a thesis-failure exit after causal source "
        "invalidation. If winner and loser temporal ordering overlaps materially, fixed "
        "threshold or time-exit tuning is not justified.",
        "",
        f"- temporal separation supported: {report['temporal_separation']['supported']}",
        f"- reason: {report['temporal_separation']['reason']}",
        "",
    ]
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    if not BASELINE.is_file():
        raise RuntimeError(f"baseline evidence missing: {BASELINE}")
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
        dump(
            EVIDENCE / "failure.json",
            {
                "returncode": completed.returncode,
                "metrics_exists": metrics_path.is_file(),
                "diagnostics_exists": diagnostics_path.is_file(),
            },
        )
        return completed.returncode or 2

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    forensics = analyze_trades(output, int(metrics.get("trades") or 0), FEATURE_KEYS)
    ledger = forensics["trade_ledger"]
    grouped = {name: [] for name in ("winner", "partial_loss", "full_stop")}
    for row in ledger:
        grouped[bucket_name(row)].append(row)
    bucket_report = {
        name: summarize_bucket(rows) for name, rows in grouped.items()
    }

    winner_orders = bucket_report["winner"]["order_counts"]
    full_orders = bucket_report["full_stop"]["order_counts"]
    winner_good = (
        int(winner_orders.get("activation_before_invalidation", 0))
        + int(winner_orders.get("activation_without_observed_invalidation", 0))
    )
    full_bad = (
        int(full_orders.get("invalidation_before_activation", 0))
        + int(full_orders.get("invalidation_without_activation", 0))
    )
    winner_rate = winner_good / max(1, bucket_report["winner"]["trades"])
    full_rate = full_bad / max(1, bucket_report["full_stop"]["trades"])
    supported = winner_rate >= 0.70 and full_rate >= 0.70
    reason = (
        f"winner activation-first rate={winner_rate:.3f}; "
        f"full-stop invalidation-first rate={full_rate:.3f}"
    )

    identity = compare_baseline(metrics)
    report = {
        "stage": {
            "start": START.isoformat(),
            "end": END.isoformat(),
            "days": DAYS,
            "consumed_for_diagnostics_only": True,
        },
        "policy_changed": False,
        "baseline_identity": identity,
        "metrics": metrics,
        "diagnostics": diagnostics,
        "trade_forensics": forensics,
        "lifecycle_buckets": bucket_report,
        "temporal_separation": {
            "supported": supported,
            "winner_activation_first_rate": winner_rate,
            "full_stop_invalidation_first_rate": full_rate,
            "reason": reason,
        },
    }
    dump(EVIDENCE / "lifecycle_report.json", report)
    render(report)
    return 0 if identity.get("identical") and forensics.get("ledger_matches_metrics") else 3


if __name__ == "__main__":
    raise SystemExit(main())

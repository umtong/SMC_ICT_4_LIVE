#!/usr/bin/env python3
"""Behaviour-identical persistence audit for public ZaratustraV5."""
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
WORK = ROOT / ".work" / "candidate-57-zara-persistence-forensic-v3"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-zara-persistence-forensic-v3"
EVIDENCE = HERE / "evidence" / "zara-persistence-forensic-v3"
CACHE = ROOT / ".cache" / "candidate-57-zara-persistence-forensic-v3"
BASELINE = (
    HERE
    / "evidence"
    / "zaratustra-v5-source-v1"
    / "cases"
    / "continuous_30d-source_level_both.json"
)
START = date(2026, 6, 1)
END = date(2026, 6, 30)

BASE_KEYS = (
    "forensic_elapsed_minutes",
    "forensic_mfe_r",
    "forensic_mae_r",
    "forensic_trailing_activation_minute",
    "forensic_time_to_mae_0p50r",
    "forensic_time_to_mfe_0p24r",
    "forensic_first_source_invalidation_minute",
    "forensic_mark_r_at_first_source_invalidation",
)
PERSISTENCE_KEYS = (
    "forensic_invalidation_episode_count",
    "forensic_recovery_count",
    "forensic_first_recovery_after_invalidation_minute",
    "forensic_first_recovered_streak_checks",
    "forensic_first_invalidation_episode_minutes",
    "forensic_max_invalidation_streak_checks",
    "forensic_closing_invalidation_streak_checks",
    "forensic_max_failed_components",
    "forensic_max_failed_timeframes",
    "forensic_first_15m_context_failure_minute",
    "forensic_mark_r_at_first_15m_context_failure",
    "forensic_first_30m_context_failure_minute",
    "forensic_mark_r_at_first_30m_context_failure",
    "forensic_first_two_timeframe_failure_minute",
    "forensic_mark_r_at_first_two_timeframe_failure",
    "forensic_first_all_timeframe_failure_minute",
    "forensic_mark_r_at_first_all_timeframe_failure",
)
STREAK_KEYS = tuple(
    key
    for threshold in (2, 3, 6)
    for key in (
        f"forensic_first_invalidation_streak_{threshold}_minute",
        f"forensic_mark_r_at_first_invalidation_streak_{threshold}",
        f"forensic_failed_components_at_first_invalidation_streak_{threshold}",
        f"forensic_failed_timeframes_at_first_invalidation_streak_{threshold}",
    )
)
FEATURE_KEYS = BASE_KEYS + PERSISTENCE_KEYS + STREAK_KEYS


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


def outcome(row: dict[str, Any]) -> str:
    actual_r = number(row.get("actual_r"), 0.0)
    if actual_r > 0.0:
        return "winner"
    if actual_r <= -0.90:
        return "full_stop"
    return "partial_loss"


def occurs_before(
    row: dict[str, Any], event_key: str, boundary_key: str
) -> bool:
    event = number(row.get(event_key))
    boundary = number(row.get(boundary_key))
    return math.isfinite(event) and (
        not math.isfinite(boundary) or event <= boundary
    )


def distribution(values: list[float]) -> dict[str, float | None]:
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        return {"min": None, "q25": None, "median": None, "q75": None, "max": None}

    def quantile(fraction: float) -> float:
        position = (len(clean) - 1) * fraction
        lo = math.floor(position)
        hi = math.ceil(position)
        if lo == hi:
            return clean[lo]
        weight = position - lo
        return clean[lo] * (1.0 - weight) + clean[hi] * weight

    return {
        "min": clean[0],
        "q25": quantile(0.25),
        "median": quantile(0.50),
        "q75": quantile(0.75),
        "max": clean[-1],
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups = {name: [] for name in ("winner", "partial_loss", "full_stop")}
    for row in rows:
        groups[outcome(row)].append(row)
    result: dict[str, Any] = {}
    for name, items in groups.items():
        boundary = (
            "forensic_trailing_activation_minute"
            if name == "winner"
            else "forensic_time_to_mae_0p50r"
        )
        row: dict[str, Any] = {
            "trades": len(items),
            "mean_r": (
                sum(number(item.get("actual_r"), 0.0) for item in items) / len(items)
                if items
                else None
            ),
        }
        for threshold in (2, 3, 6):
            key = f"forensic_first_invalidation_streak_{threshold}_minute"
            count = sum(occurs_before(item, key, boundary) for item in items)
            row[f"streak_{threshold}_before_boundary_rate"] = (
                count / len(items) if items else None
            )
            row[f"streak_{threshold}_mark_r"] = distribution(
                [
                    number(
                        item.get(
                            f"forensic_mark_r_at_first_invalidation_streak_{threshold}"
                        )
                    )
                    for item in items
                ]
            )
        for event in (
            "15m_context_failure",
            "30m_context_failure",
            "two_timeframe_failure",
            "all_timeframe_failure",
        ):
            key = f"forensic_first_{event}_minute"
            count = sum(occurs_before(item, key, boundary) for item in items)
            row[f"{event}_before_boundary_rate"] = (
                count / len(items) if items else None
            )
        row["max_streak_checks"] = distribution(
            [number(item.get("forensic_max_invalidation_streak_checks")) for item in items]
        )
        row["max_failed_timeframes"] = distribution(
            [number(item.get("forensic_max_failed_timeframes")) for item in items]
        )
        row["invalidation_episode_count"] = distribution(
            [number(item.get("forensic_invalidation_episode_count")) for item in items]
        )
        result[name] = row
    return result


def render(report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    groups = report["persistence_groups"]
    lines = [
        "# ZaratustraV5 persistent-thesis forensic v3",
        "",
        "The run exactly reproduces the frozen policy and records persistence only.",
        "",
        f"- baseline identical: {report['baseline_identity']['identical']}",
        f"- trades: {metrics.get('trades')}",
        f"- wins/losses: {metrics.get('wins')}/{metrics.get('losses')}",
        f"- PF: {metrics.get('profit_factor')}",
        "",
        "| outcome | trades | mean R | streak2 before boundary | streak3 before boundary | streak6 before boundary | two-TF failure before boundary | 30m failure before boundary | median max streak |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("winner", "partial_loss", "full_stop"):
        row = groups[name]
        lines.append(
            f"| {name} | {row.get('trades')} | {row.get('mean_r')} | "
            f"{row.get('streak_2_before_boundary_rate')} | "
            f"{row.get('streak_3_before_boundary_rate')} | "
            f"{row.get('streak_6_before_boundary_rate')} | "
            f"{row.get('two_timeframe_failure_before_boundary_rate')} | "
            f"{row.get('30m_context_failure_before_boundary_rate')} | "
            f"{(row.get('max_streak_checks') or {}).get('median')} |"
        )
    lines += [
        "",
        "## Predeclared interpretation",
        "",
        f"- persistence hypothesis supported: {report['hypothesis']['supported']}",
        f"- reason: {report['hypothesis']['reason']}",
        "",
        "A policy experiment is justified only when a three-check failure is uncommon before winner activation, common before full-stop -0.50R, and still exits materially before the source stop. Otherwise the source-entry family rather than its lifecycle management remains the primary problem.",
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
    forensic = analyze_trades(output, int(metrics.get("trades") or 0), FEATURE_KEYS)
    groups = summarize(forensic["trade_ledger"])
    winner_rate = number(groups["winner"].get("streak_3_before_boundary_rate"), 1.0)
    full_rate = number(groups["full_stop"].get("streak_3_before_boundary_rate"), 0.0)
    full_mark = number(
        (groups["full_stop"].get("streak_3_mark_r") or {}).get("median"),
        -1.0,
    )
    supported = winner_rate <= 0.20 and full_rate >= 0.70 and full_mark >= -0.60
    reason = (
        f"winner streak3-before-activation={winner_rate:.3f}; "
        f"full-stop streak3-before--0.50R={full_rate:.3f}; "
        f"full-stop median mark at streak3={full_mark:.3f}R"
    )
    identity = compare_baseline(metrics)
    report = {
        "experiment": "candidate-57-zara-persistence-forensic-v3",
        "policy_changed": False,
        "baseline_identity": identity,
        "metrics": metrics,
        "diagnostics": diagnostics,
        "trade_forensics": forensic,
        "persistence_groups": groups,
        "hypothesis": {"supported": supported, "reason": reason},
    }
    dump(EVIDENCE / "persistence_report.json", report)
    render(report)
    valid = identity["identical"] and forensic.get("ledger_matches_metrics")
    return 0 if valid else 3


if __name__ == "__main__":
    raise SystemExit(main())

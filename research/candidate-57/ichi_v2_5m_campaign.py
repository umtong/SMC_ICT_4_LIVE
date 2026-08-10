#!/usr/bin/env python3
"""Conditional, trade-forensic tournament for the public ichiV2 family."""
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
WORK = ROOT / ".work" / "candidate-57-ichi-v2-5m-v1"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-ichi-v2-5m-v1"
EVIDENCE = HERE / "evidence" / "ichi-v2-5m-v1"
CACHE = ROOT / ".cache" / "candidate-57-ichi-v2-5m-v1"
FREEZE = HERE / "ICHI_V2_5M_V1_FREEZE.md"


@dataclass(frozen=True)
class Stage:
    name: str
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


DEVELOPMENT = Stage("development", date(2026, 3, 15), date(2026, 3, 28))
UNTOUCHED = Stage("untouched", date(2025, 12, 8), date(2025, 12, 14))
CONTINUOUS = Stage("continuous_30d", date(2025, 6, 1), date(2025, 6, 30))

PROFILES: dict[str, dict[str, Any]] = {
    "report_inferred": {
        "fan_shift": 3,
        "min_gain": 1.0013,
        "stop": 0.040,
        "objective": 0.080,
        "roi_enabled": True,
        "roi": 0.015,
        "ignore_roi_if_entry_signal": True,
        "trailing_enabled": False,
        "trail": 0.0,
        "trail_offset": 0.0,
        "provenance": "report-implied 1.5% ROI and 4% stop; exact ichiV2 entry",
    },
    "ichiV2_source": {
        "fan_shift": 3,
        "min_gain": 1.0013,
        "stop": 0.100,
        "objective": 0.350,
        "roi_enabled": True,
        "roi": 0.300,
        "ignore_roi_if_entry_signal": True,
        "trailing_enabled": True,
        "trail": 0.060,
        "trail_offset": 0.080,
        "provenance": "exact public ichiV2.py management",
    },
    "ichiV2_5_source": {
        "fan_shift": 2,
        "min_gain": 1.0007,
        "stop": 0.060,
        "objective": 0.500,
        "roi_enabled": False,
        "roi": 0.500,
        "ignore_roi_if_entry_signal": False,
        "trailing_enabled": True,
        "trail": 0.030,
        "trail_offset": 0.400,
        "provenance": "exact public ichiV2_5.py management",
    },
}

VARIANTS = {
    "report_long_level": ("report_inferred", "long", "level"),
    "report_long_edge": ("report_inferred", "long", "edge"),
    "report_short_level": ("report_inferred", "short", "level"),
    "report_both_level": ("report_inferred", "both", "level"),
    "source_v2_long_level": ("ichiV2_source", "long", "level"),
    "source_v2_5_long_level": ("ichiV2_5_source", "long", "level"),
}

FEATURE_KEYS = (
    "fan_magnitude",
    "fan_magnitude_gain",
    "source_score",
    "cloud_top",
    "cloud_bottom",
    "trend_close_1h",
    "trend_close_8h",
)


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def build_config(variant: str) -> Path:
    profile_name, side, trigger = VARIANTS[variant]
    profile = PROFILES[profile_name]
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
            "picasso_source_stoploss": float(profile["stop"]),
            "picasso_trailing_positive": float(profile["trail"]),
            "picasso_trailing_offset": float(profile["trail_offset"]),
            "picasso_emergency_target_fraction": float(profile["objective"]),
            "picasso_roi_0": float(profile["roi"]),
            "picasso_roi_416": float(profile["roi"]),
            "picasso_roi_933": float(profile["roi"]),
            "picasso_roi_1982": float(profile["roi"]),
            "ichi_trigger_mode": trigger,
            "ichi_side_mode": side,
            "ichi_profile": profile_name,
            "ichi_shift_inputs_one_candle": True,
            "ichi_above_cloud_level": 1,
            "ichi_bullish_level": 4,
            "ichi_fan_shift_value": int(profile["fan_shift"]),
            "ichi_min_fan_magnitude_gain": float(profile["min_gain"]),
            "ichi_conversion_period": 20,
            "ichi_base_period": 60,
            "ichi_lagging_span_period": 120,
            "ichi_displacement": 30,
            "ichi_stop_fraction": float(profile["stop"]),
            "ichi_objective_fraction": float(profile["objective"]),
            "ichi_roi_enabled": bool(profile["roi_enabled"]),
            "ichi_ignore_roi_if_entry_signal": bool(
                profile["ignore_roi_if_entry_signal"]
            ),
            "ichi_roi_0": float(profile["roi"]),
            "ichi_roi_t1_minutes": 10_000,
            "ichi_roi_t1": float(profile["roi"]),
            "ichi_roi_t2_minutes": 20_000,
            "ichi_roi_t2": float(profile["roi"]),
            "ichi_roi_t3_minutes": 30_000,
            "ichi_roi_t3": float(profile["roi"]),
            "ichi_trailing_enabled": bool(profile["trailing_enabled"]),
            "ichi_trailing_positive": float(profile["trail"]),
            "ichi_trailing_offset": float(profile["trail_offset"]),
            "ichi_trailing_only_offset_is_reached": True,
            "ichi_exit_indicator": "trend_close_1.5h",
        }
    )
    path = WORK / "configs" / f"{variant}.json"
    dump(path, payload)
    return path


def run_case(stage: Stage, variant: str) -> dict[str, Any]:
    output = ARTIFACTS / stage.name / variant
    workspace = WORK / "workspace" / stage.name / variant
    for path in (output, workspace):
        if path.exists():
            shutil.rmtree(path)
    output.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(C51 / "launch.py"),
        "--config",
        str(build_config(variant)),
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
    profile_name, side, trigger = VARIANTS[variant]
    if completed.returncode != 0 or not metrics_path.is_file() or not diagnostics_path.is_file():
        row = {
            "stage": asdict(stage) | {"days": stage.days},
            "variant": variant,
            "profile": profile_name,
            "side": side,
            "trigger": trigger,
            "produced": False,
            "returncode": completed.returncode,
        }
        dump(EVIDENCE / "cases" / f"{stage.name}-{variant}.json", row)
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
        "ichi_trailing_activations",
        "ichi_trailing_exits",
        "ichi_roi_ignored_active_signal_minutes",
        "max_open_positions_observed",
        "max_simultaneous_entry_intents",
        "global_position_violations",
        "order_rejections",
    )
    expected = int(metrics.get("trades") or 0)
    row = {
        "stage": asdict(stage) | {"days": stage.days},
        "variant": variant,
        "profile": profile_name,
        "side": side,
        "trigger": trigger,
        "produced": True,
        "returncode": 0,
        "metrics": {key: metrics.get(key) for key in metric_keys},
        "diagnostics": {key: diagnostics.get(key) for key in diagnostic_keys},
        "trade_forensics": analyze_trades(output, expected, FEATURE_KEYS),
    }
    dump(EVIDENCE / "cases" / f"{stage.name}-{variant}.json", row)
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


def eligible(row: dict[str, Any], stage: Stage) -> bool:
    metrics = row.get("metrics") or {}
    return (
        account_ok(row)
        and int(metrics.get("trades") or 0) >= max(3, stage.days // 3)
        and number(metrics.get("max_drawdown"), 1.0) <= 0.30
        and number(metrics.get("min_equity")) > 0.0
    )


def quality_rank(row: dict[str, Any]) -> tuple[float, float, float, int, str]:
    metrics = row.get("metrics") or {}
    return (
        -number(metrics.get("geometric_daily_growth"), -math.inf),
        -number(metrics.get("expectancy_usdt"), -math.inf),
        -number(metrics.get("profit_factor"), -math.inf),
        -int(metrics.get("trades") or 0),
        str(row.get("variant")),
    )


def density_rank(row: dict[str, Any]) -> tuple[int, float, float, str]:
    metrics = row.get("metrics") or {}
    return (
        -int(metrics.get("trades") or 0),
        -number(metrics.get("total_return"), -math.inf),
        -number(metrics.get("expectancy_usdt"), -math.inf),
        str(row.get("variant")),
    )


def select_development(rows: dict[str, dict[str, Any]]) -> tuple[list[str], dict[str, Any]]:
    pool = [row for row in rows.values() if eligible(row, DEVELOPMENT)]
    selected: list[str] = []
    quality = sorted(pool, key=quality_rank)[0] if pool else None
    dense_pool = [
        row
        for row in pool
        if int((row.get("metrics") or {}).get("trades") or 0) >= DEVELOPMENT.days
    ]
    density = sorted(dense_pool, key=density_rank)[0] if dense_pool else None
    for row in (quality, density):
        if row is not None and str(row["variant"]) not in selected:
            selected.append(str(row["variant"]))
    for row in sorted(pool, key=quality_rank):
        if str(row["variant"]) not in selected:
            selected.append(str(row["variant"]))
        if len(selected) >= 2:
            break
    return selected[:2], {
        "selection_is_resource_allocation_not_truth_gate": True,
        "eligible": [str(row["variant"]) for row in pool],
        "quality_leader": None if quality is None else str(quality["variant"]),
        "opportunity_density_leader": None if density is None else str(density["variant"]),
    }


def positive(row: dict[str, Any], stage: Stage) -> bool:
    metrics = row.get("metrics") or {}
    pf = metrics.get("profit_factor")
    pf_ok = (
        number(pf) > 1.0
        if pf is not None
        else int(metrics.get("wins") or 0) > 0 and int(metrics.get("losses") or 0) == 0
    )
    return (
        account_ok(row)
        and int(metrics.get("trades") or 0) >= stage.days
        and number(metrics.get("geometric_daily_growth")) > 0.0
        and number(metrics.get("expectancy_usdt")) > 0.0
        and pf_ok
        and number(metrics.get("max_drawdown"), 1.0) <= 0.20
    )


def compact(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics") or {}
    diagnostics = row.get("diagnostics") or {}
    return {
        "trades": metrics.get("trades"),
        "wins": metrics.get("wins"),
        "losses": metrics.get("losses"),
        "profit_factor": metrics.get("profit_factor"),
        "geometric_daily_growth": metrics.get("geometric_daily_growth"),
        "total_return": metrics.get("total_return"),
        "max_drawdown": metrics.get("max_drawdown"),
        "expectancy_usdt": metrics.get("expectancy_usdt"),
        "signals": diagnostics.get("source_signals_before_execution_filters"),
        "entries": diagnostics.get("entry_submissions"),
        "exit_mix": (row.get("trade_forensics") or {}).get("by_exit_reason"),
    }


def render(comparison: dict[str, Any]) -> None:
    lines = [
        "# Public ichiV2 five-minute tournament",
        "",
        "Every row is the project's four-symbol, one-slot, cost-after account. Case JSON files contain every completed trade and its entry-state diagnostics.",
        "",
    ]
    for stage_name in ("development", "untouched", "continuous_30d"):
        lines += [
            f"## {stage_name}",
            "",
            "| variant | trades | W/L | PF | geo/day | return | MDD | expectancy | signals |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for name, row in comparison[stage_name].items():
            metrics = row.get("metrics") or {}
            diagnostics = row.get("diagnostics") or {}
            lines.append(
                f"| {name} | {metrics.get('trades')} | {metrics.get('wins')}/{metrics.get('losses')} | "
                f"{metrics.get('profit_factor')} | {metrics.get('geometric_daily_growth')} | "
                f"{metrics.get('total_return')} | {metrics.get('max_drawdown')} | "
                f"{metrics.get('expectancy_usdt')} | {diagnostics.get('source_signals_before_execution_filters')} |"
            )
        lines.append("")
    lines += [
        "## Allocation",
        "",
        f"- development survivors: {comparison['development_survivors']}",
        f"- positive untouched survivors: {comparison['untouched_positive_survivors']}",
        f"- continuous winner: {comparison['continuous_winner']}",
        f"- strict project pass: {comparison['strict_project_pass']}",
        "",
        "Development allocation preserves both the best quality candidate and a different high-opportunity candidate when present; it is not a binary truth gate.",
    ]
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if not FREEZE.is_file():
        raise RuntimeError("frozen ichiV2 tournament specification missing")
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    development = {name: run_case(DEVELOPMENT, name) for name in VARIANTS}
    survivors, allocation = select_development(development)
    untouched = {name: run_case(UNTOUCHED, name) for name in survivors}
    positive_names = [name for name, row in untouched.items() if positive(row, UNTOUCHED)]
    positive_names.sort(key=lambda name: quality_rank(untouched[name]))
    winner = positive_names[0] if positive_names else None
    continuous = {winner: run_case(CONTINUOUS, winner)} if winner else {}
    strict_pass = bool(
        winner
        and winner in continuous
        and positive(continuous[winner], CONTINUOUS)
        and number((continuous[winner].get("metrics") or {}).get("geometric_daily_growth")) >= 0.01
    )

    comparison = {
        "experiment": "candidate-57-ichi-v2-5m-v1",
        "external_claim_is_discovery_signal_only": True,
        "external_claim": {
            "period": ["2025-01-01", "2025-04-03"],
            "mode": "spot",
            "max_open_trades": 3,
            "trades": 1056,
            "trades_per_day": 11.48,
            "win_rate": 0.766,
            "profit_factor": 6.51,
            "total_return": 35.32952,
            "account_drawdown": 0.0113,
            "management_inference": "1.5% ROI and 4% stop from report outcome magnitudes",
        },
        "causality": {
            "completed_5m_only": True,
            "one_candle_shift_preserved": True,
            "recursive_heikin_ashi": True,
            "senkou_displacement_minus_one": True,
            "chikou_used": False,
        },
        "profiles": PROFILES,
        "variants": VARIANTS,
        "stages": {
            "development": asdict(DEVELOPMENT) | {"days": DEVELOPMENT.days},
            "untouched": asdict(UNTOUCHED) | {"days": UNTOUCHED.days},
            "continuous_30d": asdict(CONTINUOUS) | {"days": CONTINUOUS.days},
        },
        "development": development,
        "development_survivors": survivors,
        "development_allocation": allocation,
        "untouched": untouched,
        "untouched_positive_survivors": positive_names,
        "continuous_30d": continuous,
        "continuous_winner": winner,
        "strict_project_pass": strict_pass,
        "compact": {
            "development": {name: compact(row) for name, row in development.items()},
            "untouched": {name: compact(row) for name, row in untouched.items()},
            "continuous_30d": {name: compact(row) for name, row in continuous.items()},
        },
    }
    dump(EVIDENCE / "comparison.json", comparison)
    render(comparison)
    print(json.dumps(comparison["compact"], indent=2, sort_keys=True, allow_nan=False))

    rows = list(development.values()) + list(untouched.values()) + list(continuous.values())
    if any(not row.get("produced") for row in rows):
        return 1
    if any(not account_ok(row) for row in rows):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

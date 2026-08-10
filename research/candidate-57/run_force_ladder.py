#!/usr/bin/env python3
"""Source-first short diagnostic ladder for the public TheForce strategy."""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

ROOT = Path.cwd()
CANDIDATE = ROOT / "research" / "candidate-57"
REUSED = ROOT / "research" / "candidate-51"
WORK = ROOT / ".work" / "candidate-57-force-v1"
ARTIFACTS = ROOT / "artifacts" / "candidate-57"
EVIDENCE = CANDIDATE / "evidence" / "force-v1"
CACHE = ROOT / ".cache" / "candidate-57-force-v1"

SHORT_WINDOWS = {
    "short-a": ("2026-06-15", "2026-06-21"),
    "short-b": ("2025-10-06", "2025-10-12"),
}
INTERMEDIATE = ("2025-09-01", "2025-09-30")
MAX_HOLD_MINUTES = 240
MAX_OBSERVED_DURATION_MINUTES = 242.0
COMPACT_FILES = (
    "metrics.json",
    "strategy_diagnostics.json",
    "run.json",
    "data_manifest.json",
    "closed_scenarios.json",
)


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def create_config() -> Path:
    source = json.loads((REUSED / "config.json").read_text(encoding="utf-8"))
    config = copy.deepcopy(source)
    for key in (
        "sma_offset_low",
        "sma_offset_high",
        "sma_stop_min_fraction",
        "sma_stop_max_fraction",
        "sma_stop_atr_buffer",
    ):
        config["strategy"].pop(key, None)
    config["strategy"].update(
        {
            "cooldown_minutes": 0,
            "max_hold_minutes": MAX_HOLD_MINUTES,
            "funding_flatten_minute": 45,
            "funding_blackout_before_minutes": 25,
            "funding_blackout_after_minutes": 5,
            "force_startup_15m_candles": 30,
            "force_stop_fraction": 0.015,
            "force_roi_0": 0.012,
            "force_roi_15": 0.010,
            "force_roi_30": 0.005,
        }
    )
    WORK.mkdir(parents=True, exist_ok=True)
    path = WORK / "frozen-config.json"
    dump(path, config)
    dump(
        WORK / "freeze-manifest.json",
        {
            "candidate": "candidate-57",
            "family": "PUBLIC_THEFORCE_15M_MOMENTUM",
            "source": {
                "repository": "PeetCrypto/freqtrade-stuff",
                "path": "TheForce.py",
                "blob": "af1c8ac097afda8caa620fe3539a62f96455614c",
                "side": "long",
                "timeframe_minutes": 15,
                "startup_candles": 30,
                "stop_fraction": 0.015,
                "roi_ladder": {"0": 0.012, "15": 0.010, "30": 0.005},
                "sell_signal": True,
            },
            "project_overlays": {
                "max_hold_minutes": MAX_HOLD_MINUTES,
                "funding_safety": True,
                "one_global_slot": True,
                "current_nav_risk_fraction": 0.03,
            },
            "short_windows": SHORT_WINDOWS,
            "conditional_intermediate": INTERMEDIATE,
            "symmetric_short_added": False,
            "long_run_in_this_campaign": False,
        },
    )
    return path


def output_root(stage: str) -> Path:
    return ARTIFACTS / f"force-v1-{stage}"


def run_backtest(stage: str, interval: tuple[str, str], config: Path) -> int:
    command = [
        sys.executable,
        str(REUSED / "launch.py"),
        "--config",
        str(config),
        "--start",
        interval[0],
        "--end",
        interval[1],
        "--cache",
        str(CACHE),
        "--output",
        str(output_root(stage)),
        "--workspace",
        str(WORK / stage),
    ]
    env = dict(os.environ)
    previous = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(REUSED) if not previous else str(REUSED) + os.pathsep + previous
    )
    print("RUN", stage, interval, flush=True)
    completed = subprocess.run(command, env=env, check=False)
    return int(completed.returncode)


def maximum_duration_minutes(path: Path) -> float:
    if not path.is_file():
        return math.inf
    rows = json.loads(path.read_text(encoding="utf-8"))
    maximum = 0.0
    for row in rows:
        match = re.search(r"duration_ns=(\d+)", str(row.get("event", "")))
        if match is not None:
            maximum = max(maximum, int(match.group(1)) / 60_000_000_000.0)
    return maximum


def read_result(stage: str, returncode: int) -> dict[str, Any]:
    root = output_root(stage)
    metrics_path = root / "metrics.json"
    diagnostics_path = root / "strategy_diagnostics.json"
    if (
        returncode != 0
        or not metrics_path.is_file()
        or not diagnostics_path.is_file()
    ):
        return {
            "produced": False,
            "returncode": returncode,
            "artifact_root": str(root.relative_to(ROOT)),
        }
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    row: dict[str, Any] = {
        "produced": True,
        "returncode": returncode,
        "artifact_root": str(root.relative_to(ROOT)),
    }
    for key in (
        "calendar_days",
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
        "largest_winner_share",
        "gate_checks",
    ):
        row[key] = metrics.get(key)
    row.update(
        {
            "source_signals": diagnostics.get(
                "source_signals_before_execution_filters"
            ),
            "entry_submissions": diagnostics.get("entry_submissions"),
            "force_collision_boundaries": diagnostics.get(
                "force_collision_boundaries"
            ),
            "force_competing_candidates": diagnostics.get(
                "force_competing_candidates"
            ),
            "force_roi_exits": diagnostics.get("force_roi_exits"),
            "force_sell_signal_exits": diagnostics.get(
                "force_sell_signal_exits"
            ),
            "funding_runway_rejections": diagnostics.get(
                "funding_runway_rejections"
            ),
            "global_position_violations": diagnostics.get(
                "global_position_violations"
            ),
            "max_open_positions_observed": diagnostics.get(
                "max_open_positions_observed"
            ),
            "order_rejections": diagnostics.get("order_rejections"),
            "source_short_symmetry_added": diagnostics.get(
                "source_short_symmetry_added"
            ),
            "project_daytrade_overlay_max_hold_minutes": diagnostics.get(
                "project_daytrade_overlay_max_hold_minutes"
            ),
            "project_funding_safety_overlay": diagnostics.get(
                "project_funding_safety_overlay"
            ),
            "max_observed_duration_minutes": maximum_duration_minutes(
                root / "closed_scenarios.json"
            ),
        }
    )
    return row


def integrity_checks(row: dict[str, Any]) -> dict[str, bool]:
    gates = row.get("gate_checks") or {}
    return {
        "produced": bool(row.get("produced")),
        "positive_equity": float(row.get("min_equity") or 0.0) > 0.0,
        "no_liquidation": bool(gates.get("no_liquidation")),
        "no_rejections": int(row.get("order_rejections") or 0) == 0,
        "one_position": int(row.get("max_open_positions_observed") or 0) <= 1,
        "no_global_position_violation": int(
            row.get("global_position_violations") or 0
        ) == 0,
        "source_long_only": int(row.get("source_short_symmetry_added") or 0) == 0,
        "project_daytrade_overlay_exact": int(
            row.get("project_daytrade_overlay_max_hold_minutes") or 0
        ) == MAX_HOLD_MINUTES,
        "project_funding_overlay_active": int(
            row.get("project_funding_safety_overlay") or 0
        ) == 1,
        "daytrade_duration": float(
            row.get("max_observed_duration_minutes") or math.inf
        ) <= MAX_OBSERVED_DURATION_MINUTES,
    }


def effective_pf(row: dict[str, Any]) -> float:
    value = row.get("profit_factor")
    if value is None and int(row.get("wins") or 0) > 0 and int(row.get("losses") or 0) == 0:
        return math.inf
    return float(value or 0.0)


def short_checks(row: dict[str, Any]) -> dict[str, bool]:
    days = int(row.get("calendar_days") or 0)
    checks = integrity_checks(row)
    checks.update(
        {
            "seven_calendar_days": days == 7,
            "independent_trades_at_least_days": int(row.get("trades") or 0) >= days,
            "daily_growth_at_least_0_5pct": float(
                row.get("geometric_daily_growth") or -1.0
            ) >= 0.005,
            "positive_expectancy": float(row.get("expectancy_usdt") or 0.0) > 0.0,
            "profit_factor_at_least_1_3": effective_pf(row) >= 1.3,
            "win_rate_at_least_45pct": float(row.get("win_rate") or 0.0) >= 0.45,
            "drawdown_at_most_15pct": float(row.get("max_drawdown") or 1.0) <= 0.15,
        }
    )
    return checks


def intermediate_checks(row: dict[str, Any]) -> dict[str, bool]:
    days = int(row.get("calendar_days") or 0)
    checks = integrity_checks(row)
    checks.update(
        {
            "thirty_calendar_days": days == 30,
            "independent_trades_at_least_days": int(row.get("trades") or 0) >= days,
            "project_daily_growth": float(
                row.get("geometric_daily_growth") or -1.0
            ) >= 0.01,
            "positive_expectancy": float(row.get("expectancy_usdt") or 0.0) > 0.0,
            "profit_factor_at_least_1_3": effective_pf(row) >= 1.3,
            "win_rate_at_least_45pct": float(row.get("win_rate") or 0.0) >= 0.45,
            "drawdown_at_most_20pct": float(row.get("max_drawdown") or 1.0) <= 0.20,
        }
    )
    return checks


def pooled_growth(rows: list[dict[str, Any]]) -> float:
    total_days = sum(int(row["calendar_days"]) for row in rows)
    log_growth = sum(
        int(row["calendar_days"]) * math.log1p(float(row["geometric_daily_growth"]))
        for row in rows
    )
    return math.expm1(log_growth / total_days)


def copy_compact(source: Path, destination: Path) -> None:
    if not source.is_dir():
        return
    destination.mkdir(parents=True, exist_ok=True)
    for filename in COMPACT_FILES:
        path = source / filename
        if path.is_file():
            shutil.copy2(path, destination / filename)


def persist(short_rows: dict[str, Any], short_gate: dict[str, Any], intermediate: dict[str, Any]) -> dict[str, Any]:
    if intermediate.get("gate_pass"):
        decision = "AUTHORIZE_INTEGRATION_PRESSURE"
        reason = "The exact public source survived both short windows and the 30-day project goal."
    elif short_gate.get("pass") and intermediate.get("produced"):
        decision = "RETAIN_COMPONENT_NOT_INTEGRATION_READY"
        reason = "Short-window alpha existed but the 30-day project goal did not hold."
    elif short_gate.get("pass"):
        decision = "INTERMEDIATE_EXECUTION_INCOMPLETE"
        reason = "Short-window evidence warranted expansion but the expansion was incomplete."
    else:
        decision = "REJECT_STANDALONE_RETAIN_DIAGNOSTIC_COMPONENTS"
        reason = "The exact public source did not produce sufficiently consistent one-slot short-window evidence."
    result = {
        "candidate": "candidate-57",
        "family": "PUBLIC_THEFORCE_15M_MOMENTUM",
        "decision": decision,
        "reason": reason,
        "short": short_rows,
        "short_promotion_gate": short_gate,
        "intermediate_30d": intermediate,
        "symmetric_short_tested": False,
        "long_run_executed": False,
        "production_ready": False,
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    dump(EVIDENCE / "RESULT.json", result)
    shutil.copy2(WORK / "freeze-manifest.json", EVIDENCE / "freeze-manifest.json")
    shutil.copy2(WORK / "frozen-config.json", EVIDENCE / "frozen-config.json")
    for stage in SHORT_WINDOWS:
        copy_compact(output_root(stage), EVIDENCE / stage)
    if intermediate.get("produced"):
        copy_compact(output_root("intermediate-30d"), EVIDENCE / "intermediate-30d")
    return result


def main() -> int:
    config = create_config()
    short_rows: dict[str, dict[str, Any]] = {}
    for stage, interval in SHORT_WINDOWS.items():
        code = run_backtest(stage, interval, config)
        row = read_result(stage, code)
        checks = short_checks(row) if row.get("produced") else {}
        row["checks"] = checks
        row["gate_pass"] = bool(checks) and all(checks.values())
        short_rows[stage] = row

    produced = [row for row in short_rows.values() if row.get("produced")]
    combined_growth = pooled_growth(produced) if len(produced) == 2 else None
    short_gate = {
        "individual_windows_pass": all(row.get("gate_pass") for row in short_rows.values()),
        "pooled_geometric_daily_growth": combined_growth,
        "pooled_growth_at_least_0_75pct": combined_growth is not None and combined_growth >= 0.0075,
        "total_trades": sum(int(row.get("trades") or 0) for row in short_rows.values()),
    }
    short_gate["pass"] = bool(
        short_gate["individual_windows_pass"]
        and short_gate["pooled_growth_at_least_0_75pct"]
    )

    intermediate: dict[str, Any] = {
        "produced": False,
        "not_run_reason": "SHORT_PROMOTION_GATE_FAILED",
    }
    if short_gate["pass"]:
        code = run_backtest("intermediate-30d", INTERMEDIATE, config)
        intermediate = read_result("intermediate-30d", code)
        checks = intermediate_checks(intermediate) if intermediate.get("produced") else {}
        intermediate["checks"] = checks
        intermediate["gate_pass"] = bool(checks) and all(checks.values())

    result = persist(short_rows, short_gate, intermediate)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

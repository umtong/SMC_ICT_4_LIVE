#!/usr/bin/env python3
"""Frozen short -> conditional intermediate ladder for V13 crowd breakdown."""
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
WORK = ROOT / ".work" / "candidate-57-v13-crowd-daytrade-v1"
ARTIFACTS = ROOT / "artifacts" / "candidate-57"
EVIDENCE = CANDIDATE / "evidence" / "v13-crowd-daytrade-v1"
CACHE = ROOT / ".cache" / "candidate-57-v13-crowd-daytrade-v1"

SHORT_WINDOWS = {
    "short-a": ("2026-02-08", "2026-02-14"),
    "short-b": ("2026-03-08", "2026-03-14"),
}
INTERMEDIATE = ("2026-01-01", "2026-01-30")
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
            "zaratustra_variant": "source_short",
            "zaratustra_startup_30m_candles": 10,
            "zaratustra_rsi_period": 14,
            "zaratustra_di_period": 14,
            "zaratustra_bb_period": 20,
            "zaratustra_source_leverage": 10.0,
            "zaratustra_source_stoploss": 0.296,
            "zaratustra_trailing_positive": 0.010,
            "zaratustra_trailing_offset": 0.100,
            "zaratustra_emergency_target_fraction": 0.50,
            "zaratustra_crowd_min_ratio": 1.20,
            "zaratustra_taker_max_ratio": 1.00,
            "zaratustra_crowd_row_max_age_seconds": 65.0,
            "zaratustra_crowd_metrics_max_age_seconds": 305.0,
        }
    )
    WORK.mkdir(parents=True, exist_ok=True)
    path = WORK / "frozen-config.json"
    dump(path, config)
    dump(
        WORK / "freeze-manifest.json",
        {
            "candidate": "candidate-57",
            "family": "CROWDED_LONG_BREAKDOWN_ACCEPTANCE",
            "source_entry": "ZaratustraV13 source_short",
            "context": "sum_toptrader_long_short_ratio > 1.20",
            "confirmation": (
                "sum_taker_long_short_vol_ratio < 1.00 OR source DI+BB concurrence"
            ),
            "max_hold_minutes": MAX_HOLD_MINUTES,
            "funding_policy": "common project blackout and pre-funding flatten",
            "short_windows": SHORT_WINDOWS,
            "conditional_intermediate": INTERMEDIATE,
            "long_run_in_this_campaign": False,
            "risk_fraction": config["risk_fraction"],
            "universe": config["symbols"],
        },
    )
    return path


def output_root(stage: str) -> Path:
    return ARTIFACTS / f"v13-crowd-daytrade-v1-{stage}"


def run_backtest(stage: str, interval: tuple[str, str], config: Path) -> int:
    output = output_root(stage)
    workspace = WORK / stage
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
        str(output),
        "--workspace",
        str(workspace),
    ]
    env = dict(os.environ)
    previous = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(REUSED) if not previous else str(REUSED) + os.pathsep + previous
    )
    print("RUN", stage, interval, flush=True)
    completed = subprocess.run(command, env=env, check=False)
    (WORK / "status").mkdir(parents=True, exist_ok=True)
    (WORK / "status" / f"{stage}.txt").write_text(
        f"{completed.returncode}\n", encoding="utf-8"
    )
    return int(completed.returncode)


def maximum_duration_minutes(path: Path) -> float:
    if not path.is_file():
        return math.inf
    rows = json.loads(path.read_text(encoding="utf-8"))
    maximum = 0.0
    for row in rows:
        match = re.search(r"duration_ns=(\d+)", str(row.get("event", "")))
        if match is not None:
            maximum = max(
                maximum, int(match.group(1)) / 60_000_000_000.0
            )
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
            "entry_submissions": diagnostics.get("entry_submissions"),
            "source_signals": diagnostics.get(
                "source_signals_before_execution_filters"
            ),
            "crowd_gate_evaluations": diagnostics.get(
                "crowd_gate_evaluations"
            ),
            "crowd_gate_passes": diagnostics.get("crowd_gate_passes"),
            "crowd_gate_rejections": diagnostics.get(
                "crowd_gate_rejections"
            ),
            "crowd_gate_reason_counts": diagnostics.get(
                "crowd_gate_reason_counts"
            ),
            "crowd_gate_confirmation_counts": diagnostics.get(
                "crowd_gate_confirmation_counts"
            ),
            "global_position_violations": diagnostics.get(
                "global_position_violations"
            ),
            "max_open_positions_observed": diagnostics.get(
                "max_open_positions_observed"
            ),
            "order_rejections": diagnostics.get("order_rejections"),
            "source_entry_variant_frozen": diagnostics.get(
                "source_entry_variant_frozen"
            ),
            "crowd_gate_before_universe_arbitration": diagnostics.get(
                "crowd_gate_before_universe_arbitration"
            ),
            "source_price_stop_trailing_changed": diagnostics.get(
                "source_price_stop_trailing_changed"
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
        "source_entry_frozen": (
            row.get("source_entry_variant_frozen") == "source_short"
        ),
        "gate_before_arbitration": int(
            row.get("crowd_gate_before_universe_arbitration") or 0
        ) == 1,
        "source_price_stop_trailing_not_rewritten": int(
            row.get("source_price_stop_trailing_changed") or 0
        ) == 0,
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


def effective_profit_factor(row: dict[str, Any]) -> float:
    value = row.get("profit_factor")
    if (
        value is None
        and int(row.get("wins") or 0) > 0
        and int(row.get("losses") or 0) == 0
    ):
        return math.inf
    return float(value or 0.0)


def short_checks(row: dict[str, Any]) -> dict[str, bool]:
    days = int(row.get("calendar_days") or 0)
    checks = integrity_checks(row)
    checks.update(
        {
            "seven_calendar_days": days == 7,
            "independent_trades_at_least_days": int(row.get("trades") or 0)
            >= days,
            "strong_daily_growth": float(
                row.get("geometric_daily_growth") or -1.0
            ) >= 0.0075,
            "positive_expectancy": float(row.get("expectancy_usdt") or 0.0)
            > 0.0,
            "profit_factor_at_least_1_5": effective_profit_factor(row) >= 1.5,
            "win_rate_at_least_55pct": float(row.get("win_rate") or 0.0)
            >= 0.55,
            "drawdown_at_most_15pct": float(row.get("max_drawdown") or 1.0)
            <= 0.15,
        }
    )
    return checks


def intermediate_checks(row: dict[str, Any]) -> dict[str, bool]:
    days = int(row.get("calendar_days") or 0)
    checks = integrity_checks(row)
    checks.update(
        {
            "thirty_calendar_days": days == 30,
            "independent_trades_at_least_days": int(row.get("trades") or 0)
            >= days,
            "project_daily_growth": float(
                row.get("geometric_daily_growth") or -1.0
            ) >= 0.01,
            "positive_expectancy": float(row.get("expectancy_usdt") or 0.0)
            > 0.0,
            "profit_factor_at_least_1_3": effective_profit_factor(row) >= 1.3,
            "win_rate_at_least_50pct": float(row.get("win_rate") or 0.0)
            >= 0.50,
            "drawdown_at_most_20pct": float(row.get("max_drawdown") or 1.0)
            <= 0.20,
        }
    )
    return checks


def pooled_short_growth(rows: list[dict[str, Any]]) -> float:
    total_days = sum(int(row["calendar_days"]) for row in rows)
    log_growth = sum(
        int(row["calendar_days"])
        * math.log1p(float(row["geometric_daily_growth"]))
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


def persist(
    short_rows: dict[str, Any],
    short_gate: dict[str, Any],
    intermediate: dict[str, Any],
) -> dict[str, Any]:
    if intermediate.get("gate_pass"):
        decision = "AUTHORIZE_SEPARATE_LONG_PRESSURE"
        reason = (
            "Both strong short windows and the untouched 30-day project gate passed."
        )
    elif short_gate.get("pass") and intermediate.get("produced"):
        decision = "RETAIN_COMPONENT_NOT_LONG_READY"
        reason = "Short evidence was strong but the 30-day project gate failed."
    elif short_gate.get("pass"):
        decision = "INTERMEDIATE_EXECUTION_INCOMPLETE"
        reason = (
            "Strong short evidence existed but intermediate evidence was incomplete."
        )
    else:
        decision = "REJECT_STANDALONE_RETAIN_COMPONENT_ONLY"
        reason = (
            "At least one strong short gate or the pooled 1% growth gate failed; "
            "no intermediate or long rescue is permitted."
        )
    result = {
        "candidate": "candidate-57",
        "family": "CROWDED_LONG_BREAKDOWN_ACCEPTANCE",
        "decision": decision,
        "reason": reason,
        "short": short_rows,
        "short_promotion_gate": short_gate,
        "intermediate_30d": intermediate,
        "long_run_executed": False,
        "long_evaluation_authorized": bool(intermediate.get("gate_pass")),
        "production_ready": False,
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    dump(EVIDENCE / "RESULT.json", result)
    shutil.copy2(WORK / "freeze-manifest.json", EVIDENCE / "freeze-manifest.json")
    shutil.copy2(WORK / "frozen-config.json", EVIDENCE / "frozen-config.json")
    for stage in SHORT_WINDOWS:
        copy_compact(output_root(stage), EVIDENCE / stage)
    if intermediate.get("produced"):
        copy_compact(
            output_root("intermediate-30d"), EVIDENCE / "intermediate-30d"
        )
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
    pooled_growth = pooled_short_growth(produced) if len(produced) == 2 else None
    short_gate = {
        "individual_windows_pass": all(
            row.get("gate_pass") for row in short_rows.values()
        ),
        "pooled_geometric_daily_growth": pooled_growth,
        "pooled_growth_at_least_1pct": (
            pooled_growth is not None and pooled_growth >= 0.01
        ),
        "total_trades": sum(
            int(row.get("trades") or 0) for row in short_rows.values()
        ),
    }
    short_gate["pass"] = bool(
        short_gate["individual_windows_pass"]
        and short_gate["pooled_growth_at_least_1pct"]
    )

    intermediate: dict[str, Any] = {
        "produced": False,
        "not_run_reason": "SHORT_PROMOTION_GATE_FAILED",
    }
    if short_gate["pass"]:
        code = run_backtest("intermediate-30d", INTERMEDIATE, config)
        intermediate = read_result("intermediate-30d", code)
        checks = (
            intermediate_checks(intermediate)
            if intermediate.get("produced")
            else {}
        )
        intermediate["checks"] = checks
        intermediate["gate_pass"] = bool(checks) and all(checks.values())

    result = persist(short_rows, short_gate, intermediate)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

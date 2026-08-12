"""Evidence extension for the source-aligned EasyChart day-trading lifecycle."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from backtest_support import write_json
from mtf_backtest_support_v5 import preserve_mtf_results_v5
from mtf_strategy_day_v7 import MAX_HOLD_HOURS, MAX_HOLD_PROVENANCE


_DURATION_TOLERANCE = pd.Timedelta(milliseconds=1)


def preserve_daytrade_results(*args: Any, **kwargs: Any) -> dict[str, Any]:
    metrics = preserve_mtf_results_v5(*args, **kwargs)
    output: Path = kwargs["output"] if "output" in kwargs else args[2]

    trade_path = output / "trade_audit.csv"
    trade_audit = pd.read_csv(trade_path)
    if trade_audit.empty:
        trade_audit["hold_duration_seconds"] = pd.Series(dtype=float)
        trade_audit["max_hold_breach"] = pd.Series(dtype=bool)
        max_hold_seconds = None
        median_hold_seconds = None
        p95_hold_seconds = None
        max_hold_breaches = 0
        time_exits = 0
    else:
        opened = pd.to_datetime(trade_audit["ts_opened"], utc=True, errors="coerce")
        closed = pd.to_datetime(trade_audit["ts_closed"], utc=True, errors="coerce")
        durations = closed - opened
        if durations.isna().any():
            raise RuntimeError("day-trading audit could not parse one or more position timestamps")
        limit = pd.Timedelta(hours=MAX_HOLD_HOURS) + _DURATION_TOLERANCE
        trade_audit["hold_duration_seconds"] = durations.dt.total_seconds()
        trade_audit["max_hold_breach"] = durations > limit
        max_hold_seconds = float(trade_audit["hold_duration_seconds"].max())
        median_hold_seconds = float(trade_audit["hold_duration_seconds"].median())
        p95_hold_seconds = float(trade_audit["hold_duration_seconds"].quantile(0.95))
        max_hold_breaches = int(trade_audit["max_hold_breach"].sum())
        time_exits = int((trade_audit["exit_role"] == "TIME_EXIT").sum())
    trade_audit.to_csv(trade_path, index=False)

    event_counts = metrics.get("event_counts", {})
    state_mismatches = int(event_counts.get("emergency_exit_time_limit_state_mismatch", 0))
    metrics.update(
        {
            "candidate": "candidate-easychart-v7-source-aligned-daytrade",
            "max_hold_hours": MAX_HOLD_HOURS,
            "max_hold_provenance": MAX_HOLD_PROVENANCE,
            "maximum_observed_hold_seconds": max_hold_seconds,
            "median_hold_seconds": median_hold_seconds,
            "p95_hold_seconds": p95_hold_seconds,
            "max_hold_breaches": max_hold_breaches,
            "time_exit_positions": time_exits,
            "time_limit_state_mismatches": state_mismatches,
            "day_trading_contract_satisfied": max_hold_breaches == 0 and state_mismatches == 0,
        },
    )
    write_json(output / "metrics.json", metrics)

    run_path = output / "run.json"
    run_record = json.loads(run_path.read_text(encoding="utf-8"))
    run_record["candidate"] = "candidate-easychart-v7-source-aligned-daytrade"
    run_record["scenario"] = (
        "causal structure -> objective -> interaction -> auction state -> event-local confirmation -> "
        "full stop/full target or full 24-hour day-trading exit"
    )
    run_record.setdefault("contract", {})
    run_record["contract"].update(
        {
            "max_position_age_hours_from_first_fill": MAX_HOLD_HOURS,
            "max_position_age_provenance": MAX_HOLD_PROVENANCE,
            "time_exit_is_full_position": True,
            "partial_management": False,
        },
    )
    write_json(run_path, run_record)

    validation_path = output / "validation.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    failures = list(validation.get("failures", []))
    if max_hold_breaches:
        failures.append(
            f"{max_hold_breaches} positions exceeded the source-aligned {MAX_HOLD_HOURS}-hour maximum hold",
        )
    if state_mismatches:
        failures.append(
            f"{state_mismatches} day-trading expiry events found an inconsistent strategy position state",
        )
    validation.update(
        {
            "status": "FAIL" if failures else "PASS",
            "failures": failures,
            "max_hold_hours": MAX_HOLD_HOURS,
            "max_hold_breaches": max_hold_breaches,
            "time_limit_state_mismatches": state_mismatches,
        },
    )
    write_json(validation_path, validation)
    if failures:
        raise RuntimeError("; ".join(failures))
    return metrics

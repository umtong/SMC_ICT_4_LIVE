#!/usr/bin/env python3
"""Aggregate the frozen per-symbol liquidity-vacuum screen as one account path.

This is a causal economic screen, not a replacement matching/account engine.
The source trade path is left unchanged.  Simultaneous events are collapsed
using only entry-time information (cost-aware net reward/risk, then symbol
priority), and the global slot remains occupied until the selected source
trade's frozen exit.  The resulting 3%-risk compounding is diagnostic only; a
survivor must still be implemented in NautilusTrader.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
SYMBOL_PRIORITY = {symbol: index for index, symbol in enumerate(SYMBOLS)}
RISK_FRACTION = 0.03
ROUND_TRIP_COST_BPS = 20.0
GLOBAL_EPISODE_MINUTES = 3
SCHEMA = "candidate-60-liquidity-vacuum-repaired-v1"


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _describe(values: Iterable[float]) -> dict[str, Any]:
    data = np.asarray([float(value) for value in values if _finite(value)], dtype=float)
    if data.size == 0:
        return {"n": 0}
    absolute = float(np.abs(data).sum())
    ordered = np.sort(data)
    return {
        "n": int(data.size),
        "mean": float(data.mean()),
        "median": float(np.median(data)),
        "minimum": float(data.min()),
        "maximum": float(data.max()),
        "positive_rate": float((data > 0.0).mean()),
        "largest_absolute_share": (
            float(np.abs(data).max() / absolute) if absolute > 0.0 else 0.0
        ),
        "trim_best_mean": float(ordered[:-1].mean()) if data.size > 1 else None,
        "trim_worst_mean": float(ordered[1:].mean()) if data.size > 1 else None,
    }


def _collapse_episodes(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events.copy()
    ordered = events.sort_values(
        ["entry_time", "net_rr", "symbol_priority"],
        ascending=[True, False, True],
        kind="stable",
    )
    gap = pd.Timedelta(minutes=GLOBAL_EPISODE_MINUTES)
    clusters: list[pd.DataFrame] = []
    current_rows: list[pd.Series] = []
    last_entry: pd.Timestamp | None = None
    for _, row in ordered.iterrows():
        entry = pd.Timestamp(row["entry_time"])
        if last_entry is None or entry - last_entry <= gap:
            current_rows.append(row)
        else:
            clusters.append(pd.DataFrame(current_rows))
            current_rows = [row]
        last_entry = entry
    if current_rows:
        clusters.append(pd.DataFrame(current_rows))
    selected = [
        cluster.sort_values(
            ["net_rr", "symbol_priority", "entry_time"],
            ascending=[False, True, True],
            kind="stable",
        ).iloc[0]
        for cluster in clusters
    ]
    return pd.DataFrame(selected).reset_index(drop=True)


def _one_slot(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events.copy()
    ordered = events.sort_values(
        ["entry_time", "net_rr", "symbol_priority"],
        ascending=[True, False, True],
        kind="stable",
    )
    selected: list[pd.Series] = []
    free_time: pd.Timestamp | None = None
    for _, row in ordered.iterrows():
        entry = pd.Timestamp(row["entry_time"])
        exit_time = pd.Timestamp(row["exit_time"])
        if free_time is not None and entry < free_time:
            continue
        selected.append(row)
        free_time = max(entry, exit_time) + pd.Timedelta(nanoseconds=1)
    return pd.DataFrame(selected).reset_index(drop=True)


def _account_path(events: pd.DataFrame, calendar_days: int) -> dict[str, Any]:
    nav = 1.0
    peak = 1.0
    max_drawdown = 0.0
    returns: list[float] = []
    for _, row in events.iterrows():
        planned_loss_bps = float(row["gross_risk_bps"]) + ROUND_TRIP_COST_BPS
        net_r = float(row["net_pnl_bps"]) / planned_loss_bps
        account_return = RISK_FRACTION * net_r
        if account_return <= -1.0:
            raise RuntimeError("diagnostic account return reached ruin")
        nav *= 1.0 + account_return
        peak = max(peak, nav)
        max_drawdown = max(max_drawdown, 1.0 - nav / peak)
        returns.append(account_return)
    daily_geometric = nav ** (1.0 / calendar_days) - 1.0 if calendar_days > 0 else math.nan
    return {
        "starting_nav": 1.0,
        "ending_nav": nav,
        "total_return": nav - 1.0,
        "geometric_daily_growth": daily_geometric,
        "max_drawdown": max_drawdown,
        "completed_trades": int(len(events)),
        "account_returns": _describe(returns),
    }


def run(root: Path, output: Path, start: date, end: date) -> dict[str, Any]:
    reports: dict[str, Any] = {}
    frames: list[pd.DataFrame] = []
    for symbol in SYMBOLS:
        directory = root / symbol
        report_path = directory / "liquidity_vacuum_screen.json"
        events_path = directory / "liquidity_vacuum_events.csv"
        if not report_path.is_file() or not events_path.is_file():
            raise RuntimeError(f"missing frozen source output for {symbol}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        reports[symbol] = report
        events = pd.read_csv(events_path)
        if events.empty:
            continue
        events["symbol"] = symbol
        events["symbol_priority"] = SYMBOL_PRIORITY[symbol]
        events["entry_time"] = pd.to_datetime(events["entry_timestamp"], utc=True)
        events["exit_time"] = pd.to_datetime(events["exit_timestamp"], utc=True)
        frames.append(events)

    all_events = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not all_events.empty:
        all_events = all_events.sort_values(
            ["entry_time", "symbol_priority"], kind="stable"
        ).reset_index(drop=True)
    episodes = _collapse_episodes(all_events)
    one_slot = _one_slot(episodes)
    calendar_days = (end - start).days + 1

    output.mkdir(parents=True, exist_ok=True)
    all_events.to_csv(output / "all_symbol_events.csv", index=False)
    episodes.to_csv(output / "global_episodes_3m.csv", index=False)
    one_slot.to_csv(output / "one_slot_events.csv", index=False)

    by_symbol = {
        symbol: {
            "events": int((all_events["symbol"] == symbol).sum()) if not all_events.empty else 0,
            "net_pnl_bps": _describe(
                all_events.loc[all_events["symbol"] == symbol, "net_pnl_bps"]
            ) if not all_events.empty else {"n": 0},
        }
        for symbol in SYMBOLS
    }
    account = _account_path(one_slot, calendar_days)
    result = {
        "schema": SCHEMA,
        "role": (
            "source-faithful mechanism screen after timestamp-unit engineering repair; "
            "not a NautilusTrader fill/account or promotion claim"
        ),
        "development_period": [start.isoformat(), end.isoformat()],
        "calendar_days": calendar_days,
        "universe": list(SYMBOLS),
        "source_reports": reports,
        "source_events": int(len(all_events)),
        "global_independent_episodes_3m": int(len(episodes)),
        "one_slot_completed_trades": int(len(one_slot)),
        "by_symbol": by_symbol,
        "source_event_net_pnl_bps": (
            _describe(all_events["net_pnl_bps"]) if not all_events.empty else {"n": 0}
        ),
        "one_slot_net_pnl_bps": (
            _describe(one_slot["net_pnl_bps"]) if not one_slot.empty else {"n": 0}
        ),
        "diagnostic_three_percent_risk_account": account,
        "interpretation_contract": {
            "timestamp_repair_changed_strategy_logic": False,
            "positive_result_is_not_sufficient_for_promotion": True,
            "negative_aggregate_requires_parent_and_geometry_funnel_review": True,
            "required_if_coherent": (
                "implement unchanged scenario through NautilusTrader and evaluate one "
                "four-symbol continuous account before any fresh promotion"
            ),
        },
    }
    (output / "summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    result = run(
        args.root.resolve(), args.output.resolve(), args.start, args.end
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()

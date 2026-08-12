#!/usr/bin/env python3
"""Run source-shaped horizontal structural break/retest diagnostics.

This screen broadens the accepted-break opportunity set from hand-declared
session/funding ranges to visible price structure.  It does not add a filter to
v15.  It creates an independent EasyChart option family whose location is a
causally confirmed overlapping wick-to-body reaction shelf.

The screen is deliberately non-authoritative.  It reuses the existing cheap
continuous-account diagnostic only to decide whether the structural family is
worth NautilusTrader promotion.  Positive results are not performance evidence.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, timedelta
import json
from pathlib import Path
import sys

import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from data import load_range, resample
from domain_v3 import Candle
from instrument_contracts import CONTRACTS
from market_v5 import DirectionalChangePivotDetector
from market_v16_structure import (
    HorizontalAcceptedBreakEngine,
    StructuralAcceptedBreakConfig,
    build_horizontal_reaction_shelves,
)
from screen_v15 import merge_same_bar_options
from simulator_v15 import CancelableExpiringSimulator
from simulator_v7 import InstrumentSpec, MinuteBar
from source_footprints import detect_fvgs, detect_order_blocks
import screen_v7 as session_base


SYMBOLS = tuple(CONTRACTS)


def to_candles(frame: pd.DataFrame) -> list[Candle]:
    return [
        Candle(
            ts_open_ns=int(row.open_time_dt.value),
            ts_close_ns=int(row.close_time_dt.value),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
        )
        for row in frame.itertuples(index=False)
    ]


def build_structure(
    *,
    symbol: str,
    one: pd.DataFrame,
    structure_minutes: int,
    dc_atr_period: int,
    dc_atr_multiple: float,
):
    frame = resample(one, structure_minutes)
    candles = to_candles(frame)
    detector = DirectionalChangePivotDetector(
        timeframe_minutes=structure_minutes,
        atr_period=dc_atr_period,
        atr_multiple=dc_atr_multiple,
    )
    pivots = []
    for index, candle in enumerate(candles):
        pivot = detector.on_candle(candle, index)
        if pivot is not None:
            pivots.append(pivot)
    shelves = build_horizontal_reaction_shelves(
        symbol=symbol,
        candles=candles,
        pivots=pivots,
        timeframe_minutes=structure_minutes,
    )
    diagnostics = {
        **{f"dc_{key}": value for key, value in detector.diagnostics.items()},
        "pivots": len(pivots),
        "shelves": len(shelves),
    }
    return pivots, shelves, diagnostics


def generate_symbol_setups(
    *,
    symbol: str,
    one: pd.DataFrame,
    signal_minutes: int,
    response_minutes: int,
    structure_minutes: int,
    dc_atr_period: int,
    dc_atr_multiple: float,
    valid_until_ns: int,
):
    pivots, shelves, structure_diagnostics = build_structure(
        symbol=symbol,
        one=one,
        structure_minutes=structure_minutes,
        dc_atr_period=dc_atr_period,
        dc_atr_multiple=dc_atr_multiple,
    )
    signal = resample(one, signal_minutes)
    response = resample(one, response_minutes)
    signal_candles = to_candles(signal)
    response_candles = to_candles(response)
    footprints = [
        *detect_order_blocks(symbol, response_candles, response_minutes),
        *detect_fvgs(symbol, response_candles, response_minutes),
    ]
    footprints.sort(key=lambda item: (item.observed_time_ns, item.footprint_id))
    engine = HorizontalAcceptedBreakEngine(
        symbol,
        shelves,
        pivots,
        StructuralAcceptedBreakConfig(
            tick_size=CONTRACTS[symbol].tick_size,
            signal_timeframe_minutes=signal_minutes,
            valid_until_ns=valid_until_ns,
        ),
    )
    cursor = 0
    setups = []
    events = []
    for index, candle in enumerate(signal_candles):
        batch = []
        while (
            cursor < len(footprints)
            and footprints[cursor].observed_time_ns <= candle.ts_close_ns
        ):
            batch.append(footprints[cursor])
            cursor += 1
        if batch:
            engine.ingest_footprints(batch)
        update = engine.on_close(candle, index)
        setups.extend(update.setups)
        events.extend(update.events)
    diagnostics = {
        "structure": structure_diagnostics,
        "engine": dict(engine.diagnostics),
    }
    return signal, setups, events, diagnostics, list(engine.audit_rows), shelves, pivots


def build_minute_batches(data, *, start: date, end: date):
    start_ts = pd.Timestamp(start, tz="UTC")
    end_exclusive = pd.Timestamp(end + timedelta(days=1), tz="UTC")
    grouped: dict[int, dict[str, MinuteBar]] = {}
    for symbol, frame in data.items():
        selected = frame[
            (frame.open_time_dt >= start_ts)
            & (frame.open_time_dt < end_exclusive)
        ]
        for row in selected.itertuples(index=False):
            close_ns = int(row.close_time_dt.value)
            grouped.setdefault(close_ns, {})[symbol] = MinuteBar(
                symbol=symbol,
                ts_open_ns=int(row.open_time_dt.value),
                ts_close_ns=close_ns,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
            )
    return grouped


def run(args: argparse.Namespace) -> dict[str, object]:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    build_start = start - timedelta(days=args.warmup_days)
    start_ns = int(pd.Timestamp(start, tz="UTC").value)
    end_exclusive_ns = int(
        pd.Timestamp(end + timedelta(days=1), tz="UTC").value
    )
    costs = session_base.cost_profile(args.cost_profile)

    data = {}
    raw_setups = []
    source_events = []
    source_diagnostics = {}
    structure_audit = []
    shelf_rows = []
    pivot_rows = []

    for symbol in SYMBOLS:
        one = load_range(symbol, build_start, end, args.cache.resolve())
        data[symbol] = one
        signal, setups, events, diagnostics, audits, shelves, pivots = (
            generate_symbol_setups(
                symbol=symbol,
                one=one,
                signal_minutes=args.signal_minutes,
                response_minutes=args.response_minutes,
                structure_minutes=args.structure_minutes,
                dc_atr_period=args.dc_atr_period,
                dc_atr_multiple=args.dc_atr_multiple,
                valid_until_ns=end_exclusive_ns,
            )
        )
        del signal
        raw_setups.extend(
            setup for setup in setups if setup.observed_time_ns >= start_ns
        )
        source_events.extend(events)
        source_diagnostics[symbol] = diagnostics
        structure_audit.extend(audits)
        shelf_rows.extend(
            {
                "symbol": symbol,
                "shelf_id": item.shelf_id,
                "side": int(item.side),
                "observed_time_ns": item.observed_time_ns,
                "timeframe_minutes": item.timeframe_minutes,
                "zone_low": item.zone_low,
                "zone_high": item.zone_high,
                "first_event_time_ns": item.first.event_time_ns,
                "first_observed_time_ns": item.first.observed_time_ns,
                "second_event_time_ns": item.second.event_time_ns,
                "second_observed_time_ns": item.second.observed_time_ns,
            }
            for item in shelves
        )
        pivot_rows.extend(
            {
                "symbol": symbol,
                "side": item.side,
                "level": item.level,
                "event_time_ns": item.event_time_ns,
                "observed_time_ns": item.observed_time_ns,
                "center_index": item.center_index,
                "observed_index": item.observed_index,
            }
            for item in pivots
        )

    raw_setups.sort(
        key=lambda item: (item.observed_time_ns, item.symbol, item.setup_id)
    )
    setups, merge_diagnostics, merge_audit = merge_same_bar_options(raw_setups)

    specs = {
        symbol: InstrumentSpec(
            symbol=symbol,
            tick_size=contract.tick_size,
            size_increment=contract.size_increment,
            min_quantity=contract.min_quantity,
            min_notional=contract.min_notional,
        )
        for symbol, contract in CONTRACTS.items()
    }
    simulator = CancelableExpiringSimulator(
        starting_nav=args.starting_nav,
        specs=specs,
        costs=costs,
        default_funding_rate=args.default_funding_rate,
    )
    grouped = build_minute_batches(data, start=start, end=end)
    cursor = 0
    for close_ns in sorted(grouped):
        batch = grouped[close_ns]
        earliest_open = min(bar.ts_open_ns for bar in batch.values())
        while (
            cursor < len(setups)
            and setups[cursor].observed_time_ns < earliest_open
        ):
            simulator.add_setups([setups[cursor]])
            cursor += 1
        simulator.on_timestamp(batch)

    days = (end - start).days + 1
    metrics = simulator.metrics(days)
    metrics.update(
        {
            "candidate": "candidate-easychart-v16-structural-shelf",
            "evaluation_start": str(start),
            "evaluation_end": str(end),
            "signal_minutes": args.signal_minutes,
            "response_minutes": args.response_minutes,
            "structure_minutes": args.structure_minutes,
            "dc_atr_period": args.dc_atr_period,
            "dc_atr_multiple": args.dc_atr_multiple,
            "raw_setups_generated": len(raw_setups),
            "setups_generated": len(setups),
            "source_diagnostics": source_diagnostics,
            "merge_diagnostics": merge_diagnostics,
            "cost_profile": args.cost_profile,
            "costs": asdict(costs),
            "fixed_contract": {
                "risk_fraction": 0.03,
                "minimum_pre_entry_gross_rr": 1.0,
                "single_entry": True,
                "full_position_stop_market": True,
                "single_full_position_target": True,
                "partial_entry": False,
                "partial_stop": False,
                "partial_target": False,
                "daily_loss_limit": None,
                "trade_count_limit": None,
                "global_entry_or_position_limit": 1,
            },
        }
    )
    metrics["target_gate"] = {
        "min_geometric_daily_growth": 0.01,
        "min_completed_trades": days,
        "passed": (
            float(metrics["geometric_daily_growth"]) >= 0.01
            and int(metrics["trades"]) >= days
            and float(metrics["ending_nav"]) > 0.0
        ),
    }

    (output / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    pd.DataFrame([asdict(item) for item in raw_setups]).to_csv(
        output / "raw_setups.csv", index=False
    )
    pd.DataFrame([asdict(item) for item in setups]).to_csv(
        output / "setups.csv", index=False
    )
    pd.DataFrame(structure_audit).to_csv(
        output / "structure_setup_audit.csv", index=False
    )
    pd.DataFrame(shelf_rows).to_csv(output / "shelves.csv", index=False)
    pd.DataFrame(pivot_rows).to_csv(output / "pivots.csv", index=False)
    pd.DataFrame(merge_audit).to_csv(output / "merge_audit.csv", index=False)
    pd.DataFrame(simulator.trade_rows()).to_csv(output / "trades.csv", index=False)
    pd.DataFrame(simulator.equity).to_csv(output / "equity.csv", index=False)

    events = []
    for item in source_events:
        events.append(
            {
                "event_type": item.get("event", "STRUCTURE_STATE"),
                "event_time_ns": item.get("time_ns"),
                "observed_time_ns": item.get("time_ns"),
                "details": item,
            }
        )
    for setup in setups:
        events.append(
            {
                "scenario_id": setup.causal_event_id,
                "instrument_id": setup.symbol,
                "event_type": "SETUP_ARMED",
                "event_time_ns": setup.observed_time_ns,
                "observed_time_ns": setup.observed_time_ns,
                "previous_state": "STRUCTURAL_BREAK_ACCEPTED",
                "next_state": "FIRST_RETEST_ARMED",
                "reason_code": setup.family,
                "reference_price": str(setup.entry),
                "details": asdict(setup),
            }
        )
    for trade in simulator.trade_rows():
        events.append(
            {
                "scenario_id": trade["causal_event_id"],
                "instrument_id": trade["symbol"],
                "event_type": "POSITION_CLOSED",
                "event_time_ns": trade["exit_time_ns"],
                "observed_time_ns": trade["exit_time_ns"],
                "previous_state": "POSITION_OPEN",
                "next_state": "CLOSED",
                "reason_code": trade["outcome"],
                "reference_price": str(trade["exit"]),
                "details": trade,
            }
        )
    events.sort(
        key=lambda item: (
            int(item.get("observed_time_ns") or 0),
            str(item.get("instrument_id") or ""),
            str(item.get("event_type") or ""),
        )
    )
    (output / "scenario_events.jsonl").write_text(
        "".join(json.dumps(item, sort_keys=True, default=str) + "\n" for item in events),
        encoding="utf-8",
    )
    (output / "run.json").write_text(
        json.dumps(
            {
                "candidate": "candidate-easychart-v16-structural-shelf",
                "engine": "FAST_DIAGNOSTIC_NOT_AUTHORITATIVE",
                "config": vars(args),
                "source_status": {
                    "accepted_break_first_retest": "SOURCE_EXPLICIT",
                    "reaction_shelf_interval_overlap": "EXTERNAL_OPERATIONALIZATION_OF_MEANINGFUL_HORIZONTAL_STRUCTURE",
                    "separate_outside_open_and_close": "SOURCE_EXPLICIT_FOR_CHANNEL_AND_NAMED_CASE_INFERENCE_FOR_HORIZONTAL_SR",
                },
                "notes": [
                    "no confluence score",
                    "OB/FVG only choose entry geometry inside the same break leg",
                    "wave origin must predate the first break close",
                    "first active objective below 1R is not skipped",
                    "finer event data and NautilusTrader required for promotion",
                ],
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metrics, indent=2, sort_keys=True, allow_nan=False))
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--signal-minutes", type=int, default=5)
    parser.add_argument("--response-minutes", type=int, default=5)
    parser.add_argument("--structure-minutes", type=int, default=15)
    parser.add_argument("--dc-atr-period", type=int, default=14)
    parser.add_argument("--dc-atr-multiple", type=float, default=1.0)
    parser.add_argument("--starting-nav", type=float, default=100_000.0)
    parser.add_argument("--warmup-days", type=int, default=35)
    parser.add_argument(
        "--cost-profile",
        choices=("role", "taker", "stress"),
        default="role",
    )
    parser.add_argument("--default-funding-rate", type=float, default=0.0001)
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()

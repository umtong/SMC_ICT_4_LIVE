#!/usr/bin/env python3
"""Run EasyChart v17 common-core structural role-flip diagnostics.

This screen is a targeted semantic advance, not another parameter search.  It
changes two decisions that were materially different from the source:

* repeated reactions to one visible price area are one versioned causal
  structure, not one independent label per pivot pair;
* a three-candle wick gap whose middle body is not source-significant remains in
  the evidence census but cannot act as FVG entry geometry.

The traded family is still accepted break -> first retest.  The fixed account
contract is unchanged.  Results remain a cheap diagnostic and require
NautilusTrader/finer-data promotion before they are performance evidence.
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
from market_v17_structure import (
    CommonCoreAcceptedBreakConfig,
    CommonCoreAcceptedBreakEngine,
    build_common_core_structures,
)
from screen_v15 import merge_same_bar_options, option_root
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
    for index, current in enumerate(candles):
        pivot = detector.on_candle(current, index)
        if pivot is not None:
            pivots.append(pivot)
    versions = build_common_core_structures(
        symbol=symbol,
        candles=candles,
        pivots=pivots,
        timeframe_minutes=structure_minutes,
    )
    roots = {item.structure_id for item in versions}
    diagnostics = {
        **{f"dc_{key}": value for key, value in detector.diagnostics.items()},
        "pivots": len(pivots),
        "structure_roots": len(roots),
        "structure_versions": len(versions),
        "superseding_versions": sum(bool(item.supersedes_version_ids) for item in versions),
        "max_anchor_count": max((item.anchor_count for item in versions), default=0),
    }
    return pivots, versions, diagnostics


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
    pivots, structures, structure_diagnostics = build_structure(
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
    engine = CommonCoreAcceptedBreakEngine(
        symbol,
        structures,
        pivots,
        CommonCoreAcceptedBreakConfig(
            tick_size=CONTRACTS[symbol].tick_size,
            signal_timeframe_minutes=signal_minutes,
            valid_until_ns=valid_until_ns,
        ),
    )
    cursor = 0
    setups = []
    cancellations: list[tuple[int, str, str]] = []
    events = []
    for index, current in enumerate(signal_candles):
        batch = []
        while (
            cursor < len(footprints)
            and footprints[cursor].observed_time_ns <= current.ts_close_ns
        ):
            batch.append(footprints[cursor])
            cursor += 1
        if batch:
            engine.ingest_footprints(batch)
        update = engine.on_close(current, index)
        setups.extend(update.setups)
        cancellations.extend(
            (current.ts_open_ns, setup_id, "STRUCTURE_VERSION_SUPERSEDED")
            for setup_id in update.cancel_setup_ids
        )
        events.extend(update.events)
    diagnostics = {
        "structure": structure_diagnostics,
        "engine": dict(engine.diagnostics),
        "footprint_census": {
            "order_blocks": sum(item.__class__.__name__ == "SourceOrderBlock" for item in footprints),
            "fvgs_all_wick_gaps": sum(item.__class__.__name__ == "SourceFVG" for item in footprints),
            "fvgs_source_valid": sum(
                item.__class__.__name__ == "SourceFVG" and item.source_two_x_quality
                for item in footprints
            ),
        },
    }
    return (
        setups,
        cancellations,
        events,
        diagnostics,
        list(engine.audit_rows),
        structures,
        pivots,
    )


def _merge_aliases(raw_setups, merged_setups) -> dict[str, str]:
    """Map every pre-merge setup id to the actual pending intent id."""
    def key(item):
        return (
            item.symbol,
            int(item.side),
            int(item.observed_time_ns),
            option_root(item.family),
        )

    merged_by_key = {key(item): item.setup_id for item in merged_setups}
    return {
        item.setup_id: merged_by_key[key(item)]
        for item in raw_setups
        if key(item) in merged_by_key
    }


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
    cancellations: list[tuple[int, str, str]] = []
    source_events = []
    source_diagnostics = {}
    structure_audit = []
    structure_rows = []
    pivot_rows = []

    for symbol in SYMBOLS:
        one = load_range(symbol, build_start, end, args.cache.resolve())
        data[symbol] = one
        (
            setups,
            cancel,
            events,
            diagnostics,
            audits,
            structures,
            pivots,
        ) = generate_symbol_setups(
            symbol=symbol,
            one=one,
            signal_minutes=args.signal_minutes,
            response_minutes=args.response_minutes,
            structure_minutes=args.structure_minutes,
            dc_atr_period=args.dc_atr_period,
            dc_atr_multiple=args.dc_atr_multiple,
            valid_until_ns=end_exclusive_ns,
        )
        raw_setups.extend(
            setup for setup in setups if setup.observed_time_ns >= start_ns
        )
        cancellations.extend(item for item in cancel if item[0] >= start_ns)
        source_events.extend(events)
        source_diagnostics[symbol] = diagnostics
        structure_audit.extend(audits)
        structure_rows.extend(
            {
                "symbol": symbol,
                "structure_id": item.structure_id,
                "version_id": item.version_id,
                "version": item.version,
                "supersedes_version_ids": "|".join(item.supersedes_version_ids),
                "side": int(item.side),
                "observed_time_ns": item.observed_time_ns,
                "timeframe_minutes": item.timeframe_minutes,
                "zone_low": item.zone_low,
                "zone_high": item.zone_high,
                "anchor_count": item.anchor_count,
                "anchor_event_times_ns": "|".join(
                    str(anchor.event_time_ns) for anchor in item.anchors
                ),
                "anchor_observed_times_ns": "|".join(
                    str(anchor.observed_time_ns) for anchor in item.anchors
                ),
            }
            for item in structures
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
    aliases = _merge_aliases(raw_setups, setups)
    routed_cancellations = []
    for observed_ns, setup_id, reason in cancellations:
        actual_id = aliases.get(setup_id)
        if actual_id is not None:
            routed_cancellations.append((observed_ns, actual_id, reason))
    routed_cancellations = sorted(set(routed_cancellations))

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
    setup_cursor = 0
    cancel_cursor = 0
    for close_ns in sorted(grouped):
        batch = grouped[close_ns]
        earliest_open = min(bar.ts_open_ns for bar in batch.values())
        while (
            cancel_cursor < len(routed_cancellations)
            and routed_cancellations[cancel_cursor][0] <= earliest_open
        ):
            _, setup_id, reason = routed_cancellations[cancel_cursor]
            simulator.cancel_pending([setup_id], reason=reason)
            cancel_cursor += 1
        while (
            setup_cursor < len(setups)
            and setups[setup_cursor].observed_time_ns < earliest_open
        ):
            simulator.add_setups([setups[setup_cursor]])
            setup_cursor += 1
        simulator.on_timestamp(batch)

    days = (end - start).days + 1
    metrics = simulator.metrics(days)
    metrics.update(
        {
            "candidate": "candidate-easychart-v17-common-core-structure",
            "evaluation_start": str(start),
            "evaluation_end": str(end),
            "signal_minutes": args.signal_minutes,
            "response_minutes": args.response_minutes,
            "structure_minutes": args.structure_minutes,
            "dc_atr_period": args.dc_atr_period,
            "dc_atr_multiple": args.dc_atr_multiple,
            "raw_setups_generated": len(raw_setups),
            "setups_generated": len(setups),
            "raw_cancellation_events": len(cancellations),
            "routed_cancellation_events": len(routed_cancellations),
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
    pd.DataFrame(structure_rows).to_csv(
        output / "structure_versions.csv", index=False
    )
    pd.DataFrame(pivot_rows).to_csv(output / "pivots.csv", index=False)
    pd.DataFrame(merge_audit).to_csv(output / "merge_audit.csv", index=False)
    pd.DataFrame(
        [
            {"observed_time_ns": ts, "setup_id": setup_id, "reason": reason}
            for ts, setup_id, reason in routed_cancellations
        ]
    ).to_csv(output / "cancellations.csv", index=False)
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
                "previous_state": "COMMON_CORE_BREAK_ACCEPTED",
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
                "candidate": "candidate-easychart-v17-common-core-structure",
                "engine": "FAST_DIAGNOSTIC_NOT_AUTHORITATIVE",
                "config": vars(args),
                "source_status": {
                    "accepted_break_first_retest": "SOURCE_EXPLICIT",
                    "strict_fvg_middle_body_requirement": "SOURCE_EXPLICIT",
                    "common_core_reaction_intervals": "EXTERNAL_OPERATIONALIZATION_OF_MEANINGFUL_STRUCTURE",
                    "most_recent_compatible_structure_identity": "NAMED_HUMAN_DECISION_OPERATIONALIZATION",
                    "version_supersession": "CAUSAL_IMPLEMENTATION_REQUIREMENT",
                },
                "notes": [
                    "no confluence score or risk multiplier",
                    "one reaction cannot create independent votes for every old label",
                    "loose wick gaps remain census observations but cannot route FVG entry",
                    "wave origin must predate first break close",
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

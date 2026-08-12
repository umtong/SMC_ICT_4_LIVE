#!/usr/bin/env python3
"""Inspect source OB/FVG evidence inside each routed liquidity episode.

The audit answers a question that an aggregate backtest cannot: for every W/M
Trap accepted by the state router, which source-defined order blocks and fair-
value gaps were actually known, fresh and geometrically relevant before the
first executable retest?

It deliberately does not make a new strategy.  It rebuilds the candidate event,
checks that the recorded setup matches, enumerates two- and three-candle OBs and
strict/non-strict FVGs on 1m/5m/15m/60m, and records their temporal role:
pre-existing zone at entry, formed during the W/M episode, formed on reclaim,
or formed after confirmation but before the first retest.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import date, timedelta
import json
from pathlib import Path
import re
from typing import Iterable, Mapping

import pandas as pd

from data import load_range, resample
from domain_v3 import Candle, Side
from instrument_contracts import CONTRACTS
from market_v12 import EasyChartWMTrapEngine
from market_v7 import SessionTrapConfig
import screen_v7 as session_base
import screen_v7_fixed  # noqa: F401
from source_footprints import (
    SourceFVG,
    SourceOrderBlock,
    detect_source_footprints,
)
from trade_semantic_audit import Bar, audit_setup_lifecycle


TIMEFRAMES = (1, 5, 15, 60)
WM_PATTERN = re.compile(
    r"\|WM_OUTSIDE=(?P<outside>[^|]+)"
    r"\|WM_REBOUND=(?P<rebound>[^|]+)"
    r"\|WM_SECOND_LEG=(?P<second>[^|]+)"
    r"\|WM_EXTREME_TIME=(?P<extreme>[^|]+)"
    r"\|WM_RECLAIM=(?P<reclaim>[^|]+)"
)


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


def to_bars(symbol: str, frame: pd.DataFrame) -> list[Bar]:
    return [
        Bar(
            symbol=symbol,
            open_time_ns=int(row.open_time_dt.value),
            close_time_ns=int(row.close_time_dt.value),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
        )
        for row in frame.itertuples(index=False)
    ]


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def parse_optional_ns(value: str | None) -> int | None:
    if value is None or value == "None":
        return None
    return int(value)


def parse_wm_times(context_bias: str) -> dict[str, int | None]:
    match = WM_PATTERN.search(context_bias)
    if match is None:
        return {
            "outside_time_ns": None,
            "rebound_time_ns": None,
            "second_leg_time_ns": None,
            "extreme_time_ns": None,
            "reclaim_time_ns": None,
        }
    return {
        "outside_time_ns": parse_optional_ns(match.group("outside")),
        "rebound_time_ns": parse_optional_ns(match.group("rebound")),
        "second_leg_time_ns": parse_optional_ns(match.group("second")),
        "extreme_time_ns": parse_optional_ns(match.group("extreme")),
        "reclaim_time_ns": parse_optional_ns(match.group("reclaim")),
    }


def _after(frame: pd.DataFrame, observed_time_ns: int, before_time_ns: int) -> pd.DataFrame:
    observed = pd.Timestamp(observed_time_ns, unit="ns", tz="UTC")
    before = pd.Timestamp(before_time_ns, unit="ns", tz="UTC")
    return frame[
        (frame["open_time_dt"] > observed)
        & (frame["open_time_dt"] < before)
    ]


def ob_invalidated(
    footprint: SourceOrderBlock,
    one_minute: pd.DataFrame,
    before_time_ns: int,
) -> bool:
    selected = _after(one_minute, footprint.observed_time_ns, before_time_ns)
    if selected.empty:
        return False
    if footprint.side is Side.LONG:
        return bool((selected["low"] <= footprint.invalidation).any())
    return bool((selected["high"] >= footprint.invalidation).any())


def fvg_touched(
    footprint: SourceFVG,
    one_minute: pd.DataFrame,
    before_time_ns: int,
) -> bool:
    selected = _after(one_minute, footprint.observed_time_ns, before_time_ns)
    if selected.empty:
        return False
    return bool(
        (
            (selected["low"] <= footprint.zone_high)
            & (selected["high"] >= footprint.zone_low)
        ).any(),
    )


def overlaps(level: float, low: float, high: float) -> bool:
    return low <= level <= high


def footprint_role(
    *,
    observed_time_ns: int,
    outside_time_ns: int | None,
    reclaim_time_ns: int,
    first_retest_open_ns: int | None,
) -> str:
    if observed_time_ns == reclaim_time_ns:
        return "FORMED_ON_RECLAIM_CLOSE"
    if outside_time_ns is not None and outside_time_ns <= observed_time_ns < reclaim_time_ns:
        return "FORMED_DURING_WM_EPISODE"
    if observed_time_ns < reclaim_time_ns:
        return "PREEXISTING_BEFORE_RECLAIM"
    if first_retest_open_ns is not None and observed_time_ns < first_retest_open_ns:
        return "FORMED_AFTER_CONFIRMATION_BEFORE_FIRST_RETEST"
    return "AFTER_FIRST_RETEST_OR_UNRELATED"


def _footprint_row(
    footprint: SourceOrderBlock | SourceFVG,
    *,
    kind: str,
    entry: float,
    event_side: Side,
    one_minute: pd.DataFrame,
    outside_time_ns: int | None,
    reclaim_time_ns: int,
    first_retest_open_ns: int | None,
) -> dict[str, object]:
    before = reclaim_time_ns + 1
    row = asdict(footprint)
    row.update(
        {
            "kind": kind,
            "same_direction_as_event": footprint.side is event_side,
            "entry_inside_zone": overlaps(entry, footprint.zone_low, footprint.zone_high),
            "temporal_role": footprint_role(
                observed_time_ns=footprint.observed_time_ns,
                outside_time_ns=outside_time_ns,
                reclaim_time_ns=reclaim_time_ns,
                first_retest_open_ns=first_retest_open_ns,
            ),
        },
    )
    if isinstance(footprint, SourceOrderBlock):
        row["fresh_and_active_at_reclaim"] = not ob_invalidated(
            footprint,
            one_minute,
            before,
        )
        row["freshness_definition"] = "FORMATION_EXTREME_NOT_BREACHED_BEFORE_RECLAIM"
    else:
        row["fresh_and_active_at_reclaim"] = not fvg_touched(
            footprint,
            one_minute,
            before,
        )
        row["freshness_definition"] = "ZONE_NOT_PREVIOUSLY_TOUCHED_BEFORE_RECLAIM"
        row["wave_lifecycle_status"] = "SOURCE_WAVE_BOUNDARY_NOT_YET_MECHANIZED"
    return row


def rebuild_wm_setups(
    *,
    symbol: str,
    one_minute: pd.DataFrame,
    build_start: date,
    end: date,
    families: set[str],
    signal_minutes: int,
    accepted_break_widths: float,
    enable_immediate_fakeout: bool,
    enable_delayed_trap: bool,
) -> tuple[dict[str, object], dict[str, int]]:
    frame = resample(one_minute, signal_minutes)
    ranges = session_base.build_ranges(
        symbol,
        one_minute,
        build_start,
        end,
        families,
    )
    engine = EasyChartWMTrapEngine(
        symbol,
        ranges,
        SessionTrapConfig(
            enable_immediate_fakeout=enable_immediate_fakeout,
            enable_delayed_trap=enable_delayed_trap,
            accepted_break_range_widths=accepted_break_widths,
            tick_size=CONTRACTS[symbol].tick_size,
            source_timeframe_minutes=signal_minutes,
        ),
    )
    setups = []
    for index, candle in enumerate(to_candles(frame)):
        setups.extend(engine.on_close(candle, index))
    return {setup.setup_id: setup for setup in setups}, dict(engine.diagnostics)


def run(args: argparse.Namespace) -> dict[str, object]:
    run_dir = args.run_dir.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    run_document = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    config = run_document["config"]
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    build_start = start - timedelta(days=int(config.get("warmup_days", 5)))
    families = session_base.parse_families(str(config["families"]))
    router_rows = read_jsonl(run_dir / "target_router_audit.jsonl")
    symbols = sorted({str(row["symbol"]) for row in router_rows})

    one_by_symbol = {
        symbol: load_range(symbol, build_start, end, args.cache.resolve())
        for symbol in symbols
    }
    bars_by_symbol = {
        symbol: to_bars(symbol, frame)
        for symbol, frame in one_by_symbol.items()
    }
    rebuilt = {}
    rebuild_diagnostics = {}
    footprints_by_symbol: dict[str, list[SourceOrderBlock | SourceFVG]] = {}
    for symbol in symbols:
        setup_map, diagnostics = rebuild_wm_setups(
            symbol=symbol,
            one_minute=one_by_symbol[symbol],
            build_start=build_start,
            end=end,
            families=families,
            signal_minutes=int(config["signal_minutes"]),
            accepted_break_widths=float(config["accepted_break_widths"]),
            enable_immediate_fakeout=not bool(config["disable_fakeout"]),
            enable_delayed_trap=not bool(config["disable_trap"]),
        )
        rebuilt.update(setup_map)
        rebuild_diagnostics[symbol] = diagnostics
        symbol_footprints: list[SourceOrderBlock | SourceFVG] = []
        for minutes in TIMEFRAMES:
            frame = one_by_symbol[symbol] if minutes == 1 else resample(one_by_symbol[symbol], minutes)
            update = detect_source_footprints(symbol, to_candles(frame), minutes)
            symbol_footprints.extend(update.order_blocks)
            symbol_footprints.extend(update.fvgs)
        footprints_by_symbol[symbol] = symbol_footprints

    event_rows: list[dict[str, object]] = []
    all_relevant_footprints: list[dict[str, object]] = []
    for router in router_rows:
        setup_id = str(router["setup_id"])
        setup = rebuilt.get(setup_id)
        if setup is None:
            event_rows.append(
                {
                    "setup_id": setup_id,
                    "symbol": router["symbol"],
                    "generation_match": False,
                    "failure": "TARGET_ROUTER_SETUP_NOT_REBUILT",
                },
            )
            continue
        setup_dict = asdict(setup)
        side = setup.side
        lifecycle = audit_setup_lifecycle(
            setup_dict,
            bars_by_symbol[setup.symbol],
        )
        first_retest = lifecycle.event_open_time_ns
        wm_times = parse_wm_times(setup.context_bias)
        reclaim = int(setup.observed_time_ns)
        outside = wm_times["outside_time_ns"]
        relevant = []
        for footprint in footprints_by_symbol[setup.symbol]:
            role = footprint_role(
                observed_time_ns=footprint.observed_time_ns,
                outside_time_ns=outside,
                reclaim_time_ns=reclaim,
                first_retest_open_ns=first_retest,
            )
            if role == "AFTER_FIRST_RETEST_OR_UNRELATED":
                continue
            row = _footprint_row(
                footprint,
                kind="ORDER_BLOCK" if isinstance(footprint, SourceOrderBlock) else "FVG",
                entry=float(setup.entry),
                event_side=side,
                one_minute=one_by_symbol[setup.symbol],
                outside_time_ns=outside,
                reclaim_time_ns=reclaim,
                first_retest_open_ns=first_retest,
            )
            row["setup_id"] = setup_id
            row["event_symbol"] = setup.symbol
            relevant.append(row)
            all_relevant_footprints.append(row)

        aligned_entry = [
            item for item in relevant
            if item["same_direction_as_event"]
            and item["entry_inside_zone"]
            and item["fresh_and_active_at_reclaim"]
            and item["temporal_role"] in {
                "PREEXISTING_BEFORE_RECLAIM",
                "FORMED_DURING_WM_EPISODE",
                "FORMED_ON_RECLAIM_CLOSE",
                "FORMED_AFTER_CONFIRMATION_BEFORE_FIRST_RETEST",
            }
        ]
        strict_fvg = [
            item for item in aligned_entry
            if item["kind"] == "FVG" and item.get("source_two_x_quality")
        ]
        order_blocks = [item for item in aligned_entry if item["kind"] == "ORDER_BLOCK"]
        timeframes = sorted({int(item["timeframe_minutes"]) for item in aligned_entry})
        generation_match = (
            int(router["observed_time_ns"]) == int(setup.observed_time_ns)
            and abs(float(router["entry"]) - float(setup.entry)) < 1e-12
            and abs(float(router["stop"]) - float(setup.stop)) < 1e-12
            and abs(float(router["far_target"]) - float(setup.initial_target)) < 1e-12
        )
        event_rows.append(
            {
                "setup_id": setup_id,
                "symbol": setup.symbol,
                "family": setup.family,
                "side": int(side),
                "generation_match": generation_match,
                "router_disposition": router["disposition"],
                "observed_time_ns": int(setup.observed_time_ns),
                "entry": float(setup.entry),
                "stop": float(setup.stop),
                "far_target": float(setup.initial_target),
                **wm_times,
                "first_decisive_event": lifecycle.event,
                "first_decisive_classification": lifecycle.classification,
                "first_decisive_open_time_ns": lifecycle.event_open_time_ns,
                "aligned_fresh_entry_footprints": len(aligned_entry),
                "aligned_order_blocks": len(order_blocks),
                "aligned_strict_fvgs": len(strict_fvg),
                "aligned_timeframes": timeframes,
                "minimum_two_source_observations": (
                    1 + len(aligned_entry) >= 2
                ),
                "footprint_ids": [str(item["footprint_id"]) for item in aligned_entry],
            },
        )

    pd.DataFrame(event_rows).to_csv(output / "episode_footprint_audit.csv", index=False)
    pd.DataFrame(all_relevant_footprints).to_csv(output / "relevant_footprints.csv", index=False)
    (output / "episode_footprint_casebook.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True, default=str) + "\n" for row in event_rows),
        encoding="utf-8",
    )
    (output / "relevant_footprints.jsonl").write_text(
        "".join(
            json.dumps(row, sort_keys=True, default=str) + "\n"
            for row in all_relevant_footprints
        ),
        encoding="utf-8",
    )
    summary = {
        "events": len(event_rows),
        "generation_mismatches": sum(not bool(row.get("generation_match")) for row in event_rows),
        "events_with_aligned_order_block": sum(int(row.get("aligned_order_blocks", 0)) > 0 for row in event_rows),
        "events_with_aligned_strict_fvg": sum(int(row.get("aligned_strict_fvgs", 0)) > 0 for row in event_rows),
        "events_with_any_aligned_footprint": sum(int(row.get("aligned_fresh_entry_footprints", 0)) > 0 for row in event_rows),
        "events_meeting_minimum_two_observations": sum(bool(row.get("minimum_two_source_observations")) for row in event_rows),
        "router_dispositions": dict(Counter(str(row.get("router_disposition")) for row in event_rows)),
        "first_decisive_events": dict(Counter(str(row.get("first_decisive_event")) for row in event_rows)),
        "rebuild_diagnostics": rebuild_diagnostics,
        "limitations": [
            "FVG first-retracement wave lifecycle remains explicitly unresolved.",
            "Trendline and channel context are not yet enumerated in this footprint audit.",
            "A footprint count is descriptive evidence coverage, not a score or risk multiplier."
        ],
    }
    (output / "footprint_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()

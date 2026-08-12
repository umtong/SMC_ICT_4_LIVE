#!/usr/bin/env python3
"""Run candidate-easychart v15 role-routed option diagnostics.

The screen deliberately compares complete auction options rather than counting
patterns:

* failed-break reversal: immediate fakeout, source-shaped W/M trap, or a
  sponsored opposite footprint while price is still outside;
* accepted-break continuation: outside close plus a distinct outside
  open/close, followed by the first boundary retest.

Cross-market observations route coordinated rejection vs repricing but do not
act as a generic confidence threshold.  The first still-active opposing
directional-change pivot is the target; a reversal may fall back to the opposite
side of its declared range, whereas a continuation is rejected if no actual
external objective is already observable.

This is a cheap diagnostic only.  Positive results require NautilusTrader
promotion with finer event data before they become performance evidence.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import date, timedelta
import json
from pathlib import Path
import sys
from typing import Iterable

import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from data import load_range, resample
from domain_v3 import Candle, Side
from instrument_contracts import CONTRACTS
from market_v5 import DirectionalChangePivotDetector
from market_v13 import pivot_key, select_first_directional_objective
from market_v15 import (
    AuctionState,
    BoundaryEngineConfig,
    PeerBoundaryObservation,
    RoleRoutedBoundaryEngine,
    classify_auction_state,
)
from simulator_v15 import CancelableExpiringSimulator
from simulator_v7 import InstrumentSpec, MinuteBar
from source_footprints import detect_fvgs, detect_order_blocks
import screen_v7 as session_base
import screen_v7_fixed  # noqa: F401 -- installs unit-stable range slicing

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


def range_key(liquidity_range) -> tuple[str, str, int, int]:
    return (
        liquidity_range.reference_family,
        liquidity_range.trade_window,
        int(liquidity_range.trade_start_ns),
        int(liquidity_range.trade_end_ns),
    )


def observation_for(
    *,
    symbol: str,
    side: Side,
    liquidity_range,
    signal_frame: pd.DataFrame,
    observed_time_ns: int,
) -> PeerBoundaryObservation | None:
    start = pd.Timestamp(int(liquidity_range.trade_start_ns), unit="ns", tz="UTC")
    observed = pd.Timestamp(int(observed_time_ns), unit="ns", tz="UTC")
    selected = signal_frame[
        (signal_frame["open_time_dt"] >= start)
        & (signal_frame["close_time_dt"] <= observed)
    ]
    if selected.empty:
        return None
    return PeerBoundaryObservation(
        symbol=symbol,
        side=side,
        range_low=float(liquidity_range.low),
        range_high=float(liquidity_range.high),
        excursion_low=float(selected["low"].min()),
        excursion_high=float(selected["high"].max()),
        close=float(selected["close"].iloc[-1]),
    )


def _raid_side(setup) -> Side:
    # Reversal direction names the swept side directly: long = lower sweep,
    # short = upper sweep.  Continuation direction is the opposite of the
    # broken side: short continuation follows a lower break.
    if "ROLE_ACCEPTED_BREAK" in setup.family:
        return Side.LONG if setup.side is Side.SHORT else Side.SHORT
    return setup.side



def option_root(family: str) -> str:
    if "ROLE_FAILED_BREAK" in family:
        return "FAILED_BREAK_REVERSAL"
    if "ROLE_ACCEPTED_BREAK" in family:
        return "ACCEPTED_BREAK_CONTINUATION"
    return family


def merge_same_bar_options(setups):
    """Merge overlapping structure labels for one causal interaction.

    Several session/range definitions can describe the same sweep.  They are
    not independent trades and must not inflate opportunity count.  The merged
    option uses the first reachable entry surface, the full shared
    invalidation, and a directional far cap only for subsequent first-objective
    routing.  No confluence score is created.
    """
    grouped: dict[tuple[object, ...], list[object]] = {}
    for setup in setups:
        key = (
            setup.symbol,
            int(setup.side),
            int(setup.observed_time_ns),
            option_root(setup.family),
        )
        grouped.setdefault(key, []).append(setup)
    output = []
    diagnostics: dict[str, int] = {}
    audit_rows: list[dict[str, object]] = []

    def count(key: str, amount: int = 1) -> None:
        diagnostics[key] = diagnostics.get(key, 0) + amount

    for key, items in sorted(grouped.items(), key=lambda value: value[0]):
        items.sort(key=lambda item: (item.entry, item.source_pool_id, item.setup_id))
        if len(items) == 1:
            output.append(items[0])
            continue
        side = items[0].side
        if side is Side.LONG:
            entry_source = max(items, key=lambda item: (item.entry, item.source_pool_id))
            entry = max(item.entry for item in items)
            stop = min(item.stop for item in items)
            far_cap = max(item.initial_target for item in items)
        else:
            entry_source = min(items, key=lambda item: (item.entry, item.source_pool_id))
            entry = min(item.entry for item in items)
            stop = max(item.stop for item in items)
            far_cap = min(item.initial_target for item in items)
        valid_until = min(int(getattr(item, "valid_until_ns")) for item in items)
        pools = tuple(sorted({item.source_pool_id for item in items}))
        base = entry_source
        merged = replace(
            base,
            family=f"{option_root(base.family)}_MERGED_{len(items)}_STRUCTURES",
            causal_event_id=(
                f"MERGED_ROLE_OPTION:{base.symbol}:{option_root(base.family)}:"
                f"{base.observed_time_ns}:{base.side.name}:{'|'.join(pools)}"
            ),
            entry=float(entry),
            stop=float(stop),
            initial_target=float(far_cap),
            fixed_target_id=f"MERGED_DIRECTIONAL_CAP:{'|'.join(pools)}",
            source_pool_id=entry_source.source_pool_id,
            zone_low=float(entry_source.zone_low),
            zone_high=float(entry_source.zone_high),
            formation_extreme=float(stop),
            context_bias=(
                f"{base.context_bias}|MERGED_STRUCTURE_POOLS={'|'.join(pools)}"
                f"|MERGE_POLICY=FIRST_REACHABLE_ENTRY_FULL_SHARED_INVALIDATION"
            ),
            valid_until_ns=valid_until,
        )
        if merged.executable(
            merged.initial_target,
            target_id=merged.fixed_target_id,
            min_gross_rr=1.0,
        ) is None:
            count("merged_option_far_cap_rr_lt_1")
            audit_rows.append(
                {
                    "key": repr(key),
                    "setup_ids": ",".join(item.setup_id for item in items),
                    "source_pool_ids": ",".join(pools),
                    "disposition": "REJECT_MERGED_FAR_CAP_RR_LT_1",
                    "entry": entry,
                    "stop": stop,
                    "far_cap": far_cap,
                }
            )
            continue
        output.append(merged)
        count("overlapping_options_merged")
        count("duplicate_setup_intents_removed", len(items) - 1)
        audit_rows.append(
            {
                "key": repr(key),
                "setup_ids": ",".join(item.setup_id for item in items),
                "source_pool_ids": ",".join(pools),
                "disposition": "MERGED_ONE_CAUSAL_OPTION",
                "entry": entry,
                "stop": stop,
                "far_cap": far_cap,
            }
        )
    output.sort(key=lambda item: (item.observed_time_ns, item.symbol, item.setup_id))
    return output, diagnostics, audit_rows

def route_auction_states(
    setups,
    *,
    ranges_by_id,
    ranges_by_key,
    signal_frames,
):
    routed = []
    diagnostics: dict[str, int] = {}
    audit_rows: list[dict[str, object]] = []

    def count(key: str, amount: int = 1) -> None:
        diagnostics[key] = diagnostics.get(key, 0) + amount

    for setup in setups:
        own = ranges_by_id.get(setup.symbol, {}).get(setup.source_pool_id)
        audit: dict[str, object] = {
            "setup_id": setup.setup_id,
            "symbol": setup.symbol,
            "family": setup.family,
            "observed_time_ns": int(setup.observed_time_ns),
            "disposition": None,
        }
        if own is None:
            count("missing_candidate_range")
            audit["disposition"] = "REJECT_MISSING_CANDIDATE_RANGE"
            audit_rows.append(audit)
            continue
        side = _raid_side(setup)
        candidate = observation_for(
            symbol=setup.symbol,
            side=side,
            liquidity_range=own,
            signal_frame=signal_frames[setup.symbol],
            observed_time_ns=setup.observed_time_ns,
        )
        if candidate is None:
            count("missing_candidate_observation")
            audit["disposition"] = "REJECT_MISSING_CANDIDATE_OBSERVATION"
            audit_rows.append(audit)
            continue
        peers = []
        key = range_key(own)
        for symbol in SYMBOLS:
            if symbol == setup.symbol:
                continue
            peer_range = ranges_by_key.get(symbol, {}).get(key)
            if peer_range is None:
                continue
            item = observation_for(
                symbol=symbol,
                side=side,
                liquidity_range=peer_range,
                signal_frame=signal_frames[symbol],
                observed_time_ns=setup.observed_time_ns,
            )
            if item is not None:
                peers.append(item)
        decision = classify_auction_state(candidate=candidate, peers=peers)
        count(f"decision_{decision.state.value.lower()}")
        audit.update(
            {
                "raid_side": side.name,
                "auction_state": decision.state.value,
                "candidate_penetration": decision.candidate_penetration,
                "swept_peers": ",".join(decision.swept_peers),
                "reclaimed_peers": ",".join(decision.reclaimed_peers),
                "outside_peers": ",".join(decision.outside_peers),
                "non_swept_peers": ",".join(decision.non_swept_peers),
            }
        )

        reversal = "ROLE_FAILED_BREAK" in setup.family
        continuation = "ROLE_ACCEPTED_BREAK" in setup.family
        if reversal and decision.state is AuctionState.COORDINATED_REPRICING:
            count("reversal_rejected_coordinated_repricing")
            audit["disposition"] = "REJECT_REVERSAL_COORDINATED_REPRICING"
            audit_rows.append(audit)
            continue
        if continuation and decision.state is AuctionState.COORDINATED_REJECTION:
            count("continuation_rejected_coordinated_rejection")
            audit["disposition"] = "REJECT_CONTINUATION_COORDINATED_REJECTION"
            audit_rows.append(audit)
            continue

        suffix = decision.state.value
        context = (
            f"{setup.context_bias}|XSTATE={suffix}"
            f"|PEN={decision.candidate_penetration:.8f}"
            f"|SWEPT={','.join(decision.swept_peers)}"
            f"|RECLAIMED={','.join(decision.reclaimed_peers)}"
            f"|OUTSIDE={','.join(decision.outside_peers)}"
            f"|NON_SWEPT={','.join(decision.non_swept_peers)}"
        )
        candidate_setup = replace(
            setup,
            family=f"{setup.family}_{suffix}",
            causal_event_id=f"{setup.causal_event_id}:XSTATE={suffix}",
            context_bias=context,
        )
        routed.append(candidate_setup)
        count(f"accepted_{decision.state.value.lower()}")
        audit["disposition"] = "ACCEPT_LOCAL_OPTION_WITH_AUCTION_ROUTE"
        audit_rows.append(audit)

    routed.sort(key=lambda item: (item.observed_time_ns, item.symbol, item.setup_id))
    return routed, diagnostics, audit_rows


def target_frame(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    return frame if minutes == 1 else resample(frame, minutes)


def target_pivots(frame: pd.DataFrame, minutes: int, atr_multiple: float):
    working = target_frame(frame, minutes)
    detector = DirectionalChangePivotDetector(
        timeframe_minutes=minutes,
        atr_period=14,
        atr_multiple=atr_multiple,
    )
    output = []
    for index, candle in enumerate(to_candles(working)):
        pivot = detector.on_candle(candle, index)
        if pivot is not None:
            output.append(pivot)
    return output


def setup_bar(frame: pd.DataFrame, observed_time_ns: int):
    observed = pd.Timestamp(int(observed_time_ns), unit="ns", tz="UTC")
    selected = frame[frame["close_time_dt"] == observed]
    return None if selected.empty else selected.iloc[-1]


def historically_consumed_keys(*, setup, pivots, frame: pd.DataFrame):
    setup_observed = pd.Timestamp(int(setup.observed_time_ns), unit="ns", tz="UTC")
    consumed = set()
    for pivot in pivots:
        observed = pd.Timestamp(int(pivot.observed_time_ns), unit="ns", tz="UTC")
        selected = frame[
            (frame["close_time_dt"] > observed)
            & (frame["close_time_dt"] < setup_observed)
        ]
        if selected.empty:
            continue
        if pivot.side == "HIGH":
            hit = bool((selected["high"] >= pivot.level).any())
        else:
            hit = bool((selected["low"] <= pivot.level).any())
        if hit:
            consumed.add(pivot_key(pivot))
    return consumed


def pivot_rows(items) -> list[dict[str, object]]:
    return [
        {
            "side": item.side,
            "level": float(item.level),
            "event_time_ns": int(item.event_time_ns),
            "observed_time_ns": int(item.observed_time_ns),
        }
        for item in items
    ]


def route_targets(
    setups,
    *,
    one_minute_frames,
    target_minutes: int,
    target_dc_atr: float,
):
    frames = {
        symbol: target_frame(frame, target_minutes)
        for symbol, frame in one_minute_frames.items()
    }
    pivots_by_symbol = {
        symbol: target_pivots(frame, target_minutes, target_dc_atr)
        for symbol, frame in one_minute_frames.items()
    }
    output = []
    diagnostics: dict[str, int] = {}
    audits: list[dict[str, object]] = []

    def count(key: str, amount: int = 1) -> None:
        diagnostics[key] = diagnostics.get(key, 0) + amount

    for setup in setups:
        row = setup_bar(frames[setup.symbol], setup.observed_time_ns)
        audit: dict[str, object] = {
            "setup_id": setup.setup_id,
            "symbol": setup.symbol,
            "family": setup.family,
            "observed_time_ns": int(setup.observed_time_ns),
            "entry": float(setup.entry),
            "stop": float(setup.stop),
            "declared_cap": float(setup.initial_target),
        }
        if row is None:
            audit["disposition"] = "REJECT_MISSING_SETUP_BAR"
            audits.append(audit)
            count("missing_setup_bar")
            continue
        eligible = [
            pivot
            for pivot in pivots_by_symbol[setup.symbol]
            if pivot.observed_time_ns < setup.observed_time_ns
        ]
        if setup.side is Side.LONG:
            geometric = [
                pivot
                for pivot in eligible
                if pivot.side == "HIGH"
                and setup.entry < pivot.level <= setup.initial_target
            ]
        else:
            geometric = [
                pivot
                for pivot in eligible
                if pivot.side == "LOW"
                and setup.initial_target <= pivot.level < setup.entry
            ]
        historical = historically_consumed_keys(
            setup=setup,
            pivots=geometric,
            frame=frames[setup.symbol],
        )
        decision = select_first_directional_objective(
            setup=setup,
            pivots=eligible,
            setup_bar_high=float(row.high),
            setup_bar_low=float(row.low),
            timeframe_minutes=target_minutes,
            consumed_pivot_keys=historical,
        )
        audit.update(
            {
                "candidate_pivots": pivot_rows(decision.candidates),
                "excluded_consumed": pivot_rows(decision.excluded_consumed),
                "chosen_pivot": (
                    None
                    if decision.pivot is None
                    else {
                        "side": decision.pivot.side,
                        "level": float(decision.pivot.level),
                        "event_time_ns": int(decision.pivot.event_time_ns),
                        "observed_time_ns": int(decision.pivot.observed_time_ns),
                    }
                ),
            }
        )
        continuation = "ROLE_ACCEPTED_BREAK" in setup.family
        if decision.reason == "NO_ACTIVE_INTERNAL_OBJECTIVE_USE_FAR_CAP":
            if continuation:
                audit["disposition"] = "REJECT_CONTINUATION_NO_ACTIVE_OBJECTIVE"
                audits.append(audit)
                count("continuation_no_active_objective")
                continue
            audit["disposition"] = "USE_DECLARED_OPPOSITE_BOUNDARY"
            audits.append(audit)
            output.append(setup)
            count("reversal_far_boundary_fallback")
            continue
        if decision.reason == "FIRST_ACTIVE_OBJECTIVE_RR_LT_1":
            audit["disposition"] = "REJECT_FIRST_ACTIVE_OBJECTIVE_RR_LT_1"
            audits.append(audit)
            count("first_active_objective_rr_lt_1")
            continue
        if decision.reason != "FIRST_ACTIVE_OBJECTIVE_SELECTED":
            raise RuntimeError(decision.reason)
        assert decision.setup is not None
        audit["disposition"] = "SELECT_FIRST_ACTIVE_OBJECTIVE"
        audit["selected_target"] = float(decision.setup.initial_target)
        audits.append(audit)
        output.append(decision.setup)
        count("first_active_objective_selected")

    output.sort(key=lambda item: (item.observed_time_ns, item.symbol, item.setup_id))
    return output, diagnostics, audits


def generate_symbol_options(
    *,
    symbol: str,
    one: pd.DataFrame,
    ranges,
    signal_minutes: int,
    response_minutes: int,
    config: BoundaryEngineConfig,
):
    signal = resample(one, signal_minutes)
    response = resample(one, response_minutes)
    signal_candles = to_candles(signal)
    response_candles = to_candles(response)
    footprints = [
        *detect_order_blocks(symbol, response_candles, response_minutes),
        *detect_fvgs(symbol, response_candles, response_minutes),
    ]
    footprints.sort(key=lambda item: (item.observed_time_ns, item.footprint_id))
    cursor = 0
    engine = RoleRoutedBoundaryEngine(symbol, ranges, config)
    setups = []
    cancellations: list[tuple[int, str, str]] = []
    events: list[dict[str, object]] = []
    for index, candle in enumerate(signal_candles):
        batch = []
        while cursor < len(footprints) and footprints[cursor].observed_time_ns <= candle.ts_close_ns:
            batch.append(footprints[cursor])
            cursor += 1
        if batch:
            engine.ingest_footprints(batch)
        update = engine.on_close(candle, index)
        setups.extend(update.setups)
        cancellations.extend(
            (candle.ts_close_ns, setup_id, "COMPETING_OPTION_OR_RANGE_EXPIRY")
            for setup_id in update.cancel_setup_ids
        )
        events.extend(update.events)
    return signal, setups, cancellations, events, dict(engine.diagnostics)


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
    families = session_base.parse_families(args.families)
    costs = session_base.cost_profile(args.cost_profile)

    data = {}
    signal_frames = {}
    ranges_by_id = {}
    ranges_by_key = {}
    raw_setups = []
    cancellations: list[tuple[int, str, str]] = []
    source_events: list[dict[str, object]] = []
    source_diagnostics = {}

    for symbol in SYMBOLS:
        one = load_range(symbol, build_start, end, args.cache.resolve())
        data[symbol] = one
        ranges = session_base.build_ranges(symbol, one, build_start, end, families)
        ranges_by_id[symbol] = {item.range_id: item for item in ranges}
        ranges_by_key[symbol] = {range_key(item): item for item in ranges}
        signal, setups, cancel, events, diagnostics = generate_symbol_options(
            symbol=symbol,
            one=one,
            ranges=ranges,
            signal_minutes=args.signal_minutes,
            response_minutes=args.response_minutes,
            config=BoundaryEngineConfig(
                tick_size=CONTRACTS[symbol].tick_size,
                source_timeframe_minutes=args.signal_minutes,
                response_timeframe_minutes=args.response_minutes,
                enable_immediate_fakeout=not args.disable_immediate,
                enable_wm_trap=not args.disable_wm,
                enable_predictive_outside_footprint=not args.disable_predictive,
                enable_accepted_break_retest=not args.disable_accepted_break,
                continuation_cap_range_widths=args.continuation_cap_widths,
            ),
        )
        signal_frames[symbol] = signal
        raw_setups.extend(
            setup for setup in setups if setup.observed_time_ns >= start_ns
        )
        cancellations.extend(
            item for item in cancel if item[0] >= start_ns
        )
        source_events.extend(events)
        source_diagnostics[symbol] = diagnostics

    raw_setups.sort(key=lambda item: (item.observed_time_ns, item.symbol, item.setup_id))
    merged_setups, merge_diagnostics, merge_audit = merge_same_bar_options(raw_setups)
    auction_setups, auction_diagnostics, auction_audit = route_auction_states(
        merged_setups,
        ranges_by_id=ranges_by_id,
        ranges_by_key=ranges_by_key,
        signal_frames=signal_frames,
    )
    setups, target_diagnostics, target_audit = route_targets(
        auction_setups,
        one_minute_frames=data,
        target_minutes=args.target_minutes,
        target_dc_atr=args.target_dc_atr,
    )

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
    cancellations.sort(key=lambda item: (item[0], item[1]))
    setup_cursor = 0
    cancel_cursor = 0
    for close_ns in sorted(grouped):
        batch = grouped[close_ns]
        earliest_open = min(bar.ts_open_ns for bar in batch.values())
        while (
            cancel_cursor < len(cancellations)
            and cancellations[cancel_cursor][0] < earliest_open
        ):
            _, setup_id, reason = cancellations[cancel_cursor]
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
            "candidate": "candidate-easychart-v15",
            "evaluation_start": str(start),
            "evaluation_end": str(end),
            "session_families": sorted(families),
            "signal_minutes": args.signal_minutes,
            "response_minutes": args.response_minutes,
            "target_minutes": args.target_minutes,
            "target_dc_atr": args.target_dc_atr,
            "continuation_cap_widths_search_only": args.continuation_cap_widths,
            "raw_setups_generated": len(raw_setups),
            "merged_setups": len(merged_setups),
            "auction_routed_setups": len(auction_setups),
            "setups_generated": len(setups),
            "cancellation_events": len(cancellations),
            "source_diagnostics": source_diagnostics,
            "merge_diagnostics": merge_diagnostics,
            "auction_diagnostics": auction_diagnostics,
            "target_diagnostics": target_diagnostics,
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
    pd.DataFrame(merge_audit).to_csv(output / "merge_audit.csv", index=False)
    pd.DataFrame(auction_audit).to_csv(output / "auction_router_audit.csv", index=False)
    pd.DataFrame(target_audit).to_csv(output / "target_router_audit.csv", index=False)
    pd.DataFrame(
        [
            {"observed_time_ns": ts, "setup_id": setup_id, "reason": reason}
            for ts, setup_id, reason in cancellations
        ]
    ).to_csv(output / "cancellations.csv", index=False)
    pd.DataFrame(simulator.trade_rows()).to_csv(output / "trades.csv", index=False)
    pd.DataFrame(simulator.equity).to_csv(output / "equity.csv", index=False)

    events = []
    for item in source_events:
        events.append(
            {
                "event_type": item.get("event", "SOURCE_STATE"),
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
                "previous_state": "ROLE_COMPLETE_OPTION",
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
                "candidate": "candidate-easychart-v15",
                "engine": "FAST_DIAGNOSTIC_NOT_AUTHORITATIVE_V15",
                "config": vars(args),
                "semantic_contract": "CASE_ROLE_CONTRACT_V15.json",
                "notes": [
                    "patterns are role observations, not confluence votes",
                    "continuation search cap is never accepted as a target",
                    "ambiguous bar fills still require finer-data/Nautilus promotion",
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
    parser.add_argument("--families", required=True)
    parser.add_argument("--signal-minutes", type=int, default=5)
    parser.add_argument("--response-minutes", type=int, default=15)
    parser.add_argument("--target-minutes", type=int, default=5)
    parser.add_argument("--target-dc-atr", type=float, default=1.0)
    parser.add_argument("--continuation-cap-widths", type=float, default=10.0)
    parser.add_argument("--disable-immediate", action="store_true")
    parser.add_argument("--disable-wm", action="store_true")
    parser.add_argument("--disable-predictive", action="store_true")
    parser.add_argument("--disable-accepted-break", action="store_true")
    parser.add_argument("--starting-nav", type=float, default=100_000.0)
    parser.add_argument("--warmup-days", type=int, default=35)
    parser.add_argument(
        "--cost-profile",
        choices=("role", "taker", "stress"),
        default="role",
    )
    parser.add_argument("--default-funding-rate", type=float, default=0.0001)
    args = parser.parse_args()
    if (
        args.disable_immediate
        and args.disable_wm
        and args.disable_predictive
        and args.disable_accepted_break
    ):
        parser.error("at least one option initiation mode must remain enabled")
    run(args)


if __name__ == "__main__":
    main()

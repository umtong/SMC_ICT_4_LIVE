#!/usr/bin/env python3
"""Run cross-sectional session-raid state routing for v10."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
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
from market_v10 import (
    BROAD_RECLAIM,
    ISOLATED,
    PeerRangeObservation,
    classify_cross_sectional_raid,
)
from market_v7 import EasyChartSessionTrapEngine, SessionTrapConfig
from simulator_v7 import ExpiringContinuousAccountSimulator, InstrumentSpec, MinuteBar
import screen_v7 as session_base
import screen_v7_fixed  # noqa: F401  # installs unit-stable range slicing

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
    side,
    liquidity_range,
    five_frame: pd.DataFrame,
    observed_time_ns: int,
) -> PeerRangeObservation | None:
    start = pd.Timestamp(int(liquidity_range.trade_start_ns), unit="ns", tz="UTC")
    observed = pd.Timestamp(int(observed_time_ns), unit="ns", tz="UTC")
    selected = five_frame[
        (five_frame["open_time_dt"] >= start)
        & (five_frame["close_time_dt"] <= observed)
    ]
    if selected.empty:
        return None
    return PeerRangeObservation(
        symbol=symbol,
        side=side,
        range_low=float(liquidity_range.low),
        range_high=float(liquidity_range.high),
        excursion_low=float(selected["low"].min()),
        excursion_high=float(selected["high"].max()),
        close=float(selected["close"].iloc[-1]),
    )


def route_setups(
    setups,
    *,
    ranges_by_id,
    ranges_by_key,
    five_frames,
    mode: str,
):
    accepted_states = {
        "isolated": {ISOLATED},
        "broad": {BROAD_RECLAIM},
        "both": {ISOLATED, BROAD_RECLAIM},
    }[mode]
    routed = []
    diagnostics: dict[str, int] = {}

    def count(key: str) -> None:
        diagnostics[key] = diagnostics.get(key, 0) + 1

    for setup in setups:
        own = ranges_by_id.get(setup.symbol, {}).get(setup.source_pool_id)
        if own is None:
            count("missing_candidate_range")
            continue
        key = range_key(own)
        candidate = observation_for(
            symbol=setup.symbol,
            side=setup.side,
            liquidity_range=own,
            five_frame=five_frames[setup.symbol],
            observed_time_ns=setup.observed_time_ns,
        )
        if candidate is None:
            count("missing_candidate_observation")
            continue
        peers = []
        for peer_symbol in SYMBOLS:
            if peer_symbol == setup.symbol:
                continue
            peer_range = ranges_by_key.get(peer_symbol, {}).get(key)
            if peer_range is None:
                continue
            observation = observation_for(
                symbol=peer_symbol,
                side=setup.side,
                liquidity_range=peer_range,
                five_frame=five_frames[peer_symbol],
                observed_time_ns=setup.observed_time_ns,
            )
            if observation is not None:
                peers.append(observation)
        decision = classify_cross_sectional_raid(candidate=candidate, peers=peers)
        count(f"decision_{decision.state.lower()}")
        if decision.state not in accepted_states:
            continue
        suffix = "SMT_ISOLATED" if decision.state == ISOLATED else "BROAD_RECLAIM"
        context = (
            f"{setup.context_bias}|XSTATE={decision.state}"
            f"|PEN={decision.candidate_penetration:.8f}"
            f"|NONCONF={','.join(decision.nonconfirming_peers)}"
            f"|SWEPT={','.join(decision.swept_peers)}"
            f"|RECLAIMED={','.join(decision.reclaimed_swept_peers)}"
        )
        routed.append(
            replace(
                setup,
                family=f"{setup.family}_{suffix}",
                causal_event_id=f"{setup.causal_event_id}:{suffix}",
                context_bias=context,
            ),
        )
        count(f"accepted_{decision.state.lower()}")
    routed.sort(key=lambda item: (item.observed_time_ns, item.symbol, item.setup_id))
    return routed, diagnostics


def run(args: argparse.Namespace) -> dict[str, object]:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    build_start = start - timedelta(days=args.warmup_days)
    families = session_base.parse_families(args.families)
    costs = session_base.cost_profile(args.cost_profile)

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
    simulator = ExpiringContinuousAccountSimulator(
        starting_nav=args.starting_nav,
        specs=specs,
        costs=costs,
        default_funding_rate=args.default_funding_rate,
    )

    start_ns = int(pd.Timestamp(start, tz="UTC").value)
    end_exclusive = pd.Timestamp(end + timedelta(days=1), tz="UTC")
    data: dict[str, pd.DataFrame] = {}
    five_frames: dict[str, pd.DataFrame] = {}
    ranges_by_id = {}
    ranges_by_key = {}
    raw_setups = []
    source_diagnostics = {}

    for symbol in SYMBOLS:
        one = load_range(symbol, build_start, end, args.cache.resolve())
        data[symbol] = one
        five = resample(one, args.signal_minutes)
        five_frames[symbol] = five
        ranges = session_base.build_ranges(symbol, one, build_start, end, families)
        ranges_by_id[symbol] = {item.range_id: item for item in ranges}
        ranges_by_key[symbol] = {range_key(item): item for item in ranges}
        engine = EasyChartSessionTrapEngine(
            symbol,
            ranges,
            SessionTrapConfig(
                enable_immediate_fakeout=not args.disable_fakeout,
                enable_delayed_trap=not args.disable_trap,
                accepted_break_range_widths=args.accepted_break_widths,
                tick_size=CONTRACTS[symbol].tick_size,
                source_timeframe_minutes=args.signal_minutes,
            ),
        )
        symbol_setups = []
        for index, candle in enumerate(to_candles(five)):
            symbol_setups.extend(engine.on_close(candle, index))
        raw_setups.extend(
            setup for setup in symbol_setups if setup.observed_time_ns >= start_ns
        )
        source_diagnostics[symbol] = dict(engine.diagnostics)

    raw_setups.sort(key=lambda item: (item.observed_time_ns, item.symbol, item.setup_id))
    setups, routing_diagnostics = route_setups(
        raw_setups,
        ranges_by_id=ranges_by_id,
        ranges_by_key=ranges_by_key,
        five_frames=five_frames,
        mode=args.cross_state,
    )

    grouped: dict[int, dict[str, MinuteBar]] = {}
    for symbol, frame in data.items():
        selected = frame[
            (frame.open_time_dt >= pd.Timestamp(start, tz="UTC"))
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

    setup_cursor = 0
    for close_ns in sorted(grouped):
        batch = grouped[close_ns]
        earliest_open = min(bar.ts_open_ns for bar in batch.values())
        while setup_cursor < len(setups) and setups[setup_cursor].observed_time_ns < earliest_open:
            simulator.add_setups([setups[setup_cursor]])
            setup_cursor += 1
        simulator.on_timestamp(batch)

    days = (end - start).days + 1
    metrics = simulator.metrics(days)
    metrics.update(
        {
            "candidate": "candidate-easychart-v10",
            "evaluation_start": str(start),
            "evaluation_end": str(end),
            "session_families": sorted(families),
            "cross_state": args.cross_state,
            "signal_minutes": args.signal_minutes,
            "accepted_break_range_widths": args.accepted_break_widths,
            "raw_setups_generated": len(raw_setups),
            "setups_generated": len(setups),
            "source_diagnostics": source_diagnostics,
            "routing_diagnostics": routing_diagnostics,
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
        },
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
    pd.DataFrame([asdict(setup) for setup in setups]).to_csv(output / "setups.csv", index=False)
    pd.DataFrame(simulator.trade_rows()).to_csv(output / "trades.csv", index=False)
    pd.DataFrame(simulator.equity).to_csv(output / "equity.csv", index=False)
    events = []
    for setup in setups:
        events.append(
            {
                "scenario_id": setup.causal_event_id,
                "instrument_id": setup.symbol,
                "event_type": "SETUP_ARMED",
                "event_time_ns": setup.observed_time_ns,
                "observed_time_ns": setup.observed_time_ns,
                "previous_state": "SESSION_RANGE_RECLAIMED",
                "next_state": f"CROSS_SECTIONAL_{args.cross_state.upper()}_ARMED",
                "reason_code": setup.family,
                "reference_price": str(setup.entry),
                "details": asdict(setup),
            },
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
            },
        )
    events.sort(key=lambda item: (item["observed_time_ns"], item["instrument_id"], item["event_type"]))
    (output / "scenario_events.jsonl").write_text(
        "".join(json.dumps(event, sort_keys=True, default=str) + "\n" for event in events),
        encoding="utf-8",
    )
    (output / "run.json").write_text(
        json.dumps(
            {
                "candidate": "candidate-easychart-v10",
                "engine": "FAST_DIAGNOSTIC_NOT_AUTHORITATIVE_V10",
                "config": vars(args),
                "reuse": "candidate-05 isolated SMT session-state concept",
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
    parser.add_argument("--cross-state", choices=("isolated", "broad", "both"), required=True)
    parser.add_argument("--signal-minutes", type=int, default=5)
    parser.add_argument("--accepted-break-widths", type=float, default=1.0)
    parser.add_argument("--disable-fakeout", action="store_true")
    parser.add_argument("--disable-trap", action="store_true")
    parser.add_argument("--starting-nav", type=float, default=100_000.0)
    parser.add_argument("--warmup-days", type=int, default=5)
    parser.add_argument("--cost-profile", choices=("role", "taker", "stress"), default="role")
    parser.add_argument("--default-funding-rate", type=float, default=0.0001)
    args = parser.parse_args()
    if args.disable_fakeout and args.disable_trap:
        parser.error("at least one interaction family must be enabled")
    run(args)


if __name__ == "__main__":
    main()

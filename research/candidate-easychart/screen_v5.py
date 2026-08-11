#!/usr/bin/env python3
"""Run v5 intrinsic-time channel and delayed Trap diagnostics."""
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
from domain_v3 import Candle, CostAssumptions
from instrument_contracts import CONTRACTS
from market_v5 import (
    DirectionalChangePivotDetector,
    EasyChartIntrinsicStructureEngine,
    ScenarioConfigV5,
)
from simulator_v3 import ContinuousAccountSimulator, InstrumentSpec, MinuteBar

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


def build_setups(
    symbol: str,
    one_minute: pd.DataFrame,
    config: ScenarioConfigV5,
):
    five = to_candles(resample(one_minute, 5))
    channel_bars = to_candles(resample(one_minute, config.channel_timeframe_minutes))
    engine = EasyChartIntrinsicStructureEngine(symbol, config)

    channel_detector = DirectionalChangePivotDetector(
        timeframe_minutes=config.channel_timeframe_minutes,
        atr_period=config.dc_atr_period,
        atr_multiple=config.channel_dc_atr_multiple,
    )
    micro_detector = DirectionalChangePivotDetector(
        timeframe_minutes=5,
        atr_period=config.dc_atr_period,
        atr_multiple=config.micro_dc_atr_multiple,
    )

    channel_events = []
    for index, candle in enumerate(channel_bars):
        pivot = channel_detector.on_candle(candle, index)
        if pivot is not None:
            channel_events.append((pivot.observed_time_ns, pivot))

    channel_cursor = 0
    setups = []
    for index, candle in enumerate(five):
        close_ns = candle.ts_close_ns
        while channel_cursor < len(channel_events) and channel_events[channel_cursor][0] < close_ns:
            _, pivot = channel_events[channel_cursor]
            engine.add_directional_channel_pivot(pivot, channel_bars)
            channel_cursor += 1

        setups.extend(engine.on_five_minute_close(five, index))

        # A 5m turning point first confirmed at this close is not available to
        # an interaction or BOS decision earlier within the same close.
        micro = micro_detector.on_candle(candle, index)
        if micro is not None:
            engine.add_directional_micro_pivot(micro)

        while channel_cursor < len(channel_events) and channel_events[channel_cursor][0] == close_ns:
            _, pivot = channel_events[channel_cursor]
            engine.add_directional_channel_pivot(pivot, channel_bars)
            channel_cursor += 1

    diagnostics = dict(engine.diagnostics)
    diagnostics.update(
        {
            f"channel_dc_{key}": value
            for key, value in channel_detector.diagnostics.items()
        },
    )
    diagnostics.update(
        {
            f"micro_dc_{key}": value
            for key, value in micro_detector.diagnostics.items()
        },
    )
    return setups, diagnostics


def cost_profile(name: str) -> CostAssumptions:
    if name == "role":
        return CostAssumptions(
            entry_fee_bps=2.0,
            stop_fee_bps=5.0,
            target_fee_bps=2.0,
            entry_slippage_bps=0.0,
            stop_slippage_bps=2.5,
            target_slippage_bps=0.0,
            expected_funding_bps=1.0,
        )
    if name == "taker":
        return CostAssumptions(
            entry_fee_bps=5.0,
            stop_fee_bps=5.0,
            target_fee_bps=5.0,
            entry_slippage_bps=1.0,
            stop_slippage_bps=2.5,
            target_slippage_bps=1.0,
            expected_funding_bps=1.0,
        )
    if name == "stress":
        return CostAssumptions(
            entry_fee_bps=7.5,
            stop_fee_bps=7.5,
            target_fee_bps=7.5,
            entry_slippage_bps=2.5,
            stop_slippage_bps=5.0,
            target_slippage_bps=2.5,
            expected_funding_bps=2.0,
        )
    raise ValueError(f"unknown cost profile: {name}")


def run(args: argparse.Namespace) -> dict[str, object]:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    build_start = start - timedelta(days=args.warmup_days)

    config = ScenarioConfigV5(
        channel_timeframe_minutes=args.channel_minutes,
        min_body_ratio=args.min_body_ratio,
        min_previous_body_atr=args.min_previous_body_atr,
        strict_fvg_body_ratio=args.strict_fvg_body_ratio,
        require_bos=True,
        require_fvg=args.require_fvg,
        enable_immediate_fakeout=not args.disable_fakeout,
        enable_one_bar_trap=not args.disable_trap,
        enable_boundary_touch=args.enable_touch,
        channel_dc_atr_multiple=args.channel_dc_atr,
        micro_dc_atr_multiple=args.micro_dc_atr,
        dc_atr_period=args.dc_atr_period,
        accepted_break_channel_widths=args.accepted_break_widths,
    )
    costs = cost_profile(args.cost_profile)
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
    simulator = ContinuousAccountSimulator(
        starting_nav=args.starting_nav,
        specs=specs,
        costs=costs,
        default_funding_rate=args.default_funding_rate,
    )

    start_ns = int(pd.Timestamp(start, tz="UTC").value)
    end_exclusive = pd.Timestamp(end + timedelta(days=1), tz="UTC")
    data: dict[str, pd.DataFrame] = {}
    setups = []
    diagnostics: dict[str, dict[str, int]] = {}
    for symbol in SYMBOLS:
        one = load_range(symbol, build_start, end, args.cache.resolve())
        data[symbol] = one
        symbol_config = ScenarioConfigV5(
            **{**asdict(config), "tick_size": CONTRACTS[symbol].tick_size},
        )
        symbol_setups, symbol_diagnostics = build_setups(symbol, one, symbol_config)
        symbol_setups = [setup for setup in symbol_setups if setup.observed_time_ns >= start_ns]
        setups.extend(symbol_setups)
        diagnostics[symbol] = symbol_diagnostics

    setups.sort(key=lambda setup: (setup.observed_time_ns, setup.symbol, setup.setup_id))
    setup_cursor = 0
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
            "candidate": "candidate-easychart-v5",
            "evaluation_start": str(start),
            "evaluation_end": str(end),
            "scenario_config": asdict(config),
            "cost_profile": args.cost_profile,
            "costs": asdict(costs),
            "setups_generated": len(setups),
            "scenario_diagnostics": diagnostics,
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
                "previous_state": "SPONSORED_DISPLACEMENT",
                "next_state": "FIRST_MITIGATION_ARMED",
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
                "candidate": "candidate-easychart-v5",
                "engine": "FAST_DIAGNOSTIC_NOT_AUTHORITATIVE_V5",
                "config": vars(args),
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
    parser.add_argument("--starting-nav", type=float, default=100_000.0)
    parser.add_argument("--warmup-days", type=int, default=60)
    parser.add_argument("--channel-minutes", type=int, default=15)
    parser.add_argument("--channel-dc-atr", type=float, default=1.0)
    parser.add_argument("--micro-dc-atr", type=float, default=1.0)
    parser.add_argument("--dc-atr-period", type=int, default=14)
    parser.add_argument("--accepted-break-widths", type=float, default=1.0)
    parser.add_argument("--min-body-ratio", type=float, default=2.0)
    parser.add_argument("--min-previous-body-atr", type=float, default=0.10)
    parser.add_argument("--strict-fvg-body-ratio", type=float, default=2.0)
    parser.add_argument("--require-fvg", action="store_true")
    parser.add_argument("--enable-touch", action="store_true")
    parser.add_argument("--disable-fakeout", action="store_true")
    parser.add_argument("--disable-trap", action="store_true")
    parser.add_argument("--cost-profile", choices=("role", "taker", "stress"), default="role")
    parser.add_argument("--default-funding-rate", type=float, default=0.0001)
    args = parser.parse_args()
    if args.disable_fakeout and args.disable_trap and not args.enable_touch:
        parser.error("at least one channel interaction family must be enabled")
    run(args)


if __name__ == "__main__":
    main()

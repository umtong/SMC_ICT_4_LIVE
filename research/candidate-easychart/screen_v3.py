#!/usr/bin/env python3
"""Run candidate-easychart v3 with entry-time impulse objectives.

The screen is a non-authoritative selector.  It keeps the user-fixed execution
contract and the four-symbol single account, while testing only explicitly
named causal families.  Any surviving variant must be promoted unchanged to a
NautilusTrader BacktestNode before it can count as evidence.
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
from domain_v3 import Candle, CostAssumptions
from instrument_contracts import CONTRACTS
from market_v3 import EasyChartScenarioEngine, ScenarioConfig, confirmed_pivot
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


def build_setups(symbol: str, one_minute: pd.DataFrame, config: ScenarioConfig, context_minutes: int):
    five = to_candles(resample(one_minute, 5))
    fifteen = to_candles(resample(one_minute, 15))
    context = to_candles(resample(one_minute, context_minutes))
    engine = EasyChartScenarioEngine(symbol, config)

    context_cursor = 0
    fifteen_events: list[tuple[int, object]] = []
    for index in range(len(fifteen)):
        pivot = confirmed_pivot(fifteen, index, config.pivot_span_15m)
        if pivot is not None:
            fifteen_events.append((fifteen[pivot.observed_index].ts_close_ns, pivot))
    fifteen_cursor = 0
    setups = []

    for index in range(len(five)):
        close_ns = five[index].ts_close_ns
        while context_cursor < len(context) and context[context_cursor].ts_close_ns <= close_ns:
            pivot = confirmed_pivot(context, context_cursor, config.pivot_span_context)
            if pivot is not None:
                engine.add_context_pivot(pivot)
            engine.update_context_close(context[context_cursor].close)
            context_cursor += 1
        while fifteen_cursor < len(fifteen_events) and fifteen_events[fifteen_cursor][0] <= close_ns:
            _, pivot = fifteen_events[fifteen_cursor]
            if config.use_15m_liquidity:
                engine.add_confirmed_pool(15, pivot, fifteen)
            fifteen_cursor += 1
        pivot = confirmed_pivot(five, index, config.pivot_span_5m)
        if pivot is not None and config.use_5m_liquidity:
            engine.add_confirmed_pool(5, pivot, five)
        setups.extend(engine.on_five_minute_close(five, index))
    return setups, dict(engine.diagnostics)


def cost_profile(name: str) -> CostAssumptions:
    if name == "role":
        # Resting limit entry/target, taker stop.  Exact production rates must
        # be read from the account commission endpoint; this is a development
        # profile, not a claim about a universal Binance tier.
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

    config = ScenarioConfig(
        pivot_span_5m=args.pivot_span_5m,
        pivot_span_15m=args.pivot_span_15m,
        pivot_span_context=args.pivot_span_context,
        confirmation_window_bars=args.confirmation_window_bars,
        use_5m_liquidity=not args.no_5m_liquidity,
        use_15m_liquidity=not args.no_15m_liquidity,
        require_reclaim=not args.allow_touch_only,
        require_htf_alignment=args.require_htf_alignment,
        enable_sweep_ob=args.sweep_ob,
        enable_break_ob=args.break_ob,
        enable_direct_sweep=args.direct_sweep,
        enable_direct_break=args.direct_break,
        min_body_ratio=args.min_body_ratio,
        max_body_ratio=args.max_body_ratio,
        min_previous_body_atr=args.min_previous_body_atr,
        max_current_body_atr=args.max_current_body_atr,
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
        symbol_config = ScenarioConfig(**{**asdict(config), "tick_size": CONTRACTS[symbol].tick_size})
        symbol_setups, symbol_diagnostics = build_setups(
            symbol,
            one,
            symbol_config,
            args.context_minutes,
        )
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
            "candidate": "candidate-easychart-v3",
            "evaluation_start": str(start),
            "evaluation_end": str(end),
            "context_minutes": args.context_minutes,
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
                "previous_state": "CONFIRMATION_FORMED",
                "next_state": "RETEST_ARMED",
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
                "candidate": "candidate-easychart-v3",
                "engine": "FAST_DIAGNOSTIC_NOT_AUTHORITATIVE_V3",
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
    parser.add_argument("--warmup-days", type=int, default=35)
    parser.add_argument("--context-minutes", type=int, default=240)
    parser.add_argument("--pivot-span-5m", type=int, default=2)
    parser.add_argument("--pivot-span-15m", type=int, default=2)
    parser.add_argument("--pivot-span-context", type=int, default=1)
    parser.add_argument("--confirmation-window-bars", type=int, default=2)
    parser.add_argument("--no-5m-liquidity", action="store_true")
    parser.add_argument("--no-15m-liquidity", action="store_true")
    parser.add_argument("--allow-touch-only", action="store_true")
    parser.add_argument("--require-htf-alignment", action="store_true")
    parser.add_argument("--sweep-ob", action="store_true")
    parser.add_argument("--break-ob", action="store_true")
    parser.add_argument("--direct-sweep", action="store_true")
    parser.add_argument("--direct-break", action="store_true")
    parser.add_argument("--min-body-ratio", type=float, default=1.0)
    parser.add_argument("--max-body-ratio", type=float)
    parser.add_argument("--min-previous-body-atr", type=float, default=0.0)
    parser.add_argument("--max-current-body-atr", type=float)
    parser.add_argument("--cost-profile", choices=("role", "taker", "stress"), default="role")
    parser.add_argument("--default-funding-rate", type=float, default=0.0001)
    args = parser.parse_args()
    if not any((args.sweep_ob, args.break_ob, args.direct_sweep, args.direct_break)):
        parser.error("at least one scenario family must be enabled")
    run(args)


if __name__ == "__main__":
    main()

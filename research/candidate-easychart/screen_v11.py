#!/usr/bin/env python3
"""Run the EasyChart three-role confluence diagnostic v11."""
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
from market_v5 import DirectionalChangePivotDetector
from market_v7 import EasyChartSessionTrapEngine, SessionTrapConfig
from market_v11 import (
    TrendlineImpulseContextEngine,
    evaluate_session_impulse_confluence,
)
from simulator_v7 import ExpiringContinuousAccountSimulator, InstrumentSpec, MinuteBar
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


def build_contexts(
    symbol: str,
    one_minute: pd.DataFrame,
    *,
    context_minutes: int,
    dc_atr_period: int,
    dc_atr_multiple: float,
):
    frame = resample(one_minute, context_minutes)
    candles = to_candles(frame)
    detector = DirectionalChangePivotDetector(
        timeframe_minutes=context_minutes,
        atr_period=dc_atr_period,
        atr_multiple=dc_atr_multiple,
    )
    engine = TrendlineImpulseContextEngine(symbol)
    for index, candle in enumerate(candles):
        engine.on_close(candle)
        pivot = detector.on_candle(candle, index)
        if pivot is not None:
            engine.on_pivot(pivot)
    diagnostics = dict(engine.diagnostics)
    diagnostics.update({f"dc_{key}": value for key, value in detector.diagnostics.items()})
    return engine, diagnostics


def exact_close(frame: pd.DataFrame, observed_time_ns: int) -> float | None:
    observed = pd.Timestamp(int(observed_time_ns), unit="ns", tz="UTC")
    selected = frame[frame["close_time_dt"] == observed]
    if selected.empty:
        return None
    return float(selected["close"].iloc[-1])


def objective_revisited(
    *,
    context,
    setup_observed_time_ns: int,
    five_frame: pd.DataFrame,
) -> bool:
    start = pd.Timestamp(int(context.observed_time_ns), unit="ns", tz="UTC")
    end = pd.Timestamp(int(setup_observed_time_ns), unit="ns", tz="UTC")
    selected = five_frame[
        (five_frame["open_time_dt"] >= start)
        & (five_frame["close_time_dt"] < end)
    ]
    if selected.empty:
        return False
    if context.side.name == "LONG":
        return float(selected["high"].max()) >= context.objective
    return float(selected["low"].min()) <= context.objective


def confluence_setups(
    raw_setups,
    *,
    context_engines,
    five_frames,
    require_fib: bool,
    require_trendline: bool,
):
    output = []
    diagnostics: dict[str, int] = {}

    def count(key: str) -> None:
        diagnostics[key] = diagnostics.get(key, 0) + 1

    for setup in raw_setups:
        engine = context_engines[setup.symbol]
        eligible = [
            context
            for context in engine.contexts
            if context.side is setup.side and context.observed_time_ns < setup.observed_time_ns
        ]
        if not eligible:
            count("no_complete_impulse_context")
            continue
        reclaim_close = exact_close(five_frames[setup.symbol], setup.observed_time_ns)
        if reclaim_close is None:
            count("missing_reclaim_close")
            continue
        accepted = None
        chosen = None
        for context in reversed(eligible):
            if objective_revisited(
                context=context,
                setup_observed_time_ns=setup.observed_time_ns,
                five_frame=five_frames[setup.symbol],
            ):
                count("impulse_objective_revisited_before_setup")
                continue
            evaluation = evaluate_session_impulse_confluence(
                side=setup.side,
                context=context,
                observed_time_ns=setup.observed_time_ns,
                reclaim_close=reclaim_close,
                sweep_extreme=setup.formation_extreme,
                session_boundary=setup.entry,
                require_fib=require_fib,
                require_trendline=require_trendline,
            )
            count(f"evaluation_{evaluation.reason.lower()}")
            if evaluation.accepted:
                accepted = evaluation
                chosen = context
                break
        if accepted is None or chosen is None:
            continue
        assert accepted.entry is not None and accepted.target is not None
        roles = []
        if require_fib:
            roles.append("FIB0618")
        if require_trendline:
            roles.append("BROKEN_TRENDLINE")
        roles.append("SESSION_RAID")
        suffix = "_".join(roles)
        candidate = replace(
            setup,
            family=f"{setup.family}_{suffix}",
            causal_event_id=f"{setup.causal_event_id}:{chosen.context_id}:{suffix}",
            entry=float(accepted.entry),
            initial_target=float(accepted.target),
            fixed_target_id=f"IMPULSE_TERMINAL:{chosen.context_id}",
            zone_low=float(accepted.entry),
            zone_high=float(accepted.entry),
            context_bias=(
                f"{setup.context_bias}|IMPULSE={chosen.context_id}"
                f"|FIB0618={accepted.fib_price}"
                f"|TRENDLINE={accepted.trendline_price}"
            ),
        )
        if candidate.executable(
            candidate.initial_target,
            target_id=candidate.fixed_target_id,
            min_gross_rr=1.0,
        ) is None:
            count("post_confluence_rr_lt_1")
            continue
        output.append(candidate)
        count("setups_accepted")
        count(f"setups_accepted_{suffix}")
    output.sort(key=lambda item: (item.observed_time_ns, item.symbol, item.setup_id))
    return output, diagnostics


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
    data = {}
    five_frames = {}
    context_engines = {}
    context_diagnostics = {}
    source_diagnostics = {}
    raw_setups = []

    for symbol in SYMBOLS:
        one = load_range(symbol, build_start, end, args.cache.resolve())
        data[symbol] = one
        five = resample(one, args.signal_minutes)
        five_frames[symbol] = five
        context_engine, context_diag = build_contexts(
            symbol,
            one,
            context_minutes=args.context_minutes,
            dc_atr_period=args.dc_atr_period,
            dc_atr_multiple=args.dc_atr_multiple,
        )
        context_engines[symbol] = context_engine
        context_diagnostics[symbol] = context_diag

        ranges = session_base.build_ranges(symbol, one, build_start, end, families)
        session_engine = EasyChartSessionTrapEngine(
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
            symbol_setups.extend(session_engine.on_close(candle, index))
        raw_setups.extend(setup for setup in symbol_setups if setup.observed_time_ns >= start_ns)
        source_diagnostics[symbol] = dict(session_engine.diagnostics)

    raw_setups.sort(key=lambda item: (item.observed_time_ns, item.symbol, item.setup_id))
    setups, confluence_diagnostics = confluence_setups(
        raw_setups,
        context_engines=context_engines,
        five_frames=five_frames,
        require_fib=not args.disable_fib,
        require_trendline=not args.disable_trendline,
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
    cursor = 0
    for close_ns in sorted(grouped):
        batch = grouped[close_ns]
        earliest_open = min(bar.ts_open_ns for bar in batch.values())
        while cursor < len(setups) and setups[cursor].observed_time_ns < earliest_open:
            simulator.add_setups([setups[cursor]])
            cursor += 1
        simulator.on_timestamp(batch)

    days = (end - start).days + 1
    metrics = simulator.metrics(days)
    metrics.update(
        {
            "candidate": "candidate-easychart-v11",
            "evaluation_start": str(start),
            "evaluation_end": str(end),
            "session_families": sorted(families),
            "context_minutes": args.context_minutes,
            "dc_atr_period": args.dc_atr_period,
            "dc_atr_multiple": args.dc_atr_multiple,
            "require_fib": not args.disable_fib,
            "require_trendline": not args.disable_trendline,
            "raw_setups_generated": len(raw_setups),
            "setups_generated": len(setups),
            "source_diagnostics": source_diagnostics,
            "context_diagnostics": context_diagnostics,
            "confluence_diagnostics": confluence_diagnostics,
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
    (output / "metrics.json").write_text(json.dumps(metrics, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    pd.DataFrame([asdict(setup) for setup in setups]).to_csv(output / "setups.csv", index=False)
    pd.DataFrame(simulator.trade_rows()).to_csv(output / "trades.csv", index=False)
    pd.DataFrame(simulator.equity).to_csv(output / "equity.csv", index=False)
    (output / "run.json").write_text(
        json.dumps({"candidate": "candidate-easychart-v11", "engine": "FAST_DIAGNOSTIC_NOT_AUTHORITATIVE_V11", "config": vars(args)}, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    (output / "scenario_events.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "scenario_id": setup.causal_event_id,
                    "instrument_id": setup.symbol,
                    "event_type": "SETUP_ARMED",
                    "event_time_ns": setup.observed_time_ns,
                    "observed_time_ns": setup.observed_time_ns,
                    "previous_state": "SESSION_RAID_RECLAIMED_IN_VALUE",
                    "next_state": "CONFLUENCE_RETEST_ARMED",
                    "reason_code": setup.family,
                    "reference_price": str(setup.entry),
                    "details": asdict(setup),
                },
                sort_keys=True,
                default=str,
            ) + "\n"
            for setup in setups
        ),
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
    parser.add_argument("--context-minutes", type=int, default=15)
    parser.add_argument("--dc-atr-period", type=int, default=14)
    parser.add_argument("--dc-atr-multiple", type=float, default=1.0)
    parser.add_argument("--accepted-break-widths", type=float, default=1.0)
    parser.add_argument("--disable-fakeout", action="store_true")
    parser.add_argument("--disable-trap", action="store_true")
    parser.add_argument("--disable-fib", action="store_true")
    parser.add_argument("--disable-trendline", action="store_true")
    parser.add_argument("--starting-nav", type=float, default=100_000.0)
    parser.add_argument("--warmup-days", type=int, default=60)
    parser.add_argument("--cost-profile", choices=("role", "taker", "stress"), default="role")
    parser.add_argument("--default-funding-rate", type=float, default=0.0001)
    args = parser.parse_args()
    if args.disable_fakeout and args.disable_trap:
        parser.error("at least one session interaction must be enabled")
    if args.disable_fib and args.disable_trendline:
        parser.error("at least one context role must remain enabled")
    run(args)


if __name__ == "__main__":
    main()

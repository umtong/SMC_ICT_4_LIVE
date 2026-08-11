#!/usr/bin/env python3
"""Run source/external-time-structure session trap diagnostics."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from data import load_range, resample
from domain_v3 import Candle, CostAssumptions
from instrument_contracts import CONTRACTS
from market_v7 import EasyChartSessionTrapEngine, SessionLiquidityRange, SessionTrapConfig
from simulator_v7 import ExpiringContinuousAccountSimulator, InstrumentSpec, MinuteBar

SYMBOLS = tuple(CONTRACTS)
NEW_YORK = ZoneInfo("America/New_York")
UTC = timezone.utc
DECLARED_FAMILIES = {
    "ASIA_LONDON",
    "ASIA_NY",
    "LONDON_NY",
    "PRIOR_DAY_LONDON",
    "PRIOR_DAY_NY",
    "FUNDING_00_08",
    "FUNDING_08_16",
    "FUNDING_16_00",
}


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


def wall_ns(day: date, hour: int, minute: int = 0, *, zone: ZoneInfo = NEW_YORK) -> int:
    value = datetime.combine(day, time(hour, minute), tzinfo=zone).astimezone(UTC)
    return int(pd.Timestamp(value).value)


def utc_ns(day: date, hour: int, minute: int = 0) -> int:
    value = datetime.combine(day, time(hour, minute), tzinfo=UTC)
    return int(pd.Timestamp(value).value)


def range_prices(frame: pd.DataFrame, start_ns: int, end_ns: int) -> tuple[float, float] | None:
    opens_ns = frame["open_time_dt"].astype("int64")
    selected = frame[(opens_ns >= start_ns) & (opens_ns < end_ns)]
    if selected.empty:
        return None
    high = float(selected["high"].max())
    low = float(selected["low"].min())
    if not high > low:
        return None
    return high, low


def append_range(
    output: list[SessionLiquidityRange],
    frame: pd.DataFrame,
    *,
    symbol: str,
    label: str,
    reference_family: str,
    trade_window: str,
    reference_start_ns: int,
    reference_end_ns: int,
    trade_start_ns: int,
    trade_end_ns: int,
) -> None:
    prices = range_prices(frame, reference_start_ns, reference_end_ns)
    if prices is None:
        return
    high, low = prices
    output.append(
        SessionLiquidityRange(
            range_id=f"{symbol}:{label}:{reference_start_ns}:{reference_end_ns}",
            reference_family=reference_family,
            trade_window=trade_window,
            observed_time_ns=reference_end_ns,
            trade_start_ns=trade_start_ns,
            trade_end_ns=trade_end_ns,
            high=high,
            low=low,
        ),
    )


def build_ranges(
    symbol: str,
    frame: pd.DataFrame,
    start: date,
    end: date,
    families: set[str],
) -> list[SessionLiquidityRange]:
    ranges: list[SessionLiquidityRange] = []
    day = start - timedelta(days=2)
    final_day = end + timedelta(days=2)
    while day <= final_day:
        previous = day - timedelta(days=1)
        asia_start = wall_ns(previous, 20)
        asia_end = wall_ns(day, 0)
        london_start = wall_ns(day, 2)
        london_end = wall_ns(day, 5)
        ny_start = wall_ns(day, 7)
        ny_end = wall_ns(day, 10)
        prior_day_start = wall_ns(previous, 0)
        prior_day_end = wall_ns(day, 0)

        if "ASIA_LONDON" in families:
            append_range(
                ranges,
                frame,
                symbol=symbol,
                label=f"ASIA_LONDON:{day}",
                reference_family="ASIA_RANGE",
                trade_window="LONDON_KZ",
                reference_start_ns=asia_start,
                reference_end_ns=asia_end,
                trade_start_ns=london_start,
                trade_end_ns=london_end,
            )
        if "ASIA_NY" in families:
            append_range(
                ranges,
                frame,
                symbol=symbol,
                label=f"ASIA_NY:{day}",
                reference_family="ASIA_RANGE",
                trade_window="NY_KZ",
                reference_start_ns=asia_start,
                reference_end_ns=asia_end,
                trade_start_ns=ny_start,
                trade_end_ns=ny_end,
            )
        if "LONDON_NY" in families:
            append_range(
                ranges,
                frame,
                symbol=symbol,
                label=f"LONDON_NY:{day}",
                reference_family="LONDON_RANGE",
                trade_window="NY_KZ",
                reference_start_ns=london_start,
                reference_end_ns=london_end,
                trade_start_ns=ny_start,
                trade_end_ns=ny_end,
            )
        if "PRIOR_DAY_LONDON" in families:
            append_range(
                ranges,
                frame,
                symbol=symbol,
                label=f"PRIOR_DAY_LONDON:{day}",
                reference_family="PRIOR_NY_DAY_RANGE",
                trade_window="LONDON_KZ",
                reference_start_ns=prior_day_start,
                reference_end_ns=prior_day_end,
                trade_start_ns=london_start,
                trade_end_ns=london_end,
            )
        if "PRIOR_DAY_NY" in families:
            append_range(
                ranges,
                frame,
                symbol=symbol,
                label=f"PRIOR_DAY_NY:{day}",
                reference_family="PRIOR_NY_DAY_RANGE",
                trade_window="NY_KZ",
                reference_start_ns=prior_day_start,
                reference_end_ns=prior_day_end,
                trade_start_ns=ny_start,
                trade_end_ns=ny_end,
            )

        # Crypto-specific eight-hour reference ranges align with the standard
        # historical Binance perpetual funding cycle.  The following two hours
        # are treated as the next auction's opening interaction window.
        d0, d1 = utc_ns(day, 0), utc_ns(day, 8)
        d2, d3 = utc_ns(day, 16), utc_ns(day + timedelta(days=1), 0)
        if "FUNDING_00_08" in families:
            append_range(
                ranges,
                frame,
                symbol=symbol,
                label=f"FUNDING_00_08:{day}",
                reference_family="FUNDING_00_08_RANGE",
                trade_window="FUNDING_08_OPEN",
                reference_start_ns=d0,
                reference_end_ns=d1,
                trade_start_ns=d1,
                trade_end_ns=utc_ns(day, 10),
            )
        if "FUNDING_08_16" in families:
            append_range(
                ranges,
                frame,
                symbol=symbol,
                label=f"FUNDING_08_16:{day}",
                reference_family="FUNDING_08_16_RANGE",
                trade_window="FUNDING_16_OPEN",
                reference_start_ns=d1,
                reference_end_ns=d2,
                trade_start_ns=d2,
                trade_end_ns=utc_ns(day, 18),
            )
        if "FUNDING_16_00" in families:
            append_range(
                ranges,
                frame,
                symbol=symbol,
                label=f"FUNDING_16_00:{day}",
                reference_family="FUNDING_16_00_RANGE",
                trade_window="FUNDING_00_OPEN",
                reference_start_ns=d2,
                reference_end_ns=d3,
                trade_start_ns=d3,
                trade_end_ns=utc_ns(day + timedelta(days=1), 2),
            )
        day += timedelta(days=1)
    unique = {item.range_id: item for item in ranges}
    return sorted(unique.values(), key=lambda item: (item.trade_start_ns, item.range_id))


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


def parse_families(raw: str) -> set[str]:
    values = {part.strip().upper() for part in raw.split(",") if part.strip()}
    unknown = values - DECLARED_FAMILIES
    if unknown:
        raise ValueError(f"unknown session families: {sorted(unknown)}")
    if not values:
        raise ValueError("at least one session family is required")
    return values


def run(args: argparse.Namespace) -> dict[str, object]:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    build_start = start - timedelta(days=args.warmup_days)
    families = parse_families(args.families)
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
    simulator = ExpiringContinuousAccountSimulator(
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
    ranges_by_symbol: dict[str, int] = {}

    for symbol in SYMBOLS:
        one = load_range(symbol, build_start, end, args.cache.resolve())
        data[symbol] = one
        candles = to_candles(resample(one, args.signal_minutes))
        ranges = build_ranges(symbol, one, build_start, end, families)
        ranges_by_symbol[symbol] = len(ranges)
        config = SessionTrapConfig(
            enable_immediate_fakeout=not args.disable_fakeout,
            enable_delayed_trap=not args.disable_trap,
            accepted_break_range_widths=args.accepted_break_widths,
            tick_size=CONTRACTS[symbol].tick_size,
            source_timeframe_minutes=args.signal_minutes,
        )
        engine = EasyChartSessionTrapEngine(symbol, ranges, config)
        symbol_setups = []
        for index, candle in enumerate(candles):
            symbol_setups.extend(engine.on_close(candle, index))
        setups.extend(setup for setup in symbol_setups if setup.observed_time_ns >= start_ns)
        diagnostics[symbol] = dict(engine.diagnostics)

    setups.sort(key=lambda setup: (setup.observed_time_ns, setup.symbol, setup.setup_id))
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
            "candidate": "candidate-easychart-v7",
            "evaluation_start": str(start),
            "evaluation_end": str(end),
            "session_families": sorted(families),
            "signal_minutes": args.signal_minutes,
            "accepted_break_range_widths": args.accepted_break_widths,
            "cost_profile": args.cost_profile,
            "costs": asdict(costs),
            "setups_generated": len(setups),
            "ranges_by_symbol": ranges_by_symbol,
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
                "previous_state": "SESSION_RANGE_INTERACTION",
                "next_state": "FIRST_BOUNDARY_RETEST_ARMED",
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
                "candidate": "candidate-easychart-v7",
                "engine": "FAST_DIAGNOSTIC_NOT_AUTHORITATIVE_V7",
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
    parser.add_argument("--families", required=True, help="comma-separated declared session families")
    parser.add_argument("--signal-minutes", type=int, default=5)
    parser.add_argument("--accepted-break-widths", type=float, default=1.0)
    parser.add_argument("--disable-fakeout", action="store_true")
    parser.add_argument("--disable-trap", action="store_true")
    parser.add_argument("--starting-nav", type=float, default=100_000.0)
    parser.add_argument("--warmup-days", type=int, default=5)
    parser.add_argument("--cost-profile", choices=("role", "taker", "stress"), default="role")
    parser.add_argument("--default-funding-rate", type=float, default=0.0001)
    args = parser.parse_args()
    if args.signal_minutes <= 0:
        parser.error("signal minutes must be positive")
    if args.disable_fakeout and args.disable_trap:
        parser.error("at least one interaction family must be enabled")
    try:
        parse_families(args.families)
    except ValueError as exc:
        parser.error(str(exc))
    run(args)


if __name__ == "__main__":
    main()

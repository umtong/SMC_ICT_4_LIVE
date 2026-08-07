#!/usr/bin/env python3
"""Cross-asset consensus laggard transfer to unconsumed hourly liquidity.

This is a causal SMC/ICT state machine, not a generic indicator conjunction.
For each completed UTC minute and each allowed USD-M perpetual market:

1. Freeze the immediately preceding 60 completed-minute high/low as external
   intraday liquidity and the preceding five-minute high/low as internal
   structure.
2. A leader accepts beyond its hourly external boundary with same-direction
   aggressive flow and range expansion relative to the preceding 20 minutes.
3. A laggard has not consumed the corresponding hourly boundary, but completes
   an aligned break of its internal five-minute structure with the same range
   and flow confirmation.
4. Primary requires two independent peer leaders, including BTC or ETH. The
   single ablation requires only one such leader. Everything else is identical.
5. The first strictly later venue TradeTick enters only while the broken local
   boundary still holds. Invalidation is beyond the laggard's complete local
   displacement path plus one side-cost buffer. The frozen hourly external
   boundary is the take-profit destination.

Candidate logic creates immutable symbol-tagged ScenarioPlan objects. Official
Binance Vision aggregate trades are represented one-for-one as NautilusTrader
TradeTicks. NautilusTrader exclusively owns orders, fills, fees, margin,
positions, PnL and shared-account NAV. There is one global pending entry or
open position across BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import timedelta
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from aggtrade_data import (  # noqa: E402
    AggTrade,
    AggTradeDownload,
    download_aggtrade_days,
    iter_downloads,
)
from core import Side  # noqa: E402
from data import parse_utc_date  # noqa: E402
from impact_regime_probe import ScenarioPlan  # noqa: E402
from nautilus_multi_tick_plan_backtest import (  # noqa: E402
    InstrumentSpec,
    SymbolScenarioPlan,
    run_nautilus_multi_tick_plan_backtest,
)
from resolved_impact_v17_nautilus_week import atomic_json, load_execution  # noqa: E402

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
CORE_LEADERS = frozenset(("BTCUSDT", "ETHUSDT"))
VARIANTS = ("primary", "control")
MINUTE_NS = 60_000_000_000
DAY_NS = 86_400_000_000_000
EXTERNAL_LOOKBACK_MINUTES = 60
INTERNAL_LOOKBACK_MINUTES = 5
DISPLACEMENT_LOOKBACK_MINUTES = 20
CONTEXT_MINUTES = EXTERNAL_LOOKBACK_MINUTES + DISPLACEMENT_LOOKBACK_MINUTES + 10
STOP_BUFFER_FRACTION = 7.0 / 10_000.0
MAXIMUM_HOLD_HOURS = 4
MAXIMUM_HOLD_NS = MAXIMUM_HOLD_HOURS * 3_600_000_000_000
FLUSH_TICKS = 3

INSTRUMENT_SPECS: dict[str, InstrumentSpec] = {
    "BTCUSDT": InstrumentSpec(
        symbol="BTCUSDT",
        base_currency="BTC",
        price_increment="0.1",
        quantity_increment="0.001",
        min_quantity="0.001",
        min_notional=5.0,
        min_price="0.1",
        max_price="10000000.0",
    ),
    "ETHUSDT": InstrumentSpec(
        symbol="ETHUSDT",
        base_currency="ETH",
        price_increment="0.01",
        quantity_increment="0.001",
        min_quantity="0.001",
        min_notional=5.0,
        min_price="0.01",
        max_price="1000000.0",
    ),
    # Coarse quantity increments are conservative relative to the historical
    # contract filters and prevent fictitious precision.
    "SOLUSDT": InstrumentSpec(
        symbol="SOLUSDT",
        base_currency="SOL",
        price_increment="0.001",
        quantity_increment="1",
        min_quantity="1",
        min_notional=5.0,
        min_price="0.001",
        max_price="100000.0",
    ),
    "XRPUSDT": InstrumentSpec(
        symbol="XRPUSDT",
        base_currency="XRP",
        price_increment="0.0001",
        quantity_increment="1",
        min_quantity="1",
        min_notional=5.0,
        min_price="0.0001",
        max_price="10000.0",
    ),
}


@dataclass(frozen=True, slots=True)
class LaggardDiagnostic:
    variant: str
    symbol: str
    side: str
    signal_time_ns: int
    leader_symbols: str
    leader_count: int
    external_target: float
    internal_boundary: float
    structural_stop: float
    signal_close: float
    signal_flow_imbalance: float
    signal_range_bps: float
    prior_range_median_bps: float
    signal_net_reward_risk: float
    signal_price_risk_fraction: float


@dataclass(frozen=True, slots=True)
class CandidateRow:
    symbol: str
    side: Side
    signal_index: int
    signal_time_ns: int
    leaders: tuple[str, ...]
    entry_reference: float
    stop: float
    target: float
    hold: float
    external_high: float
    external_low: float
    pulse_high: float
    pulse_low: float
    flow: float
    range_bps: float
    range_median_bps: float
    body_efficiency: float
    directional_close_location: float
    price_risk_fraction: float
    net_reward_risk: float


def _minute_end_ns(minute_start_ns: int) -> int:
    return int(minute_start_ns) + MINUTE_NS - 1


def _utc_day_id(ts_ns: int) -> int:
    return int(ts_ns) // DAY_NS


def aggregate_trade_minutes(
    records: Sequence[AggTradeDownload],
    *,
    start_ns: int,
    end_ns: int,
) -> pd.DataFrame:
    """Aggregate official trades into completed UTC-minute OHLC and flow."""

    rows: dict[int, list[float]] = {}
    for trade in iter_downloads(records):
        ts_ns = int(trade.ts_event_ns)
        if ts_ns < start_ns:
            continue
        if ts_ns >= end_ns:
            break
        minute_ns = (ts_ns // MINUTE_NS) * MINUTE_NS
        price = float(trade.price)
        quote = float(trade.quote_notional)
        signed = float(trade.signed_aggressive_quote)
        current = rows.get(minute_ns)
        if current is None:
            rows[minute_ns] = [
                price,
                price,
                price,
                price,
                float(trade.quantity),
                quote,
                signed,
                1.0,
            ]
        else:
            current[1] = max(current[1], price)
            current[2] = min(current[2], price)
            current[3] = price
            current[4] += float(trade.quantity)
            current[5] += quote
            current[6] += signed
            current[7] += 1.0

    frame = pd.DataFrame.from_dict(
        rows,
        orient="index",
        columns=(
            "open",
            "high",
            "low",
            "close",
            "base_volume",
            "quote_notional",
            "signed_quote_notional",
            "trade_count",
        ),
    ).sort_index()
    frame.index.name = "minute_start_ns"
    if frame.empty:
        raise RuntimeError("no completed minute bars were produced")
    if frame.index.has_duplicates:
        raise RuntimeError("duplicate minute rows")
    return frame


def add_causal_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Add only lagged structure/baseline fields to completed-minute rows."""

    result = frame.copy()
    prior_close = result["close"].shift(1)
    result["return_bps"] = (result["close"] / prior_close - 1.0) * 10_000.0
    result["range_bps"] = (
        (result["high"] - result["low"]) / prior_close * 10_000.0
    )
    result["flow_imbalance"] = (
        result["signed_quote_notional"] / result["quote_notional"]
    )
    full_range = result["high"] - result["low"]
    nonzero_range = full_range.where(full_range != 0.0)
    result["body_efficiency"] = (
        (result["close"] - result["open"]).abs() / nonzero_range
    ).fillna(0.0)
    result["close_location"] = (
        (result["close"] - result["low"]) / nonzero_range
    ).fillna(0.5)
    result["external_high"] = (
        result["high"]
        .rolling(EXTERNAL_LOOKBACK_MINUTES, min_periods=EXTERNAL_LOOKBACK_MINUTES)
        .max()
        .shift(1)
    )
    result["external_low"] = (
        result["low"]
        .rolling(EXTERNAL_LOOKBACK_MINUTES, min_periods=EXTERNAL_LOOKBACK_MINUTES)
        .min()
        .shift(1)
    )
    result["internal_high"] = (
        result["high"]
        .rolling(INTERNAL_LOOKBACK_MINUTES, min_periods=INTERNAL_LOOKBACK_MINUTES)
        .max()
        .shift(1)
    )
    result["internal_low"] = (
        result["low"]
        .rolling(INTERNAL_LOOKBACK_MINUTES, min_periods=INTERNAL_LOOKBACK_MINUTES)
        .min()
        .shift(1)
    )
    result["range_median_bps"] = (
        result["range_bps"]
        .rolling(
            DISPLACEMENT_LOOKBACK_MINUTES,
            min_periods=DISPLACEMENT_LOOKBACK_MINUTES,
        )
        .median()
        .shift(1)
    )
    return result


def _leader_side(row: pd.Series) -> Side | None:
    required = (
        "external_high",
        "external_low",
        "range_median_bps",
        "return_bps",
        "range_bps",
        "flow_imbalance",
    )
    if any(pd.isna(row[name]) for name in required):
        return None
    expanded = float(row["range_bps"]) >= float(row["range_median_bps"])
    if not expanded:
        return None
    long = (
        float(row["close"]) > float(row["external_high"])
        and float(row["return_bps"]) > 0.0
        and float(row["flow_imbalance"]) > 0.0
        and float(row["close_location"]) >= 0.5
    )
    short = (
        float(row["close"]) < float(row["external_low"])
        and float(row["return_bps"]) < 0.0
        and float(row["flow_imbalance"]) < 0.0
        and float(row["close_location"]) <= 0.5
    )
    if long == short:
        return None
    return Side.LONG if long else Side.SHORT


def _execution_geometry(
    *,
    side: Side,
    entry: float,
    stop: float,
    target: float,
    cost_fraction_per_side: float,
) -> tuple[float, float] | None:
    geometry_ok = stop < entry < target if side is Side.LONG else target < entry < stop
    if not geometry_ok:
        return None
    price_risk = abs(entry - stop)
    planned_loss = (
        price_risk
        + entry * cost_fraction_per_side
        + stop * cost_fraction_per_side
    )
    planned_gain = (
        abs(target - entry)
        - entry * cost_fraction_per_side
        - target * cost_fraction_per_side
    )
    if planned_loss <= 0.0 or planned_gain <= 0.0:
        return None
    return price_risk / planned_loss, planned_gain / planned_loss


def _laggard_candidate(
    *,
    symbol: str,
    side: Side,
    row: pd.Series,
    signal_index: int,
    signal_time_ns: int,
    leaders: tuple[str, ...],
    cost_fraction_per_side: float,
    minimum_price_risk_fraction: float,
    minimum_net_reward_risk: float,
) -> CandidateRow | None:
    required = (
        "external_high",
        "external_low",
        "internal_high",
        "internal_low",
        "range_median_bps",
        "return_bps",
        "range_bps",
        "flow_imbalance",
    )
    if any(pd.isna(row[name]) for name in required):
        return None
    if float(row["range_bps"]) < float(row["range_median_bps"]):
        return None

    entry = float(row["close"])
    external_high = float(row["external_high"])
    external_low = float(row["external_low"])
    internal_high = float(row["internal_high"])
    internal_low = float(row["internal_low"])
    if side is Side.LONG:
        valid = (
            float(row["high"]) < external_high
            and entry > internal_high
            and float(row["return_bps"]) > 0.0
            and float(row["flow_imbalance"]) > 0.0
            and float(row["close_location"]) >= 0.5
        )
        stop = min(float(row["low"]), internal_low) * (1.0 - STOP_BUFFER_FRACTION)
        target = external_high
        hold = internal_high
        directional_close = float(row["close_location"])
    else:
        valid = (
            float(row["low"]) > external_low
            and entry < internal_low
            and float(row["return_bps"]) < 0.0
            and float(row["flow_imbalance"]) < 0.0
            and float(row["close_location"]) <= 0.5
        )
        stop = max(float(row["high"]), internal_high) * (1.0 + STOP_BUFFER_FRACTION)
        target = external_low
        hold = internal_low
        directional_close = 1.0 - float(row["close_location"])
    if not valid:
        return None

    geometry = _execution_geometry(
        side=side,
        entry=entry,
        stop=stop,
        target=target,
        cost_fraction_per_side=cost_fraction_per_side,
    )
    if geometry is None:
        return None
    price_fraction, net_rr = geometry
    if (
        price_fraction < minimum_price_risk_fraction
        or net_rr < minimum_net_reward_risk
    ):
        return None
    return CandidateRow(
        symbol=symbol,
        side=side,
        signal_index=signal_index,
        signal_time_ns=signal_time_ns,
        leaders=leaders,
        entry_reference=entry,
        stop=stop,
        target=target,
        hold=hold,
        external_high=external_high,
        external_low=external_low,
        pulse_high=float(row["high"]),
        pulse_low=float(row["low"]),
        flow=float(row["flow_imbalance"]),
        range_bps=float(row["range_bps"]),
        range_median_bps=float(row["range_median_bps"]),
        body_efficiency=float(row["body_efficiency"]),
        directional_close_location=directional_close,
        price_risk_fraction=price_fraction,
        net_reward_risk=net_rr,
    )


def _target_key(candidate: CandidateRow) -> tuple[str, str, int]:
    spec = INSTRUMENT_SPECS[candidate.symbol]
    tick = float(spec.price_increment)
    return candidate.symbol, candidate.side.value, int(round(candidate.target / tick))


def _to_plan(candidate: CandidateRow, *, variant: str) -> SymbolScenarioPlan:
    reason = (
        "TWO_PEER_CROSS_ASSET_ACCEPTANCE_LAGGARD_TO_HOURLY_LIQUIDITY"
        if variant == "primary"
        else "ONE_PEER_CROSS_ASSET_ACCEPTANCE_LAGGARD_TO_HOURLY_LIQUIDITY"
    )
    plan = ScenarioPlan(
        scenario_id=(
            f"v39:{variant}:{candidate.signal_time_ns}:{candidate.symbol}:"
            f"{candidate.side.value.lower()}"
        ),
        response="CONTINUATION",
        side=candidate.side,
        signal_bar_index=candidate.signal_index,
        signal_time_ns=candidate.signal_time_ns,
        stop_price=candidate.stop,
        target_price=candidate.target,
        confirmation_hold_price=candidate.hold,
        structure_high=max(candidate.external_high, candidate.pulse_high),
        structure_low=min(candidate.external_low, candidate.pulse_low),
        structure_midpoint=0.5 * (candidate.external_high + candidate.external_low),
        pulse_high=candidate.pulse_high,
        pulse_low=candidate.pulse_low,
        pulse_flow_score=candidate.flow,
        pulse_move_atr=(
            abs(candidate.range_bps) / candidate.range_median_bps
            if candidate.range_median_bps > 0.0
            else 0.0
        ),
        pulse_path_efficiency=candidate.body_efficiency,
        pulse_close_location=candidate.directional_close_location,
        reason_code=reason,
    )
    return SymbolScenarioPlan(symbol=candidate.symbol, plan=plan)


def generate_symbol_plans(
    featured_by_symbol: Mapping[str, pd.DataFrame],
    *,
    variant: str,
    evaluation_start_ns: int,
    evaluation_end_ns: int,
    cost_fraction_per_side: float,
    minimum_price_risk_fraction: float,
    minimum_net_reward_risk: float,
) -> tuple[list[SymbolScenarioPlan], list[LaggardDiagnostic], Counter[str]]:
    """Generate one cross-sectionally routed immutable plan per completed minute."""

    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant}")
    if set(featured_by_symbol) != set(SYMBOLS):
        raise ValueError("all four allowed symbols are required")
    common_index: set[int] | None = None
    for frame in featured_by_symbol.values():
        values = {int(value) for value in frame.index}
        common_index = values if common_index is None else common_index & values
    minute_starts = sorted(common_index or ())
    plans: list[SymbolScenarioPlan] = []
    diagnostics: list[LaggardDiagnostic] = []
    counts: Counter[str] = Counter()
    emitted_targets: set[tuple[str, str, int]] = set()
    required_leaders = 2 if variant == "primary" else 1

    for signal_index, minute_start_ns in enumerate(minute_starts):
        signal_time_ns = _minute_end_ns(minute_start_ns)
        if not evaluation_start_ns <= signal_time_ns < evaluation_end_ns:
            continue
        rows = {
            symbol: featured_by_symbol[symbol].loc[minute_start_ns]
            for symbol in SYMBOLS
        }
        leader_sides = {symbol: _leader_side(row) for symbol, row in rows.items()}
        counts["joint_completed_minutes"] += 1
        for side in (Side.LONG, Side.SHORT):
            all_leaders = tuple(
                symbol for symbol in SYMBOLS if leader_sides[symbol] is side
            )
            if all_leaders:
                counts[f"{side.value.lower()}_leader_minutes"] += 1
            candidates: list[CandidateRow] = []
            for symbol in SYMBOLS:
                peer_leaders = tuple(item for item in all_leaders if item != symbol)
                if len(peer_leaders) < required_leaders:
                    continue
                if not CORE_LEADERS.intersection(peer_leaders):
                    continue
                candidate = _laggard_candidate(
                    symbol=symbol,
                    side=side,
                    row=rows[symbol],
                    signal_index=signal_index,
                    signal_time_ns=signal_time_ns,
                    leaders=peer_leaders,
                    cost_fraction_per_side=cost_fraction_per_side,
                    minimum_price_risk_fraction=minimum_price_risk_fraction,
                    minimum_net_reward_risk=minimum_net_reward_risk,
                )
                if candidate is None:
                    continue
                if _target_key(candidate) in emitted_targets:
                    counts["duplicate_active_target_rejected"] += 1
                    continue
                candidates.append(candidate)
            if not candidates:
                continue
            chosen = sorted(
                candidates,
                key=lambda item: (
                    -len(item.leaders),
                    -item.net_reward_risk,
                    -item.price_risk_fraction,
                    item.symbol,
                ),
            )[0]
            emitted_targets.add(_target_key(chosen))
            plans.append(_to_plan(chosen, variant=variant))
            diagnostics.append(
                LaggardDiagnostic(
                    variant=variant,
                    symbol=chosen.symbol,
                    side=chosen.side.value,
                    signal_time_ns=chosen.signal_time_ns,
                    leader_symbols="|".join(chosen.leaders),
                    leader_count=len(chosen.leaders),
                    external_target=chosen.target,
                    internal_boundary=chosen.hold,
                    structural_stop=chosen.stop,
                    signal_close=chosen.entry_reference,
                    signal_flow_imbalance=chosen.flow,
                    signal_range_bps=chosen.range_bps,
                    prior_range_median_bps=chosen.range_median_bps,
                    signal_net_reward_risk=chosen.net_reward_risk,
                    signal_price_risk_fraction=chosen.price_risk_fraction,
                ),
            )
            counts["plans_emitted"] += 1
            counts[f"{chosen.symbol}_plans"] += 1
            counts[f"{chosen.side.value.lower()}_plans"] += 1
    return plans, diagnostics, counts


def execution_trade_windows(
    records: Sequence[AggTradeDownload],
    *,
    plans: Sequence[SymbolScenarioPlan],
    start_ns: int,
    end_ns: int,
    maximum_hold_ns: int,
) -> tuple[list[AggTrade], list[tuple[int, int]]]:
    """Keep outcome-independent plan windows plus daily NAV marker ticks."""

    before_ns = MINUTE_NS
    after_ns = 2 * MINUTE_NS
    intervals = sorted(
        (
            max(start_ns, int(item.plan.signal_time_ns) - before_ns),
            min(
                end_ns - 1,
                int(item.plan.signal_time_ns) + maximum_hold_ns + after_ns,
            ),
        )
        for item in plans
        if start_ns <= int(item.plan.signal_time_ns) < end_ns
    )
    merged: list[tuple[int, int]] = []
    for left, right in intervals:
        if right < left:
            continue
        if not merged or left > merged[-1][1] + 1:
            merged.append((left, right))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))

    result: list[AggTrade] = []
    interval_index = 0
    flush = 0
    marker_days: set[int] = set()
    for trade in iter_downloads(records):
        ts_ns = int(trade.ts_event_ns)
        if ts_ns < start_ns:
            continue
        if ts_ns >= end_ns:
            if flush < FLUSH_TICKS:
                result.append(trade)
                flush += 1
                continue
            break
        day_id = _utc_day_id(ts_ns)
        if day_id not in marker_days:
            marker_days.add(day_id)
            result.append(trade)
            continue
        while interval_index < len(merged) and ts_ns > merged[interval_index][1]:
            interval_index += 1
        if (
            interval_index < len(merged)
            and merged[interval_index][0] <= ts_ns <= merged[interval_index][1]
        ):
            result.append(trade)

    expected_days = (end_ns - start_ns) // DAY_NS
    if len(marker_days) != expected_days:
        raise RuntimeError(
            f"expected {expected_days} daily markers, found {len(marker_days)}",
        )
    if flush != FLUSH_TICKS:
        raise RuntimeError(f"expected {FLUSH_TICKS} flush ticks, found {flush}")
    return result, merged


def _joint_gap_count(
    frames: Mapping[str, pd.DataFrame],
    *,
    start_ns: int,
    end_ns: int,
) -> int:
    expected = set(range(start_ns, end_ns, MINUTE_NS))
    common: set[int] | None = None
    for frame in frames.values():
        observed = {int(value) for value in frame.index if start_ns <= int(value) < end_ns}
        common = observed if common is None else common & observed
    return len(expected - (common or set()))


def run(args: argparse.Namespace) -> int:
    if args.variant not in VARIANTS:
        raise ValueError(f"variant must be one of {VARIANTS}")
    execution = load_execution(args.execution_config)
    evaluation_start = parse_utc_date(args.week)
    evaluation_end = evaluation_start + timedelta(days=7)
    context_start = evaluation_start - timedelta(minutes=CONTEXT_MINUTES)
    download_end = evaluation_end + timedelta(minutes=1)
    context_start_ns = int(pd.Timestamp(context_start).as_unit("ns").value)
    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)
    download_end_ns = int(pd.Timestamp(download_end).as_unit("ns").value)

    records_by_symbol: dict[str, list[AggTradeDownload]] = {}
    raw_frames: dict[str, pd.DataFrame] = {}
    featured_frames: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOLS:
        records = download_aggtrade_days(
            symbol=symbol,
            start=context_start,
            end=download_end,
            cache_dir=args.cache,
            workers=args.workers,
        )
        records_by_symbol[symbol] = records
        raw = aggregate_trade_minutes(
            records,
            start_ns=context_start_ns,
            end_ns=download_end_ns,
        )
        raw_frames[symbol] = raw
        featured_frames[symbol] = add_causal_features(raw)

    plans, diagnostics, counts = generate_symbol_plans(
        featured_frames,
        variant=args.variant,
        evaluation_start_ns=start_ns,
        evaluation_end_ns=end_ns,
        cost_fraction_per_side=execution.all_in_cost_bps_per_side / 10_000.0,
        minimum_price_risk_fraction=execution.minimum_price_risk_fraction,
        minimum_net_reward_risk=execution.minimum_net_reward_risk,
    )
    trades_by_symbol: dict[str, list[AggTrade]] = {}
    windows_by_symbol: dict[str, list[tuple[int, int]]] = {}
    for symbol in SYMBOLS:
        symbol_plans = [item for item in plans if item.symbol == symbol]
        selected, windows = execution_trade_windows(
            records_by_symbol[symbol],
            plans=symbol_plans,
            start_ns=start_ns,
            end_ns=end_ns,
            maximum_hold_ns=MAXIMUM_HOLD_NS,
        )
        trades_by_symbol[symbol] = selected
        windows_by_symbol[symbol] = windows

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    evidence = run_nautilus_multi_tick_plan_backtest(
        label=(
            f"v39-{args.variant}-{evaluation_start.date().isoformat()}-7d-"
            "single-account-four-symbol"
        ),
        trades_by_symbol=trades_by_symbol,
        plans=plans,
        instrument_specs=INSTRUMENT_SPECS,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        execution=execution,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
        output_dir=output / "nautilus",
    )

    pd.DataFrame(asdict(row) for row in diagnostics).to_csv(
        output / "laggard_diagnostics.csv",
        index=False,
    )
    pd.DataFrame(
        {
            "symbol": item.symbol,
            **asdict(item.plan),
            "side": item.plan.side.value,
        }
        for item in plans
    ).to_csv(output / "scenario_plans.csv", index=False)

    payload: dict[str, Any] = {
        "candidate": "cross-asset consensus laggard transfer to hourly liquidity",
        "version": 39,
        "variant": args.variant,
        "authoritative_backtest": True,
        "execution_engine": "NautilusTrader",
        "execution_data_type": "official Binance Vision USD-M aggTrades as TradeTick",
        "custom_fill_simulator": False,
        "custom_pnl_or_nav_ledger": False,
        "single_shared_account": True,
        "one_global_pending_entry_or_position": True,
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "frozen_random_seed": 3901,
        "scenario_contract": (
            "peer hourly-liquidity acceptance -> laggard internal displacement "
            "while its hourly target remains unconsumed -> first later own-symbol "
            "TradeTick -> local-path invalidation -> frozen hourly target"
        ),
        "primary_variable": (
            "two independent peer leaders including BTC or ETH"
            if args.variant == "primary"
            else "one peer leader including BTC or ETH"
        ),
        "parameters": {
            "external_lookback_minutes": EXTERNAL_LOOKBACK_MINUTES,
            "internal_lookback_minutes": INTERNAL_LOOKBACK_MINUTES,
            "displacement_baseline_minutes": DISPLACEMENT_LOOKBACK_MINUTES,
            "maximum_hold_hours": MAXIMUM_HOLD_HOURS,
            "stop_buffer_bps": STOP_BUFFER_FRACTION * 10_000.0,
        },
        "counts": dict(counts),
        "selected_plan_count": len(plans),
        "selected_symbol_counts": dict(Counter(item.symbol for item in plans)),
        "selected_side_counts": dict(Counter(item.plan.side.value for item in plans)),
        "joint_evaluation_minute_gaps": _joint_gap_count(
            raw_frames,
            start_ns=start_ns,
            end_ns=end_ns,
        ),
        "official_execution_trade_ticks_by_symbol": {
            symbol: len(trades_by_symbol[symbol]) for symbol in SYMBOLS
        },
        "execution_tick_windows_by_symbol": {
            symbol: [list(item) for item in windows_by_symbol[symbol]]
            for symbol in SYMBOLS
        },
        "risk_fraction": execution.risk_fraction,
        "all_in_cost_bps_per_side": execution.all_in_cost_bps_per_side,
        "metrics": evidence.metrics,
        "downloads": {
            symbol: [record.to_dict() for record in records_by_symbol[symbol]]
            for symbol in SYMBOLS
        },
        "long_evaluation_run": False,
    }
    atomic_json(output / "cross_asset_laggard_v39_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--variant", required=True, choices=VARIANTS)
    parser.add_argument(
        "--execution-config",
        type=Path,
        default=HERE / "nautilus_execution.json",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-v39",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-v39",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))

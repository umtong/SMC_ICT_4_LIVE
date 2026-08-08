"""Causal session-opening state router for day-trading candidate-08.

A complete previous UTC day defines value (VWAP +/- one weighted sigma).  A complete thirty-minute
Asia/London/New-York initial balance then classifies the opening auction before any entry trigger:
initiative acceptance outside previous value, responsive rejection back into previous value, or
unresolved/no-trade.  A later completed M5 displacement triggers the first contiguous 10-second
execution observation.  Ten-second data is execution-only and never selects direction or state.
"""
from __future__ import annotations

from collections import Counter
from math import isfinite
from typing import Any, Mapping

import numpy as np
import pandas as pd

from aggtrade_acceptance_signals import causal_stop_slippage_reserve_series
from opening_initial_balance_failed_auction_signals_v1 import (
    InitialBalance,
    OpeningAuctionConfig,
    build_initial_balances,
)
from quote_resiliency_signals import (
    QuoteResiliencyLogicEvent,
    QuoteResiliencySignal,
    QuoteResiliencySignalBundle,
)
from range_fvg_logic import ExternalLevel, FiveMinuteBar
from session_raid_reversal_signals_v2 import contiguous_first_execution_position_after
from session_value_migration_signals_v1 import build_completed_daily_value_profiles

SIGNAL_REVISION = "OPENING_TYPE_STATE_ROUTER_SIGNALS_V1"
INITIATIVE_FAMILY = "OPENING_TYPE_INITIATIVE_CONTINUATION"
RESPONSIVE_FAMILY = "OPENING_TYPE_RESPONSIVE_REVERSAL"
FIVE_NS = 300_000_000_000
DAY_NS = 86_400_000_000_000


def _start(bar: FiveMinuteBar) -> int:
    return int(bar.ts_event_ns) // FIVE_NS * FIVE_NS


def _ib_vwap(bars: tuple[FiveMinuteBar, ...], balance: InitialBalance) -> float | None:
    rows = bars[balance.first_five_position : balance.last_five_position + 1]
    if len(rows) != 6:
        return None
    volume = sum(float(row.volume) for row in rows)
    if volume <= 0 or any(float(row.volume) <= 0 for row in rows):
        return None
    return sum(
        float(row.volume) * (float(row.high) + float(row.low) + float(row.close)) / 3.0
        for row in rows
    ) / volume


def classify_opening_state(
    bars: tuple[FiveMinuteBar, ...], balance: InitialBalance, profile: Any
) -> tuple[str, int, float] | None:
    rows = bars[balance.first_five_position : balance.last_five_position + 1]
    if len(rows) != 6:
        return None
    opened, closed, vwap = float(rows[0].open), float(rows[-1].close), _ib_vwap(bars, balance)
    if vwap is None or not all(isfinite(x) for x in (opened, closed, vwap)):
        return None
    lo, hi = float(profile.value_low), float(profile.value_high)
    if opened > hi:
        if closed > hi and vwap > hi:
            return ("OPEN_DRIVE" if balance.low > hi else "OPEN_TEST_DRIVE", 1, vwap)
        if lo < closed < hi and lo <= vwap <= hi:
            return ("OPEN_REJECTION_REVERSE", -1, vwap)
    elif opened < lo:
        if closed < lo and vwap < lo:
            return ("OPEN_DRIVE" if balance.high < lo else "OPEN_TEST_DRIVE", -1, vwap)
        if lo < closed < hi and lo <= vwap <= hi:
            return ("OPEN_REJECTION_REVERSE", 1, vwap)
    return None


def _prior_medians(bars: tuple[FiveMinuteBar, ...], lookback: int) -> tuple[np.ndarray, np.ndarray]:
    body = pd.Series([abs(float(x.close) - float(x.open)) for x in bars])
    span = pd.Series([float(x.high) - float(x.low) for x in bars])
    minimum = min(6, lookback)
    return (
        body.shift(1).rolling(lookback, min_periods=minimum).median().to_numpy(),
        span.shift(1).rolling(lookback, min_periods=minimum).median().to_numpy(),
    )


def _cost(direction: int, entry: float, stop: float, target: float, fee: float, tick: float, reserve: float):
    if not (stop < entry < target if direction > 0 else target < entry < stop):
        return None
    loss = abs(entry - stop) + fee * (entry + stop) + tick + max(tick, reserve)
    gain = abs(target - entry) - fee * (entry + target) - 2.0 * tick
    return None if loss <= 0 or gain <= 0 else (loss, gain, gain / loss)


def build_opening_type_state_router_signals(
    *, data: pd.DataFrame, context_times: np.ndarray, context_bars: tuple[FiveMinuteBar, ...],
    snapshots: tuple[tuple[ExternalLevel, ...], ...], symbol: str, instrument_id: str,
    tick: float, fee_rate: float, minimum_net_reward_risk: float,
    router_config: Mapping[str, Any], require_retest_contraction: bool = True,
) -> QuoteResiliencySignalBundle:
    del context_times, snapshots, require_retest_contraction
    if not isinstance(data.index, pd.DatetimeIndex) or data.index.tz is None:
        raise TypeError("execution index must be timezone-aware")
    lookback = int(router_config.get("displacement_lookback", 12))
    close_location = float(router_config.get("displacement_close_location", 2 / 3))
    excursion_atr = float(router_config.get("breakout_excursion_atr", 0.05))
    stop_buffer = float(router_config.get("stop_buffer_atr", 0.05))
    minimum_stop = float(router_config.get("minimum_stop_distance_atr", 0.10))
    opening = OpeningAuctionConfig(
        initial_balance_minutes=30,
        opportunity_minutes_after_balance=int(router_config.get("opportunity_minutes_after_balance", 180)),
        sweep_excursion_atr=excursion_atr,
        displacement_lookback=lookback,
        displacement_close_location=close_location,
        stop_buffer_atr=stop_buffer,
        minimum_stop_distance_atr=minimum_stop,
    )
    opening.validate()
    counts: Counter[str] = Counter()
    rejected: list[dict[str, Any]] = []
    grouped: dict[int, list[QuoteResiliencySignal]] = {}
    balances = build_initial_balances(context_bars, opening, counts)
    profiles = build_completed_daily_value_profiles(context_bars, counts)
    body_med, range_med = _prior_medians(context_bars, lookback)
    times = data.index.as_unit("ns").asi8
    reserves = causal_stop_slippage_reserve_series(data, tick=tick)

    for serial, balance in enumerate(balances, 1):
        profile = profiles.get(balance.start_time_ns // DAY_NS * DAY_NS - DAY_NS)
        if profile is None or int(profile.observed_time_ns) >= balance.start_time_ns:
            counts["NO_CAUSAL_PREVIOUS_DAY_VALUE_PROFILE"] += 1
            continue
        state = classify_opening_state(context_bars, balance, profile)
        if state is None:
            counts["UNRESOLVED_OPENING_AUCTION_NO_TRADE"] += 1
            continue
        opening_type, direction, ib_vwap = state
        counts[f"OPENING_TYPE_{opening_type}"] += 1
        scenario = f"opening-type-{symbol.lower()}-{balance.session_name.lower()}-{balance.local_date}-{serial:05d}"
        initiative = opening_type != "OPEN_REJECTION_REVERSE"
        width = float(balance.high) - float(balance.low)
        target = (
            (float(balance.high) + width if direction > 0 else float(balance.low) - width)
            if initiative else
            (float(profile.value_high) if direction > 0 else float(profile.value_low))
        )
        target_source = "INITIAL_BALANCE_EXTENSION" if initiative else "PREVIOUS_DAY_VALUE_OPPOSITE_EDGE"
        route = [i for i, bar in enumerate(context_bars) if balance.end_time_ns <= _start(bar) < balance.opportunity_end_ns]
        emitted = terminal = False
        for pos in route:
            bar = context_bars[pos]
            if (float(bar.high) >= target if direction > 0 else float(bar.low) <= target):
                counts["OBJECTIVE_CONSUMED_BEFORE_TRIGGER"] += 1; terminal = True; break
            invalid = (
                (float(bar.close) < balance.high if direction > 0 else float(bar.close) > balance.low)
                if initiative else
                (float(bar.close) <= profile.value_low if direction > 0 else float(bar.close) >= profile.value_high)
            )
            if invalid:
                counts["OPENING_STATE_INVALIDATED_BEFORE_TRIGGER"] += 1; terminal = True; break
            body, span = abs(float(bar.close)-float(bar.open)), float(bar.high)-float(bar.low)
            if not all(isfinite(x) for x in (bar.atr, body_med[pos], range_med[pos])) or span <= 0:
                continue
            located = ((float(bar.close)-float(bar.low))/span >= close_location if direction > 0
                       else (float(bar.close)-float(bar.low))/span <= 1-close_location)
            threshold = (
                (float(balance.high)+excursion_atr*float(bar.atr) if direction > 0 else float(balance.low)-excursion_atr*float(bar.atr))
                if initiative else (float(balance.high)+float(balance.low))/2.0
            )
            crossed = float(bar.close) >= threshold if direction > 0 else float(bar.close) <= threshold
            if not (direction*(float(bar.close)-float(bar.open)) > 0 and located and crossed
                    and body >= body_med[pos] and span >= range_med[pos]):
                continue
            exec_pos = contiguous_first_execution_position_after(times, int(bar.ts_event_ns))
            if exec_pos is None:
                counts["NO_CONTIGUOUS_NEXT_TEN_SECOND_EXECUTION_BUCKET"] += 1; terminal = True; break
            row, entry = data.iloc[exec_pos], float(data.iloc[exec_pos]["close"])
            if not isfinite(entry) or entry <= 0:
                counts["UNOBSERVABLE_EXECUTION_PRICE"] += 1; terminal = True; break
            buffer, minimum = stop_buffer*float(bar.atr), minimum_stop*float(bar.atr)
            if initiative:
                reference = min(float(balance.high), float(bar.low)) if direction > 0 else max(float(balance.low), float(bar.high))
            else:
                reference = float(balance.low) if direction > 0 else float(balance.high)
            stop = (min(reference-buffer, entry-minimum) if direction > 0
                    else max(reference+buffer, entry+minimum))
            geometry = _cost(direction, entry, stop, target, fee_rate, tick, float(reserves.iloc[exec_pos]))
            if geometry is None or geometry[2] < minimum_net_reward_risk:
                counts["INSUFFICIENT_COST_AFTER_OPENING_TYPE_GEOMETRY"] += 1; terminal = True; break
            loss, gain, rr = geometry
            family = INITIATIVE_FAMILY if initiative else RESPONSIVE_FAMILY
            common = {"session": balance.session_name, "opening_type": opening_type,
                      "ib_high": float(balance.high), "ib_low": float(balance.low), "ib_vwap": float(ib_vwap),
                      "previous_value_low": float(profile.value_low), "previous_value_high": float(profile.value_high),
                      "ten_second_alpha_inputs": False, "signal_revision": SIGNAL_REVISION}
            events = tuple(
                QuoteResiliencyLogicEvent(
                    scenario, symbol, instrument_id, event, when, when, previous, nxt, reason, price, common
                )
                for event, when, previous, nxt, reason, price in (
                    ("OPENING_TYPE_CLASSIFIED", int(context_bars[balance.last_five_position].ts_event_ns), "IDLE", "OPENING_STATE_CLASSIFIED", opening_type, float(context_bars[balance.first_five_position].open)),
                    ("POST_IB_DISPLACEMENT_CONFIRMED", int(bar.ts_event_ns), "OPENING_STATE_CLASSIFIED", "TRIGGER_CONFIRMED", f"M5_DISPLACEMENT_{'LONG' if direction>0 else 'SHORT'}", float(bar.close)),
                    ("NEXT_EXECUTION_BUCKET_OBSERVED", int(times[exec_pos]), "TRIGGER_CONFIRMED", "CONFIRMED", "CONTIGUOUS_EXECUTION_OBSERVATION", entry),
                )
            )
            signal = QuoteResiliencySignal(
                scenario, family, symbol, instrument_id, direction, exec_pos, int(times[exec_pos]),
                f"ib-{balance.session_name.lower()}-{balance.local_date}", "SESSION_INITIAL_BALANCE",
                float(balance.high if direction>0 else balance.low), f"target-{scenario}", target_source,
                float(target), entry, float(stop), float(reference), "OPENING_STATE_STRUCTURE", float(bar.atr),
                float(reserves.iloc[exec_pos]), float(loss), float(gain), float(rr),
                int(context_bars[balance.last_five_position].ts_event_ns), int(bar.ts_event_ns), None,
                events, {**common, "trigger_position": pos, "execution_position": exec_pos, "target_contract": target_source},
            )
            grouped.setdefault(int(times[exec_pos]), []).append(signal)
            counts["TRADEABLE_OPENING_TYPE_SIGNAL"] += 1; counts[f"TRADEABLE_{opening_type}"] += 1
            emitted = True; break
        if not emitted and not terminal:
            counts["NO_POST_IB_TRIGGER_BEFORE_ROUTE_END"] += 1
    immutable = {t: tuple(sorted(v, key=lambda x: (x.net_reward_risk, x.scenario_id), reverse=True)) for t,v in sorted(grouped.items())}
    counts["SIGNAL"] = sum(map(len, immutable.values())); counts["SIGNAL_TIMES"] = len(immutable)
    return QuoteResiliencySignalBundle(immutable, dict(sorted(counts.items())), tuple(rejected))

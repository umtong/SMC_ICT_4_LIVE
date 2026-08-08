"""Opening-type first-pullback day-trading state router V2.

The previous UTC day's completed value distribution and a completed thirty-minute
Asia/London/New-York initial balance classify the opening auction before any trade.
Unlike V1, an initiative state is invalidated only by re-entry into previous value,
not by a rotation inside the initial balance.  Entry is reserved for a new auction
leg: a completed post-IB pullback must hold the state-defining value edge and a
separate completed M5 displacement must then break that pullback structure.
Ten-second data is execution-only.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
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
from opening_type_state_router_signals_v1 import classify_opening_state
from quote_resiliency_signals import (
    QuoteResiliencyLogicEvent,
    QuoteResiliencySignal,
    QuoteResiliencySignalBundle,
)
from range_fvg_logic import ExternalLevel, FiveMinuteBar
from session_raid_reversal_execution_v2 import apply_bar_market_entry_cost_contract
from session_raid_reversal_signals_v2 import contiguous_first_execution_position_after
from session_value_migration_signals_v1 import build_completed_daily_value_profiles

SIGNAL_REVISION = "OPENING_TYPE_FIRST_PULLBACK_NEW_LEG_SIGNALS_V2"
IMPLEMENTATION_REVISION = "OPENING_TYPE_FIRST_PULLBACK_STATE_ROUTER_V2"
INITIATIVE_FAMILY = "OPENING_TYPE_INITIATIVE_FIRST_PULLBACK_CONTINUATION"
RESPONSIVE_FAMILY = "OPENING_TYPE_RESPONSIVE_FIRST_PULLBACK_REVERSAL"
FIVE_NS = 300_000_000_000
DAY_NS = 86_400_000_000_000


@dataclass(slots=True)
class Pullback:
    position: int
    observed_time_ns: int
    high: float
    low: float
    atr: float


def _start(bar: FiveMinuteBar) -> int:
    return int(bar.ts_event_ns) // FIVE_NS * FIVE_NS


def _prior_medians(
    bars: tuple[FiveMinuteBar, ...], lookback: int
) -> tuple[np.ndarray, np.ndarray]:
    body = pd.Series([abs(float(row.close) - float(row.open)) for row in bars])
    span = pd.Series([float(row.high) - float(row.low) for row in bars])
    minimum = min(6, lookback)
    return (
        body.shift(1).rolling(lookback, min_periods=minimum).median().to_numpy(),
        span.shift(1).rolling(lookback, min_periods=minimum).median().to_numpy(),
    )


def _target_consumed(direction: int, bar: FiveMinuteBar, target: float) -> bool:
    return float(bar.high) >= target if direction > 0 else float(bar.low) <= target


def _state_invalidated(
    *, direction: int, initiative: bool, close: float, value_low: float, value_high: float
) -> bool:
    if initiative:
        return close <= value_high if direction > 0 else close >= value_low
    return close <= value_low if direction > 0 else close >= value_high


def _pullback_holds(
    *, direction: int, initiative: bool, bar: FiveMinuteBar,
    balance: InitialBalance, value_low: float, value_high: float,
) -> bool:
    close = float(bar.close)
    if initiative:
        if direction > 0:
            return float(bar.low) <= float(balance.high) and close > value_high
        return float(bar.high) >= float(balance.low) and close < value_low
    if direction > 0:
        return float(bar.low) <= value_low and close > value_low
    return float(bar.high) >= value_high and close < value_high


def _new_leg_confirms(
    *, direction: int, initiative: bool, bar: FiveMinuteBar, pullback: Pullback,
    balance: InitialBalance, body_median: float, range_median: float,
    close_location: float,
) -> bool:
    values = (float(bar.atr), body_median, range_median)
    if not all(isfinite(value) for value in values):
        return False
    span = float(bar.high) - float(bar.low)
    body = abs(float(bar.close) - float(bar.open))
    if span <= 0.0 or body < body_median or span < range_median:
        return False
    location = (float(bar.close) - float(bar.low)) / span
    located = location >= close_location if direction > 0 else location <= 1.0 - close_location
    directional = direction * (float(bar.close) - float(bar.open)) > 0.0
    if initiative:
        structure = max(float(balance.high), pullback.high) if direction > 0 else min(float(balance.low), pullback.low)
    else:
        midpoint = (float(balance.high) + float(balance.low)) / 2.0
        structure = max(midpoint, pullback.high) if direction > 0 else min(midpoint, pullback.low)
    crossed = float(bar.close) > structure if direction > 0 else float(bar.close) < structure
    return directional and located and crossed


def _raw_cost(
    *, direction: int, entry: float, stop: float, target: float,
    fee_rate: float, tick: float, stop_slippage_reserve: float,
) -> tuple[float, float, float] | None:
    if not (stop < entry < target if direction > 0 else target < entry < stop):
        return None
    loss = abs(entry - stop) + fee_rate * (entry + stop) + tick + max(tick, stop_slippage_reserve)
    gain = abs(target - entry) - fee_rate * (entry + target) - 2.0 * tick
    if loss <= 0.0 or gain <= 0.0:
        return None
    return loss, gain, gain / loss


def reprice_bundle_for_bar_market_preserving_events(
    bundle: QuoteResiliencySignalBundle, *, tick: float, minimum_net_reward_risk: float
) -> QuoteResiliencySignalBundle:
    """Reserve the verified two-tick bar entry without rewriting a four-event state ledger."""
    diagnostics: Counter[str] = Counter(bundle.diagnostics)
    rejected = list(bundle.rejected_scenarios)
    grouped: dict[int, list[QuoteResiliencySignal]] = {}
    accepted = 0
    for timestamp, signals in bundle.signals_by_time_ns.items():
        for signal in signals:
            geometry = apply_bar_market_entry_cost_contract(
                {
                    "expected_loss_per_unit": float(signal.expected_loss_per_unit),
                    "expected_gain_per_unit": float(signal.expected_gain_per_unit),
                    "net_reward_risk": float(signal.net_reward_risk),
                    "entry_slippage_reserve_per_unit": tick,
                },
                tick=tick,
            )
            if geometry is None or float(geometry["net_reward_risk"]) < minimum_net_reward_risk:
                diagnostics["V2_INSUFFICIENT_COST_AFTER_BAR_MARKET_ENTRY"] += 1
                rejected.append(
                    {
                        "scenario_id": signal.scenario_id,
                        "scenario_family": signal.scenario_family,
                        "reason": "V2_INSUFFICIENT_COST_AFTER_BAR_MARKET_ENTRY",
                        "signal_time_ns": int(signal.signal_time_ns),
                    }
                )
                continue
            details = dict(signal.details)
            details.update(
                {
                    "execution_risk_revision": "BAR_MARKET_TWO_TICK_ENTRY_RESERVE_V1",
                    "bar_market_entry_reserve_ticks": 2.0,
                    "event_ledger_revision": "OPENING_TYPE_FOUR_CAUSAL_EVENTS_V2",
                }
            )
            events = tuple(
                replace(
                    event,
                    details={
                        **dict(event.details),
                        "execution_risk_revision": "BAR_MARKET_TWO_TICK_ENTRY_RESERVE_V1",
                        "event_ledger_revision": "OPENING_TYPE_FOUR_CAUSAL_EVENTS_V2",
                    },
                )
                for event in signal.events
            )
            grouped.setdefault(int(timestamp), []).append(
                replace(
                    signal,
                    expected_loss_per_unit=float(geometry["expected_loss_per_unit"]),
                    expected_gain_per_unit=float(geometry["expected_gain_per_unit"]),
                    net_reward_risk=float(geometry["net_reward_risk"]),
                    events=events,
                    details=details,
                )
            )
            accepted += 1
    diagnostics["V2_BAR_MARKET_COST_CONTRACT_PASS"] = accepted
    diagnostics["V2_FOUR_EVENT_LEDGER_PASS"] = accepted
    diagnostics["SIGNAL"] = accepted
    diagnostics["SIGNAL_TIMES"] = len(grouped)
    return QuoteResiliencySignalBundle(
        signals_by_time_ns={
            timestamp: tuple(sorted(signals, key=lambda item: (item.net_reward_risk, item.scenario_id), reverse=True))
            for timestamp, signals in sorted(grouped.items())
        },
        diagnostics=dict(sorted(diagnostics.items())),
        rejected_scenarios=tuple(rejected),
    )


def build_opening_type_first_pullback_signals(
    *, data: pd.DataFrame, context_times: np.ndarray,
    context_bars: tuple[FiveMinuteBar, ...], snapshots: tuple[tuple[ExternalLevel, ...], ...],
    symbol: str, instrument_id: str, tick: float, fee_rate: float,
    minimum_net_reward_risk: float, router_config: Mapping[str, Any],
    require_retest_contraction: bool = True,
) -> QuoteResiliencySignalBundle:
    del context_times, snapshots, require_retest_contraction
    if not isinstance(data.index, pd.DatetimeIndex) or data.index.tz is None:
        raise TypeError("execution index must be timezone-aware")
    if tick <= 0.0 or fee_rate < 0.0 or minimum_net_reward_risk <= 0.0:
        raise ValueError("invalid cost contract")
    lookback = int(router_config.get("displacement_lookback", 12))
    close_location = float(router_config.get("displacement_close_location", 2.0 / 3.0))
    stop_buffer = float(router_config.get("stop_buffer_atr", 0.05))
    minimum_stop = float(router_config.get("minimum_stop_distance_atr", 0.10))
    opening = OpeningAuctionConfig(
        initial_balance_minutes=30,
        opportunity_minutes_after_balance=int(router_config.get("opportunity_minutes_after_balance", 180)),
        sweep_excursion_atr=0.05,
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
        initiative = opening_type != "OPEN_REJECTION_REVERSE"
        width = float(balance.high) - float(balance.low)
        if width <= 0.0:
            counts["DEGENERATE_INITIAL_BALANCE"] += 1
            continue
        target = (
            float(balance.high) + width if initiative and direction > 0
            else float(balance.low) - width if initiative
            else float(profile.value_high) if direction > 0
            else float(profile.value_low)
        )
        target_source = "INITIAL_BALANCE_EXTENSION" if initiative else "PREVIOUS_DAY_VALUE_OPPOSITE_EDGE"
        state_edge = (
            float(profile.value_high) if direction > 0 and initiative
            else float(profile.value_low) if direction < 0 and initiative
            else float(profile.value_low) if direction > 0
            else float(profile.value_high)
        )
        if not initiative and (
            float(balance.high) >= target if direction > 0 else float(balance.low) <= target
        ):
            counts["OBJECTIVE_CONSUMED_DURING_INITIAL_BALANCE"] += 1
            continue
        scenario = (
            f"opening-pullback-{symbol.lower()}-{balance.session_name.lower()}-"
            f"{balance.local_date}-{serial:05d}"
        )
        route = [
            position for position, bar in enumerate(context_bars)
            if balance.end_time_ns <= _start(bar) < balance.opportunity_end_ns
        ]
        pullback: Pullback | None = None
        emitted = terminal = False
        for position in route:
            bar = context_bars[position]
            if _target_consumed(direction, bar, target):
                counts["OBJECTIVE_CONSUMED_BEFORE_NEW_LEG"] += 1
                terminal = True
                break
            close = float(bar.close)
            if _state_invalidated(
                direction=direction,
                initiative=initiative,
                close=close,
                value_low=float(profile.value_low),
                value_high=float(profile.value_high),
            ):
                counts["OPENING_VALUE_STATE_INVALIDATED"] += 1
                terminal = True
                break
            if pullback is None:
                if _pullback_holds(
                    direction=direction,
                    initiative=initiative,
                    bar=bar,
                    balance=balance,
                    value_low=float(profile.value_low),
                    value_high=float(profile.value_high),
                ):
                    pullback = Pullback(
                        position=position,
                        observed_time_ns=int(bar.ts_event_ns),
                        high=float(bar.high),
                        low=float(bar.low),
                        atr=float(bar.atr),
                    )
                    counts["FIRST_PULLBACK_HELD_STATE_EDGE"] += 1
                continue
            if _new_leg_confirms(
                direction=direction,
                initiative=initiative,
                bar=bar,
                pullback=pullback,
                balance=balance,
                body_median=float(body_med[position]),
                range_median=float(range_med[position]),
                close_location=close_location,
            ):
                exec_pos = contiguous_first_execution_position_after(times, int(bar.ts_event_ns))
                if exec_pos is None:
                    counts["NO_CONTIGUOUS_NEXT_TEN_SECOND_EXECUTION_BUCKET"] += 1
                    terminal = True
                    break
                entry = float(data.iloc[exec_pos]["close"])
                if not isfinite(entry) or entry <= 0.0:
                    counts["UNOBSERVABLE_EXECUTION_PRICE"] += 1
                    terminal = True
                    break
                buffer = stop_buffer * float(bar.atr)
                minimum = minimum_stop * float(bar.atr)
                reference = pullback.low if direction > 0 else pullback.high
                stop = min(reference - buffer, entry - minimum) if direction > 0 else max(reference + buffer, entry + minimum)
                raw = _raw_cost(
                    direction=direction,
                    entry=entry,
                    stop=stop,
                    target=target,
                    fee_rate=fee_rate,
                    tick=tick,
                    stop_slippage_reserve=float(reserves.iloc[exec_pos]),
                )
                if raw is None or raw[2] < minimum_net_reward_risk:
                    counts["INSUFFICIENT_COST_AFTER_NEW_LEG_GEOMETRY"] += 1
                    terminal = True
                    break
                loss, gain, rr = raw
                family = INITIATIVE_FAMILY if initiative else RESPONSIVE_FAMILY
                common = {
                    "session": balance.session_name,
                    "opening_type": opening_type,
                    "ib_high": float(balance.high),
                    "ib_low": float(balance.low),
                    "ib_vwap": float(ib_vwap),
                    "previous_value_low": float(profile.value_low),
                    "previous_value_high": float(profile.value_high),
                    "state_edge": state_edge,
                    "pullback_high": pullback.high,
                    "pullback_low": pullback.low,
                    "ten_second_alpha_inputs": False,
                    "signal_revision": SIGNAL_REVISION,
                }
                state_ns = int(context_bars[balance.last_five_position].ts_event_ns)
                trigger_ns = int(bar.ts_event_ns)
                events = (
                    QuoteResiliencyLogicEvent(
                        scenario, symbol, instrument_id, "OPENING_TYPE_CLASSIFIED", state_ns, state_ns,
                        "IDLE", "OPENING_STATE_CLASSIFIED", opening_type,
                        float(context_bars[balance.first_five_position].open), common,
                    ),
                    QuoteResiliencyLogicEvent(
                        scenario, symbol, instrument_id, "FIRST_PULLBACK_HELD", pullback.observed_time_ns,
                        pullback.observed_time_ns, "OPENING_STATE_CLASSIFIED", "PULLBACK_HELD",
                        "POST_IB_PULLBACK_HELD_STATE_DEFINING_VALUE_EDGE", state_edge, common,
                    ),
                    QuoteResiliencyLogicEvent(
                        scenario, symbol, instrument_id, "NEW_AUCTION_LEG_CONFIRMED", trigger_ns, trigger_ns,
                        "PULLBACK_HELD", "TRIGGER_CONFIRMED",
                        f"SEPARATE_M5_NEW_LEG_{'LONG' if direction > 0 else 'SHORT'}", float(bar.close), common,
                    ),
                    QuoteResiliencyLogicEvent(
                        scenario, symbol, instrument_id, "NEXT_EXECUTION_BUCKET_OBSERVED",
                        int(times[exec_pos]), int(times[exec_pos]), "TRIGGER_CONFIRMED", "CONFIRMED",
                        "CONTIGUOUS_EXECUTION_OBSERVATION", entry, common,
                    ),
                )
                signal = QuoteResiliencySignal(
                    scenario, family, symbol, instrument_id, direction, exec_pos, int(times[exec_pos]),
                    f"state-edge-{scenario}", "PREVIOUS_DAY_VALUE_EDGE", state_edge,
                    f"target-{scenario}", target_source, float(target), entry, float(stop),
                    float(reference), "FIRST_PULLBACK_AND_VALUE_STATE", float(bar.atr),
                    float(reserves.iloc[exec_pos]), float(loss), float(gain), float(rr),
                    state_ns, trigger_ns, pullback.observed_time_ns, events,
                    {
                        **common,
                        "pullback_position": pullback.position,
                        "trigger_position": position,
                        "execution_position": exec_pos,
                        "target_contract": target_source,
                    },
                )
                grouped.setdefault(int(times[exec_pos]), []).append(signal)
                counts["TRADEABLE_OPENING_TYPE_FIRST_PULLBACK_SIGNAL"] += 1
                counts[f"TRADEABLE_{opening_type}"] += 1
                emitted = True
                break
            pullback.high = max(pullback.high, float(bar.high))
            pullback.low = min(pullback.low, float(bar.low))
            pullback.atr = float(bar.atr)
        if not emitted and not terminal:
            counts["NO_NEW_LEG_CONFIRMATION_BEFORE_ROUTE_END"] += 1

    immutable = {
        timestamp: tuple(sorted(signals, key=lambda item: (item.net_reward_risk, item.scenario_id), reverse=True))
        for timestamp, signals in sorted(grouped.items())
    }
    counts["SIGNAL"] = sum(len(signals) for signals in immutable.values())
    counts["SIGNAL_TIMES"] = len(immutable)
    return QuoteResiliencySignalBundle(
        signals_by_time_ns=immutable,
        diagnostics=dict(sorted(counts.items())),
        rejected_scenarios=tuple(rejected),
    )


__all__ = [
    "IMPLEMENTATION_REVISION",
    "INITIATIVE_FAMILY",
    "RESPONSIVE_FAMILY",
    "SIGNAL_REVISION",
    "build_opening_type_first_pullback_signals",
    "reprice_bundle_for_bar_market_preserving_events",
]

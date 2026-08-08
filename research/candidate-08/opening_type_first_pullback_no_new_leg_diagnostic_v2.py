"""Diagnostic-only ablation for Opening-Type First-Pullback V2.

Removes exactly one variable: the separate later M5 new-leg confirmation.  The opening
state, first post-IB pullback, structural stop, frozen objective, full cost geometry,
shared-account risk and execution contract remain unchanged.  A signal is observed in
the first contiguous ten-second bucket after the completed first pullback.  Results from
this module are diagnostic and can never be promoted directly.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
from math import isfinite
from typing import Any, Mapping

import numpy as np
import pandas as pd

from aggtrade_acceptance_signals import causal_stop_slippage_reserve_series
from opening_initial_balance_failed_auction_signals_v1 import (
    OpeningAuctionConfig,
    build_initial_balances,
)
from opening_type_state_router_signals_v1 import classify_opening_state
from opening_type_first_pullback_signals_v2 import (
    DAY_NS,
    FIVE_NS,
    IMPLEMENTATION_REVISION,
    INITIATIVE_FAMILY,
    RESPONSIVE_FAMILY,
    Pullback,
    _pullback_holds,
    _raw_cost,
    _start,
    _state_invalidated,
    _target_consumed,
)
from quote_resiliency_signals import (
    QuoteResiliencyLogicEvent,
    QuoteResiliencySignal,
    QuoteResiliencySignalBundle,
)
from range_fvg_logic import ExternalLevel, FiveMinuteBar
from session_raid_reversal_execution_v2 import apply_bar_market_entry_cost_contract
from session_raid_reversal_signals_v2 import contiguous_first_execution_position_after
from session_value_migration_signals_v1 import build_completed_daily_value_profiles

DIAGNOSTIC_ABLATION = "REMOVE_SEPARATE_M5_NEW_LEG_CONFIRMATION"
SIGNAL_REVISION = "OPENING_TYPE_FIRST_PULLBACK_DIRECT_DIAGNOSTIC_V2"


def reprice_diagnostic_bundle(
    bundle: QuoteResiliencySignalBundle,
    *,
    tick: float,
    minimum_net_reward_risk: float,
) -> QuoteResiliencySignalBundle:
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
                diagnostics["DIAGNOSTIC_INSUFFICIENT_COST_AFTER_BAR_MARKET_ENTRY"] += 1
                rejected.append(
                    {
                        "scenario_id": signal.scenario_id,
                        "scenario_family": signal.scenario_family,
                        "reason": "DIAGNOSTIC_INSUFFICIENT_COST_AFTER_BAR_MARKET_ENTRY",
                        "signal_time_ns": int(signal.signal_time_ns),
                    }
                )
                continue
            details = {
                **dict(signal.details),
                "diagnostic_only": True,
                "diagnostic_ablation": DIAGNOSTIC_ABLATION,
                "promotion_permitted": False,
                "execution_risk_revision": "BAR_MARKET_TWO_TICK_ENTRY_RESERVE_V1",
                "event_ledger_revision": "OPENING_TYPE_THREE_EVENT_DIAGNOSTIC_V2",
            }
            events = tuple(
                replace(event, details={**dict(event.details), **details})
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
    diagnostics["DIAGNOSTIC_BAR_MARKET_COST_CONTRACT_PASS"] = accepted
    diagnostics["DIAGNOSTIC_THREE_EVENT_LEDGER_PASS"] = accepted
    diagnostics["SIGNAL"] = accepted
    diagnostics["SIGNAL_TIMES"] = len(grouped)
    return QuoteResiliencySignalBundle(
        signals_by_time_ns={
            timestamp: tuple(
                sorted(
                    signals,
                    key=lambda item: (item.net_reward_risk, item.scenario_id),
                    reverse=True,
                )
            )
            for timestamp, signals in sorted(grouped.items())
        },
        diagnostics=dict(sorted(diagnostics.items())),
        rejected_scenarios=tuple(rejected),
    )


def build_opening_type_first_pullback_direct_diagnostic(
    *,
    data: pd.DataFrame,
    context_times: np.ndarray,
    context_bars: tuple[FiveMinuteBar, ...],
    snapshots: tuple[tuple[ExternalLevel, ...], ...],
    symbol: str,
    instrument_id: str,
    tick: float,
    fee_rate: float,
    minimum_net_reward_risk: float,
    router_config: Mapping[str, Any],
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
        opportunity_minutes_after_balance=int(
            router_config.get("opportunity_minutes_after_balance", 180)
        ),
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
            float(balance.high) + width
            if initiative and direction > 0
            else float(balance.low) - width
            if initiative
            else float(profile.value_high)
            if direction > 0
            else float(profile.value_low)
        )
        target_source = (
            "INITIAL_BALANCE_EXTENSION"
            if initiative
            else "PREVIOUS_DAY_VALUE_OPPOSITE_EDGE"
        )
        state_edge = (
            float(profile.value_high)
            if direction > 0 and initiative
            else float(profile.value_low)
            if direction < 0 and initiative
            else float(profile.value_low)
            if direction > 0
            else float(profile.value_high)
        )
        if not initiative and (
            float(balance.high) >= target
            if direction > 0
            else float(balance.low) <= target
        ):
            counts["OBJECTIVE_CONSUMED_DURING_INITIAL_BALANCE"] += 1
            continue

        scenario = (
            f"opening-pullback-direct-diagnostic-{symbol.lower()}-"
            f"{balance.session_name.lower()}-{balance.local_date}-{serial:05d}"
        )
        route = [
            position
            for position, bar in enumerate(context_bars)
            if balance.end_time_ns <= _start(bar) < balance.opportunity_end_ns
        ]
        terminal = False
        for position in route:
            bar = context_bars[position]
            if _target_consumed(direction, bar, target):
                counts["OBJECTIVE_CONSUMED_BEFORE_FIRST_PULLBACK"] += 1
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
            if not _pullback_holds(
                direction=direction,
                initiative=initiative,
                bar=bar,
                balance=balance,
                value_low=float(profile.value_low),
                value_high=float(profile.value_high),
            ):
                continue

            counts["FIRST_PULLBACK_HELD_STATE_EDGE"] += 1
            pullback = Pullback(
                position=position,
                observed_time_ns=int(bar.ts_event_ns),
                high=float(bar.high),
                low=float(bar.low),
                atr=float(bar.atr),
            )
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
            buffer = stop_buffer * pullback.atr
            minimum = minimum_stop * pullback.atr
            reference = pullback.low if direction > 0 else pullback.high
            stop = (
                min(reference - buffer, entry - minimum)
                if direction > 0
                else max(reference + buffer, entry + minimum)
            )
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
                counts["INSUFFICIENT_COST_AFTER_FIRST_PULLBACK_GEOMETRY"] += 1
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
                "diagnostic_only": True,
                "diagnostic_ablation": DIAGNOSTIC_ABLATION,
                "promotion_permitted": False,
            }
            state_ns = int(context_bars[balance.last_five_position].ts_event_ns)
            signal_ns = int(times[exec_pos])
            events = (
                QuoteResiliencyLogicEvent(
                    scenario,
                    symbol,
                    instrument_id,
                    "OPENING_TYPE_CLASSIFIED",
                    state_ns,
                    state_ns,
                    "IDLE",
                    "OPENING_STATE_CLASSIFIED",
                    opening_type,
                    float(context_bars[balance.first_five_position].open),
                    common,
                ),
                QuoteResiliencyLogicEvent(
                    scenario,
                    symbol,
                    instrument_id,
                    "FIRST_PULLBACK_HELD_AND_DIAGNOSTIC_ENTRY_ARMED",
                    pullback.observed_time_ns,
                    pullback.observed_time_ns,
                    "OPENING_STATE_CLASSIFIED",
                    "TRIGGER_CONFIRMED",
                    DIAGNOSTIC_ABLATION,
                    state_edge,
                    common,
                ),
                QuoteResiliencyLogicEvent(
                    scenario,
                    symbol,
                    instrument_id,
                    "NEXT_EXECUTION_BUCKET_OBSERVED",
                    signal_ns,
                    signal_ns,
                    "TRIGGER_CONFIRMED",
                    "CONFIRMED",
                    "CONTIGUOUS_EXECUTION_OBSERVATION",
                    entry,
                    common,
                ),
            )
            signal = QuoteResiliencySignal(
                scenario_id=scenario,
                scenario_family=family,
                symbol=symbol,
                instrument_id=instrument_id,
                direction=direction,
                signal_index=exec_pos,
                signal_time_ns=signal_ns,
                boundary_id=f"state-edge-{scenario}",
                boundary_source="PREVIOUS_DAY_VALUE_EDGE",
                boundary_level=state_edge,
                target_id=f"target-{scenario}",
                target_source=target_source,
                external_target=float(target),
                entry_reference=entry,
                structural_stop=float(stop),
                stop_reference=float(reference),
                stop_reference_source="FIRST_PULLBACK_AND_VALUE_STATE",
                atr=pullback.atr,
                causal_stop_slippage_reserve=float(reserves.iloc[exec_pos]),
                expected_loss_per_unit=float(loss),
                expected_gain_per_unit=float(gain),
                net_reward_risk=float(rr),
                interaction_time_ns=state_ns,
                response_time_ns=pullback.observed_time_ns,
                retest_time_ns=pullback.observed_time_ns,
                events=events,
                details={
                    **common,
                    "pullback_position": pullback.position,
                    "execution_position": exec_pos,
                    "target_contract": target_source,
                },
            )
            grouped.setdefault(signal_ns, []).append(signal)
            counts["DIAGNOSTIC_FIRST_PULLBACK_DIRECT_ENTRY_SIGNAL"] += 1
            counts[f"DIAGNOSTIC_TRADEABLE_{opening_type}"] += 1
            break
        else:
            counts["NO_FIRST_PULLBACK_BEFORE_ROUTE_END"] += 1
        if terminal:
            continue

    immutable = {
        timestamp: tuple(
            sorted(
                signals,
                key=lambda item: (item.net_reward_risk, item.scenario_id),
                reverse=True,
            )
        )
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
    "DIAGNOSTIC_ABLATION",
    "SIGNAL_REVISION",
    "build_opening_type_first_pullback_direct_diagnostic",
    "reprice_diagnostic_bundle",
]

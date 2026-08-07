"""Pure causal direct session-raid-reversal signal builder.

A completed H4 displacement supplies direction.  A completed destination-session 15-minute raid and
reclaim of the source boundary opposite that draw is the scenario confirmation.  Entry is the first
completed ten-second bucket strictly after the completed 15-minute bar; ten-second data is execution
granularity only.  No later microstructure or scalping alpha condition is used.
"""

from __future__ import annotations

from collections import Counter
from math import isfinite
from typing import Any, Mapping

import numpy as np
import pandas as pd

from aggtrade_acceptance_signals import causal_stop_slippage_reserve_series
from day_liquidity_delivery_context_v1 import (
    RAID_FAMILY,
    DayLiquidityDeliveryConfig,
    RouteCandidate,
    build_session_ranges,
    day_start_ns,
    first_execution_position_after,
)
from day_liquidity_delivery_htf_v1 import build_draw_contexts, target_still_active
from day_liquidity_delivery_routes_v1 import build_route_candidates
from day_liquidity_delivery_signals_v1 import _cost_geometry, _structural_stop
from quote_resiliency_signals import (
    QuoteResiliencyLogicEvent,
    QuoteResiliencySignal,
    QuoteResiliencySignalBundle,
)
from range_fvg_logic import ExternalLevel, FiveMinuteBar
from session_liquidity_transfer_signals_v1 import (
    _entry_in_source_location,
    _select_target,
    _source_target_consumed_at_interaction,
)


SIGNAL_REVISION = "SESSION_RAID_REVERSAL_SIGNALS_V1"
SCENARIO_FAMILY = "H4_DRAW_DIRECT_SESSION_RAID_REVERSAL"


def _event(
    *,
    scenario_id: str,
    candidate: RouteCandidate,
    symbol: str,
    instrument_id: str,
    event_time_ns: int,
    entry: float,
    target_id: str,
    target_source: str,
    target_level: float,
    net_reward_risk: float,
    source_low: float,
    source_high: float,
) -> QuoteResiliencyLogicEvent:
    return QuoteResiliencyLogicEvent(
        scenario_id=scenario_id,
        symbol=symbol,
        instrument_id=instrument_id,
        event_type="H4_DRAW_ALIGNED_SESSION_RAID_RECLAIM_ENTRY_CONFIRMED",
        event_time_ns=int(event_time_ns),
        observed_time_ns=int(event_time_ns),
        previous_state="HTF_DRAW_ARMED",
        next_state="CONFIRMED",
        reason_code="COMPLETED_SOURCE_BOUNDARY_OPPOSITE_DRAW_RAIDED_AND_CLOSED_BACK_INSIDE",
        reference_price=entry,
        details={
            "scenario_family": SCENARIO_FAMILY,
            "route_name": candidate.route_name,
            "source_name": candidate.source_name,
            "draw_signature": candidate.draw.signature,
            "source_low": source_low,
            "source_high": source_high,
            "raid_boundary": candidate.boundary_level,
            "raid_extreme": candidate.structural_reference,
            "target_id": target_id,
            "target_source": target_source,
            "target_level": target_level,
            "net_reward_risk": net_reward_risk,
        },
    )


def _reject(
    rejected: list[dict[str, Any]],
    diagnostics: Counter[str],
    *,
    candidate: RouteCandidate,
    reason: str,
    details: Mapping[str, Any] | None = None,
) -> None:
    diagnostics[reason] += 1
    rejected.append(
        {
            "scenario_id": candidate.scenario_id.replace("day-delivery", "session-raid"),
            "scenario_family": SCENARIO_FAMILY,
            "route_name": candidate.route_name,
            "source_name": candidate.source_name,
            "reason": reason,
            "trigger_time_ns": candidate.trigger_time_ns,
            "interaction_time_ns": candidate.interaction_time_ns,
            "details": dict(details or {}),
        }
    )


def build_session_raid_reversal_signals(
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
    day_config: DayLiquidityDeliveryConfig,
) -> QuoteResiliencySignalBundle:
    day_config.validate()
    if tick <= 0.0 or fee_rate < 0.0 or minimum_net_reward_risk <= 0.0:
        raise ValueError("invalid cost contract")
    if not isinstance(data.index, pd.DatetimeIndex) or data.index.tz is None:
        raise TypeError("ten-second execution data must use a timezone-aware DatetimeIndex")
    if len(context_times) != len(context_bars) or len(snapshots) != len(context_bars):
        raise ValueError("five-minute context arrays must have equal lengths")
    if tuple(int(bar.ts_event_ns) for bar in context_bars) != tuple(int(value) for value in context_times):
        raise ValueError("context_times must exactly match completed five-minute bars")

    diagnostics: Counter[str] = Counter()
    rejected: list[dict[str, Any]] = []
    signals: dict[int, list[QuoteResiliencySignal]] = {}

    draws = build_draw_contexts(context_bars, day_config)
    diagnostics["ACTIVE_H4_DRAW_BARS"] = sum(draw is not None for draw in draws)
    routes = build_route_candidates(
        bars=context_bars,
        draw_by_five=draws,
        snapshots=snapshots,
        config=day_config,
        symbol=symbol,
        diagnostics=diagnostics,
        rejected=rejected,
    )
    raid_routes = tuple(candidate for candidate in routes if candidate.family == RAID_FAMILY)
    diagnostics["ALL_ROUTE_CANDIDATES"] = len(routes)
    diagnostics["DIRECT_RAID_CANDIDATES"] = len(raid_routes)
    diagnostics["ACCEPTANCE_ROUTES_EXCLUDED_FROM_V1"] = len(routes) - len(raid_routes)

    sessions = build_session_ranges(context_bars)
    data_times = data.index.as_unit("ns").asi8
    stop_reserves = causal_stop_slippage_reserve_series(data, tick=tick)

    for candidate in raid_routes:
        trigger = int(candidate.trigger_five_index)
        if trigger < 0 or trigger >= len(context_bars):
            _reject(rejected, diagnostics, candidate=candidate, reason="INVALID_ROUTE_TRIGGER_INDEX")
            continue
        source = sessions.get((day_start_ns(candidate.trigger_time_ns), candidate.source_name))
        if source is None:
            _reject(rejected, diagnostics, candidate=candidate, reason="SOURCE_SESSION_RANGE_NOT_AVAILABLE")
            continue
        if _source_target_consumed_at_interaction(candidate, source, tick=tick):
            _reject(rejected, diagnostics, candidate=candidate, reason="SOURCE_OPPOSITE_LIQUIDITY_ALREADY_CONSUMED_BY_RAID_BAR")
            continue
        if not target_still_active(snapshots, trigger, candidate.target.level_id):
            _reject(rejected, diagnostics, candidate=candidate, reason="FROZEN_HTF_CONTEXT_TARGET_NOT_ACTIVE_AT_ENTRY")
            continue
        diagnostics["DIRECT_RAID_CONTEXT_VALID"] += 1

        execution_position = first_execution_position_after(data_times, int(candidate.trigger_time_ns))
        if execution_position is None:
            _reject(rejected, diagnostics, candidate=candidate, reason="NO_LATER_COMPLETED_TEN_SECOND_EXECUTION_BUCKET")
            continue
        execution_time_ns = int(data.index[execution_position].as_unit("ns").value)
        if execution_time_ns <= int(candidate.trigger_time_ns):
            raise RuntimeError("execution must follow the completed fifteen-minute raid bar")
        entry = float(data.iloc[execution_position]["close"])
        if not isfinite(entry) or entry <= 0.0:
            _reject(rejected, diagnostics, candidate=candidate, reason="INVALID_EXECUTION_REFERENCE")
            continue
        if not _entry_in_source_location(direction=candidate.direction, entry=entry, source=source):
            _reject(
                rejected,
                diagnostics,
                candidate=candidate,
                reason="ENTRY_OUTSIDE_REQUIRED_SOURCE_SESSION_HALF",
                details={
                    "entry": entry,
                    "source_low": source.low,
                    "source_high": source.high,
                    "source_midpoint": (source.low + source.high) / 2.0,
                },
            )
            continue
        diagnostics["SOURCE_SESSION_HALF_LOCATION_PASS"] += 1

        target = _select_target(candidate=candidate, source=source, entry=entry, tick=tick)
        if target is None:
            _reject(rejected, diagnostics, candidate=candidate, reason="NO_UNCONSUMED_TARGET_AFTER_ENTRY")
            continue
        trigger_bar = context_bars[trigger]
        if not isfinite(float(trigger_bar.atr)) or float(trigger_bar.atr) <= 0.0:
            _reject(rejected, diagnostics, candidate=candidate, reason="NO_CAUSAL_FIVE_MINUTE_ATR_FOR_STOP")
            continue
        stop = _structural_stop(
            direction=candidate.direction,
            structural_reference=float(candidate.structural_reference),
            atr=float(trigger_bar.atr),
            stop_buffer_atr=day_config.structural_stop_buffer_atr,
        )
        reserve = float(stop_reserves.iloc[execution_position])
        geometry = _cost_geometry(
            direction=candidate.direction,
            entry=entry,
            stop=stop,
            target=target.target_level,
            fee_rate=fee_rate,
            tick=tick,
            stop_slippage_reserve=reserve,
        )
        if geometry is None:
            _reject(rejected, diagnostics, candidate=candidate, reason="INVALID_COST_AFTER_GEOMETRY")
            continue
        expected_loss, expected_gain, net_reward_risk = geometry
        if net_reward_risk < minimum_net_reward_risk:
            _reject(
                rejected,
                diagnostics,
                candidate=candidate,
                reason="INSUFFICIENT_COST_AFTER_SESSION_RAID_TARGET",
                details={"net_reward_risk": net_reward_risk, "target": target.target_level},
            )
            continue
        diagnostics["COST_AFTER_SESSION_RAID_TARGET_PASS"] += 1

        scenario_id = candidate.scenario_id.replace("day-delivery", "session-raid")
        event = _event(
            scenario_id=scenario_id,
            candidate=candidate,
            symbol=symbol,
            instrument_id=instrument_id,
            event_time_ns=int(candidate.trigger_time_ns),
            entry=entry,
            target_id=target.target_id,
            target_source=target.target_source,
            target_level=target.target_level,
            net_reward_risk=net_reward_risk,
            source_low=source.low,
            source_high=source.high,
        )
        signal = QuoteResiliencySignal(
            scenario_id=scenario_id,
            scenario_family=SCENARIO_FAMILY,
            symbol=symbol,
            instrument_id=instrument_id,
            direction=candidate.direction,
            signal_index=execution_position,
            signal_time_ns=execution_time_ns,
            boundary_id=candidate.boundary_id,
            boundary_source=candidate.boundary_source,
            boundary_level=float(candidate.boundary_level),
            target_id=target.target_id,
            target_source=target.target_source,
            external_target=target.target_level,
            entry_reference=entry,
            structural_stop=stop,
            stop_reference=float(candidate.structural_reference),
            stop_reference_source="COMPLETED_FIFTEEN_MINUTE_RAID_EXTREME",
            atr=float(trigger_bar.atr),
            causal_stop_slippage_reserve=reserve,
            expected_loss_per_unit=expected_loss,
            expected_gain_per_unit=expected_gain,
            net_reward_risk=net_reward_risk,
            interaction_time_ns=int(candidate.interaction_time_ns),
            response_time_ns=int(candidate.trigger_time_ns),
            retest_time_ns=None,
            events=(event,),
            details={
                "scenario_family": SCENARIO_FAMILY,
                "signal_revision": SIGNAL_REVISION,
                "route_name": candidate.route_name,
                "source_name": source.name,
                "draw_signature": candidate.draw.signature,
                "draw_origin": candidate.draw.origin_level,
                "source_low": source.low,
                "source_high": source.high,
                "source_midpoint": (source.low + source.high) / 2.0,
                "session_target_level": target.source_target_level,
                "frozen_htf_target_level": target.frozen_htf_target_level,
                "selected_target_contract": "NEAREST_UNCONSUMED_SOURCE_OPPOSITE_OR_FROZEN_HTF_LIQUIDITY",
                "entry_mode": "FIRST_TEN_SECOND_BUCKET_AFTER_COMPLETED_FIFTEEN_MINUTE_RAID_RECLAIM",
                "holding_horizon": "INTRADAY_TENS_OF_MINUTES_TO_SIX_HOURS",
                "scalping_alpha_inputs": False,
            },
        )
        signals.setdefault(execution_time_ns, []).append(signal)
        diagnostics["SIGNAL"] += 1

    grouped = {
        timestamp: tuple(sorted(items, key=lambda signal: (signal.net_reward_risk, signal.scenario_id), reverse=True))
        for timestamp, items in sorted(signals.items())
    }
    diagnostics["SIGNAL_TIMES"] = len(grouped)
    return QuoteResiliencySignalBundle(
        signals_by_time_ns=grouped,
        diagnostics=dict(sorted(diagnostics.items())),
        rejected_scenarios=tuple(rejected),
    )


__all__ = ["SCENARIO_FAMILY", "SIGNAL_REVISION", "build_session_raid_reversal_signals"]

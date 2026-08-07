"""Pure causal session-liquidity-transfer signal builder.

The detector is day-trading scale.  A completed H4 draw supplies direction, a completed 15-minute
source-session raid/reclaim supplies scenario confirmation, and the first later five-minute retest
of the reclaimed boundary supplies execution location.  The first unconsumed opposite boundary of
the completed source session is the natural intraday delivery objective; a closer frozen HTF target
may terminate the path first.  Ten-second bars are used only for the first execution timestamp after
the completed five-minute retest and for causal execution-cost reserves.

There is no order, fill, account, quantity, PnL, or backtest-engine logic in this module.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping

import numpy as np
import pandas as pd

from aggtrade_acceptance_signals import causal_stop_slippage_reserve_series
from day_liquidity_delivery_context_v1 import (
    RAID_FAMILY,
    DayLiquidityDeliveryConfig,
    RouteCandidate,
    SessionRange,
    build_session_ranges,
    day_start_ns,
    first_execution_position_after,
)
from day_liquidity_delivery_htf_v1 import (
    build_draw_contexts,
    same_draw,
    target_still_active,
)
from day_liquidity_delivery_routes_v1 import build_route_candidates
from day_liquidity_delivery_signals_v1 import _cost_geometry, _structural_stop
from quote_resiliency_signals import (
    QuoteResiliencyLogicEvent,
    QuoteResiliencySignal,
    QuoteResiliencySignalBundle,
)
from range_fvg_logic import ExternalLevel, FiveMinuteBar


SIGNAL_REVISION = "SESSION_LIQUIDITY_TRANSFER_SIGNALS_V1"
SCENARIO_FAMILY = "H4_DRAW_SESSION_RAID_TRANSFER"


@dataclass(frozen=True, slots=True)
class SessionLiquidityTransferConfig:
    boundary_retest_tolerance_atr: float = 0.05
    require_directional_retest_close: bool = True

    def validate(self) -> None:
        if not 0.0 < self.boundary_retest_tolerance_atr <= 0.25:
            raise ValueError("boundary retest tolerance must be structural and small")
        if self.require_directional_retest_close is not True:
            raise ValueError("V1 freezes directional first-retest close; removal is one ablation")


@dataclass(frozen=True, slots=True)
class _TargetSelection:
    target_id: str
    target_source: str
    target_level: float
    source_target_level: float
    frozen_htf_target_level: float


def _event(
    *,
    candidate: RouteCandidate,
    symbol: str,
    instrument_id: str,
    event_type: str,
    event_time_ns: int,
    previous_state: str,
    next_state: str,
    reason_code: str,
    reference_price: float | None,
    details: Mapping[str, Any],
) -> QuoteResiliencyLogicEvent:
    return QuoteResiliencyLogicEvent(
        scenario_id=candidate.scenario_id,
        symbol=symbol,
        instrument_id=instrument_id,
        event_type=event_type,
        event_time_ns=int(event_time_ns),
        observed_time_ns=int(event_time_ns),
        previous_state=previous_state,
        next_state=next_state,
        reason_code=reason_code,
        reference_price=reference_price,
        details=dict(details),
    )


def _reject(
    rejected: list[dict[str, Any]],
    diagnostics: Counter[str],
    *,
    candidate: RouteCandidate,
    reason: str,
    position: int | None = None,
    details: Mapping[str, Any] | None = None,
) -> None:
    diagnostics[reason] += 1
    rejected.append(
        {
            "scenario_id": candidate.scenario_id,
            "scenario_family": SCENARIO_FAMILY,
            "route_name": candidate.route_name,
            "source_name": candidate.source_name,
            "reason": reason,
            "position": position,
            "trigger_time_ns": candidate.trigger_time_ns,
            "interaction_time_ns": candidate.interaction_time_ns,
            "details": dict(details or {}),
        }
    )


def _source_target_consumed_at_interaction(
    candidate: RouteCandidate,
    source: SessionRange,
    *,
    tick: float,
) -> bool:
    if candidate.direction > 0:
        return float(candidate.interaction_details.get("raid_high", float("inf"))) >= source.high - tick
    return float(candidate.interaction_details.get("raid_low", float("-inf"))) <= source.low + tick


def _source_target_consumed_by_bar(
    bar: FiveMinuteBar,
    *,
    direction: int,
    source: SessionRange,
    tick: float,
) -> bool:
    return (
        float(bar.high) >= source.high - tick
        if direction > 0
        else float(bar.low) <= source.low + tick
    )


def _first_boundary_retest(
    bar: FiveMinuteBar,
    *,
    direction: int,
    boundary: float,
    tolerance_atr: float,
    require_directional_close: bool,
) -> str:
    if not isfinite(float(bar.atr)) or float(bar.atr) <= 0.0:
        return "UNOBSERVABLE"
    tolerance = tolerance_atr * float(bar.atr)
    if direction > 0:
        touched = float(bar.low) <= boundary + tolerance
        invalidated = float(bar.close) < boundary - tolerance
        inside = float(bar.close) > boundary
        directional = float(bar.close) > float(bar.open)
    elif direction < 0:
        touched = float(bar.high) >= boundary - tolerance
        invalidated = float(bar.close) > boundary + tolerance
        inside = float(bar.close) < boundary
        directional = float(bar.close) < float(bar.open)
    else:
        raise ValueError("direction must be -1 or +1")
    if invalidated:
        return "INVALIDATED"
    if not touched:
        return "NO_TOUCH"
    if not inside:
        return "INVALID_FIRST_TOUCH"
    if require_directional_close and not directional:
        return "NON_DIRECTIONAL_FIRST_TOUCH"
    return "VALID_RETEST"


def _entry_in_source_location(
    *,
    direction: int,
    entry: float,
    source: SessionRange,
) -> bool:
    if not source.low < entry < source.high:
        return False
    midpoint = (source.low + source.high) / 2.0
    return entry <= midpoint if direction > 0 else entry >= midpoint


def _select_target(
    *,
    candidate: RouteCandidate,
    source: SessionRange,
    entry: float,
    tick: float,
) -> _TargetSelection | None:
    source_level = source.high if candidate.direction > 0 else source.low
    htf_level = float(candidate.target.level)
    options: list[tuple[float, str, str]] = []
    if candidate.direction > 0:
        if source_level > entry + tick:
            options.append((source_level, f"{source.name}-{source.day_start_ns}-HIGH", f"{source.name}_HIGH"))
        if htf_level > entry + tick:
            options.append((htf_level, candidate.target.level_id, str(getattr(candidate.target.source, "value", candidate.target.source))))
        if not options:
            return None
        level, target_id, target_source = min(options, key=lambda item: item[0])
    else:
        if source_level < entry - tick:
            options.append((source_level, f"{source.name}-{source.day_start_ns}-LOW", f"{source.name}_LOW"))
        if htf_level < entry - tick:
            options.append((htf_level, candidate.target.level_id, str(getattr(candidate.target.source, "value", candidate.target.source))))
        if not options:
            return None
        level, target_id, target_source = max(options, key=lambda item: item[0])
    return _TargetSelection(
        target_id=target_id,
        target_source=target_source,
        target_level=float(level),
        source_target_level=float(source_level),
        frozen_htf_target_level=htf_level,
    )


def build_session_liquidity_transfer_signals(
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
    transfer_config: SessionLiquidityTransferConfig,
) -> QuoteResiliencySignalBundle:
    day_config.validate()
    transfer_config.validate()
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

    draw_contexts = build_draw_contexts(context_bars, day_config)
    diagnostics["ACTIVE_H4_DRAW_BARS"] = sum(item is not None for item in draw_contexts)
    routes = build_route_candidates(
        bars=context_bars,
        draw_by_five=draw_contexts,
        snapshots=snapshots,
        config=day_config,
        symbol=symbol,
        diagnostics=diagnostics,
        rejected=rejected,
    )
    raid_routes = tuple(candidate for candidate in routes if candidate.family == RAID_FAMILY)
    diagnostics["ALL_ROUTE_CANDIDATES"] = len(routes)
    diagnostics["RAID_TRANSFER_CANDIDATES"] = len(raid_routes)
    diagnostics["ACCEPTANCE_ROUTES_EXCLUDED_FROM_V1"] = len(routes) - len(raid_routes)

    sessions = build_session_ranges(context_bars)
    data_times = data.index.as_unit("ns").asi8
    stop_reserves = causal_stop_slippage_reserve_series(data, tick=tick)

    for candidate in raid_routes:
        source_key = (day_start_ns(candidate.trigger_time_ns), candidate.source_name)
        source = sessions.get(source_key)
        if source is None:
            _reject(rejected, diagnostics, candidate=candidate, reason="SOURCE_SESSION_RANGE_NOT_AVAILABLE")
            continue
        if _source_target_consumed_at_interaction(candidate, source, tick=tick):
            _reject(rejected, diagnostics, candidate=candidate, reason="SOURCE_OPPOSITE_LIQUIDITY_ALREADY_CONSUMED_BY_RAID_BAR")
            continue
        diagnostics["UNCONSUMED_SOURCE_OPPOSITE_LIQUIDITY"] += 1

        touched = False
        for position in range(candidate.trigger_five_index + 1, len(context_bars)):
            bar = context_bars[position]
            if int(bar.ts_event_ns) > int(candidate.route_end_ns):
                break
            if not same_draw(draw_contexts[position], candidate.draw):
                _reject(rejected, diagnostics, candidate=candidate, reason="HTF_DRAW_CHANGED_BEFORE_RETEST", position=position)
                touched = True
                break
            if not target_still_active(snapshots, position, candidate.target.level_id):
                _reject(rejected, diagnostics, candidate=candidate, reason="FROZEN_HTF_CONTEXT_TARGET_CONSUMED_BEFORE_RETEST", position=position)
                touched = True
                break
            if _source_target_consumed_by_bar(bar, direction=candidate.direction, source=source, tick=tick):
                _reject(rejected, diagnostics, candidate=candidate, reason="SOURCE_OPPOSITE_LIQUIDITY_CONSUMED_BEFORE_ENTRY", position=position)
                touched = True
                break

            result = _first_boundary_retest(
                bar,
                direction=candidate.direction,
                boundary=float(candidate.boundary_level),
                tolerance_atr=transfer_config.boundary_retest_tolerance_atr,
                require_directional_close=transfer_config.require_directional_retest_close,
            )
            if result == "UNOBSERVABLE":
                diagnostics["NO_CAUSAL_FIVE_MINUTE_ATR_FOR_RETEST"] += 1
                continue
            if result == "NO_TOUCH":
                continue
            touched = True
            if result != "VALID_RETEST":
                _reject(
                    rejected,
                    diagnostics,
                    candidate=candidate,
                    reason=f"FIRST_SOURCE_BOUNDARY_RETEST_{result}",
                    position=position,
                    details={
                        "boundary": candidate.boundary_level,
                        "bar_open": bar.open,
                        "bar_high": bar.high,
                        "bar_low": bar.low,
                        "bar_close": bar.close,
                    },
                )
                break
            diagnostics["FIRST_SOURCE_BOUNDARY_RETEST_CONFIRMED"] += 1

            execution_position = first_execution_position_after(data_times, int(bar.ts_event_ns))
            if execution_position is None:
                _reject(rejected, diagnostics, candidate=candidate, reason="NO_LATER_COMPLETED_TEN_SECOND_EXECUTION_BUCKET", position=position)
                break
            execution_time_ns = int(data.index[execution_position].as_unit("ns").value)
            if execution_time_ns <= int(bar.ts_event_ns):
                raise RuntimeError("execution must follow the completed five-minute retest")
            entry = float(data.iloc[execution_position]["close"])
            if not isfinite(entry) or entry <= 0.0:
                _reject(rejected, diagnostics, candidate=candidate, reason="INVALID_EXECUTION_REFERENCE", position=position)
                break
            if not _entry_in_source_location(direction=candidate.direction, entry=entry, source=source):
                _reject(
                    rejected,
                    diagnostics,
                    candidate=candidate,
                    reason="ENTRY_OUTSIDE_REQUIRED_SOURCE_SESSION_HALF",
                    position=position,
                    details={
                        "entry": entry,
                        "source_low": source.low,
                        "source_high": source.high,
                        "source_midpoint": (source.low + source.high) / 2.0,
                    },
                )
                break
            diagnostics["SOURCE_SESSION_HALF_LOCATION_PASS"] += 1

            target = _select_target(candidate=candidate, source=source, entry=entry, tick=tick)
            if target is None:
                _reject(rejected, diagnostics, candidate=candidate, reason="NO_UNCONSUMED_TARGET_AFTER_ENTRY", position=position)
                break
            stop = _structural_stop(
                direction=candidate.direction,
                structural_reference=float(candidate.structural_reference),
                atr=float(bar.atr),
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
                _reject(rejected, diagnostics, candidate=candidate, reason="INVALID_COST_AFTER_GEOMETRY", position=position)
                break
            expected_loss, expected_gain, net_reward_risk = geometry
            if net_reward_risk < minimum_net_reward_risk:
                _reject(
                    rejected,
                    diagnostics,
                    candidate=candidate,
                    reason="INSUFFICIENT_COST_AFTER_SESSION_TRANSFER_TARGET",
                    position=position,
                    details={"net_reward_risk": net_reward_risk, "target": target.target_level},
                )
                break
            diagnostics["COST_AFTER_SESSION_TRANSFER_TARGET_PASS"] += 1

            events = (
                _event(
                    candidate=candidate,
                    symbol=symbol,
                    instrument_id=instrument_id,
                    event_type="H4_DRAW_ALIGNED_SESSION_RAID_RECLAIM_CONFIRMED",
                    event_time_ns=int(candidate.trigger_time_ns),
                    previous_state="HTF_DRAW_ARMED",
                    next_state="SESSION_RAID_RECLAIM_CONFIRMED",
                    reason_code="COMPLETED_SOURCE_BOUNDARY_OPPOSITE_DRAW_RAIDED_AND_RECLAIMED",
                    reference_price=float(candidate.boundary_level),
                    details={
                        "scenario_family": SCENARIO_FAMILY,
                        "draw_signature": candidate.draw.signature,
                        "source_name": source.name,
                        "source_low": source.low,
                        "source_high": source.high,
                        "raid_extreme": candidate.structural_reference,
                    },
                ),
                _event(
                    candidate=candidate,
                    symbol=symbol,
                    instrument_id=instrument_id,
                    event_type="FIRST_FIVE_MINUTE_RECLAIMED_BOUNDARY_RETEST_CONFIRMED",
                    event_time_ns=int(bar.ts_event_ns),
                    previous_state="SESSION_RAID_RECLAIM_CONFIRMED",
                    next_state="CONFIRMED",
                    reason_code="FIRST_LATER_BOUNDARY_TOUCH_CLOSED_INSIDE_AND_WITH_H4_DRAW",
                    reference_price=entry,
                    details={
                        "scenario_family": SCENARIO_FAMILY,
                        "execution_time_ns": execution_time_ns,
                        "target_id": target.target_id,
                        "target_source": target.target_source,
                        "target_level": target.target_level,
                        "net_reward_risk": net_reward_risk,
                    },
                ),
            )
            signal = QuoteResiliencySignal(
                scenario_id=candidate.scenario_id.replace("day-delivery", "session-transfer"),
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
                atr=float(bar.atr),
                causal_stop_slippage_reserve=reserve,
                expected_loss_per_unit=expected_loss,
                expected_gain_per_unit=expected_gain,
                net_reward_risk=net_reward_risk,
                interaction_time_ns=int(candidate.interaction_time_ns),
                response_time_ns=int(candidate.trigger_time_ns),
                retest_time_ns=int(bar.ts_event_ns),
                events=events,
                details={
                    "scenario_family": SCENARIO_FAMILY,
                    "signal_revision": SIGNAL_REVISION,
                    "route_name": candidate.route_name,
                    "source_name": source.name,
                    "draw_signature": candidate.draw.signature,
                    "draw_direction": candidate.draw.direction,
                    "draw_origin": candidate.draw.origin_level,
                    "source_low": source.low,
                    "source_high": source.high,
                    "source_midpoint": (source.low + source.high) / 2.0,
                    "session_target_level": target.source_target_level,
                    "frozen_htf_target_level": target.frozen_htf_target_level,
                    "selected_target_contract": "NEAREST_UNCONSUMED_SOURCE_OPPOSITE_OR_FROZEN_HTF_LIQUIDITY",
                    "entry_mode": "FIRST_TEN_SECOND_BUCKET_AFTER_COMPLETED_FIRST_FIVE_MINUTE_BOUNDARY_RETEST",
                    "holding_horizon": "INTRADAY_TENS_OF_MINUTES_TO_SIX_HOURS",
                    "scalping_alpha_inputs": False,
                    "retest_five_index": position,
                },
            )
            signals.setdefault(execution_time_ns, []).append(signal)
            diagnostics["SIGNAL"] += 1
            break

        if not touched:
            _reject(rejected, diagnostics, candidate=candidate, reason="NO_SOURCE_BOUNDARY_RETEST_BEFORE_ROUTE_END")

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


__all__ = [
    "SCENARIO_FAMILY",
    "SIGNAL_REVISION",
    "SessionLiquidityTransferConfig",
    "build_session_liquidity_transfer_signals",
]

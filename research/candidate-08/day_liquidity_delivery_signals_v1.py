"""Pure causal day-liquidity-delivery signal builder for candidate-08.

The detector is deliberately day-trading scale. It combines:
- a completed four-hour draw on liquidity,
- a completed Europe/US session raid or acceptance route,
- a later five-minute market-structure displacement with a real three-bar FVG,
- the first subsequent FVG mitigation that closes back with the higher-time-frame draw, and
- the first completed ten-second execution bucket after that five-minute confirmation.

It contains no order, fill, account, sizing, PnL, or backtest-engine logic. NautilusTrader owns
execution and shared-account accounting.
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
    DayLiquidityDeliveryConfig,
    RouteCandidate,
    Swing,
    entry_is_in_draw_location,
    first_execution_position_after,
)
from day_liquidity_delivery_htf_v1 import (
    build_draw_contexts,
    same_draw,
    target_still_active,
)
from day_liquidity_delivery_routes_v1 import build_route_candidates
from quote_resiliency_signals import (
    QuoteResiliencyLogicEvent,
    QuoteResiliencySignal,
    QuoteResiliencySignalBundle,
)
from range_fvg_logic import ExternalLevel, FiveMinuteBar


SIGNAL_REVISION = "DAY_LIQUIDITY_DELIVERY_SIGNALS_V1"
RAID_FAMILY = "DRAW_ALIGNED_RAID_REVERSAL"
ACCEPTANCE_FAMILY = "DRAW_ALIGNED_ACCEPTANCE_CONTINUATION"
FAMILIES = frozenset((RAID_FAMILY, ACCEPTANCE_FAMILY))


@dataclass(frozen=True, slots=True)
class _FiveSwingState:
    latest_high: Swing | None
    latest_low: Swing | None


@dataclass(frozen=True, slots=True)
class _Displacement:
    position: int
    time_ns: int
    fvg_low: float
    fvg_high: float
    broken_swing: Swing


def _source_name(level: ExternalLevel) -> str:
    return str(getattr(level.source, "value", level.source))


def _finite_five_bar(bar: FiveMinuteBar) -> bool:
    return all(
        isfinite(float(value))
        for value in (
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            bar.atr,
            bar.volume,
            bar.trade_count,
        )
    ) and float(bar.atr) > 0.0


def _confirmed_five_swings(
    bars: tuple[FiveMinuteBar, ...],
    *,
    span: int,
) -> tuple[_FiveSwingState, ...]:
    """Return the latest five-minute swings observable at every completed bar."""

    if span <= 0:
        raise ValueError("five-minute swing span must be positive")
    latest_high: Swing | None = None
    latest_low: Swing | None = None
    result: list[_FiveSwingState] = []
    for current in range(len(bars)):
        candidate = current - span
        if candidate >= span:
            bar = bars[candidate]
            left = bars[candidate - span : candidate]
            right = bars[candidate + 1 : current + 1]
            if len(left) == span and len(right) == span:
                if (
                    float(bar.high) > max(float(item.high) for item in left)
                    and float(bar.high) >= max(float(item.high) for item in right)
                ):
                    latest_high = Swing(
                        kind="HIGH",
                        level=float(bar.high),
                        formed_index=candidate,
                        formed_time_ns=int(bar.ts_event_ns),
                        confirmed_index=current,
                        confirmed_time_ns=int(bars[current].ts_event_ns),
                    )
                if (
                    float(bar.low) < min(float(item.low) for item in left)
                    and float(bar.low) <= min(float(item.low) for item in right)
                ):
                    latest_low = Swing(
                        kind="LOW",
                        level=float(bar.low),
                        formed_index=candidate,
                        formed_time_ns=int(bar.ts_event_ns),
                        confirmed_index=current,
                        confirmed_time_ns=int(bars[current].ts_event_ns),
                    )
        result.append(_FiveSwingState(latest_high=latest_high, latest_low=latest_low))
    return tuple(result)


def _shifted_prior_medians(
    bars: tuple[FiveMinuteBar, ...],
    *,
    lookback: int,
) -> tuple[np.ndarray, np.ndarray]:
    if lookback <= 0:
        raise ValueError("five-minute displacement lookback must be positive")
    bodies = pd.Series([abs(float(bar.close) - float(bar.open)) for bar in bars], dtype=float)
    ranges = pd.Series([float(bar.high) - float(bar.low) for bar in bars], dtype=float)
    body = bodies.shift(1).rolling(lookback, min_periods=lookback).median().to_numpy()
    spread = ranges.shift(1).rolling(lookback, min_periods=lookback).median().to_numpy()
    return body, spread


def _draw_close_location(bar: FiveMinuteBar, direction: int) -> float:
    spread = float(bar.high) - float(bar.low)
    if spread <= 0.0:
        return 0.5
    location = (float(bar.close) - float(bar.low)) / spread
    return location if direction > 0 else 1.0 - location


def _five_displacement_fvg(
    *,
    bars: tuple[FiveMinuteBar, ...],
    position: int,
    direction: int,
    frozen_swing: Swing,
    prior_body_median: np.ndarray,
    prior_range_median: np.ndarray,
    close_location: float,
    tick: float,
) -> _Displacement | None:
    """Detect a separate five-minute MSS/displacement and standard three-bar FVG."""

    if position < 2 or direction not in (-1, 1):
        return None
    bar = bars[position]
    two_back = bars[position - 2]
    if not _finite_five_bar(bar):
        return None
    body_reference = float(prior_body_median[position])
    range_reference = float(prior_range_median[position])
    if (
        not isfinite(body_reference)
        or not isfinite(range_reference)
        or body_reference <= 0.0
        or range_reference <= 0.0
    ):
        return None
    directional_body = direction * (float(bar.close) - float(bar.open))
    bar_range = float(bar.high) - float(bar.low)
    if direction > 0:
        broke = float(bar.close) > float(frozen_swing.level) + tick
        fvg_low = float(two_back.high)
        fvg_high = float(bar.low)
    else:
        broke = float(bar.close) < float(frozen_swing.level) - tick
        fvg_low = float(bar.high)
        fvg_high = float(two_back.low)
    if not (
        broke
        and directional_body >= body_reference
        and bar_range >= range_reference
        and _draw_close_location(bar, direction) >= close_location
        and fvg_high >= fvg_low + tick
    ):
        return None
    return _Displacement(
        position=position,
        time_ns=int(bar.ts_event_ns),
        fvg_low=fvg_low,
        fvg_high=fvg_high,
        broken_swing=frozen_swing,
    )


def _first_fvg_touch_result(
    *,
    bar: FiveMinuteBar,
    direction: int,
    fvg_low: float,
    fvg_high: float,
) -> str:
    """Classify the first post-displacement FVG touch without cherry-picking later touches."""

    if direction > 0:
        touched = float(bar.low) <= float(fvg_high)
        if not touched:
            return "NO_TOUCH"
        held = float(bar.close) >= float(fvg_low)
        closes_with_draw = float(bar.close) > float(bar.open)
    elif direction < 0:
        touched = float(bar.high) >= float(fvg_low)
        if not touched:
            return "NO_TOUCH"
        held = float(bar.close) <= float(fvg_high)
        closes_with_draw = float(bar.close) < float(bar.open)
    else:
        raise ValueError("direction must be -1 or +1")
    return "VALID_RETRACE" if held and closes_with_draw else "INVALID_FIRST_TOUCH"


def _structural_stop(
    *,
    direction: int,
    structural_reference: float,
    atr: float,
    stop_buffer_atr: float,
) -> float:
    buffer = stop_buffer_atr * atr
    return structural_reference - buffer if direction > 0 else structural_reference + buffer


def _cost_geometry(
    *,
    direction: int,
    entry: float,
    stop: float,
    target: float,
    fee_rate: float,
    tick: float,
    stop_slippage_reserve: float,
) -> tuple[float, float, float] | None:
    valid = stop < entry < target if direction > 0 else target < entry < stop
    if not valid:
        return None
    stop_reserve = max(tick, float(stop_slippage_reserve))
    loss = abs(entry - stop) + fee_rate * (entry + stop) + tick + stop_reserve
    gross_gain = target - entry if direction > 0 else entry - target
    gain = gross_gain - fee_rate * (entry + target) - 2.0 * tick
    if loss <= 0.0 or gain <= 0.0:
        return None
    return loss, gain, gain / loss


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


def _route_event(
    candidate: RouteCandidate,
    symbol: str,
    instrument_id: str,
) -> QuoteResiliencyLogicEvent:
    if candidate.family == RAID_FAMILY:
        event_type = "DRAW_ALIGNED_SESSION_RAID_RECLAIM_CONFIRMED"
        reason = "OPPOSITE_SOURCE_SESSION_LIQUIDITY_RAIDED_AND_RECLAIMED"
    elif candidate.family == ACCEPTANCE_FAMILY:
        event_type = "DRAW_ALIGNED_SESSION_ACCEPTANCE_RETEST_CONFIRMED"
        reason = "SAME_SIDE_SOURCE_SESSION_BOUNDARY_ACCEPTED_THEN_SEPARATELY_RETESTED"
    else:
        raise RuntimeError(f"unexpected route family: {candidate.family!r}")
    return _event(
        candidate=candidate,
        symbol=symbol,
        instrument_id=instrument_id,
        event_type=event_type,
        event_time_ns=candidate.trigger_time_ns,
        previous_state="HTF_DRAW_ARMED",
        next_state="SESSION_ROUTE_CONFIRMED",
        reason_code=reason,
        reference_price=float(candidate.boundary_level),
        details={
            "scenario_family": candidate.family,
            "route_name": candidate.route_name,
            "source_name": candidate.source_name,
            "draw_signature": candidate.draw.signature,
            "draw_origin": candidate.draw.origin_level,
            "target_id": candidate.target.level_id,
            "target_source": _source_name(candidate.target),
            "target_level": candidate.target.level,
            "structural_reference": candidate.structural_reference,
        },
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
            "scenario_family": candidate.family,
            "route_name": candidate.route_name,
            "reason": reason,
            "position": position,
            "trigger_time_ns": candidate.trigger_time_ns,
            "interaction_time_ns": candidate.interaction_time_ns,
            "target_id": candidate.target.level_id,
            "details": dict(details or {}),
        }
    )


def build_day_liquidity_delivery_signals(
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
    config: DayLiquidityDeliveryConfig,
) -> QuoteResiliencySignalBundle:
    """Build immutable, future-free day-liquidity-delivery signals."""

    config.validate()
    if tick <= 0.0 or fee_rate < 0.0 or minimum_net_reward_risk <= 0.0:
        raise ValueError("invalid cost contract")
    if not isinstance(data.index, pd.DatetimeIndex) or data.index.tz is None:
        raise TypeError("ten-second execution data must use a timezone-aware DatetimeIndex")
    if len(context_times) != len(context_bars) or len(snapshots) != len(context_bars):
        raise ValueError("five-minute context arrays must have equal lengths")
    if tuple(int(bar.ts_event_ns) for bar in context_bars) != tuple(
        int(value) for value in context_times
    ):
        raise ValueError("context_times must exactly match completed five-minute bars")

    diagnostics: Counter[str] = Counter()
    rejected: list[dict[str, Any]] = []
    signals: dict[int, list[QuoteResiliencySignal]] = {}

    draw_contexts = build_draw_contexts(context_bars, snapshots, config)
    diagnostics["ACTIVE_H4_DRAW_BARS"] = sum(item is not None for item in draw_contexts)
    diagnostics["ACTIVE_HTF_TARGET_BARS"] = sum(
        item is not None and item.target is not None for item in draw_contexts
    )
    route_candidates, route_diagnostics, route_rejections = build_route_candidates(
        symbol=symbol,
        bars=context_bars,
        snapshots=snapshots,
        draw_contexts=draw_contexts,
        config=config,
    )
    diagnostics.update(route_diagnostics)
    diagnostics["ROUTE_CANDIDATES"] = len(route_candidates)
    for candidate in route_candidates:
        diagnostics[f"ROUTE_FAMILY_{candidate.family}"] += 1
    rejected.extend(route_rejections)

    swing_states = _confirmed_five_swings(context_bars, span=config.five_swing_span)
    prior_body, prior_range = _shifted_prior_medians(
        context_bars,
        lookback=config.five_displacement_lookback,
    )
    stop_reserves = causal_stop_slippage_reserve_series(data, tick=tick)
    data_times = data.index.as_unit("ns").asi8

    for candidate in route_candidates:
        if candidate.family not in FAMILIES:
            _reject(rejected, diagnostics, candidate=candidate, reason="UNRECOGNIZED_ROUTE_FAMILY")
            continue
        trigger = int(candidate.trigger_five_index)
        if trigger < 0 or trigger >= len(context_bars):
            _reject(rejected, diagnostics, candidate=candidate, reason="INVALID_ROUTE_TRIGGER_INDEX")
            continue
        state = swing_states[trigger]
        frozen_swing = state.latest_high if candidate.direction > 0 else state.latest_low
        if frozen_swing is None:
            _reject(
                rejected,
                diagnostics,
                candidate=candidate,
                reason="NO_CAUSALLY_CONFIRMED_OPPOSING_FIVE_MINUTE_SWING",
            )
            continue
        diagnostics["FROZEN_FIVE_MINUTE_SWING"] += 1

        deadline_ns = min(
            int(candidate.route_end_ns),
            int(candidate.trigger_time_ns)
            + int(config.maximum_delivery_minutes) * 60 * 1_000_000_000,
        )
        displacement: _Displacement | None = None
        first_touch_seen = False

        for position in range(trigger + 1, len(context_bars)):
            bar = context_bars[position]
            if int(bar.ts_event_ns) > deadline_ns:
                break
            current_draw = draw_contexts[position]
            if not same_draw(current_draw, candidate.draw):
                _reject(
                    rejected,
                    diagnostics,
                    candidate=candidate,
                    reason="HTF_DRAW_CHANGED_BEFORE_DELIVERY",
                    position=position,
                )
                displacement = None
                first_touch_seen = True
                break
            if not target_still_active(
                snapshots,
                five_index=position,
                target_id=candidate.target.level_id,
            ):
                _reject(
                    rejected,
                    diagnostics,
                    candidate=candidate,
                    reason="HTF_TARGET_CONSUMED_BEFORE_DELIVERY",
                    position=position,
                )
                displacement = None
                first_touch_seen = True
                break

            if displacement is None:
                found = _five_displacement_fvg(
                    bars=context_bars,
                    position=position,
                    direction=candidate.direction,
                    frozen_swing=frozen_swing,
                    prior_body_median=prior_body,
                    prior_range_median=prior_range,
                    close_location=config.five_close_location,
                    tick=tick,
                )
                if found is None:
                    continue
                displacement = found
                diagnostics["FIVE_MINUTE_MSS_FVG_CONFIRMED"] += 1
                continue

            if position <= displacement.position:
                raise RuntimeError("FVG retrace cannot be evaluated on displacement bar")
            touch = _first_fvg_touch_result(
                bar=bar,
                direction=candidate.direction,
                fvg_low=displacement.fvg_low,
                fvg_high=displacement.fvg_high,
            )
            if touch == "NO_TOUCH":
                continue
            first_touch_seen = True
            if touch != "VALID_RETRACE":
                _reject(
                    rejected,
                    diagnostics,
                    candidate=candidate,
                    reason="FIRST_FVG_TOUCH_FAILED_DELIVERY_CONFIRMATION",
                    position=position,
                    details={"fvg_low": displacement.fvg_low, "fvg_high": displacement.fvg_high},
                )
                break
            diagnostics["FIRST_FVG_RETRACE_HELD"] += 1

            execution_position = first_execution_position_after(data_times, int(bar.ts_event_ns))
            if execution_position is None:
                _reject(
                    rejected,
                    diagnostics,
                    candidate=candidate,
                    reason="NO_LATER_COMPLETED_TEN_SECOND_EXECUTION_BUCKET",
                    position=position,
                )
                break
            execution_time = data.index[execution_position]
            execution_time_ns = int(execution_time.as_unit("ns").value)
            if execution_time_ns <= int(bar.ts_event_ns):
                raise RuntimeError("execution bucket did not occur after completed retrace")
            entry_row = data.iloc[execution_position]
            try:
                entry = float(entry_row["close"])
            except (KeyError, TypeError, ValueError):
                entry = float("nan")
            if not isfinite(entry) or entry <= 0.0:
                _reject(
                    rejected,
                    diagnostics,
                    candidate=candidate,
                    reason="INVALID_EXECUTION_REFERENCE",
                    position=position,
                )
                break
            if not entry_is_in_draw_location(candidate.draw, candidate.target, entry=entry):
                _reject(
                    rejected,
                    diagnostics,
                    candidate=candidate,
                    reason="ENTRY_OUTSIDE_PREMIUM_DISCOUNT_DRAW_LOCATION",
                    position=position,
                    details={
                        "entry": entry,
                        "origin": candidate.draw.origin_level,
                        "target": candidate.target.level,
                    },
                )
                break
            diagnostics["PREMIUM_DISCOUNT_LOCATION_PASS"] += 1

            atr = float(bar.atr)
            stop = _structural_stop(
                direction=candidate.direction,
                structural_reference=float(candidate.structural_reference),
                atr=atr,
                stop_buffer_atr=config.structural_stop_buffer_atr,
            )
            reserve = float(stop_reserves.iloc[execution_position])
            geometry = _cost_geometry(
                direction=candidate.direction,
                entry=entry,
                stop=stop,
                target=float(candidate.target.level),
                fee_rate=fee_rate,
                tick=tick,
                stop_slippage_reserve=reserve,
            )
            if geometry is None:
                _reject(
                    rejected,
                    diagnostics,
                    candidate=candidate,
                    reason="INVALID_COST_AFTER_GEOMETRY",
                    position=position,
                )
                break
            expected_loss, expected_gain, net_reward_risk = geometry
            if net_reward_risk < minimum_net_reward_risk:
                _reject(
                    rejected,
                    diagnostics,
                    candidate=candidate,
                    reason="INSUFFICIENT_COST_AFTER_HTF_TARGET",
                    position=position,
                    details={"net_reward_risk": net_reward_risk},
                )
                break
            diagnostics["COST_AFTER_TARGET_PASS"] += 1

            events = (
                _route_event(candidate, symbol, instrument_id),
                _event(
                    candidate=candidate,
                    symbol=symbol,
                    instrument_id=instrument_id,
                    event_type="FIVE_MINUTE_MSS_DISPLACEMENT_FVG_CONFIRMED",
                    event_time_ns=displacement.time_ns,
                    previous_state="SESSION_ROUTE_CONFIRMED",
                    next_state="FIVE_MINUTE_DELIVERY_DISPLACEMENT",
                    reason_code="FROZEN_OPPOSING_SWING_BROKEN_WITH_DISPLACEMENT_AND_STANDARD_FVG",
                    reference_price=float(displacement.broken_swing.level),
                    details={
                        "scenario_family": candidate.family,
                        "fvg_low": displacement.fvg_low,
                        "fvg_high": displacement.fvg_high,
                        "broken_swing_kind": displacement.broken_swing.kind,
                        "broken_swing_level": displacement.broken_swing.level,
                        "broken_swing_confirmed_time_ns": displacement.broken_swing.confirmed_time_ns,
                    },
                ),
                _event(
                    candidate=candidate,
                    symbol=symbol,
                    instrument_id=instrument_id,
                    event_type="FIRST_FIVE_MINUTE_FVG_RETRACE_DELIVERY_CONFIRMED",
                    event_time_ns=int(bar.ts_event_ns),
                    previous_state="FIVE_MINUTE_DELIVERY_DISPLACEMENT",
                    next_state="CONFIRMED",
                    reason_code="FIRST_SUBSEQUENT_FVG_TOUCH_HELD_AND_CLOSED_WITH_HTF_DRAW",
                    reference_price=entry,
                    details={
                        "scenario_family": candidate.family,
                        "execution_time_ns": execution_time_ns,
                        "execution_contract": (
                            "FIRST_COMPLETED_TEN_SECOND_BUCKET_STRICTLY_AFTER_FIVE_MINUTE_RETRACE"
                        ),
                        "net_reward_risk": net_reward_risk,
                    },
                ),
            )
            signal = QuoteResiliencySignal(
                scenario_id=candidate.scenario_id,
                scenario_family=candidate.family,
                symbol=symbol,
                instrument_id=instrument_id,
                direction=candidate.direction,
                signal_index=execution_position,
                signal_time_ns=execution_time_ns,
                boundary_id=candidate.boundary_id,
                boundary_source=f"SESSION_{candidate.route_name}",
                boundary_level=float(candidate.boundary_level),
                target_id=candidate.target.level_id,
                target_source=_source_name(candidate.target),
                external_target=float(candidate.target.level),
                entry_reference=entry,
                structural_stop=stop,
                stop_reference=float(candidate.structural_reference),
                stop_reference_source="SESSION_RAID_OR_ACCEPTANCE_RETEST_EXTREME",
                atr=atr,
                causal_stop_slippage_reserve=reserve,
                expected_loss_per_unit=expected_loss,
                expected_gain_per_unit=expected_gain,
                net_reward_risk=net_reward_risk,
                interaction_time_ns=int(candidate.interaction_time_ns),
                response_time_ns=int(displacement.time_ns),
                retest_time_ns=int(bar.ts_event_ns),
                events=events,
                details={
                    "scenario_family": candidate.family,
                    "signal_revision": SIGNAL_REVISION,
                    "route_name": candidate.route_name,
                    "source_name": candidate.source_name,
                    "draw_signature": candidate.draw.signature,
                    "draw_direction": candidate.draw.direction,
                    "draw_origin": candidate.draw.origin_level,
                    "draw_origin_time_ns": candidate.draw.observed_time_ns,
                    "draw_displacement_time_ns": candidate.draw.observed_time_ns,
                    "target_contract": "NEAREST_ACTIVE_COMPLETED_DAY_WEEK_ELSE_MINIMUM_H4_ATR",
                    "target_id": candidate.target.level_id,
                    "target_source": _source_name(candidate.target),
                    "frozen_five_swing_kind": frozen_swing.kind,
                    "frozen_five_swing_level": frozen_swing.level,
                    "frozen_five_swing_confirmed_time_ns": frozen_swing.confirmed_time_ns,
                    "displacement_five_index": displacement.position,
                    "retrace_five_index": position,
                    "execution_position": execution_position,
                    "fvg_low": displacement.fvg_low,
                    "fvg_high": displacement.fvg_high,
                    "entry_location_contract": (
                        "LONG_DISCOUNT_SHORT_PREMIUM_OF_ORIGIN_TO_TARGET_RANGE"
                    ),
                    "entry_mode": (
                        "TEN_SECOND_MARKET_AFTER_COMPLETED_FIRST_FIVE_MINUTE_FVG_RETRACE"
                    ),
                    "invalidation_contract": (
                        "SESSION_RAID_OR_ACCEPTANCE_RETEST_EXTREME_PLUS_FIXED_FIVE_ATR_BUFFER"
                    ),
                },
            )
            signals.setdefault(execution_time_ns, []).append(signal)
            diagnostics["SIGNAL"] += 1
            break

        if displacement is None and not first_touch_seen:
            _reject(
                rejected,
                diagnostics,
                candidate=candidate,
                reason="NO_FIVE_MINUTE_MSS_FVG_BEFORE_ROUTE_EXPIRY",
            )
        elif displacement is not None and not first_touch_seen:
            _reject(
                rejected,
                diagnostics,
                candidate=candidate,
                reason="NO_FIRST_FVG_RETRACE_BEFORE_ROUTE_EXPIRY",
                details={
                    "displacement_time_ns": displacement.time_ns,
                    "fvg_low": displacement.fvg_low,
                    "fvg_high": displacement.fvg_high,
                },
            )

    grouped = {
        timestamp: tuple(
            sorted(
                items,
                key=lambda signal: (
                    signal.net_reward_risk,
                    signal.target_source,
                    signal.scenario_id,
                ),
                reverse=True,
            )
        )
        for timestamp, items in sorted(signals.items())
    }
    diagnostics["SIGNAL_TIMES"] = len(grouped)
    return QuoteResiliencySignalBundle(
        signals_by_time_ns=grouped,
        diagnostics=dict(sorted(diagnostics.items())),
        rejected_scenarios=tuple(rejected),
    )

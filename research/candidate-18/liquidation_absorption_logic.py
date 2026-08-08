"""Pure causal state transitions for forced-basis liquidation absorption."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


@dataclass(frozen=True, slots=True)
class LiquidationEvent:
    event_direction: int
    event_open: float
    event_high: float
    event_low: float
    event_close: float
    rolling_vwap: float
    pre_event_objective: float
    atr: float
    perp_basis_z_directional: float
    mark_basis_z_directional: float


@dataclass(frozen=True, slots=True)
class MinuteObservation:
    high: float
    low: float
    close: float
    aggressor_imbalance: float
    perp_basis_z_directional: float
    mark_basis_z_directional: float


@dataclass(frozen=True, slots=True)
class Confirmation:
    confirmed: bool
    reason: str
    entry: float | None
    stop: float | None
    target: float | None


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(value)) for value in values)


def _reversal_side(event: LiquidationEvent) -> int:
    if event.event_direction not in (-1, 1):
        raise ValueError("event_direction must be -1 or 1")
    return -event.event_direction


def _basis_compressed(event: LiquidationEvent, bar: MinuteObservation) -> bool:
    event_peak = max(event.perp_basis_z_directional, event.mark_basis_z_directional)
    current_peak = max(bar.perp_basis_z_directional, bar.mark_basis_z_directional)
    return event_peak >= 1.0 and current_peak <= 0.5 * event_peak


def _geometry(
    event: LiquidationEvent,
    observations: Iterable[MinuteObservation],
    entry: float,
) -> tuple[float, float] | None:
    bars = list(observations)
    side = _reversal_side(event)
    if side > 0:
        extreme = min([event.event_low, *(bar.low for bar in bars)])
        stop = extreme - 0.05 * event.atr
        objectives = [x for x in (event.rolling_vwap, event.pre_event_objective) if x > entry]
        natural_reward = min(objectives) - entry if objectives else float("nan")
        risk = entry - stop
    else:
        extreme = max([event.event_high, *(bar.high for bar in bars)])
        stop = extreme + 0.05 * event.atr
        objectives = [x for x in (event.rolling_vwap, event.pre_event_objective) if x < entry]
        natural_reward = entry - max(objectives) if objectives else float("nan")
        risk = stop - entry
    if not _finite(stop, natural_reward, risk) or risk <= 0.0 or natural_reward < risk:
        return None
    target = entry + side * min(natural_reward, 1.5 * risk)
    return stop, target


def evaluate_confirmation(
    policy: str,
    event: LiquidationEvent,
    observations: list[MinuteObservation],
) -> Confirmation:
    """Evaluate only completed observations after the liquidation minute."""
    if not observations:
        return Confirmation(False, "NO_LATER_OBSERVATION", None, None, None)
    if not _finite(
        event.event_open,
        event.event_high,
        event.event_low,
        event.event_close,
        event.rolling_vwap,
        event.pre_event_objective,
        event.atr,
        event.perp_basis_z_directional,
        event.mark_basis_z_directional,
    ) or event.atr <= 0.0:
        return Confirmation(False, "EVENT_INPUT_NOT_FINITE", None, None, None)
    side = _reversal_side(event)
    current = observations[-1]
    if not _finite(
        current.high,
        current.low,
        current.close,
        current.aggressor_imbalance,
        current.perp_basis_z_directional,
        current.mark_basis_z_directional,
    ):
        return Confirmation(False, "OBSERVATION_NOT_FINITE", None, None, None)
    if side * current.aggressor_imbalance < 0.05:
        return Confirmation(False, "REVERSAL_AGGRESSOR_FLOW_NOT_PRESENT", None, None, None)
    if not _basis_compressed(event, current):
        return Confirmation(False, "DERIVATIVE_BASIS_NOT_COMPRESSED", None, None, None)

    midpoint = 0.5 * (event.event_high + event.event_low)
    if policy == "OPEN_RECLAIM":
        crossed = current.close >= event.event_open if side > 0 else current.close <= event.event_open
        if not crossed:
            return Confirmation(False, "EVENT_OPEN_NOT_RECLAIMED", None, None, None)
    elif policy == "TWO_MINUTE_ABSORPTION":
        if len(observations) < 2:
            return Confirmation(False, "NEEDS_TWO_COMPLETED_MINUTES", None, None, None)
        previous = observations[-2]
        no_new_extreme = (
            min(previous.low, current.low) >= event.event_low - 0.05 * event.atr
            if side > 0
            else max(previous.high, current.high) <= event.event_high + 0.05 * event.atr
        )
        cumulative_flow = side * (previous.aggressor_imbalance + current.aggressor_imbalance)
        crossed = current.close >= midpoint if side > 0 else current.close <= midpoint
        if not no_new_extreme:
            return Confirmation(False, "LIQUIDATION_EXTREME_STILL_EXPANDING", None, None, None)
        if cumulative_flow < 0.10:
            return Confirmation(False, "TWO_MINUTE_REVERSAL_FLOW_TOO_WEAK", None, None, None)
        if not crossed:
            return Confirmation(False, "EVENT_MIDPOINT_NOT_RECLAIMED", None, None, None)
    elif policy == "VWAP_RECLAIM":
        crossed = current.close >= event.rolling_vwap if side > 0 else current.close <= event.rolling_vwap
        if not crossed:
            return Confirmation(False, "ROLLING_VWAP_NOT_RECLAIMED", None, None, None)
    else:
        raise ValueError(f"unknown policy: {policy}")

    geometry = _geometry(event, observations, current.close)
    if geometry is None:
        return Confirmation(False, "STRUCTURAL_TARGET_BELOW_ONE_R", None, None, None)
    stop, target = geometry
    return Confirmation(True, f"{policy}_FORCED_BASIS_ABSORPTION_CONFIRMED", current.close, stop, target)


__all__ = ["Confirmation", "LiquidationEvent", "MinuteObservation", "evaluate_confirmation"]

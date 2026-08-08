"""Pure L1 pressure-transition logic for Candidate 16 v4.

This module has no orders, fills, portfolio state, or PnL. It uses only the
information present in the frozen one-minute bookTicker dataset:

- whole-minute time-weighted imbalance;
- end-of-minute imbalance;
- end microprice premium;
- average versus ending spread;
- quote update activity.

The comparison is categorical and direction-relative. No magnitude threshold
was selected from Candidate 16 v1/v2 outcomes.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class PressureObservation:
    imbalance_twap: float
    imbalance_close: float
    microprice_premium_close: float
    spread_bps_twap: float
    spread_bps_close: float
    update_rate: float

    def validate(self) -> None:
        values = (
            self.imbalance_twap,
            self.imbalance_close,
            self.microprice_premium_close,
            self.spread_bps_twap,
            self.spread_bps_close,
            self.update_rate,
        )
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("L1 pressure observation must be finite")
        if self.spread_bps_twap <= 0.0 or self.spread_bps_close <= 0.0:
            raise ValueError("L1 spreads must be positive")
        if self.update_rate <= 0.0:
            raise ValueError("L1 update rate must be positive")


def _direction(value: int) -> int:
    if value not in (-1, 1):
        raise ValueError("direction must be -1 or +1")
    return value


def orderly_close(observation: PressureObservation) -> bool:
    """Require pressure to resolve without ending in a wider spread."""
    observation.validate()
    return observation.spread_bps_close <= observation.spread_bps_twap


def failure_pressure_transition(
    parent_direction: int,
    observation: PressureObservation,
) -> bool:
    """Return whether average attack pressure flipped by minute completion.

    Example for an upward parent attack:
    - positive TWAP imbalance says the minute was mostly bid-pressure dominated;
    - negative closing imbalance and microprice premium say that pressure had
      transferred to the sell side by the completed observation;
    - the spread may not end wider than its time-weighted average.
    """
    direction = _direction(parent_direction)
    if not orderly_close(observation):
        return False
    reversal = -direction
    return (
        direction * observation.imbalance_twap > 0.0
        and reversal * observation.imbalance_close > 0.0
        and reversal * observation.microprice_premium_close > 0.0
    )


def pressure_persistence(
    direction: int,
    observation: PressureObservation,
) -> bool:
    """Return whether average and closing L1 pressure persist directionally."""
    side = _direction(direction)
    if not orderly_close(observation):
        return False
    return (
        side * observation.imbalance_twap > 0.0
        and side * observation.imbalance_close > 0.0
        and side * observation.microprice_premium_close > 0.0
    )


def pressure_state(
    parent_direction: int,
    observation: PressureObservation,
) -> str:
    """Return one mutually exclusive state for audit evidence."""
    if failure_pressure_transition(parent_direction, observation):
        return "PRESSURE_FLIPPED"
    if pressure_persistence(parent_direction, observation):
        return "PRESSURE_PERSISTED"
    return "PRESSURE_UNRESOLVED"


__all__ = [
    "PressureObservation",
    "failure_pressure_transition",
    "orderly_close",
    "pressure_persistence",
    "pressure_state",
]

"""Typed observations, primitives, transitions, and signals for candidate-06."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

@dataclass(frozen=True, slots=True)
class BarObservation:
    ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float
    trades: int

    @property
    def flow_ratio(self) -> float:
        if self.volume <= 0.0:
            return 0.0
        return (2.0 * self.taker_buy_volume - self.volume) / self.volume


@dataclass(frozen=True, slots=True)
class PrimitiveSnapshot:
    index: int
    observation: BarObservation
    ready: bool
    atr: float
    rel_volume: float
    flow_ratio: float
    body_atr: float
    range_atr: float
    upper_wick_fraction: float
    lower_wick_fraction: float
    close_location: float
    upper_fast: float | None
    lower_fast: float | None
    upper_slow: float | None
    lower_slow: float | None
    slow_mid: float | None
    range_position: float | None
    upper_pool_touches: int
    lower_pool_touches: int


@dataclass(frozen=True, slots=True)
class SweepPrimitive:
    side: str  # UPPER or LOWER
    level: float
    depth_atr: float
    pool_touches: int
    external_to_slow_range: bool


@dataclass(frozen=True, slots=True)
class ScenarioTransition:
    scenario_id: str
    event_type: str
    previous_state: str
    next_state: str
    reason_code: str
    reference_price: float | None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScenarioSignal:
    scenario_id: str
    family: str  # SRR or SAC
    direction: str  # LONG or SHORT
    observed_ts_ns: int
    reference_entry: float
    stop_price: float
    target_price: float
    target_reason: str
    atr: float
    liquidity_level: float
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScenarioStep:
    transitions: tuple[ScenarioTransition, ...] = ()
    signal: ScenarioSignal | None = None


@dataclass(slots=True)
class _Episode:
    scenario_id: str
    family: str
    direction: str
    side: str
    state: str
    level: float
    extreme: float
    started_index: int
    started_ts_ns: int
    atr_at_start: float
    flow_at_start: float
    rel_volume_at_start: float
    midpoint: float
    lower_fast_at_start: float
    upper_fast_at_start: float
    lower_slow_at_start: float
    upper_slow_at_start: float



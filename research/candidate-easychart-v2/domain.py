"""Pure data contracts for the EasyChart v2 causal state engine."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math


class Side(int, Enum):
    LONG = 1
    SHORT = -1


class Family(str, Enum):
    REJECTION_RETEST_CLOSE = "REJECTION_RETEST_CLOSE"
    ACCEPTANCE_RETEST_CLOSE = "ACCEPTANCE_RETEST_CLOSE"


@dataclass(frozen=True, slots=True)
class Candle:
    ts_close_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        values = (self.open, self.high, self.low, self.close, self.volume)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("candle values must be finite")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("invalid OHLC geometry")


@dataclass(slots=True)
class Boundary:
    boundary_id: str
    side: str  # HIGH or LOW
    level: float
    event_time_ns: int
    observed_time_ns: int
    span: int
    prominence_atr: float
    consumed: bool = False


@dataclass(frozen=True, slots=True)
class TradePlan:
    plan_id: str
    causal_event_id: str
    symbol: str
    family: Family
    side: Side
    observed_time_ns: int
    entry: float
    stop: float
    target: float
    gross_rr: float
    source_boundary_id: str
    target_boundary_id: str
    source_level: float
    source_event_time_ns: int
    source_observed_time_ns: int
    source_span: int
    source_prominence_atr: float
    target_event_time_ns: int
    target_observed_time_ns: int
    target_span: int
    target_prominence_atr: float
    interaction_index: int
    confirmation_index: int
    interaction_time_ns: int
    confirmation_time_ns: int
    trigger_extreme: float
    origin_boundary_id: str | None = None
    origin_level: float | None = None


@dataclass(slots=True)
class RejectionCandidate:
    source: Boundary
    target: Boundary
    side: Side
    sweep_index: int
    sweep_time_ns: int
    sweep_close: float
    excursion: float
    confirmation_deadline: int
    confirmed_index: int | None = None
    confirmed_time_ns: int | None = None


@dataclass(slots=True)
class AcceptanceCandidate:
    source: Boundary
    target: Boundary
    side: Side
    break_index: int
    break_time_ns: int
    break_extreme: float
    origin: Boundary
    confirmed_index: int | None = None
    confirmed_time_ns: int | None = None


@dataclass(frozen=True, slots=True)
class EngineConfig:
    pivot_spans: tuple[int, ...] = (2, 6, 12)
    atr_period: int = 14
    min_prominence_atr: float = 1.0
    min_gross_rr: float = 1.0
    tick_size: float = 0.1
    rejection_confirmation_bars: int = 2
    enable_rejection: bool = True
    enable_acceptance: bool = True

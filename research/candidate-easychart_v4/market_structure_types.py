"""Data contracts for the causal EasyChart market-structure grammar."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math

from domain import Side

class PivotKind(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class BoundaryRole(str, Enum):
    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"


class StructureKind(str, Enum):
    SWING_HIGH = "SWING_HIGH"
    SWING_LOW = "SWING_LOW"
    TRENDLINE_SUPPORT = "TRENDLINE_SUPPORT"
    TRENDLINE_RESISTANCE = "TRENDLINE_RESISTANCE"
    CHANNEL_LOWER = "CHANNEL_LOWER"
    CHANNEL_UPPER = "CHANNEL_UPPER"
    CHANNEL_MIDLINE = "CHANNEL_MIDLINE"
    CHANNEL_EXTENSION = "CHANNEL_EXTENSION"


class ChannelDirection(str, Enum):
    ASCENDING = "ASCENDING"
    DESCENDING = "DESCENDING"


class StructurePath(str, Enum):
    BOUNCE = "BOUNCE"
    FAKEOUT = "FAKEOUT"
    TRAP_REENTRY = "TRAP_REENTRY"
    ACCEPTANCE = "ACCEPTANCE"
    CHANNEL_FAILURE_ACCEPTANCE = "CHANNEL_FAILURE_ACCEPTANCE"


@dataclass(frozen=True, slots=True)
class ConfirmedPivot:
    pivot_id: str
    kind: PivotKind
    timeframe_minutes: int
    span: int
    index: int
    event_time_ns: int
    observed_index: int
    observed_time_ns: int
    price: float
    strength_ratio: float


@dataclass(slots=True)
class StructuralBoundary:
    boundary_id: str
    kind: StructureKind
    role: BoundaryRole
    timeframe_minutes: int
    observed_time_ns: int
    observed_index: int
    anchor_1_time_ns: int
    anchor_1_price: float
    anchor_2_time_ns: int
    anchor_2_price: float
    strength_ratio: float
    pivot_span: int
    channel_id: str | None = None
    opposite_boundary_id: str | None = None
    midline_boundary_id: str | None = None
    active: bool = True
    rejection_used: bool = False
    acceptance_used: bool = False
    first_touch_index: int | None = None
    first_touch_time_ns: int | None = None
    consumed_time_ns: int | None = None

    def __post_init__(self) -> None:
        if self.timeframe_minutes <= 0 or self.pivot_span <= 0:
            raise ValueError("timeframe and pivot span must be positive")
        values = (
            self.anchor_1_price,
            self.anchor_2_price,
            self.strength_ratio,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("boundary values must be finite")
        if self.anchor_2_time_ns < self.anchor_1_time_ns:
            raise ValueError("boundary anchors must be time ordered")

    @property
    def slope_per_ns(self) -> float:
        elapsed = self.anchor_2_time_ns - self.anchor_1_time_ns
        if elapsed == 0:
            return 0.0
        return (self.anchor_2_price - self.anchor_1_price) / elapsed

    @property
    def normalized_slope(self) -> float:
        base = max(abs(self.anchor_1_price), abs(self.anchor_2_price), 1e-12)
        timeframe_ns = self.timeframe_minutes * 60_000_000_000
        return self.slope_per_ns * timeframe_ns / base

    def level_at(self, ts_ns: int) -> float:
        return self.anchor_1_price + self.slope_per_ns * (ts_ns - self.anchor_1_time_ns)

    @property
    def zone_id(self) -> str:
        return self.boundary_id

    @property
    def lower(self) -> float:
        return min(self.anchor_1_price, self.anchor_2_price)

    @property
    def upper(self) -> float:
        return max(self.anchor_1_price, self.anchor_2_price)

    @property
    def invalidation(self) -> float:
        return self.anchor_2_price

    @property
    def impulse_extreme(self) -> float:
        return self.anchor_2_price

    @property
    def formed_index(self) -> int:
        return self.observed_index

    @property
    def formed_time_ns(self) -> int:
        return self.anchor_2_time_ns

    @property
    def formation_indices(self) -> tuple[int, ...]:
        return (self.observed_index,)


@dataclass(slots=True)
class ChannelState:
    channel_id: str
    direction: ChannelDirection
    timeframe_minutes: int
    pivot_span: int
    observed_index: int
    observed_time_ns: int
    lower_boundary_id: str
    upper_boundary_id: str
    midline_boundary_id: str
    anchor_pivot_ids: tuple[str, str, str]
    active: bool = True
    first_interaction_time_ns: int | None = None
    last_bounce_boundary_id: str | None = None
    last_bounce_time_ns: int | None = None
    midline_reached_after_bounce: bool = False


@dataclass(frozen=True, slots=True)
class StructureEvent:
    event_id: str
    path: StructurePath
    side: Side
    primary_boundary_id: str
    supporting_boundary_ids: tuple[str, ...]
    interaction_index: int
    interaction_time_ns: int
    interaction_extreme: float
    reference_close: float
    stop_reference: float
    target_boundary_id: str | None
    target_price_at_interaction: float | None
    origin_pivot_id: str | None
    origin_price: float | None
    structure_kind: StructureKind
    channel_id: str | None
    rule_provenance: tuple[str, ...]


@dataclass(slots=True)
class _BreakAttempt:
    boundary_id: str
    break_index: int
    break_time_ns: int
    break_extreme: float
    break_close: float


__all__ = [
    "BoundaryRole",
    "ChannelDirection",
    "ChannelState",
    "ConfirmedPivot",
    "PivotKind",
    "StructuralBoundary",
    "StructureEvent",
    "StructureKind",
    "StructurePath",
    "_BreakAttempt",
]

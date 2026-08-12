"""Pure contracts for the EasyChart v5 structure-first research policy.

The module intentionally contains no order, fill, portfolio or NAV logic.
NautilusTrader remains the execution/accounting authority.  These objects only
represent information that was observable before a decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any

from domain import Side
from easychart_zones import ZoneSide


class RuleOrigin(str, Enum):
    SOURCE_EXPLICIT = "SOURCE_EXPLICIT"
    SOURCE_AMBIGUITY_TRANSLATION = "SOURCE_AMBIGUITY_TRANSLATION"
    RESEARCH_HYPOTHESIS = "RESEARCH_HYPOTHESIS"
    EXTERNAL_METHOD = "EXTERNAL_METHOD"


class ObjectKind(str, Enum):
    SWING_HIGH = "SWING_HIGH"
    SWING_LOW = "SWING_LOW"
    HORIZONTAL_RESISTANCE = "HORIZONTAL_RESISTANCE"
    HORIZONTAL_SUPPORT = "HORIZONTAL_SUPPORT"
    UPTREND_LINE = "UPTREND_LINE"
    DOWNTREND_LINE = "DOWNTREND_LINE"
    ASCENDING_CHANNEL_LOWER = "ASCENDING_CHANNEL_LOWER"
    ASCENDING_CHANNEL_UPPER = "ASCENDING_CHANNEL_UPPER"
    DESCENDING_CHANNEL_UPPER = "DESCENDING_CHANNEL_UPPER"
    DESCENDING_CHANNEL_LOWER = "DESCENDING_CHANNEL_LOWER"
    CHANNEL_MIDLINE = "CHANNEL_MIDLINE"


class StructureFamily(str, Enum):
    HORIZONTAL = "HORIZONTAL"
    TREND_LINE = "TREND_LINE"
    CHANNEL = "CHANNEL"


class ScenarioPath(str, Enum):
    REJECTION = "REJECTION"
    ACCEPTANCE = "ACCEPTANCE"
    ROTATION = "ROTATION"
    BOUNCE = "BOUNCE"


class SetupState(str, Enum):
    WAITING_RECLAIM = "WAITING_RECLAIM"
    WAITING_ACCEPTANCE_HOLD = "WAITING_ACCEPTANCE_HOLD"
    WAITING_ACCEPTANCE_RETEST = "WAITING_ACCEPTANCE_RETEST"
    WAITING_DISPLACEMENT = "WAITING_DISPLACEMENT"
    WAITING_FOOTPRINT_RETEST = "WAITING_FOOTPRINT_RETEST"
    PLANNED = "PLANNED"
    INVALIDATED = "INVALIDATED"
    TARGET_SPENT = "TARGET_SPENT"
    NO_TARGET = "NO_TARGET"
    NO_TRADE_GEOMETRY = "NO_TRADE_GEOMETRY"
    UNRESOLVED = "UNRESOLVED"
    DUPLICATE_EPISODE = "DUPLICATE_EPISODE"


@dataclass(slots=True)
class Pivot:
    pivot_id: str
    side: str  # HIGH or LOW
    price: float
    index: int
    event_time_ns: int
    observed_index: int
    observed_time_ns: int
    span: int
    strength_ratio: float
    first_touch_index: int | None = None
    first_touch_time_ns: int | None = None
    consumed: bool = False
    consumed_time_ns: int | None = None

    def __post_init__(self) -> None:
        if self.side not in {"HIGH", "LOW"}:
            raise ValueError("pivot side must be HIGH or LOW")
        if self.span <= 0 or not math.isfinite(self.price):
            raise ValueError("invalid pivot")


@dataclass(slots=True)
class StructureZone:
    """A fixed snapshot of a structure boundary at one observable time.

    Trend lines and channel edges move with time.  A trade episode therefore
    snapshots the boundary on the interaction bar.  This keeps entry, stop and
    target geometry immutable and makes the audit record self-contained.
    """

    zone_id: str
    kind: Any
    family: StructureFamily
    side: ZoneSide
    timeframe_minutes: int
    lower: float
    upper: float
    invalidation: float
    impulse_extreme: float
    formed_index: int
    formed_time_ns: int
    observed_time_ns: int
    formation_indices: tuple[int, ...]
    strength_ratio: float
    source_structure_id: str
    source_pivot_span: int
    first_touch_index: int | None = None
    first_touch_time_ns: int | None = None
    invalidated_index: int | None = None
    invalidated_time_ns: int | None = None
    consumed: bool = False

    def __post_init__(self) -> None:
        values = (
            self.lower,
            self.upper,
            self.invalidation,
            self.impulse_extreme,
            self.strength_ratio,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("structure zone values must be finite")
        if self.timeframe_minutes <= 0 or self.source_pivot_span <= 0:
            raise ValueError("timeframe and pivot span must be positive")
        if not self.lower < self.upper:
            raise ValueError("structure lower must be below upper")

    @property
    def active(self) -> bool:
        return self.invalidated_index is None and not self.consumed


@dataclass(frozen=True, slots=True)
class TrendLine:
    structure_id: str
    kind: ObjectKind
    side: ZoneSide
    timeframe_minutes: int
    first_pivot_id: str
    second_pivot_id: str
    first_time_ns: int
    second_time_ns: int
    first_price: float
    second_price: float
    observed_time_ns: int
    pivot_span: int
    strength_ratio: float

    def __post_init__(self) -> None:
        if self.second_time_ns <= self.first_time_ns:
            raise ValueError("trend line anchors must be chronological")
        if self.timeframe_minutes <= 0 or self.pivot_span <= 0:
            raise ValueError("invalid trend line scale")

    @property
    def slope_per_ns(self) -> float:
        return (self.second_price - self.first_price) / (self.second_time_ns - self.first_time_ns)

    def value_at(self, time_ns: int) -> float:
        return self.first_price + self.slope_per_ns * (time_ns - self.first_time_ns)


@dataclass(frozen=True, slots=True)
class Channel:
    channel_id: str
    timeframe_minutes: int
    direction: str  # ASCENDING or DESCENDING
    main_first_pivot_id: str
    main_second_pivot_id: str
    opposite_pivot_id: str
    first_time_ns: int
    second_time_ns: int
    first_price: float
    second_price: float
    offset: float
    observed_time_ns: int
    pivot_span: int
    strength_ratio: float

    def __post_init__(self) -> None:
        if self.direction not in {"ASCENDING", "DESCENDING"}:
            raise ValueError("channel direction must be ASCENDING or DESCENDING")
        if self.second_time_ns <= self.first_time_ns or self.offset <= 0.0:
            raise ValueError("invalid channel geometry")

    @property
    def slope_per_ns(self) -> float:
        return (self.second_price - self.first_price) / (self.second_time_ns - self.first_time_ns)

    def main_at(self, time_ns: int) -> float:
        return self.first_price + self.slope_per_ns * (time_ns - self.first_time_ns)

    def lower_at(self, time_ns: int) -> float:
        main = self.main_at(time_ns)
        return main if self.direction == "ASCENDING" else main - self.offset

    def upper_at(self, time_ns: int) -> float:
        main = self.main_at(time_ns)
        return main + self.offset if self.direction == "ASCENDING" else main

    def mid_at(self, time_ns: int) -> float:
        return (self.lower_at(time_ns) + self.upper_at(time_ns)) / 2.0


@dataclass(slots=True)
class ScenarioSetup:
    setup_id: str
    scale_name: str
    path: ScenarioPath
    side: Side
    state: SetupState
    context: StructureZone
    context_members: tuple[StructureZone, ...]
    observed_time_ns: int
    interaction_time_ns: int
    interaction_index: int
    interaction_extreme: float
    target_zone: StructureZone | None
    target_price: float | None
    confirmation_time_ns: int | None = None
    acceptance_break_index: int | None = None
    acceptance_origin: Pivot | None = None
    trigger_zone: Any | None = None
    trigger_index: int | None = None
    channel_id: str | None = None
    midline_price_at_interaction: float | None = None
    first_retest_consumed: bool = False
    terminal_reason: str | None = None


@dataclass(frozen=True, slots=True)
class V5TradePlan:
    plan_id: str
    causal_event_id: str
    symbol: str
    family: str
    side: Side
    observed_time_ns: int
    entry: float
    stop: float
    target: float
    gross_rr: float
    setup_id: str
    higher_zone_id: str
    higher_zone_kind: Any
    higher_strength_ratio: float
    lower_zone_id: str
    lower_zone_kind: Any
    lower_strength_ratio: float
    trigger_zone_id: str
    trigger_strength_ratio: float
    target_zone_id: str
    target_zone_kind: Any
    overlap_lower: float
    overlap_upper: float
    interaction_time_ns: int
    trigger_time_ns: int
    scenario_path: str
    setup_observed_time_ns: int
    trigger_zone_kind: str
    source_rule_count: int
    rule_provenance: tuple[str, ...]
    scale_name: str
    higher_timeframe_minutes: int
    decision_timeframe_minutes: int
    trigger_timeframe_minutes: int


SOURCE_RULES: tuple[str, ...] = (
    "SOURCE_EXPLICIT:MARKET_STRUCTURE_GIVES_DIRECTION_AND_RANGE",
    "SOURCE_EXPLICIT:OB_FVG_ARE_FOOTPRINTS_NOT_UNCONDITIONAL_ENTRIES",
    "SOURCE_EXPLICIT:MEANINGFUL_OB_AT_LIQUIDITY_OR_STRUCTURE",
    "SOURCE_EXPLICIT:FVG_REQUIRES_CONSPICUOUS_MIDDLE_DISPLACEMENT",
    "SOURCE_EXPLICIT:FAKEOUT_REQUIRES_PREEXISTING_STRUCTURE_AND_RETURN",
    "SOURCE_EXPLICIT:CONFIRMATION_OR_RETEST_IS_CONSERVATIVE_ENTRY",
    "SOURCE_EXPLICIT:TRENDLINES_AND_CHANNELS_USE_WICK_PIVOTS",
    "SOURCE_EXPLICIT:CHANNEL_REQUIRES_THREE_POINTS_BEFORE_FOURTH_INTERACTION",
    "SOURCE_EXPLICIT:CHANNEL_BREAK_ACCEPTANCE_NEEDS_BODY_CLOSE_AND_NEXT_BAR_OUTSIDE",
    "SOURCE_EXPLICIT:NO_CHASE_WHEN_PLANNED_AREA_IS_NOT_REACHED",
    "SOURCE_EXPLICIT:STOP_AT_CAUSAL_INVALIDATION",
    "SOURCE_EXPLICIT:TARGET_AT_PREEXISTING_OPPOSING_STRUCTURE",
)

TRANSLATION_RULES: tuple[str, ...] = (
    "SOURCE_AMBIGUITY_TRANSLATION:ONE_TICK_STRUCTURE_BAND_REPLACES_ZERO_WIDTH_LINE",
    "SOURCE_AMBIGUITY_TRANSLATION:PARTIAL_RECLAIM_REMAINS_UNRESOLVED",
    "SOURCE_AMBIGUITY_TRANSLATION:FIRST_LATER_RETEST_IS_CONSUMED",
    "SOURCE_AMBIGUITY_TRANSLATION:SAME_CAUSAL_INTERACTION_IS_ONE_EPISODE",
    "SOURCE_AMBIGUITY_TRANSLATION:DIAGONAL_STATE_USES_CURRENT_PROJECTED_LINE_VALUE",
    "SOURCE_AMBIGUITY_TRANSLATION:CHANNEL_TARGET_IS_FROZEN_AT_ENTRY_TIME",
    "SOURCE_AMBIGUITY_TRANSLATION:DECISION_BAR_OWNS_INTRABAR_STRUCTURE_INTERACTION",
)

RESEARCH_RULES: tuple[str, ...] = (
    "RESEARCH_HYPOTHESIS:CAUSALLY_CONFIRMED_PIVOTS_DEFINE_MACHINE_STRUCTURE",
    "RESEARCH_HYPOTHESIS:60_15_5_AND_15_5_1_DECISION_STACKS",
    "RESEARCH_HYPOTHESIS:PIVOT_SPANS_2_AND_6_REPRESENT_LOCAL_AND_LARGER_AUCTIONS",
    "RESEARCH_HYPOTHESIS:CHANNEL_USES_TWO_SAME_SIDE_AND_ONE_INTERVENING_OPPOSITE_PIVOT",
)

EXTERNAL_RULES: tuple[str, ...] = ()


def provenance() -> tuple[str, ...]:
    return SOURCE_RULES + TRANSLATION_RULES + RESEARCH_RULES + EXTERNAL_RULES

"""Intrinsic-time auction episodes for the EasyChart breakthrough candidate.

This module does not treat OB, FVG, trend lines or channels as independent
entry signals.  It translates their shared market logic into one causal state
machine:

1. a pre-existing public liquidity boundary is observed;
2. price either sweeps and reclaims it, or breaks and proves acceptance;
3. completed one-minute price/flow shows transfer of control;
4. the first later mitigation/retest responds in the intended direction;
5. entry, invalidation and the nearest pre-existing opposing liquidity target
   are frozen before submitting one full-position trade.

The engine uses directional-change turning points rather than fixed pivot spans.
This makes the structural clock adapt to price activity while preserving strict
information availability.  Binance aggressor flow is transition evidence, not
a global filter or a substitute for a coherent auction episode.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
import math
from statistics import median
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ObjectKind, ScenarioPath, V5TradePlan
from domain import Candle, Side
from easychart_re1_flow import CausalFlowAnalyzer, FlowObservation


INTRINSIC_TIME_RULE = (
    "EXTERNAL_METHOD:DIRECTIONAL_CHANGE_EVENTS_DEFINE_ACTIVITY_ADAPTIVE_"
    "TURNING_POINTS_WITHOUT_FIXED_CLOCK_PIVOT_SPANS"
)
PUBLIC_LIQUIDITY_RULE = (
    "RESEARCH_HYPOTHESIS:CAUSALLY_CONFIRMED_INTRINSIC_SWINGS_AND_CLUSTERED_"
    "EQUAL_EXTREMES_DEFINE_PUBLIC_STOP_AND_OBJECTIVE_LIQUIDITY"
)
COMPLETED_REFERENCE_LIQUIDITY_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:COMPLETED_PREVIOUS_UTC_DAY_AND_PREVIOUS_"
    "FOUR_HOUR_AUCTION_EXTREMES_ENTER_THE_SAME_PUBLIC_LIQUIDITY_BOOK"
)
AUCTION_RECLAIM_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:PREEXISTING_BOUNDARY_SWEEP_RETURN_"
    "DISPLACEMENT_AND_FIRST_MITIGATION_FORM_ONE_REJECTION_EPISODE"
)
AUCTION_ACCEPTANCE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:BODY_BREAK_NEXT_COMPLETED_HOLD_AND_FIRST_"
    "BOUNDARY_RETEST_FORM_ONE_ACCEPTED_BREAK_EPISODE"
)
FLOW_TRANSITION_RULE = (
    "EXTERNAL_METHOD:PRIOR_NORMALIZED_AGGRESSOR_INITIATIVE_OR_ADVERSE_FLOW_"
    "ABSORPTION_CONFIRMS_TRANSFER_OF_CONTROL_AT_THE_DECISION_BOUNDARY"
)
PREEXISTING_TARGET_RULE = (
    "SOURCE_EXPLICIT:TARGET_IS_NEAREST_PREEXISTING_UNSPENT_OPPOSING_"
    "LIQUIDITY_OR_FROZEN_AUCTION_BOUNDARY"
)
COMMITTED_OBJECTIVE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:FIRST_CAUSAL_OPPOSING_OBJECTIVE_IS_"
    "COMMITTED_WHEN_EACH_DIRECTIONAL_SETTLEMENT_BEGINS_AND_MAY_NOT_BE_"
    "REPLACED_WHILE_THAT_SETTLEMENT_LIVES"
)
SAME_LEVEL_ROLE_FLIP_OBJECTIVE_RULE = (
    "IMPLEMENTATION_VALIDITY:THE_ONE_ALLOWED_SAME_LEVEL_ROLE_FLIP_ENDS_THE_"
    "PRIOR_DIRECTIONAL_SETTLEMENT_AND_COMMITS_THE_NEW_SIDE_OBJECTIVE_BEFORE_"
    "THE_NEW_SETTLEMENT_LIVES"
)
ONE_EPISODE_RULE = (
    "IMPLEMENTATION_VALIDITY:ONE_BOUNDARY_INTERACTION_IS_ONE_CAUSAL_EPISODE_"
    "REGARDLESS_OF_INTERNAL_CONFIRMATION_BARS"
)
ACTIVE_AUCTION_MAP_RULE = (
    "RESEARCH_HYPOTHESIS:ONLY_CURRENT_AUCTION_EDGES_RECENT_EQUAL_EXTREMA_AND_"
    "CURRENT_HIGHER_TIMEFRAME_BOUNDARIES_MAY_START_NEW_EPISODES"
)
FIRST_REACTION_TARGET_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:SINGLE_EXIT_TARGETS_THE_FIRST_PREEXISTING_"
    "REACTION_OBSTACLE_AND_NEVER_SKIPS_A_CLOSER_STRUCTURE_FOR_DISTANT_RR"
)
for _rule in (
    INTRINSIC_TIME_RULE,
    PUBLIC_LIQUIDITY_RULE,
    AUCTION_RECLAIM_RULE,
    AUCTION_ACCEPTANCE_RULE,
    FLOW_TRANSITION_RULE,
    PREEXISTING_TARGET_RULE,
    ONE_EPISODE_RULE,
    ACTIVE_AUCTION_MAP_RULE,
    FIRST_REACTION_TARGET_RULE,
    COMPLETED_REFERENCE_LIQUIDITY_RULE,
    COMMITTED_OBJECTIVE_RULE,
    SAME_LEVEL_ROLE_FLIP_OBJECTIVE_RULE,
):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


NS_PER_MINUTE = 60_000_000_000


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _side_name(side: Side) -> str:
    return "LONG" if side is Side.LONG else "SHORT"


@dataclass(frozen=True, slots=True)
class IntrinsicSwing:
    swing_id: str
    side: str  # HIGH or LOW
    price: float
    event_time_ns: int
    observed_time_ns: int
    threshold: float
    timeframe_minutes: int
    overshoot: float


class AdaptiveDirectionalChange:
    """Confirm turning points after a prior-range-scaled reversal.

    Thresholds use only completed ranges preceding the current bar.  The
    turning point's event time is the extreme time, while observed_time is the
    later confirmation close.  This distinction is central to causal use.
    """

    def __init__(
        self,
        symbol: str,
        timeframe_minutes: int,
        tick_size: float,
        *,
        range_window: int = 48,
        threshold_multiplier: float = 1.35,
    ) -> None:
        self.symbol = symbol
        self.timeframe_minutes = int(timeframe_minutes)
        self.tick_size = float(tick_size)
        self.range_window = int(range_window)
        self.threshold_multiplier = float(threshold_multiplier)
        self.ranges: deque[float] = deque(maxlen=self.range_window)
        self.mode: str | None = None  # TRACK_HIGH or TRACK_LOW
        self.extreme_price: float | None = None
        self.extreme_time_ns: int | None = None
        self.last_opposite_extreme: float | None = None
        self.sequence = 0
        self.last_close: float | None = None
        self.last_ts: int | None = None
        self.bars: list[Candle] = []
        self.zones: list[AuctionZone] = []
        self.counts: dict[str, int] = {}

    def _inc(self, key: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + 1

    def _threshold(self, bar: Candle) -> float | None:
        if len(self.ranges) < max(12, self.range_window // 4):
            return None
        baseline = median(self.ranges)
        return max(self.tick_size * 2.0, baseline * self.threshold_multiplier)

    def on_bar(self, bar: Candle) -> list[IntrinsicSwing]:
        if bar.ts_close_ns <= 0:
            raise ValueError("bar close timestamp must be positive")
        if self.last_ts is not None and bar.ts_close_ns <= self.last_ts:
            raise ValueError("bars must arrive in strictly increasing close time")
        threshold = self._threshold(bar)
        created: list[IntrinsicSwing] = []

        if self.mode is None:
            # Start in the direction of the first completed body.  The choice
            # affects only warm-up; no swing can be emitted until a reversal.
            self.mode = "TRACK_HIGH" if bar.close >= bar.open else "TRACK_LOW"
            self.extreme_price = bar.high if self.mode == "TRACK_HIGH" else bar.low
            self.extreme_time_ns = bar.ts_close_ns
            self._inc("initialized")
        elif threshold is not None:
            assert self.extreme_price is not None
            assert self.extreme_time_ns is not None
            prior_extreme = self.extreme_price
            prior_extreme_time = self.extreme_time_ns
            if self.mode == "TRACK_HIGH":
                reversal = bar.low <= prior_extreme - threshold
                extension = bar.high > prior_extreme
                if reversal and extension:
                    # OHLC does not reveal whether the new high preceded the
                    # low.  Treat this bar as an ambiguous extension and wait
                    # for a later completed bar to confirm reversal from it.
                    self.extreme_price = bar.high
                    self.extreme_time_ns = bar.ts_close_ns
                    self._inc("ambiguous_high_extension_reversal")
                elif reversal:
                    overshoot = 0.0
                    if self.last_opposite_extreme is not None:
                        overshoot = max(0.0, prior_extreme - self.last_opposite_extreme)
                    self.sequence += 1
                    created.append(
                        IntrinsicSwing(
                            swing_id=(
                                f"DC:{self.symbol}:{self.timeframe_minutes}m:HIGH:"
                                f"{prior_extreme_time}:{self.sequence}"
                            ),
                            side="HIGH",
                            price=prior_extreme,
                            event_time_ns=prior_extreme_time,
                            observed_time_ns=bar.ts_close_ns,
                            threshold=threshold,
                            timeframe_minutes=self.timeframe_minutes,
                            overshoot=overshoot,
                        )
                    )
                    self.last_opposite_extreme = prior_extreme
                    self.mode = "TRACK_LOW"
                    self.extreme_price = bar.low
                    self.extreme_time_ns = bar.ts_close_ns
                    self._inc("high_confirmed")
                elif extension:
                    self.extreme_price = bar.high
                    self.extreme_time_ns = bar.ts_close_ns
            else:
                reversal = bar.high >= prior_extreme + threshold
                extension = bar.low < prior_extreme
                if reversal and extension:
                    self.extreme_price = bar.low
                    self.extreme_time_ns = bar.ts_close_ns
                    self._inc("ambiguous_low_extension_reversal")
                elif reversal:
                    overshoot = 0.0
                    if self.last_opposite_extreme is not None:
                        overshoot = max(0.0, self.last_opposite_extreme - prior_extreme)
                    self.sequence += 1
                    created.append(
                        IntrinsicSwing(
                            swing_id=(
                                f"DC:{self.symbol}:{self.timeframe_minutes}m:LOW:"
                                f"{prior_extreme_time}:{self.sequence}"
                            ),
                            side="LOW",
                            price=prior_extreme,
                            event_time_ns=prior_extreme_time,
                            observed_time_ns=bar.ts_close_ns,
                            threshold=threshold,
                            timeframe_minutes=self.timeframe_minutes,
                            overshoot=overshoot,
                        )
                    )
                    self.last_opposite_extreme = prior_extreme
                    self.mode = "TRACK_HIGH"
                    self.extreme_price = bar.high
                    self.extreme_time_ns = bar.ts_close_ns
                    self._inc("low_confirmed")
                elif extension:
                    self.extreme_price = bar.low
                    self.extreme_time_ns = bar.ts_close_ns
        else:
            self._inc("warmup")
            if self.mode == "TRACK_HIGH" and bar.high >= _finite(self.extreme_price, bar.high):
                self.extreme_price = bar.high
                self.extreme_time_ns = bar.ts_close_ns
            elif self.mode == "TRACK_LOW" and bar.low <= _finite(self.extreme_price, bar.low):
                self.extreme_price = bar.low
                self.extreme_time_ns = bar.ts_close_ns

        true_range = bar.high - bar.low
        if self.last_close is not None:
            true_range = max(
                true_range,
                abs(bar.high - self.last_close),
                abs(bar.low - self.last_close),
            )
        self.ranges.append(max(true_range, self.tick_size))
        self.last_close = bar.close
        self.last_ts = bar.ts_close_ns
        self.bars.append(bar)
        return created

    def active_zones(self) -> list[AuctionZone]:
        return []

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self.counts.items())),
            "bars": len(self.bars),
            "range_window": self.range_window,
            "threshold_multiplier": self.threshold_multiplier,
            "prior_range": self.prior_range,
        }

    @property
    def prior_range(self) -> float:
        return median(self.ranges) if self.ranges else self.tick_size * 2.0


class PoolRole(str, Enum):
    EXTERNAL_STOP_POOL = "EXTERNAL_STOP_POOL"
    VALUE_BOUNDARY = "VALUE_BOUNDARY"
    REACTION_OBSTACLE = "REACTION_OBSTACLE"


@dataclass(slots=True)
class LiquidityPool:
    pool_id: str
    side: str  # HIGH or LOW
    timeframe_minutes: int
    lower: float
    upper: float
    center: float
    first_event_time_ns: int
    last_event_time_ns: int
    observed_time_ns: int
    first_sequence: int = 0
    last_sequence: int = 0
    member_ids: list[str] = field(default_factory=list)
    member_prices: list[float] = field(default_factory=list)
    thresholds: list[float] = field(default_factory=list)
    overshoots: list[float] = field(default_factory=list)
    engaged_event_id: str | None = None
    consumed_time_ns: int | None = None
    objective_spent_time_ns: int | None = None
    last_touch_time_ns: int | None = None
    touch_count: int = 0
    five_minute_touch_count: int = 0
    last_five_minute_touch_ns: int | None = None
    source_family: str = "HORIZONTAL_LIQUIDITY"
    source_structure_ids: tuple[str, ...] = ()
    object_kind: ObjectKind | None = None

    @property
    def active(self) -> bool:
        return self.consumed_time_ns is None

    @property
    def member_count(self) -> int:
        return len(self.member_ids)

    @property
    def strength(self) -> float:
        tf_weight = 1.0 + math.log1p(self.timeframe_minutes / 5.0)
        density = 1.0 + math.log1p(max(0, self.member_count - 1))
        overshoot = median(self.overshoots) if self.overshoots else 0.0
        threshold = median(self.thresholds) if self.thresholds else 1.0
        excursion = min(3.0, overshoot / max(threshold, 1e-12))
        return tf_weight * density * (1.0 + 0.25 * excursion)

    @property
    def kind(self) -> ObjectKind:
        if self.object_kind is not None:
            return self.object_kind
        return ObjectKind.SWING_HIGH if self.side == "HIGH" else ObjectKind.SWING_LOW


@dataclass(frozen=True, slots=True)
class AuctionZone:
    zone_id: str
    kind: ObjectKind
    side: str
    timeframe_minutes: int
    lower: float
    upper: float
    invalidation: float
    impulse_extreme: float
    formed_time_ns: int
    observed_time_ns: int
    strength_ratio: float


class LiquidityBook:
    """Current auction objects, not an immortal catalogue of every swing.

    Equal extrema are clustered only while they belong to the same recent
    intrinsic auction.  Older same-price extrema remain separate historical
    objects instead of accumulating hundreds of touches into a false signal.
    """

    _CLUSTER_TURN_HORIZON = {1: 20, 5: 12, 15: 8, 60: 4}
    _TARGET_TURN_HORIZON = {1: 28, 5: 18, 15: 10, 60: 6}

    def __init__(self, symbol: str, tick_size: float) -> None:
        self.symbol = symbol
        self.tick_size = float(tick_size)
        self.pools: list[LiquidityPool] = []
        self.by_id: dict[str, LiquidityPool] = {}
        self.zone_by_id: dict[str, AuctionZone] = {}
        self.counts: dict[str, int] = {}
        self.sequence_by_timeframe: dict[int, int] = {}

    def _inc(self, key: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + 1

    def current_sequence(self, timeframe_minutes: int) -> int:
        return self.sequence_by_timeframe.get(int(timeframe_minutes), 0)

    def is_recent(self, pool: LiquidityPool, max_turns: int | None = None) -> bool:
        horizon = (
            self._TARGET_TURN_HORIZON.get(pool.timeframe_minutes, 6)
            if max_turns is None
            else int(max_turns)
        )
        return self.current_sequence(pool.timeframe_minutes) - pool.last_sequence <= horizon

    def latest_pool(self, timeframe_minutes: int, side: str) -> LiquidityPool | None:
        candidates = [
            pool
            for pool in self.pools
            if pool.active
            and pool.source_family == "HORIZONTAL_LIQUIDITY"
            and pool.timeframe_minutes == int(timeframe_minutes)
            and pool.side == side
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item.last_sequence, item.observed_time_ns))

    def register(self, swing: IntrinsicSwing) -> LiquidityPool:
        timeframe = int(swing.timeframe_minutes)
        sequence = self.current_sequence(timeframe) + 1
        self.sequence_by_timeframe[timeframe] = sequence
        tolerance = max(self.tick_size * 2.0, swing.threshold * 0.20)
        cluster_horizon = self._CLUSTER_TURN_HORIZON.get(timeframe, 6)
        candidates = [
            pool
            for pool in self.pools
            if pool.active
            and pool.source_family == "HORIZONTAL_LIQUIDITY"
            and pool.side == swing.side
            and pool.timeframe_minutes == timeframe
            and sequence - pool.last_sequence <= cluster_horizon
            and abs(pool.center - swing.price) <= tolerance
        ]
        if candidates:
            pool = min(candidates, key=lambda item: abs(item.center - swing.price))
            pool.member_ids.append(swing.swing_id)
            pool.member_prices.append(swing.price)
            pool.thresholds.append(swing.threshold)
            pool.overshoots.append(swing.overshoot)
            pool.center = median(pool.member_prices)
            half_width = max(tolerance, max(abs(price - pool.center) for price in pool.member_prices))
            pool.lower = pool.center - half_width
            pool.upper = pool.center + half_width
            pool.last_event_time_ns = swing.event_time_ns
            pool.observed_time_ns = swing.observed_time_ns
            pool.last_sequence = sequence
            self._inc("swing_clustered_current_auction")
        else:
            pool_id = (
                f"LP:{self.symbol}:{timeframe}m:{swing.side}:"
                f"{swing.event_time_ns}:{sequence}"
            )
            pool = LiquidityPool(
                pool_id=pool_id,
                side=swing.side,
                timeframe_minutes=timeframe,
                lower=swing.price - tolerance,
                upper=swing.price + tolerance,
                center=swing.price,
                first_event_time_ns=swing.event_time_ns,
                last_event_time_ns=swing.event_time_ns,
                observed_time_ns=swing.observed_time_ns,
                first_sequence=sequence,
                last_sequence=sequence,
                member_ids=[swing.swing_id],
                member_prices=[swing.price],
                thresholds=[swing.threshold],
                overshoots=[swing.overshoot],
            )
            self.pools.append(pool)
            self.by_id[pool_id] = pool
            self._inc("pool_created")
        self.zone_by_id[pool.pool_id] = self.snapshot(pool)
        if len(self.pools) > 768:
            keep = [
                item
                for item in self.pools
                if item.active and self.is_recent(item, self._TARGET_TURN_HORIZON.get(item.timeframe_minutes, 6) * 2)
            ][-512:]
            keep_ids = {item.pool_id for item in keep}
            self.pools = keep
            self.by_id = {key: value for key, value in self.by_id.items() if key in keep_ids}
            self.zone_by_id = {key: value for key, value in self.zone_by_id.items() if key in keep_ids}
        return pool

    def register_public_reference(
        self,
        *,
        source_id: str,
        side: str,
        price: float,
        event_time_ns: int,
        observed_time_ns: int,
        timeframe_minutes: int,
    ) -> LiquidityPool:
        """Register a completed clock-auction extreme without clustering it."""
        pool_id = f"PUBLIC:{self.symbol}:{source_id}:{side}"
        existing = self.by_id.get(pool_id)
        if existing is not None:
            return existing
        timeframe = int(timeframe_minutes)
        sequence = self.current_sequence(timeframe) + 1
        self.sequence_by_timeframe[timeframe] = sequence
        half_width = self.tick_size
        pool = LiquidityPool(
            pool_id=pool_id,
            side=side,
            timeframe_minutes=timeframe,
            lower=float(price) - half_width,
            upper=float(price) + half_width,
            center=float(price),
            first_event_time_ns=int(event_time_ns),
            last_event_time_ns=int(event_time_ns),
            observed_time_ns=int(observed_time_ns),
            first_sequence=sequence,
            last_sequence=sequence,
            member_ids=[source_id],
            member_prices=[float(price)],
            thresholds=[self.tick_size * 2.0],
            overshoots=[0.0],
            source_family="PUBLIC_REFERENCE",
            source_structure_ids=(source_id,),
        )
        self.pools.append(pool)
        self.by_id[pool_id] = pool
        self.zone_by_id[pool_id] = self.snapshot(pool)
        self._inc("completed_public_reference_registered")
        return pool

    def register_projected_structure(
        self,
        *,
        source_ids: tuple[str, ...],
        side: str,
        lower: float,
        upper: float,
        formed_time_ns: int,
        observed_time_ns: int,
        interaction_time_ns: int,
        timeframe_minutes: int,
        strength: float,
        object_kind: ObjectKind,
    ) -> LiquidityPool:
        """Snapshot one already-observable diagonal at its first interaction.

        The source-faithful structure book owns freshness and projection.  The
        auction policy receives one immutable boundary snapshot so a trendline
        or channel cannot become a parallel strategy or drift after its event.
        """
        source_key = "|".join(sorted(source_ids))
        pool_id = (
            f"PROJECTED:{self.symbol}:{timeframe_minutes}m:{source_key}:"
            f"{interaction_time_ns}"
        )
        existing = self.by_id.get(pool_id)
        if existing is not None:
            return existing
        center = (float(lower) + float(upper)) / 2.0
        width = max(float(upper) - float(lower), self.tick_size * 2.0)
        sequence = self.current_sequence(timeframe_minutes)
        pool = LiquidityPool(
            pool_id=pool_id,
            side=side,
            timeframe_minutes=int(timeframe_minutes),
            lower=float(lower),
            upper=float(upper),
            center=center,
            first_event_time_ns=int(formed_time_ns),
            last_event_time_ns=int(formed_time_ns),
            observed_time_ns=int(observed_time_ns),
            first_sequence=sequence,
            last_sequence=sequence,
            member_ids=list(source_ids),
            member_prices=[center],
            thresholds=[width],
            overshoots=[max(0.0, float(strength)) * width],
            source_family="PROJECTED_STRUCTURE",
            source_structure_ids=tuple(source_ids),
            object_kind=object_kind,
        )
        self.pools.append(pool)
        self.by_id[pool_id] = pool
        self.zone_by_id[pool_id] = self.snapshot(pool)
        self._inc("projected_structure_first_interaction_registered")
        return pool

    def snapshot(self, pool: LiquidityPool) -> AuctionZone:
        if pool.side == "LOW":
            invalidation = pool.lower - self.tick_size
            impulse = pool.upper
        else:
            invalidation = pool.upper + self.tick_size
            impulse = pool.lower
        return AuctionZone(
            zone_id=pool.pool_id,
            kind=pool.kind,
            side=pool.side,
            timeframe_minutes=pool.timeframe_minutes,
            lower=pool.lower,
            upper=pool.upper,
            invalidation=invalidation,
            impulse_extreme=impulse,
            formed_time_ns=pool.first_event_time_ns,
            observed_time_ns=pool.observed_time_ns,
            strength_ratio=pool.strength,
        )

    def eligible_sources(self, time_ns: int) -> list[LiquidityPool]:
        return [
            pool
            for pool in self.pools
            if pool.active
            and pool.source_family != "PROJECTED_STRUCTURE"
            and pool.engaged_event_id is None
            and pool.observed_time_ns < time_ns
            and pool.timeframe_minutes in {5, 15, 60, 240, 1440}
            and self.is_recent(pool)
        ]

    def target_for(
        self,
        side: Side,
        entry: float,
        time_ns: int,
        *,
        exclude_pool_id: str,
    ) -> LiquidityPool | None:
        # The target is chosen before entry from liquidity which still exists.
        # A level already traded through or reacted from cannot be renamed an
        # unspent objective merely because its source pool remains structurally
        # active for a later auction episode.
        candidates = [
            pool
            for pool in self.pools
            if pool.active
            and pool.source_family != "PROJECTED_STRUCTURE"
            and pool.pool_id != exclude_pool_id
            and pool.observed_time_ns < time_ns
            and pool.objective_spent_time_ns is None
            and self.is_recent(pool)
            # A single one-minute turn is internal path noise, not a level at
            # which a full-position day trade must exit.  One-minute liquidity
            # becomes a decision obstacle only when equal extrema cluster.
            # Five-minute and higher completed turns remain valid objectives.
            and (pool.timeframe_minutes >= 5 or pool.member_count >= 2)
            and (
                (side is Side.LONG and pool.side == "HIGH" and pool.lower > entry)
                or (side is Side.SHORT and pool.side == "LOW" and pool.upper < entry)
            )
        ]
        if not candidates:
            return None
        if side is Side.LONG:
            return min(candidates, key=lambda item: (item.lower, -item.strength, -item.last_sequence))
        return max(candidates, key=lambda item: (item.upper, item.strength, item.last_sequence))

    def observe_touch(self, bar: Candle, timeframe_minutes: int) -> None:
        timeframe = int(timeframe_minutes)
        for pool in self.pools:
            if not pool.active or pool.observed_time_ns >= bar.ts_close_ns:
                continue
            if bar.low <= pool.upper and bar.high >= pool.lower:
                pool.last_touch_time_ns = bar.ts_close_ns
                if pool.objective_spent_time_ns is None:
                    pool.objective_spent_time_ns = bar.ts_close_ns
                    self._inc("reaction_obstacle_first_touch")
                if timeframe == 5 and pool.last_five_minute_touch_ns != bar.ts_close_ns:
                    pool.five_minute_touch_count += 1
                    pool.touch_count = pool.five_minute_touch_count
                    pool.last_five_minute_touch_ns = bar.ts_close_ns
                    self._inc("five_minute_boundary_touch")

    def find_zone(self, zone_id: str) -> AuctionZone | None:
        pool = self.by_id.get(zone_id)
        if pool is not None:
            zone = self.snapshot(pool)
            self.zone_by_id[zone_id] = zone
            return zone
        return self.zone_by_id.get(zone_id)


class AuditBarTape:
    """Evidence-only completed-bar tape compatible with the reused reports."""

    def __init__(self, timeframe_minutes: int) -> None:
        self.timeframe_minutes = int(timeframe_minutes)
        self.bars: list[Candle] = []
        self.zones: list[AuctionZone] = []

    def observe(self, bar: Candle) -> None:
        if self.bars and bar.ts_close_ns <= self.bars[-1].ts_close_ns:
            raise ValueError("audit bars must arrive in strictly increasing close time")
        self.bars.append(bar)

    def active_zones(self) -> list[AuctionZone]:
        return []

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {"bars": len(self.bars), "timeframe_minutes": self.timeframe_minutes}


class EpisodeKind(str, Enum):
    LIQUIDITY_RECLAIM = "INTRINSIC_LIQUIDITY_RECLAIM"
    ACCEPTED_BREAK_RETEST = "INTRINSIC_ACCEPTED_BREAK_RETEST"


class EpisodePhase(str, Enum):
    BREAK_PENDING = "BREAK_PENDING"
    WAIT_DISPLACEMENT = "WAIT_DISPLACEMENT"
    WAIT_RETEST = "WAIT_RETEST"
    WAIT_RESPONSE = "WAIT_RESPONSE"
    DONE = "DONE"
    INVALID = "INVALID"


@dataclass(slots=True)
class AuctionEpisode:
    episode_id: str
    pool_id: str
    kind: EpisodeKind
    phase: EpisodePhase
    side: Side
    interaction_time_ns: int
    phase_time_ns: int
    source_lower: float
    source_upper: float
    source_center: float
    sweep_extreme: float
    break_extreme: float
    break_count: int = 0
    origin_lower: float | None = None
    origin_upper: float | None = None
    displacement_time_ns: int | None = None
    retest_time_ns: int | None = None
    retest_extreme: float | None = None
    response_time_ns: int | None = None
    evidence_strength: float = 0.0
    terminal_reason: str | None = None
    causal_event_id: str | None = None
    source_roles: tuple[str, ...] = ()
    transition_strength: float = 0.0
    break_excursion: float = 0.0
    role_flip_count: int = 0
    objective_pool_id: str | None = None
    objective_zone: AuctionZone | None = None
    objective_price: float | None = None
    objective_committed_time_ns: int | None = None


class IntrinsicAuctionBundle:
    """Independent opportunity generator implementing one auction policy."""

    SUPPORTED_TIMEFRAMES = (60, 15, 5, 1)

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        self.symbol = symbol
        self.tick_size = float(tick_size)
        self.minimum_gross_rr = float(minimum_gross_rr)
        self.dc = {
            60: AdaptiveDirectionalChange(symbol, 60, tick_size, range_window=36, threshold_multiplier=1.20),
            15: AdaptiveDirectionalChange(symbol, 15, tick_size, range_window=48, threshold_multiplier=1.30),
            5: AdaptiveDirectionalChange(symbol, 5, tick_size, range_window=60, threshold_multiplier=1.45),
            # One-minute turns are reaction obstacles only; they can never start
            # a source episode.  They prevent a full-position target from
            # skipping the first local opposing structure.
            1: AdaptiveDirectionalChange(symbol, 1, tick_size, range_window=60, threshold_multiplier=1.55),
        }
        self.liquidity = LiquidityBook(symbol, tick_size)
        self.flow = CausalFlowAnalyzer(self.tick_size)
        self.audit_one_minute = AuditBarTape(1)
        self.one_minute: deque[Candle] = deque(maxlen=240)
        self.one_minute_ranges: deque[float] = deque(maxlen=60)
        self.one_minute_bodies: deque[float] = deque(maxlen=60)
        self.episodes: dict[str, AuctionEpisode] = {}
        self._plans: list[V5TradePlan] = []
        self._trace: list[dict[str, Any]] = []
        self._counts: dict[str, int] = {}
        self._market_factor_state: Any | None = None
        self._last_swing: dict[tuple[int, str], IntrinsicSwing] = {}
        self._trend_side: Side | None = None
        self._sequence = 0
        self._causal_event_owner: dict[str, str] = {}
        self._daily_date: date | None = None
        self._daily_high: float | None = None
        self._daily_low: float | None = None
        self._daily_last_close_ns: int | None = None
        self._h4_bucket: int | None = None
        self._h4_high: float | None = None
        self._h4_low: float | None = None
        self._h4_bars = 0

    def _inc(self, key: str) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1

    def _audit(self, kind: str, time_ns: int, **values: Any) -> None:
        self._trace.append(
            {
                "scenario_kind": kind,
                "event_time_ns": int(time_ns),
                "symbol": self.symbol,
                **values,
            }
        )

    @property
    def _macro_side(self) -> Side | None:
        return self._trend_side

    def set_market_factor_state(self, state: Any | None) -> None:
        # Common-market state is exposed to the model/trace.  It does not veto a
        # complete local auction by itself.
        self._market_factor_state = state

    def _refresh_trend(self, swing: IntrinsicSwing) -> None:
        self._last_swing[(swing.timeframe_minutes, swing.side)] = swing
        if swing.timeframe_minutes != 15:
            return
        highs = [
            pool
            for pool in self.liquidity.pools
            if pool.timeframe_minutes == 15 and pool.side == "HIGH"
        ][-2:]
        lows = [
            pool
            for pool in self.liquidity.pools
            if pool.timeframe_minutes == 15 and pool.side == "LOW"
        ][-2:]
        if len(highs) < 2 or len(lows) < 2:
            return
        if highs[-1].center > highs[-2].center and lows[-1].center > lows[-2].center:
            self._trend_side = Side.LONG
        elif highs[-1].center < highs[-2].center and lows[-1].center < lows[-2].center:
            self._trend_side = Side.SHORT
        else:
            self._trend_side = None

    def _register_swings(self, timeframe: int, bar: Candle) -> None:
        for swing in self.dc[timeframe].on_bar(bar):
            pool = self.liquidity.register(swing)
            self._refresh_trend(swing)
            self._audit(
                "intrinsic_liquidity_observed",
                swing.observed_time_ns,
                pool_id=pool.pool_id,
                swing_id=swing.swing_id,
                liquidity_side=swing.side,
                source_timeframe_minutes=timeframe,
                event_time_ns_source=swing.event_time_ns,
                observed_time_ns=swing.observed_time_ns,
                level=swing.price,
                lower=pool.lower,
                upper=pool.upper,
                members=pool.member_count,
                strength=pool.strength,
                rule_provenance=(INTRINSIC_TIME_RULE, PUBLIC_LIQUIDITY_RULE),
            )

    @staticmethod
    def _utc_interval_date(time_ns: int) -> date:
        # The nanosecond before a close belongs to the candle's traded minute.
        return datetime.fromtimestamp(
            (time_ns - 1) / 1_000_000_000,
            timezone.utc,
        ).date()

    def _register_completed_reference(
        self,
        *,
        source_id: str,
        timeframe_minutes: int,
        high: float,
        low: float,
        event_time_ns: int,
        observed_time_ns: int,
    ) -> None:
        for side, price in (("HIGH", high), ("LOW", low)):
            pool = self.liquidity.register_public_reference(
                source_id=source_id,
                side=side,
                price=price,
                event_time_ns=event_time_ns,
                observed_time_ns=observed_time_ns,
                timeframe_minutes=timeframe_minutes,
            )
            self._audit(
                "completed_public_liquidity_observed",
                observed_time_ns,
                pool_id=pool.pool_id,
                liquidity_side=side,
                source_timeframe_minutes=timeframe_minutes,
                event_time_ns_source=event_time_ns,
                observed_time_ns=observed_time_ns,
                level=price,
                lower=pool.lower,
                upper=pool.upper,
                source_id=source_id,
                rule_provenance=COMPLETED_REFERENCE_LIQUIDITY_RULE,
            )

    def _update_previous_day(self, bar: Candle) -> None:
        interval_date = self._utc_interval_date(bar.ts_close_ns)
        if self._daily_date is None:
            self._daily_date = interval_date
            self._daily_high = bar.high
            self._daily_low = bar.low
            self._daily_last_close_ns = bar.ts_close_ns
            return
        if interval_date != self._daily_date:
            assert self._daily_high is not None and self._daily_low is not None
            assert self._daily_last_close_ns is not None
            day_text = self._daily_date.isoformat()
            self._register_completed_reference(
                source_id=f"PREVIOUS_DAY:{day_text}",
                timeframe_minutes=1440,
                high=self._daily_high,
                low=self._daily_low,
                event_time_ns=self._daily_last_close_ns,
                observed_time_ns=bar.ts_close_ns,
            )
            self._daily_date = interval_date
            self._daily_high = bar.high
            self._daily_low = bar.low
            self._daily_last_close_ns = bar.ts_close_ns
            return
        assert self._daily_high is not None and self._daily_low is not None
        self._daily_high = max(self._daily_high, bar.high)
        self._daily_low = min(self._daily_low, bar.low)
        self._daily_last_close_ns = bar.ts_close_ns

    def _update_previous_h4(self, bar: Candle) -> None:
        h4_ns = 240 * NS_PER_MINUTE
        bucket = (bar.ts_close_ns - 1) // h4_ns
        if self._h4_bucket is None or bucket != self._h4_bucket:
            self._h4_bucket = bucket
            self._h4_high = bar.high
            self._h4_low = bar.low
            self._h4_bars = 1
        else:
            assert self._h4_high is not None and self._h4_low is not None
            self._h4_high = max(self._h4_high, bar.high)
            self._h4_low = min(self._h4_low, bar.low)
            self._h4_bars += 1
        if bar.ts_close_ns % h4_ns != 0:
            return
        if self._h4_bars == 4:
            assert self._h4_high is not None and self._h4_low is not None
            self._register_completed_reference(
                source_id=f"PREVIOUS_H4:{bar.ts_close_ns}",
                timeframe_minutes=240,
                high=self._h4_high,
                low=self._h4_low,
                event_time_ns=bar.ts_close_ns,
                observed_time_ns=bar.ts_close_ns,
            )
        else:
            self._inc("incomplete_h4_reference_not_registered")
        self._h4_bucket = None
        self._h4_high = None
        self._h4_low = None
        self._h4_bars = 0

    def _causal_event_id(self, time_ns: int) -> str:
        # Every boundary hypothesis born from the same completed 5-minute
        # auction belongs to one causal episode.  Different source scales may
        # propose alternative geometry, but they are never independent trades.
        return f"IAE:{self.symbol}:{time_ns}:BOUNDARY_EVENT"

    def _episode_id(self, pool: LiquidityPool, time_ns: int, tag: str) -> str:
        return f"IAEH:{self.symbol}:{pool.pool_id}:{time_ns}:{tag}"

    def _objective_for_event(
        self,
        side: Side,
        source: LiquidityPool,
        bar: Candle,
    ) -> tuple[LiquidityPool, AuctionZone, float] | None:
        """Choose the named destination before an episode is allowed to live.

        The attack bar's full high/low is used rather than its close so a level
        already traversed intrabar cannot be called a future objective.  This
        is the frozen-target lifecycle already established in the RE1 lineage,
        now made an invariant of the single auction episode.
        """
        untouched_edge = bar.high if side is Side.LONG else bar.low
        target = self.liquidity.target_for(
            side,
            untouched_edge,
            bar.ts_close_ns,
            exclude_pool_id=source.pool_id,
        )
        if target is None:
            return None
        zone = self.liquidity.snapshot(target)
        price = zone.lower if side is Side.LONG else zone.upper
        return target, zone, float(price)

    def _reject_source_without_objective(
        self,
        source: LiquidityPool,
        side: Side,
        bar: Candle,
        family: EpisodeKind,
    ) -> None:
        source.consumed_time_ns = bar.ts_close_ns
        self._inc("boundary_event_without_preexisting_objective")
        self._audit(
            "intrinsic_boundary_event_without_objective",
            bar.ts_close_ns,
            pool_id=source.pool_id,
            side=_side_name(side),
            family=family.value,
            source_family=source.source_family,
            source_structure_ids=source.source_structure_ids,
            rule_provenance=(PREEXISTING_TARGET_RULE, COMMITTED_OBJECTIVE_RULE),
        )

    def _recommit_objective_after_role_flip(
        self,
        episode: AuctionEpisode,
        bar: Candle,
    ) -> bool:
        source = self.liquidity.by_id.get(episode.pool_id)
        if source is None:
            self._invalidate(episode, bar.ts_close_ns, "role_flip_source_missing")
            return False
        committed = self._objective_for_event(episode.side, source, bar)
        if committed is None:
            self._invalidate(
                episode,
                bar.ts_close_ns,
                "role_flip_has_no_preexisting_objective",
            )
            return False
        target, zone, price = committed
        episode.objective_pool_id = target.pool_id
        episode.objective_zone = zone
        episode.objective_price = price
        episode.objective_committed_time_ns = bar.ts_close_ns
        self._audit(
            "intrinsic_role_flip_objective_committed",
            bar.ts_close_ns,
            episode_id=episode.episode_id,
            pool_id=episode.pool_id,
            side=_side_name(episode.side),
            target_pool_id=target.pool_id,
            target=price,
            rule_provenance=(
                COMMITTED_OBJECTIVE_RULE,
                SAME_LEVEL_ROLE_FLIP_OBJECTIVE_RULE,
            ),
        )
        return True

    def _start_reclaim(
        self,
        pool: LiquidityPool,
        side: Side,
        bar: Candle,
        *,
        extreme: float,
        delayed: bool,
        roles: tuple[PoolRole, ...],
        transition_strength: float,
    ) -> None:
        episode_id = self._episode_id(pool, bar.ts_close_ns, "RECLAIM")
        if pool.engaged_event_id is not None:
            return
        committed = self._objective_for_event(side, pool, bar)
        if committed is None:
            self._reject_source_without_objective(
                pool,
                side,
                bar,
                EpisodeKind.LIQUIDITY_RECLAIM,
            )
            return
        target_pool, target_zone, target_price = committed
        pool.engaged_event_id = episode_id
        episode = AuctionEpisode(
            episode_id=episode_id,
            pool_id=pool.pool_id,
            kind=EpisodeKind.LIQUIDITY_RECLAIM,
            phase=EpisodePhase.WAIT_DISPLACEMENT,
            side=side,
            interaction_time_ns=bar.ts_close_ns,
            phase_time_ns=bar.ts_close_ns,
            source_lower=pool.lower,
            source_upper=pool.upper,
            source_center=pool.center,
            sweep_extreme=extreme,
            break_extreme=extreme,
            causal_event_id=self._causal_event_id(bar.ts_close_ns),
            source_roles=tuple(role.value for role in roles),
            transition_strength=float(transition_strength),
            objective_pool_id=target_pool.pool_id,
            objective_zone=target_zone,
            objective_price=target_price,
            objective_committed_time_ns=bar.ts_close_ns,
        )
        self.episodes[episode_id] = episode
        self._inc("reclaim_episode_started")
        self._audit(
            "intrinsic_reclaim_started",
            bar.ts_close_ns,
            episode_id=episode_id,
            causal_event_id=episode.causal_event_id,
            pool_id=pool.pool_id,
            side=_side_name(side),
            delayed_trap=delayed,
            source_lower=pool.lower,
            source_upper=pool.upper,
            sweep_extreme=extreme,
            pool_strength=pool.strength,
            pool_members=pool.member_count,
            source_roles=episode.source_roles,
            transition_strength=episode.transition_strength,
            target_pool_id=target_pool.pool_id,
            target=target_price,
            rule_provenance=(AUCTION_RECLAIM_RULE, ACTIVE_AUCTION_MAP_RULE, ONE_EPISODE_RULE),
        )

    def _start_break(
        self,
        pool: LiquidityPool,
        side: Side,
        bar: Candle,
        *,
        extreme: float,
        roles: tuple[PoolRole, ...],
        transition_strength: float,
        break_excursion: float,
    ) -> None:
        episode_id = self._episode_id(pool, bar.ts_close_ns, "BREAK")
        if pool.engaged_event_id is not None:
            return
        committed = self._objective_for_event(side, pool, bar)
        if committed is None:
            self._reject_source_without_objective(
                pool,
                side,
                bar,
                EpisodeKind.ACCEPTED_BREAK_RETEST,
            )
            return
        target_pool, target_zone, target_price = committed
        pool.engaged_event_id = episode_id
        episode = AuctionEpisode(
            episode_id=episode_id,
            pool_id=pool.pool_id,
            kind=EpisodeKind.ACCEPTED_BREAK_RETEST,
            phase=EpisodePhase.BREAK_PENDING,
            side=side,
            interaction_time_ns=bar.ts_close_ns,
            phase_time_ns=bar.ts_close_ns,
            source_lower=pool.lower,
            source_upper=pool.upper,
            source_center=pool.center,
            sweep_extreme=extreme,
            break_extreme=extreme,
            break_count=1,
            causal_event_id=self._causal_event_id(bar.ts_close_ns),
            source_roles=tuple(role.value for role in roles),
            transition_strength=float(transition_strength),
            break_excursion=float(break_excursion),
            objective_pool_id=target_pool.pool_id,
            objective_zone=target_zone,
            objective_price=target_price,
            objective_committed_time_ns=bar.ts_close_ns,
        )
        self.episodes[episode_id] = episode
        self._inc("break_episode_started")
        self._audit(
            "intrinsic_break_started",
            bar.ts_close_ns,
            episode_id=episode_id,
            causal_event_id=episode.causal_event_id,
            pool_id=pool.pool_id,
            side=_side_name(side),
            source_lower=pool.lower,
            source_upper=pool.upper,
            break_extreme=extreme,
            pool_strength=pool.strength,
            pool_members=pool.member_count,
            source_roles=episode.source_roles,
            transition_strength=episode.transition_strength,
            break_excursion=episode.break_excursion,
            target_pool_id=target_pool.pool_id,
            target=target_price,
            rule_provenance=(AUCTION_ACCEPTANCE_RULE, ACTIVE_AUCTION_MAP_RULE, ONE_EPISODE_RULE),
        )

    def _current_auction_edges(self) -> tuple[LiquidityPool, LiquidityPool] | None:
        low = self.liquidity.latest_pool(15, "LOW")
        high = self.liquidity.latest_pool(15, "HIGH")
        if low is None or high is None or low.center >= high.center:
            return None
        return low, high

    def _source_roles(self, pool: LiquidityPool, bar: Candle) -> tuple[PoolRole, ...]:
        roles: list[PoolRole] = [PoolRole.REACTION_OBSTACLE]
        edges = self._current_auction_edges()
        current_edge = False
        range_width = 0.0
        if edges is not None:
            low_edge, high_edge = edges
            current_edge = pool.pool_id in {low_edge.pool_id, high_edge.pool_id}
            range_width = max(high_edge.center - low_edge.center, self.dc[15].prior_range)
        latest_same_side = self.liquidity.latest_pool(pool.timeframe_minutes, pool.side)
        is_latest = latest_same_side is not None and latest_same_side.pool_id == pool.pool_id
        near_current_auction = True
        if edges is not None:
            low_edge, high_edge = edges
            allowance = range_width * (0.35 if pool.timeframe_minutes <= 15 else 1.25)
            near_current_auction = low_edge.center - allowance <= pool.center <= high_edge.center + allowance

        external = (
            (pool.timeframe_minutes == 5 and pool.member_count >= 2 and near_current_auction)
            or (pool.timeframe_minutes == 15 and current_edge)
            or (pool.timeframe_minutes == 60 and is_latest and near_current_auction)
            or pool.timeframe_minutes in {240, 1440}
        )
        value_boundary = (
            (pool.timeframe_minutes == 15 and current_edge)
            or (pool.timeframe_minutes == 60 and is_latest and near_current_auction)
            or (
                pool.timeframe_minutes == 5
                and pool.member_count >= 2
                and pool.five_minute_touch_count >= 2
                and near_current_auction
            )
            or pool.timeframe_minutes in {240, 1440}
        )
        if external:
            roles.append(PoolRole.EXTERNAL_STOP_POOL)
        if value_boundary:
            roles.append(PoolRole.VALUE_BOUNDARY)
        return tuple(roles)

    def _prior_five_minute_bars(self, time_ns: int, count: int = 8) -> list[Candle]:
        return [bar for bar in self.dc[5].bars if bar.ts_close_ns < time_ns][-count:]

    def _five_minute_stats(self, bar: Candle) -> tuple[float, float, float, float, float]:
        prior = self._prior_five_minute_bars(bar.ts_close_ns, 20)
        ranges = [max(item.high - item.low, self.tick_size) for item in prior]
        bodies = [max(abs(item.close - item.open), self.tick_size) for item in prior]
        baseline_range = median(ranges) if ranges else max(bar.high - bar.low, self.tick_size)
        baseline_body = median(bodies) if bodies else max(abs(bar.close - bar.open), self.tick_size)
        price_range = max(bar.high - bar.low, self.tick_size)
        body = bar.close - bar.open
        range_ratio = price_range / max(baseline_range, self.tick_size)
        body_ratio = abs(body) / max(baseline_body, self.tick_size)
        close_location = (bar.close - bar.low) / price_range
        return body, range_ratio, body_ratio, close_location, baseline_range

    def _approached_from_inside(self, pool: LiquidityPool, bar: Candle) -> bool:
        prior = self._prior_five_minute_bars(bar.ts_close_ns, 5)
        if len(prior) < 3:
            return False
        if pool.side == "LOW":
            inside = [item.close >= pool.lower for item in prior[-4:]]
            near = any(item.low <= pool.upper + self.dc[5].prior_range for item in prior[-3:])
        else:
            inside = [item.close <= pool.upper for item in prior[-4:]]
            near = any(item.high >= pool.lower - self.dc[5].prior_range for item in prior[-3:])
        return sum(inside) >= 3 and near

    def _sweep_transition_evidence(self, pool: LiquidityPool, bar: Candle) -> tuple[bool, float]:
        if not self._approached_from_inside(pool, bar):
            return False, 0.0
        body, range_ratio, body_ratio, close_location, baseline_range = self._five_minute_stats(bar)
        price_range = max(bar.high - bar.low, self.tick_size)
        if pool.side == "LOW":
            depth = pool.lower - bar.low
            reclaimed = bar.close >= pool.lower
            wick_fraction = (min(bar.open, bar.close) - bar.low) / price_range
            close_control = close_location >= 0.52
        else:
            depth = bar.high - pool.upper
            reclaimed = bar.close <= pool.upper
            wick_fraction = (bar.high - max(bar.open, bar.close)) / price_range
            close_control = close_location <= 0.48
        depth_ratio = depth / max(baseline_range, self.tick_size)
        accepted = (
            reclaimed
            and 0.08 <= depth_ratio <= 2.25
            and (wick_fraction >= 0.22 or close_control)
            and range_ratio >= 0.75
        )
        strength = 0.0
        if accepted:
            strength = (1.0 + depth_ratio) * max(1.0, range_ratio) * max(1.0, wick_fraction * 3.0)
        return accepted, strength

    def _break_transition_evidence(self, pool: LiquidityPool, side: Side, bar: Candle) -> tuple[bool, float, float]:
        prior = self._prior_five_minute_bars(bar.ts_close_ns, 6)
        if len(prior) < 4 or not self._approached_from_inside(pool, bar):
            return False, 0.0, 0.0
        body, range_ratio, body_ratio, close_location, baseline_range = self._five_minute_stats(bar)
        if side is Side.LONG:
            excursion = bar.close - pool.upper
            aligned = body > 0.0
            controlled_close = close_location >= 0.68
            prior_inside = sum(item.close <= pool.upper for item in prior[-5:]) >= 4
        else:
            excursion = pool.lower - bar.close
            aligned = body < 0.0
            controlled_close = close_location <= 0.32
            prior_inside = sum(item.close >= pool.lower for item in prior[-5:]) >= 4
        excursion_ratio = excursion / max(baseline_range, self.tick_size)
        prior_closes = [item.close for item in prior[-5:]]
        balance_width = max(prior_closes) - min(prior_closes)
        locally_balanced = balance_width <= baseline_range * 3.5
        accepted = (
            aligned
            and controlled_close
            and prior_inside
            and locally_balanced
            and excursion_ratio >= 0.20
            and range_ratio >= 0.90
            and body_ratio >= 1.00
        )
        strength = 0.0
        if accepted:
            strength = (1.0 + excursion_ratio) * range_ratio * body_ratio
        return accepted, strength, max(0.0, excursion)

    def _observe_new_interactions(self, bar: Candle) -> None:
        for pool in self.liquidity.eligible_sources(bar.ts_close_ns):
            roles = self._source_roles(pool, bar)
            role_set = set(roles)
            buffer = max(self.tick_size, (pool.upper - pool.lower) * 0.10)
            if pool.side == "LOW" and bar.low < pool.lower - buffer:
                sweep_ok, sweep_strength = self._sweep_transition_evidence(pool, bar)
                if (
                    PoolRole.EXTERNAL_STOP_POOL in role_set
                    and bar.close >= pool.lower
                    and sweep_ok
                ):
                    self._start_reclaim(
                        pool,
                        Side.LONG,
                        bar,
                        extreme=bar.low,
                        delayed=False,
                        roles=roles,
                        transition_strength=sweep_strength,
                    )
                    continue
                break_ok, break_strength, excursion = self._break_transition_evidence(pool, Side.SHORT, bar)
                if PoolRole.VALUE_BOUNDARY in role_set and break_ok:
                    self._start_break(
                        pool,
                        Side.SHORT,
                        bar,
                        extreme=bar.low,
                        roles=roles,
                        transition_strength=break_strength,
                        break_excursion=excursion,
                    )
            elif pool.side == "HIGH" and bar.high > pool.upper + buffer:
                sweep_ok, sweep_strength = self._sweep_transition_evidence(pool, bar)
                if (
                    PoolRole.EXTERNAL_STOP_POOL in role_set
                    and bar.close <= pool.upper
                    and sweep_ok
                ):
                    self._start_reclaim(
                        pool,
                        Side.SHORT,
                        bar,
                        extreme=bar.high,
                        delayed=False,
                        roles=roles,
                        transition_strength=sweep_strength,
                    )
                    continue
                break_ok, break_strength, excursion = self._break_transition_evidence(
                    pool,
                    Side.LONG,
                    bar,
                )
                if PoolRole.VALUE_BOUNDARY in role_set and break_ok:
                    self._start_break(
                        pool,
                        Side.LONG,
                        bar,
                        extreme=bar.high,
                        roles=roles,
                        transition_strength=break_strength,
                        break_excursion=excursion,
                    )
        self._settle_causal_event_owner(bar)

    def _episode_owner_key(
        self,
        episode: AuctionEpisode,
        bar: Candle,
    ) -> tuple[int, int, int, float, int, float, str]:
        """Order competing readings of one completed boundary attack.

        This is categorical arbitration, not an alpha score.  The bar's final
        settlement owns direction first.  A genuine reclaim then supersedes a
        nested break which ran into that outer rejection; public source roles,
        proximity to the settled close and scale resolve only representations
        of the same directional event.
        """
        pool = self.liquidity.by_id[episode.pool_id]
        body_aligned = (
            (episode.side is Side.LONG and bar.close > bar.open)
            or (episode.side is Side.SHORT and bar.close < bar.open)
        )
        settlement_rank = 0 if body_aligned else 1
        mechanism_rank = (
            0 if episode.kind is EpisodeKind.LIQUIDITY_RECLAIM else 1
        )
        roles = set(episode.source_roles)
        source_rank = (
            0
            if PoolRole.EXTERNAL_STOP_POOL.value in roles
            else 1
            if PoolRole.VALUE_BOUNDARY.value in roles
            else 2
        )
        settlement_distance = (
            max(0.0, pool.lower - bar.close, bar.close - pool.upper)
            / max(self.dc[5].prior_range, self.tick_size)
        )
        return (
            settlement_rank,
            mechanism_rank,
            source_rank,
            settlement_distance,
            -pool.timeframe_minutes,
            -episode.transition_strength,
            episode.pool_id,
        )

    def _settle_causal_event_owner(self, bar: Candle) -> None:
        event_id = self._causal_event_id(bar.ts_close_ns)
        candidates = [
            episode
            for episode in self.episodes.values()
            if episode.causal_event_id == event_id
            and episode.phase not in {EpisodePhase.DONE, EpisodePhase.INVALID}
        ]
        if not candidates:
            return

        sides = {episode.side for episode in candidates}
        if len(sides) > 1 and abs(bar.close - bar.open) < self.tick_size:
            for episode in candidates:
                self._invalidate(
                    episode,
                    bar.ts_close_ns,
                    "two_sided_event_without_causal_settlement",
                )
            self._audit(
                "intrinsic_causal_event_abstained",
                bar.ts_close_ns,
                causal_event_id=event_id,
                candidate_episode_ids=tuple(
                    sorted(episode.episode_id for episode in candidates)
                ),
                reason="OHLC_CANNOT_ORDER_TWO_SIDED_ATTACK_WITHOUT_SETTLEMENT",
            )
            return

        winner = min(candidates, key=lambda item: self._episode_owner_key(item, bar))
        self._causal_event_owner[event_id] = winner.episode_id
        for episode in candidates:
            if episode.episode_id == winner.episode_id:
                continue
            self._invalidate(
                episode,
                bar.ts_close_ns,
                "competing_interpretation_lost_causal_event_ownership",
            )
        self._audit(
            "intrinsic_causal_event_owned",
            bar.ts_close_ns,
            causal_event_id=event_id,
            owner_episode_id=winner.episode_id,
            owner_pool_id=winner.pool_id,
            owner_side=_side_name(winner.side),
            owner_family=winner.kind.value,
            suppressed_episode_ids=tuple(
                sorted(
                    episode.episode_id
                    for episode in candidates
                    if episode.episode_id != winner.episode_id
                )
            ),
            rule_provenance=ONE_EPISODE_RULE,
        )

    def _invalidate(self, episode: AuctionEpisode, time_ns: int, reason: str) -> None:
        episode.phase = EpisodePhase.INVALID
        episode.terminal_reason = reason
        pool = self.liquidity.by_id.get(episode.pool_id)
        if pool is not None:
            pool.consumed_time_ns = int(time_ns)
        self._inc(f"episode_invalid_{reason}")
        self._audit(
            "intrinsic_episode_invalidated",
            time_ns,
            episode_id=episode.episode_id,
            pool_id=episode.pool_id,
            side=_side_name(episode.side),
            phase=episode.phase.value,
            reason=reason,
        )

    def _clear_role_context(self, episode: AuctionEpisode) -> None:
        """Discard the prior side's frozen context after a same-level handoff."""
        for name in ("_episode_context", "_latent_episode"):
            values = getattr(self, name, None)
            if isinstance(values, dict):
                values.pop(episode.episode_id, None)

    def _handoff_reclaim_to_acceptance(
        self,
        episode: AuctionEpisode,
        bar: Candle,
    ) -> bool:
        """Transfer one failed reversal to same-level acceptance ownership."""
        if (
            episode.role_flip_count > 0
            or PoolRole.VALUE_BOUNDARY.value not in episode.source_roles
        ):
            return False
        accepted_outside = (
            episode.side is Side.LONG
            and bar.close < episode.source_lower
            and bar.close < bar.open
        ) or (
            episode.side is Side.SHORT
            and bar.close > episode.source_upper
            and bar.close > bar.open
        )
        if not accepted_outside:
            return False
        prior_side = episode.side
        episode.kind = EpisodeKind.ACCEPTED_BREAK_RETEST
        episode.side = Side.SHORT if prior_side is Side.LONG else Side.LONG
        if not self._recommit_objective_after_role_flip(episode, bar):
            return True
        episode.phase = EpisodePhase.BREAK_PENDING
        episode.phase_time_ns = bar.ts_close_ns
        episode.break_extreme = bar.low if episode.side is Side.SHORT else bar.high
        episode.break_count = 1
        episode.origin_lower = None
        episode.origin_upper = None
        episode.displacement_time_ns = None
        episode.retest_time_ns = None
        episode.retest_extreme = None
        episode.response_time_ns = None
        episode.role_flip_count += 1
        self._clear_role_context(episode)
        self._inc("failed_reclaim_handed_to_same_level_acceptance")
        self._audit(
            "intrinsic_reclaim_failed_into_acceptance",
            bar.ts_close_ns,
            episode_id=episode.episode_id,
            causal_event_id=episode.causal_event_id,
            pool_id=episode.pool_id,
            prior_side=_side_name(prior_side),
            side=_side_name(episode.side),
            source_lower=episode.source_lower,
            source_upper=episode.source_upper,
            break_extreme=episode.break_extreme,
            role_flip_count=episode.role_flip_count,
            rule_provenance=(AUCTION_RECLAIM_RULE, AUCTION_ACCEPTANCE_RULE),
        )
        return True

    def _advance_five_minute(self, bar: Candle) -> None:
        for episode in list(self.episodes.values()):
            if episode.phase is not EpisodePhase.BREAK_PENDING:
                continue
            if bar.ts_close_ns <= episode.phase_time_ns:
                continue
            bars_elapsed = int((bar.ts_close_ns - episode.phase_time_ns) // (5 * NS_PER_MINUTE))
            if bars_elapsed < 1:
                continue
            _, range_ratio, body_ratio, close_location, baseline_range = self._five_minute_stats(bar)
            if episode.side is Side.LONG:
                outside = bar.close > episode.source_upper
                opened_outside = bar.open > episode.source_upper - self.tick_size
                episode.break_extreme = max(episode.break_extreme, bar.high)
                total_excursion = episode.break_extreme - episode.source_upper
                retained = bar.close - episode.source_upper
                controlled = close_location >= 0.48 and retained >= total_excursion * 0.25
            else:
                outside = bar.close < episode.source_lower
                opened_outside = bar.open < episode.source_lower + self.tick_size
                episode.break_extreme = min(episode.break_extreme, bar.low)
                total_excursion = episode.source_lower - episode.break_extreme
                retained = episode.source_lower - bar.close
                controlled = close_location <= 0.52 and retained >= total_excursion * 0.25
            separated = total_excursion >= max(self.tick_size * 2.0, baseline_range * 0.45)
            hold_accepted = (
                outside
                and opened_outside
                and controlled
                and separated
                and range_ratio >= 0.55
                and body_ratio >= 0.35
            )

            if hold_accepted:
                episode.break_count += 1
                episode.break_excursion = max(episode.break_excursion, total_excursion)
                episode.phase = EpisodePhase.WAIT_RETEST
                episode.phase_time_ns = bar.ts_close_ns
                episode.displacement_time_ns = bar.ts_close_ns
                episode.origin_lower = episode.source_lower
                episode.origin_upper = episode.source_upper
                self._inc("break_accepted_state_transition")
                self._audit(
                    "intrinsic_break_accepted",
                    bar.ts_close_ns,
                    episode_id=episode.episode_id,
                    pool_id=episode.pool_id,
                    side=_side_name(episode.side),
                    break_count=episode.break_count,
                    break_excursion=episode.break_excursion,
                    retained_excursion=retained,
                    transition_strength=episode.transition_strength,
                    rule_provenance=(AUCTION_ACCEPTANCE_RULE, ACTIVE_AUCTION_MAP_RULE),
                )
                continue

            returned_inside = not outside
            can_be_trap = PoolRole.EXTERNAL_STOP_POOL.value in episode.source_roles
            if returned_inside and can_be_trap and episode.role_flip_count == 0:
                episode.kind = EpisodeKind.LIQUIDITY_RECLAIM
                episode.side = Side.SHORT if episode.side is Side.LONG else Side.LONG
                if not self._recommit_objective_after_role_flip(episode, bar):
                    continue
                episode.phase = EpisodePhase.WAIT_DISPLACEMENT
                episode.phase_time_ns = bar.ts_close_ns
                episode.interaction_time_ns = bar.ts_close_ns
                episode.sweep_extreme = episode.break_extreme
                episode.transition_strength *= 1.0 + min(2.0, total_excursion / max(baseline_range, self.tick_size))
                episode.role_flip_count += 1
                self._clear_role_context(episode)
                self._inc("failed_break_converted_to_liquidity_trap")
                self._audit(
                    "intrinsic_break_failed_into_reclaim",
                    bar.ts_close_ns,
                    episode_id=episode.episode_id,
                    pool_id=episode.pool_id,
                    side=_side_name(episode.side),
                    sweep_extreme=episode.sweep_extreme,
                    source_roles=episode.source_roles,
                    role_flip_count=episode.role_flip_count,
                    rule_provenance=(AUCTION_RECLAIM_RULE, AUCTION_ACCEPTANCE_RULE),
                )
            elif returned_inside and can_be_trap:
                self._invalidate(
                    episode,
                    bar.ts_close_ns,
                    "same_level_role_flip_already_used",
                )
            elif returned_inside:
                self._invalidate(episode, bar.ts_close_ns, "weak_break_returned_inside")
            elif bars_elapsed >= 2:
                self._invalidate(episode, bar.ts_close_ns, "break_failed_to_prove_acceptance")

    def _price_stats(self, bar: Candle) -> tuple[float, float, float, float]:
        price_range = max(bar.high - bar.low, self.tick_size)
        body = bar.close - bar.open
        baseline_range = median(self.one_minute_ranges) if self.one_minute_ranges else price_range
        baseline_body = median(self.one_minute_bodies) if self.one_minute_bodies else abs(body)
        range_ratio = price_range / max(baseline_range, self.tick_size)
        body_ratio = abs(body) / max(baseline_body, self.tick_size)
        close_location = (bar.close - bar.low) / price_range
        return body, range_ratio, body_ratio, close_location

    @staticmethod
    def _aligned_flow(side: Side, observation: FlowObservation | None) -> bool:
        if observation is None or not observation.active or not observation.directed:
            return False
        return (
            side is Side.LONG
            and observation.signed_taker_quote > 0.0
            and observation.body > 0.0
            and observation.material_progress
        ) or (
            side is Side.SHORT
            and observation.signed_taker_quote < 0.0
            and observation.body < 0.0
            and observation.material_progress
        )

    @staticmethod
    def _adverse_absorption(
        side: Side,
        observation: FlowObservation | None,
        *,
        close_away: bool,
    ) -> bool:
        if observation is None or not observation.active or not observation.directed or not close_away:
            return False
        return (
            side is Side.LONG and observation.signed_taker_quote < 0.0
        ) or (
            side is Side.SHORT and observation.signed_taker_quote > 0.0
        )

    def _displacement_evidence(
        self,
        episode: AuctionEpisode,
        bar: Candle,
        observation: FlowObservation | None,
    ) -> tuple[bool, float]:
        body, range_ratio, body_ratio, close_location = self._price_stats(bar)
        aligned_body = body > 0.0 if episode.side is Side.LONG else body < 0.0
        intended_close = close_location >= 0.62 if episode.side is Side.LONG else close_location <= 0.38
        away = (
            bar.close > episode.source_upper
            if episode.side is Side.LONG
            else bar.close < episode.source_lower
        )
        price_displacement = aligned_body and intended_close and away and range_ratio >= 1.0 and body_ratio >= 1.0
        flow_initiative = self._aligned_flow(episode.side, observation)
        absorption = self._adverse_absorption(
            episode.side,
            observation,
            close_away=away and intended_close,
        )
        prior_micro = [item for item in self.one_minute if item.ts_close_ns < bar.ts_close_ns][-3:]
        micro_break = True
        if prior_micro:
            micro_break = (
                bar.close > max(item.high for item in prior_micro)
                if episode.side is Side.LONG
                else bar.close < min(item.low for item in prior_micro)
            )
        accepted = (
            price_displacement
            and micro_break
            and (flow_initiative or absorption or body_ratio >= 1.75)
        )
        strength = 0.0
        if accepted:
            strength = range_ratio * body_ratio
            if observation is not None:
                strength *= max(1.0, observation.activity_ratio)
                strength *= max(1.0, observation.delta_ratio)
        return accepted, strength

    def _origin_zone(self, episode: AuctionEpisode, bar: Candle) -> tuple[float, float]:
        prior = [item for item in self.one_minute if item.ts_close_ns < bar.ts_close_ns]
        # Prefer the actual three-candle imbalance created by the control
        # transfer.  This encodes the source material's FVG/mitigation logic
        # instead of naming an arbitrary opposite candle an order block.
        if len(prior) >= 2:
            first = prior[-2]
            if episode.side is Side.LONG and bar.low > first.high:
                return first.high, bar.low
            if episode.side is Side.SHORT and bar.high < first.low:
                return bar.high, first.low
        # When no clean imbalance exists, the final opposite body before the
        # displacement is the remaining executable footprint.
        for item in reversed(prior[-6:]):
            opposite = item.close <= item.open if episode.side is Side.LONG else item.close >= item.open
            if not opposite:
                continue
            lower = min(item.open, item.close)
            upper = max(item.open, item.close)
            if upper - lower >= self.tick_size:
                return lower, upper
        # No synthetic footprint is invented.  When displacement leaves no
        # actual FVG and no opposite body, the public source itself is the only
        # causal first-return location.
        return episode.source_lower, episode.source_upper

    def _touches_zone(self, episode: AuctionEpisode, bar: Candle) -> bool:
        assert episode.origin_lower is not None and episode.origin_upper is not None
        return bar.low <= episode.origin_upper and bar.high >= episode.origin_lower

    def _response_evidence(
        self,
        episode: AuctionEpisode,
        bar: Candle,
        observation: FlowObservation | None,
    ) -> tuple[bool, float]:
        assert episode.origin_lower is not None and episode.origin_upper is not None
        body, range_ratio, body_ratio, close_location = self._price_stats(bar)
        mid = (episode.origin_lower + episode.origin_upper) / 2.0
        prior_micro = [item for item in self.one_minute if item.ts_close_ns < bar.ts_close_ns][-3:]
        if episode.side is Side.LONG:
            aligned_body = body > 0.0
            close_away = bar.close > max(mid, episode.origin_upper - self.tick_size)
            intended_close = close_location >= 0.64
            micro_break = not prior_micro or bar.close > max(item.high for item in prior_micro)
        else:
            aligned_body = body < 0.0
            close_away = bar.close < min(mid, episode.origin_lower + self.tick_size)
            intended_close = close_location <= 0.36
            micro_break = not prior_micro or bar.close < min(item.low for item in prior_micro)
        initiative = self._aligned_flow(episode.side, observation)
        absorption = self._adverse_absorption(episode.side, observation, close_away=close_away)
        price_response = (
            close_away
            and intended_close
            and range_ratio >= 0.75
            and (
                (aligned_body and micro_break)
                or (absorption and body_ratio >= 0.85)
            )
        )
        accepted = price_response and (
            initiative
            or absorption
            or (micro_break and body_ratio >= 1.35)
        )
        strength = range_ratio * max(1.0, body_ratio)
        if micro_break:
            strength *= 1.25
        if observation is not None and accepted:
            strength *= max(1.0, observation.activity_ratio)
            strength *= max(1.0, observation.delta_ratio)
        return accepted, strength

    def _build_plan(
        self,
        episode: AuctionEpisode,
        bar: Candle,
        response_strength: float,
    ) -> V5TradePlan | None:
        pool = self.liquidity.by_id.get(episode.pool_id)
        if pool is None:
            self._invalidate(episode, bar.ts_close_ns, "source_pool_missing")
            return None
        event_id = episode.causal_event_id or self._causal_event_id(
            episode.interaction_time_ns,
        )
        owner = self._causal_event_owner.get(event_id)
        if owner is not None and owner != episode.episode_id:
            self._invalidate(
                episode,
                bar.ts_close_ns,
                "causal_event_owned_by_competing_episode",
            )
            return None
        entry = float(bar.close)
        if episode.kind is EpisodeKind.LIQUIDITY_RECLAIM:
            stop = (
                episode.sweep_extreme - self.tick_size
                if episode.side is Side.LONG
                else episode.sweep_extreme + self.tick_size
            )
        else:
            retest_extreme = episode.retest_extreme
            if retest_extreme is None:
                return None
            stop = (
                min(retest_extreme, episode.source_lower) - self.tick_size
                if episode.side is Side.LONG
                else max(retest_extreme, episode.source_upper) + self.tick_size
            )
        risk = entry - stop if episode.side is Side.LONG else stop - entry
        if risk <= self.tick_size * 0.5:
            self._invalidate(episode, bar.ts_close_ns, "nonpositive_risk")
            return None

        target_pool = (
            None
            if episode.objective_pool_id is None
            else self.liquidity.by_id.get(episode.objective_pool_id)
        )
        target_zone = episode.objective_zone
        target = episode.objective_price
        if target_pool is None or target_zone is None or target is None:
            self._invalidate(episode, bar.ts_close_ns, "episode_lost_committed_objective")
            return None
        if (
            not target_pool.active
            or target_pool.objective_spent_time_ns is not None
        ):
            self._invalidate(
                episode,
                bar.ts_close_ns,
                "committed_objective_spent_or_superseded_before_entry",
            )
            return None
        reward = target - entry if episode.side is Side.LONG else entry - target
        gross_rr = reward / risk
        if reward <= 0.0 or gross_rr + 1e-12 < self.minimum_gross_rr:
            self._invalidate(episode, bar.ts_close_ns, "nearest_target_below_minimum_rr")
            return None

        self._sequence += 1
        plan_id = f"IAEPLAN:{self.symbol}:{bar.ts_close_ns}:{self._sequence}"
        source_zone = self.liquidity.snapshot(pool)
        origin_lower = episode.origin_lower if episode.origin_lower is not None else pool.lower
        origin_upper = episode.origin_upper if episode.origin_upper is not None else pool.upper
        scenario = (
            ScenarioPath.REJECTION.value
            if episode.kind is EpisodeKind.LIQUIDITY_RECLAIM
            else ScenarioPath.ACCEPTANCE.value
        )
        trigger_kind = (
            "FLOW_OR_PRICE_RESPONSE_AFTER_SWEEP_MITIGATION"
            if episode.kind is EpisodeKind.LIQUIDITY_RECLAIM
            else "FLOW_OR_PRICE_RESPONSE_AT_ACCEPTED_BREAK_RETEST"
        )
        factor_side = getattr(getattr(self._market_factor_state, "side", None), "name", "NEUTRAL")
        provenance = (
            INTRINSIC_TIME_RULE,
            PUBLIC_LIQUIDITY_RULE,
            AUCTION_RECLAIM_RULE
            if episode.kind is EpisodeKind.LIQUIDITY_RECLAIM
            else AUCTION_ACCEPTANCE_RULE,
            FLOW_TRANSITION_RULE,
            PREEXISTING_TARGET_RULE,
            COMMITTED_OBJECTIVE_RULE,
            ACTIVE_AUCTION_MAP_RULE,
            FIRST_REACTION_TARGET_RULE,
            ONE_EPISODE_RULE,
            f"RESEARCH_STATE:MACRO_SIDE={getattr(self._trend_side, 'name', 'NEUTRAL')}",
            f"RESEARCH_STATE:COMMON_FACTOR_SIDE={factor_side}",
        )
        plan = V5TradePlan(
            plan_id=plan_id,
            causal_event_id=(
                episode.causal_event_id
                or self._causal_event_id(episode.interaction_time_ns)
            ),
            symbol=self.symbol,
            family=episode.kind.value,
            side=episode.side,
            observed_time_ns=bar.ts_close_ns,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
            setup_id=episode.episode_id,
            higher_zone_id=source_zone.zone_id,
            higher_zone_kind=source_zone.kind,
            higher_strength_ratio=source_zone.strength_ratio,
            lower_zone_id=f"MITIGATION:{episode.episode_id}",
            lower_zone_kind=source_zone.kind,
            lower_strength_ratio=max(1.0, episode.evidence_strength),
            trigger_zone_id=f"RESPONSE:{episode.episode_id}:{bar.ts_close_ns}",
            trigger_strength_ratio=max(1.0, response_strength),
            target_zone_id=target_zone.zone_id,
            target_zone_kind=target_zone.kind,
            overlap_lower=float(origin_lower),
            overlap_upper=float(origin_upper),
            interaction_time_ns=episode.interaction_time_ns,
            trigger_time_ns=bar.ts_close_ns,
            scenario_path=scenario,
            setup_observed_time_ns=episode.phase_time_ns,
            trigger_zone_kind=trigger_kind,
            source_rule_count=len(provenance),
            rule_provenance=provenance,
            scale_name=f"{pool.timeframe_minutes}m_INTRINSIC_TO_5m_TO_1m",
            higher_timeframe_minutes=pool.timeframe_minutes,
            decision_timeframe_minutes=5,
            trigger_timeframe_minutes=1,
        )
        pool.consumed_time_ns = bar.ts_close_ns
        episode.phase = EpisodePhase.DONE
        episode.response_time_ns = bar.ts_close_ns
        self._plans.append(plan)
        self._inc(f"plan_{episode.kind.value.lower()}")
        self._audit(
            "intrinsic_plan_emitted",
            bar.ts_close_ns,
            episode_id=episode.episode_id,
            causal_event_id=plan.causal_event_id,
            plan_id=plan_id,
            pool_id=pool.pool_id,
            target_pool_id=target_pool.pool_id,
            target_timeframe_minutes=target_pool.timeframe_minutes,
            target_members=target_pool.member_count,
            target_touches=target_pool.five_minute_touch_count,
            objective_committed_time_ns=episode.objective_committed_time_ns,
            source_roles=episode.source_roles,
            transition_strength=episode.transition_strength,
            family=episode.kind.value,
            side=_side_name(episode.side),
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
            origin_lower=origin_lower,
            origin_upper=origin_upper,
            response_strength=response_strength,
            pool_strength=pool.strength,
            pool_members=pool.member_count,
            pool_touches=pool.touch_count,
            macro_side=getattr(self._trend_side, "name", "NEUTRAL"),
            common_factor_side=factor_side,
            rule_provenance=provenance,
        )
        return plan

    def _advance_one_minute(
        self,
        bar: Candle,
        observation: FlowObservation | None,
    ) -> list[V5TradePlan]:
        emitted: list[V5TradePlan] = []
        for episode in list(self.episodes.values()):
            if episode.phase in {EpisodePhase.DONE, EpisodePhase.INVALID, EpisodePhase.BREAK_PENDING}:
                continue
            if bar.ts_close_ns <= episode.phase_time_ns:
                continue
            pool = self.liquidity.by_id.get(episode.pool_id)
            if pool is None or not self.liquidity.is_recent(pool):
                self._invalidate(episode, bar.ts_close_ns, "source_auction_superseded")
                continue

            if episode.kind is EpisodeKind.LIQUIDITY_RECLAIM:
                if episode.side is Side.LONG and bar.low <= episode.sweep_extreme:
                    if not self._handoff_reclaim_to_acceptance(episode, bar):
                        self._invalidate(episode, bar.ts_close_ns, "sweep_extreme_rebroken_before_entry")
                    continue
                if episode.side is Side.SHORT and bar.high >= episode.sweep_extreme:
                    if not self._handoff_reclaim_to_acceptance(episode, bar):
                        self._invalidate(episode, bar.ts_close_ns, "sweep_extreme_rebroken_before_entry")
                    continue
            elif episode.phase in {EpisodePhase.WAIT_RETEST, EpisodePhase.WAIT_RESPONSE}:
                if episode.side is Side.LONG and bar.close < episode.source_lower - self.tick_size:
                    self._invalidate(episode, bar.ts_close_ns, "accepted_break_reentered_old_range")
                    continue
                if episode.side is Side.SHORT and bar.close > episode.source_upper + self.tick_size:
                    self._invalidate(episode, bar.ts_close_ns, "accepted_break_reentered_old_range")
                    continue

            if episode.phase is EpisodePhase.WAIT_DISPLACEMENT:
                accepted, strength = self._displacement_evidence(episode, bar, observation)
                if not accepted:
                    continue
                lower, upper = self._origin_zone(episode, bar)
                # Keep mitigation geometry near the causal boundary.  An origin
                # entirely beyond the source is still valid, but a remote zone
                # would turn the retest into a second unrelated episode.
                source_width = max(episode.source_upper - episode.source_lower, self.tick_size)
                if episode.side is Side.LONG and lower > episode.source_upper + source_width * 4.0:
                    lower, upper = episode.source_lower, episode.source_upper
                elif episode.side is Side.SHORT and upper < episode.source_lower - source_width * 4.0:
                    lower, upper = episode.source_lower, episode.source_upper
                episode.origin_lower = lower
                episode.origin_upper = upper
                episode.displacement_time_ns = bar.ts_close_ns
                episode.phase_time_ns = bar.ts_close_ns
                episode.phase = EpisodePhase.WAIT_RETEST
                episode.evidence_strength = strength
                self._inc("displacement_confirmed")
                self._audit(
                    "intrinsic_displacement_confirmed",
                    bar.ts_close_ns,
                    episode_id=episode.episode_id,
                    pool_id=episode.pool_id,
                    side=_side_name(episode.side),
                    origin_lower=lower,
                    origin_upper=upper,
                    strength=strength,
                    flow_available=observation is not None,
                    rule_provenance=FLOW_TRANSITION_RULE,
                )
                continue

            if episode.phase is EpisodePhase.WAIT_RETEST:
                if episode.origin_lower is None or episode.origin_upper is None:
                    episode.origin_lower = episode.source_lower
                    episode.origin_upper = episode.source_upper
                if not self._touches_zone(episode, bar):
                    continue
                episode.retest_time_ns = bar.ts_close_ns
                episode.retest_extreme = (
                    bar.low
                    if episode.side is Side.LONG
                    else bar.high
                )
                accepted, strength = self._response_evidence(episode, bar, observation)
                if accepted:
                    touch_strength = max(
                        1.0,
                        episode.transition_strength,
                        episode.evidence_strength,
                        strength,
                    )
                    plan = self._build_plan(episode, bar, touch_strength)
                    if plan is not None:
                        emitted.append(plan)
                else:
                    # A first touch without a completed response is not an
                    # entry.  Exactly the next completed minute may prove that
                    # the touch was defended; otherwise the opportunity ends.
                    episode.phase = EpisodePhase.WAIT_RESPONSE
                    episode.phase_time_ns = bar.ts_close_ns
                    self._inc("first_return_waiting_one_response_bar")
                    self._audit(
                        "intrinsic_first_return_waiting_response",
                        bar.ts_close_ns,
                        episode_id=episode.episode_id,
                        pool_id=episode.pool_id,
                        side=_side_name(episode.side),
                        retest_extreme=episode.retest_extreme,
                    )
                continue

            if episode.phase is EpisodePhase.WAIT_RESPONSE:
                if episode.retest_time_ns is None:
                    self._invalidate(episode, bar.ts_close_ns, "missing_retest_state")
                    continue
                response_age = (bar.ts_close_ns - episode.retest_time_ns) / NS_PER_MINUTE
                if response_age > 1.0:
                    self._invalidate(episode, bar.ts_close_ns, "first_response_bar_missing")
                    continue
                if episode.side is Side.LONG:
                    episode.retest_extreme = min(_finite(episode.retest_extreme, bar.low), bar.low)
                else:
                    episode.retest_extreme = max(_finite(episode.retest_extreme, bar.high), bar.high)
                accepted, strength = self._response_evidence(episode, bar, observation)
                if accepted:
                    plan = self._build_plan(episode, bar, strength)
                    if plan is not None:
                        emitted.append(plan)
                else:
                    self._invalidate(
                        episode,
                        bar.ts_close_ns,
                        "first_return_not_defended",
                    )
        return emitted

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes not in self.SUPPORTED_TIMEFRAMES:
            return []
        if timeframe_minutes == 60:
            self._update_previous_h4(bar)
        if timeframe_minutes in self.dc:
            self._register_swings(timeframe_minutes, bar)
        if timeframe_minutes == 5:
            self._advance_five_minute(bar)
            self._observe_new_interactions(bar)
            self.liquidity.observe_touch(bar, 5)
            return []
        if timeframe_minutes != 1:
            return []

        self._update_previous_day(bar)
        self.audit_one_minute.observe(bar)
        self.liquidity.observe_touch(bar, 1)
        observation = self.flow.observe(bar)
        emitted = self._advance_one_minute(bar, observation)
        price_range = max(bar.high - bar.low, self.tick_size)
        self.one_minute_ranges.append(price_range)
        self.one_minute_bodies.append(abs(bar.close - bar.open))
        self.one_minute.append(bar)
        # Remove terminal episodes after their traces and plans are durable.
        self.episodes = {
            key: value
            for key, value in self.episodes.items()
            if value.phase not in {EpisodePhase.DONE, EpisodePhase.INVALID}
        }
        return emitted

    def drain_trace(self) -> list[dict[str, Any]]:
        output = self._trace
        self._trace = []
        return output

    def find_zone(self, zone_id: str) -> AuctionZone | None:
        return self.liquidity.find_zone(zone_id)

    @property
    def plans(self) -> list[V5TradePlan]:
        return list(self._plans)

    @property
    def setups(self) -> list[AuctionEpisode]:
        return list(self.episodes.values())

    @property
    def detectors(self) -> dict[int, Any]:
        return {60: self.dc[60], 15: self.dc[15], 5: self.dc[5], 1: self.audit_one_minute}

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "intrinsic_auction": {
                "counts": dict(sorted(self._counts.items())),
                "active_episodes": len(self.episodes),
                "plans": len(self._plans),
                "macro_side": getattr(self._trend_side, "name", None),
                "liquidity_counts": dict(sorted(self.liquidity.counts.items())),
                "pools": len(self.liquidity.pools),
                "active_pools": sum(pool.active for pool in self.liquidity.pools),
                "flow": self.flow.diagnostics,
                "directional_change": {
                    str(timeframe): dict(sorted(detector.counts.items()))
                    for timeframe, detector in self.dc.items()
                },
                "rules": (
                    INTRINSIC_TIME_RULE,
                    PUBLIC_LIQUIDITY_RULE,
                    AUCTION_RECLAIM_RULE,
                    AUCTION_ACCEPTANCE_RULE,
                    FLOW_TRANSITION_RULE,
                    PREEXISTING_TARGET_RULE,
                    COMMITTED_OBJECTIVE_RULE,
                    ACTIVE_AUCTION_MAP_RULE,
                    FIRST_REACTION_TARGET_RULE,
                    ONE_EPISODE_RULE,
                    COMPLETED_REFERENCE_LIQUIDITY_RULE,
                ),
            }
        }


MultiScaleScenarioBundle = IntrinsicAuctionBundle

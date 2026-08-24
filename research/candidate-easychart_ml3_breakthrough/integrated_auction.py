"""Hierarchical EasyChart auction policy: direction, liquidity, structure, event, entry.

The previous intrinsic candidate correctly made liquidity interaction the causal
unit, but its directional state was only the last two 15-minute highs/lows and
its source map did not understand diagonal structure.  This module keeps one
auction policy and adds the missing hierarchy:

* 60m and 15m confirmed intrinsic swings define structural direction;
* robust wick-based high/low lines form a causal channel when they are parallel;
* source-faithful trend lines and fresh channel edges may own the same boundary
  event as horizontal liquidity, rather than acting as a score-only filter;
* a sweep/reclaim may reverse an established move only at an external edge;
* the named boundary's failed or accepted settlement owns direction and target;
* OB/FVG are still used only as the first mitigation footprint;
* invalidation includes prior-only microstructure noise so ordinary one-minute
  fluctuation and fees cannot masquerade as several R.

No symbol identity, clock session, configured win rate, or user target statistic
enters the policy.  Every state is computed from completed bars and confirmed
turning points shared by BTC, ETH, SOL and XRP.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from statistics import median
from typing import Any, Iterable

import contracts_v5 as _contracts
from contracts_v5 import ObjectKind, StructureFamily, StructureZone, V5TradePlan
from domain import Candle, Side
from easychart_zones import ZoneSide
from intrinsic_auction import (
    ACTIVE_AUCTION_MAP_RULE,
    AUCTION_ACCEPTANCE_RULE,
    AUCTION_RECLAIM_RULE,
    FIRST_REACTION_TARGET_RULE,
    FLOW_TRANSITION_RULE,
    INTRINSIC_TIME_RULE,
    ONE_EPISODE_RULE,
    PREEXISTING_TARGET_RULE,
    PUBLIC_LIQUIDITY_RULE,
    AdaptiveDirectionalChange,
    AuctionEpisode,
    EpisodeKind,
    IntrinsicAuctionBundle,
    IntrinsicSwing,
    LiquidityPool,
    NS_PER_MINUTE,
    PoolRole,
    _side_name,
)
from structure_admission_v5 import (
    CHANNEL_FOURTH_POINT_RULE,
    MEANINGFUL_HORIZONTAL_RULE,
    OBSERVABLE_STRUCTURE_RULE,
    SourceFaithfulStructureBook,
)


HIERARCHICAL_DIRECTION_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:CONFIRMED_60M_AND_15M_INTRINSIC_HIGH_LOW_"
    "SEQUENCES_AND_ROBUST_WICK_SLOPES_DEFINE_DIRECTION_WITHOUT_ONE_LINEAR_TOOL_RANK"
)
CAUSAL_CHANNEL_RULE = (
    "SOURCE_EXPLICIT:TRENDLINES_AND_CHANNELS_ARE_WICK_BASED_STRUCTURAL_CONTEXT_"
    "AND_PARALLEL_CONFIRMED_HIGH_LOW_LINES_DEFINE_THE_CURRENT_AUCTION_RANGE"
)
CHANNEL_LIQUIDITY_RULE = (
    "SOURCE_EXPLICIT:CHANNEL_AND_TRENDLINE_EDGES_CONCENTRATE_LIQUIDITY_AND_"
    "MAY_OWN_OR_CONFLUENCE_WITH_HORIZONTAL_PUBLIC_DECISION_BOUNDARIES"
)
CONTEXT_EVENT_RULE = (
    "SOURCE_EXPLICIT:ONE_NAMED_PUBLIC_BOUNDARY_AND_ITS_FAILED_OR_ACCEPTED_"
    "SETTLEMENT_OWN_DIRECTION_WHILE_STRUCTURE_AND_FLOW_DESCRIBE_CONTEXT"
)
CAUSAL_NOISE_INVALIDATION_RULE = (
    "EXTERNAL_METHOD:STOP_INVALIDATION_EXTENDS_BEYOND_THE_EVENT_EXTREME_BY_A_"
    "PRIOR_ONLY_MICROSTRUCTURE_NOISE_BUFFER_RATHER_THAN_ONE_ARBITRARY_TICK"
)
INTEGRATED_POLICY_RULE = (
    "SOURCE_EXPLICIT:PRICE_AND_VOLUME_DEFINE_DIRECTION_LIQUIDITY_STRUCTURE_EVENT_"
    "THEN_OB_FVG_OR_RETEST_PRECISE_ENTRY_AND_OPPOSING_STRUCTURE_EXIT_AS_ONE_POLICY"
)
PROJECTED_STRUCTURE_EVENT_RULE = (
    "SOURCE_EXPLICIT:AN_OBSERVABLE_TRENDLINE_OR_FRESH_EXPECTED_CHANNEL_EDGE_"
    "MAY_OWN_THE_SAME_FAILED_OR_ACCEPTED_AUCTION_EPISODE_AS_HORIZONTAL_LIQUIDITY"
)
for _rule in (
    HIERARCHICAL_DIRECTION_RULE,
    CAUSAL_CHANNEL_RULE,
    CHANNEL_LIQUIDITY_RULE,
    CONTEXT_EVENT_RULE,
    CAUSAL_NOISE_INVALIDATION_RULE,
    INTEGRATED_POLICY_RULE,
    PROJECTED_STRUCTURE_EVENT_RULE,
):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


@dataclass(frozen=True, slots=True)
class RobustWickLine:
    side: str
    timeframe_minutes: int
    slope_per_ns: float
    intercept: float
    residual_scale: float
    anchor_count: int
    first_time_ns: int
    last_time_ns: int
    observed_time_ns: int

    def value_at(self, time_ns: int) -> float:
        return self.intercept + self.slope_per_ns * float(time_ns)


@dataclass(frozen=True, slots=True)
class CausalChannelState:
    timeframe_minutes: int
    lower: float
    upper: float
    mid: float
    width: float
    slope_per_ns: float
    normalized_slope: float
    quality: float
    position: float
    direction: str
    observed_time_ns: int
    lower_line: RobustWickLine
    upper_line: RobustWickLine


@dataclass(frozen=True, slots=True)
class HierarchicalAuctionContext:
    time_ns: int
    price: float
    structure_60: float
    structure_15: float
    signed_direction_score: float
    macro_side: Side | None
    channel_60: CausalChannelState | None
    channel_15: CausalChannelState | None
    common_factor_side: Side | None

    def side_alignment(self, side: Side) -> float:
        sign = 1.0 if side is Side.LONG else -1.0
        return sign * self.signed_direction_score


@dataclass(frozen=True, slots=True)
class EpisodeContextSnapshot:
    context: HierarchicalAuctionContext
    source_edge_score: float
    channel_confluence: float
    common_factor_alignment: float
    structural_alignment: float
    compatible: bool
    reason: str



def _clip(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


def _median_pairwise_slope(points: list[tuple[int, float]]) -> float:
    slopes: list[float] = []
    for i, (time_a, price_a) in enumerate(points):
        for time_b, price_b in points[i + 1 :]:
            elapsed = time_b - time_a
            if elapsed > 0:
                slopes.append((price_b - price_a) / elapsed)
    return median(slopes) if slopes else 0.0


def _fit_line(
    pools: Iterable[LiquidityPool],
    *,
    side: str,
    timeframe_minutes: int,
    now_ns: int,
    limit: int = 6,
) -> RobustWickLine | None:
    selected = [
        pool
        for pool in pools
        if pool.side == side
        and pool.source_family == "HORIZONTAL_LIQUIDITY"
        and pool.timeframe_minutes == timeframe_minutes
        and pool.observed_time_ns < now_ns
    ]
    selected = sorted(selected, key=lambda item: (item.last_sequence, item.observed_time_ns))[-limit:]
    if len(selected) < 2:
        return None
    points = [(int(pool.last_event_time_ns), float(pool.center)) for pool in selected]
    points = sorted(dict(points).items())
    if len(points) < 2:
        return None
    slope = _median_pairwise_slope(points)
    intercept = median(price - slope * time_ns for time_ns, price in points)
    residuals = [abs(price - (intercept + slope * time_ns)) for time_ns, price in points]
    residual = median(residuals) if residuals else 0.0
    return RobustWickLine(
        side=side,
        timeframe_minutes=timeframe_minutes,
        slope_per_ns=slope,
        intercept=intercept,
        residual_scale=residual,
        anchor_count=len(points),
        first_time_ns=points[0][0],
        last_time_ns=points[-1][0],
        observed_time_ns=max(pool.observed_time_ns for pool in selected),
    )


def _sequence_direction(pools: Iterable[LiquidityPool], timeframe: int, now_ns: int) -> float:
    highs = sorted(
        (
            pool
            for pool in pools
            if pool.timeframe_minutes == timeframe
            and pool.source_family == "HORIZONTAL_LIQUIDITY"
            and pool.side == "HIGH"
            and pool.observed_time_ns < now_ns
        ),
        key=lambda item: (item.last_sequence, item.observed_time_ns),
    )[-3:]
    lows = sorted(
        (
            pool
            for pool in pools
            if pool.timeframe_minutes == timeframe
            and pool.source_family == "HORIZONTAL_LIQUIDITY"
            and pool.side == "LOW"
            and pool.observed_time_ns < now_ns
        ),
        key=lambda item: (item.last_sequence, item.observed_time_ns),
    )[-3:]
    if len(highs) < 2 or len(lows) < 2:
        return 0.0
    high_delta = highs[-1].center - highs[-2].center
    low_delta = lows[-1].center - lows[-2].center
    if high_delta > 0.0 and low_delta > 0.0:
        score = 1.0
    elif high_delta < 0.0 and low_delta < 0.0:
        score = -1.0
    else:
        # A mixed sequence is transition/range, but the latest side still
        # contributes weak directional information rather than an abrupt flip.
        score = 0.25 * (math.copysign(1.0, high_delta) if high_delta else 0.0)
        score += 0.25 * (math.copysign(1.0, low_delta) if low_delta else 0.0)
    if len(highs) >= 3 and len(lows) >= 3:
        persistent_up = highs[-1].center > highs[-2].center > highs[-3].center and lows[-1].center > lows[-2].center > lows[-3].center
        persistent_down = highs[-1].center < highs[-2].center < highs[-3].center and lows[-1].center < lows[-2].center < lows[-3].center
        if persistent_up:
            score = 1.25
        elif persistent_down:
            score = -1.25
    return _clip(score, -1.25, 1.25)


class IntegratedAuctionBundle(IntrinsicAuctionBundle):
    """One hierarchical policy, not a vote among tool-specific strategies."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._episode_context: dict[str, EpisodeContextSnapshot] = {}
        self._context_counts: dict[str, int] = {}
        self._last_context: HierarchicalAuctionContext | None = None
        self._projected_structure = {
            60: SourceFaithfulStructureBook(self.symbol, 60, self.tick_size),
            15: SourceFaithfulStructureBook(self.symbol, 15, self.tick_size),
        }

    def _cinc(self, key: str) -> None:
        self._context_counts[key] = self._context_counts.get(key, 0) + 1

    @staticmethod
    def _structure_touches(bar: Candle, zone: StructureZone) -> bool:
        return bar.low <= zone.upper and bar.high >= zone.lower

    @staticmethod
    def _structure_family_priority(zone: StructureZone) -> int:
        return {
            StructureFamily.CHANNEL: 2,
            StructureFamily.TREND_LINE: 1,
            StructureFamily.HORIZONTAL: 0,
        }[zone.family]

    def _projected_interaction_groups(
        self,
        bar: Candle,
    ) -> list[tuple[StructureZone, tuple[StructureZone, ...]]]:
        """Return one-sided confluence groups at fresh diagonal interactions."""
        by_source: dict[str, StructureZone] = {}
        for book in self._projected_structure.values():
            for zone in book.boundaries_at(bar.ts_close_ns):
                if (
                    zone.family not in {StructureFamily.TREND_LINE, StructureFamily.CHANNEL}
                    or zone.observed_time_ns >= bar.ts_close_ns
                    or not self._structure_touches(bar, zone)
                ):
                    continue
                current = by_source.get(zone.source_structure_id)
                if current is None or zone.timeframe_minutes > current.timeframe_minutes:
                    by_source[zone.source_structure_id] = zone
        zones = sorted(
            by_source.values(),
            key=lambda item: (item.side.value, item.lower, item.upper, item.source_structure_id),
        )
        if not zones:
            return []
        sides = {zone.side for zone in zones}
        if len(sides) > 1:
            self._cinc("projected_structure_two_sided_interaction_unresolved")
            self._audit(
                "projected_structure_two_sided_interaction_unresolved",
                bar.ts_close_ns,
                source_structure_ids=tuple(sorted(by_source)),
                rule_provenance=PROJECTED_STRUCTURE_EVENT_RULE,
            )
            return []

        clusters: list[list[StructureZone]] = []
        for zone in zones:
            if not clusters or zone.lower > max(item.upper for item in clusters[-1]) + self.tick_size:
                clusters.append([zone])
            else:
                clusters[-1].append(zone)
        output: list[tuple[StructureZone, tuple[StructureZone, ...]]] = []
        for members in clusters:
            primary = max(
                members,
                key=lambda item: (
                    item.timeframe_minutes,
                    item.source_pivot_span,
                    self._structure_family_priority(item),
                    item.strength_ratio,
                    item.source_structure_id,
                ),
            )
            output.append((primary, tuple(members)))
        return output

    def _observe_projected_structure_interactions(self, bar: Candle) -> None:
        """Translate admitted diagonal structure into the existing episode FSM.

        No trendline or channel strategy is added.  A fresh projected boundary
        can only create the same reclaim or accepted-break episode already used
        by horizontal liquidity, and the causal-event arbiter still chooses one
        owner when representations overlap.
        """
        roles = (
            PoolRole.REACTION_OBSTACLE,
            PoolRole.EXTERNAL_STOP_POOL,
            PoolRole.VALUE_BOUNDARY,
        )
        for primary, members in self._projected_interaction_groups(bar):
            lower = min(item.lower for item in members)
            upper = max(item.upper for item in members)
            pool = self.liquidity.register_projected_structure(
                source_ids=tuple(item.source_structure_id for item in members),
                side="LOW" if primary.side is ZoneSide.SUPPORT else "HIGH",
                lower=lower,
                upper=upper,
                formed_time_ns=max(item.formed_time_ns for item in members),
                observed_time_ns=max(item.observed_time_ns for item in members),
                interaction_time_ns=bar.ts_close_ns,
                timeframe_minutes=max(item.timeframe_minutes for item in members),
                strength=max(item.strength_ratio for item in members),
                object_kind=(
                    primary.kind
                    if isinstance(primary.kind, ObjectKind)
                    else ObjectKind.SWING_LOW
                    if primary.side is ZoneSide.SUPPORT
                    else ObjectKind.SWING_HIGH
                ),
            )
            started = False
            if pool.side == "LOW" and bar.low < pool.lower:
                sweep_ok, sweep_strength = self._sweep_transition_evidence(pool, bar)
                if bar.close >= pool.lower and sweep_ok:
                    self._start_reclaim(
                        pool,
                        Side.LONG,
                        bar,
                        extreme=bar.low,
                        delayed=False,
                        roles=roles,
                        transition_strength=sweep_strength,
                    )
                    started = pool.engaged_event_id is not None
                else:
                    break_ok, break_strength, excursion = self._break_transition_evidence(
                        pool,
                        Side.SHORT,
                        bar,
                    )
                    if break_ok:
                        self._start_break(
                            pool,
                            Side.SHORT,
                            bar,
                            extreme=bar.low,
                            roles=roles,
                            transition_strength=break_strength,
                            break_excursion=excursion,
                        )
                        started = pool.engaged_event_id is not None
            elif pool.side == "HIGH" and bar.high > pool.upper:
                sweep_ok, sweep_strength = self._sweep_transition_evidence(pool, bar)
                if bar.close <= pool.upper and sweep_ok:
                    self._start_reclaim(
                        pool,
                        Side.SHORT,
                        bar,
                        extreme=bar.high,
                        delayed=False,
                        roles=roles,
                        transition_strength=sweep_strength,
                    )
                    started = pool.engaged_event_id is not None
                else:
                    break_ok, break_strength, excursion = self._break_transition_evidence(
                        pool,
                        Side.LONG,
                        bar,
                    )
                    if break_ok:
                        self._start_break(
                            pool,
                            Side.LONG,
                            bar,
                            extreme=bar.high,
                            roles=roles,
                            transition_strength=break_strength,
                            break_excursion=excursion,
                        )
                        started = pool.engaged_event_id is not None

            if not started and pool.active:
                pool.consumed_time_ns = bar.ts_close_ns
                self._cinc("projected_structure_first_interaction_unresolved")
            self._audit(
                "projected_structure_interaction_classified",
                bar.ts_close_ns,
                pool_id=pool.pool_id,
                started=started,
                source_structure_ids=pool.source_structure_ids,
                source_kind=pool.kind.value,
                source_lower=pool.lower,
                source_upper=pool.upper,
                rule_provenance=(
                    PROJECTED_STRUCTURE_EVENT_RULE,
                    MEANINGFUL_HORIZONTAL_RULE,
                    CHANNEL_FOURTH_POINT_RULE,
                    OBSERVABLE_STRUCTURE_RULE,
                ),
            )

    def _observe_new_interactions(self, bar: Candle) -> None:
        self._observe_projected_structure_interactions(bar)
        super()._observe_new_interactions(bar)
        # First interaction remains available for the decision above, then the
        # source-faithful books retire every touched projected boundary.
        for book in self._projected_structure.values():
            book.observe_price(bar)

    def _line_and_structure_score(self, timeframe: int, now_ns: int) -> tuple[float, RobustWickLine | None, RobustWickLine | None]:
        high_line = _fit_line(
            self.liquidity.pools,
            side="HIGH",
            timeframe_minutes=timeframe,
            now_ns=now_ns,
        )
        low_line = _fit_line(
            self.liquidity.pools,
            side="LOW",
            timeframe_minutes=timeframe,
            now_ns=now_ns,
        )
        sequence = _sequence_direction(self.liquidity.pools, timeframe, now_ns)
        if high_line is None or low_line is None:
            return sequence, low_line, high_line
        prior_range = max(self.dc[timeframe].prior_range, self.tick_size)
        horizon_ns = timeframe * NS_PER_MINUTE * 4
        average_slope = (high_line.slope_per_ns + low_line.slope_per_ns) / 2.0
        slope_score = _clip(average_slope * horizon_ns / prior_range, -1.25, 1.25)
        return _clip(0.65 * sequence + 0.35 * slope_score, -1.5, 1.5), low_line, high_line

    def _channel_state(
        self,
        timeframe: int,
        time_ns: int,
        price: float,
        low_line: RobustWickLine | None = None,
        high_line: RobustWickLine | None = None,
    ) -> CausalChannelState | None:
        low_line = low_line or _fit_line(
            self.liquidity.pools,
            side="LOW",
            timeframe_minutes=timeframe,
            now_ns=time_ns,
        )
        high_line = high_line or _fit_line(
            self.liquidity.pools,
            side="HIGH",
            timeframe_minutes=timeframe,
            now_ns=time_ns,
        )
        if low_line is None or high_line is None:
            return None
        lower = low_line.value_at(time_ns)
        upper = high_line.value_at(time_ns)
        width = upper - lower
        prior_range = max(self.dc[timeframe].prior_range, self.tick_size)
        if width <= max(self.tick_size * 4.0, prior_range * 0.35):
            return None
        average_slope = (low_line.slope_per_ns + high_line.slope_per_ns) / 2.0
        horizon_ns = timeframe * NS_PER_MINUTE * 4
        normalized_slope = average_slope * horizon_ns / prior_range
        slope_gap = abs(low_line.slope_per_ns - high_line.slope_per_ns) * horizon_ns
        parallel_quality = math.exp(-slope_gap / max(width, prior_range))
        residual_quality = math.exp(
            -(low_line.residual_scale + high_line.residual_scale)
            / max(width, prior_range),
        )
        anchor_quality = min(1.0, (low_line.anchor_count + high_line.anchor_count) / 8.0)
        quality = _clip(parallel_quality * residual_quality * anchor_quality, 0.0, 1.0)
        direction = "ASCENDING" if normalized_slope > 0.20 else "DESCENDING" if normalized_slope < -0.20 else "RANGE"
        position = _clip((price - lower) / width, -1.0, 2.0)
        return CausalChannelState(
            timeframe_minutes=timeframe,
            lower=lower,
            upper=upper,
            mid=(lower + upper) / 2.0,
            width=width,
            slope_per_ns=average_slope,
            normalized_slope=normalized_slope,
            quality=quality,
            position=position,
            direction=direction,
            observed_time_ns=max(low_line.observed_time_ns, high_line.observed_time_ns),
            lower_line=low_line,
            upper_line=high_line,
        )

    def _context(self, time_ns: int, price: float) -> HierarchicalAuctionContext:
        score_60, low_60, high_60 = self._line_and_structure_score(60, time_ns)
        score_15, low_15, high_15 = self._line_and_structure_score(15, time_ns)
        channel_60 = self._channel_state(60, time_ns, price, low_60, high_60)
        channel_15 = self._channel_state(15, time_ns, price, low_15, high_15)
        # Higher scale owns direction, while 15m refines rather than overrides.
        signed = _clip((1.55 * score_60 + score_15) / 2.55, -1.5, 1.5)
        macro = Side.LONG if signed >= 0.28 else Side.SHORT if signed <= -0.28 else None
        factor_side = getattr(self._market_factor_state, "side", None)
        context = HierarchicalAuctionContext(
            time_ns=int(time_ns),
            price=float(price),
            structure_60=score_60,
            structure_15=score_15,
            signed_direction_score=signed,
            macro_side=macro,
            channel_60=channel_60,
            channel_15=channel_15,
            common_factor_side=factor_side,
        )
        self._last_context = context
        self._trend_side = macro
        return context

    @staticmethod
    def _edge_alignment(channel: CausalChannelState | None, pool: LiquidityPool, side: Side) -> float:
        if channel is None:
            return 0.0
        edge = channel.lower if pool.side == "LOW" else channel.upper
        distance = abs(pool.center - edge)
        tolerance = max(channel.width * 0.12, channel.lower_line.residual_scale + channel.upper_line.residual_scale)
        proximity = _clip(1.0 - distance / max(tolerance, 1e-12), 0.0, 1.0)
        desired_edge = (side is Side.LONG and pool.side == "LOW") or (side is Side.SHORT and pool.side == "HIGH")
        return proximity * channel.quality if desired_edge else 0.0

    def _channel_confluence(self, context: HierarchicalAuctionContext, pool: LiquidityPool, side: Side) -> float:
        return max(
            self._edge_alignment(context.channel_60, pool, side),
            self._edge_alignment(context.channel_15, pool, side),
        )

    @staticmethod
    def _factor_alignment(context: HierarchicalAuctionContext, side: Side) -> float:
        state = context.common_factor_side
        if state is None:
            return 0.0
        return 1.0 if state is side else -1.0

    def _snapshot_for_event(
        self,
        *,
        pool: LiquidityPool,
        side: Side,
        bar: Candle,
        kind: EpisodeKind,
        roles: tuple[PoolRole, ...],
    ) -> EpisodeContextSnapshot:
        context = self._context(bar.ts_close_ns, bar.close)
        structural_alignment = context.side_alignment(side)
        channel_confluence = self._channel_confluence(context, pool, side)
        factor_alignment = self._factor_alignment(context, side)
        role_set = set(roles)
        source_edge_score = 1.0 if PoolRole.EXTERNAL_STOP_POOL in role_set else 0.5 if PoolRole.VALUE_BOUNDARY in role_set else 0.0

        if kind is EpisodeKind.ACCEPTED_BREAK_RETEST:
            # A true break can start from a range, but it may not fight both
            # confirmed higher-scale structures without common initiative.
            both_opposed = (
                (context.structure_60 < -0.45 and context.structure_15 < -0.45 and side is Side.LONG)
                or (context.structure_60 > 0.45 and context.structure_15 > 0.45 and side is Side.SHORT)
            )
            compatible = not both_opposed or factor_alignment > 0.0
            reason = "ACCEPTED_BREAK_CONTEXT_COMPATIBLE" if compatible else "ACCEPTED_BREAK_FIGHTS_60M_AND_15M"
        else:
            # Reversal is legitimate precisely when liquidity at an external
            # edge has been taken.  Outside such an edge, countertrend reclaim
            # is just catching ordinary noise.
            strongly_opposed = structural_alignment < -0.55
            external_edge = source_edge_score >= 1.0 and (channel_confluence >= 0.18 or pool.timeframe_minutes >= 15)
            common_opposed = factor_alignment < 0.0
            compatible = (not strongly_opposed) or external_edge
            if common_opposed and strongly_opposed and channel_confluence < 0.35:
                compatible = False
            reason = "RECLAIM_AT_EXTERNAL_EDGE_OR_WITH_STRUCTURE" if compatible else "RECLAIM_IS_ORDINARY_COUNTERTREND_NOISE"

        return EpisodeContextSnapshot(
            context=context,
            source_edge_score=source_edge_score,
            channel_confluence=channel_confluence,
            common_factor_alignment=factor_alignment,
            structural_alignment=structural_alignment,
            compatible=compatible,
            reason=reason,
        )

    def _source_roles(self, pool: LiquidityPool, bar: Candle) -> tuple[PoolRole, ...]:
        roles = list(super()._source_roles(pool, bar))
        intended = Side.LONG if pool.side == "LOW" else Side.SHORT
        context = self._context(bar.ts_close_ns, bar.close)
        confluence = self._channel_confluence(context, pool, intended)
        # A confirmed horizontal turn sitting on a robust diagonal edge is the
        # same public decision area seen through two structural representations.
        if confluence >= 0.18:
            if PoolRole.EXTERNAL_STOP_POOL not in roles:
                roles.append(PoolRole.EXTERNAL_STOP_POOL)
            if PoolRole.VALUE_BOUNDARY not in roles:
                roles.append(PoolRole.VALUE_BOUNDARY)
            self._cinc("horizontal_pool_promoted_by_channel_edge")
        return tuple(roles)

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
        snapshot = self._snapshot_for_event(
            pool=pool,
            side=side,
            bar=bar,
            kind=EpisodeKind.LIQUIDITY_RECLAIM,
            roles=roles,
        )
        if not snapshot.compatible:
            self._cinc("reclaim_context_suppressed")
            self._audit(
                "integrated_event_suppressed",
                bar.ts_close_ns,
                pool_id=pool.pool_id,
                family=EpisodeKind.LIQUIDITY_RECLAIM.value,
                side=_side_name(side),
                reason=snapshot.reason,
                structure_60=snapshot.context.structure_60,
                structure_15=snapshot.context.structure_15,
                structural_alignment=snapshot.structural_alignment,
                channel_confluence=snapshot.channel_confluence,
                common_factor_alignment=snapshot.common_factor_alignment,
            )
            pool.consumed_time_ns = bar.ts_close_ns
            return
        before = set(self.episodes)
        super()._start_reclaim(
            pool,
            side,
            bar,
            extreme=extreme,
            delayed=delayed,
            roles=roles,
            transition_strength=transition_strength,
        )
        created = set(self.episodes) - before
        for episode_id in created:
            self._episode_context[episode_id] = snapshot
            self._cinc("reclaim_context_accepted")

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
        snapshot = self._snapshot_for_event(
            pool=pool,
            side=side,
            bar=bar,
            kind=EpisodeKind.ACCEPTED_BREAK_RETEST,
            roles=roles,
        )
        if not snapshot.compatible:
            self._cinc("break_context_suppressed")
            self._audit(
                "integrated_event_suppressed",
                bar.ts_close_ns,
                pool_id=pool.pool_id,
                family=EpisodeKind.ACCEPTED_BREAK_RETEST.value,
                side=_side_name(side),
                reason=snapshot.reason,
                structure_60=snapshot.context.structure_60,
                structure_15=snapshot.context.structure_15,
                structural_alignment=snapshot.structural_alignment,
                channel_confluence=snapshot.channel_confluence,
                common_factor_alignment=snapshot.common_factor_alignment,
            )
            pool.consumed_time_ns = bar.ts_close_ns
            return
        before = set(self.episodes)
        super()._start_break(
            pool,
            side,
            bar,
            extreme=extreme,
            roles=roles,
            transition_strength=transition_strength,
            break_excursion=break_excursion,
        )
        created = set(self.episodes) - before
        for episode_id in created:
            self._episode_context[episode_id] = snapshot
            self._cinc("break_context_accepted")

    def _causal_noise_buffer(self) -> float:
        one_minute = median(self.one_minute_ranges) if self.one_minute_ranges else self.tick_size
        five_minute = self.dc[5].prior_range
        # The max combines execution granularity, ordinary one-minute noise and
        # a small share of the decision bar scale; all inputs precede entry.
        return max(self.tick_size * 2.0, one_minute * 0.30, five_minute * 0.035)

    def _build_plan(
        self,
        episode: AuctionEpisode,
        bar: Candle,
        response_strength: float,
    ) -> V5TradePlan | None:
        snapshot = self._episode_context.get(episode.episode_id)
        if snapshot is None:
            pool = self.liquidity.by_id.get(episode.pool_id)
            if pool is None:
                return super()._build_plan(episode, bar, response_strength)
            snapshot = self._snapshot_for_event(
                pool=pool,
                side=episode.side,
                bar=bar,
                kind=episode.kind,
                roles=tuple(PoolRole(value) for value in episode.source_roles),
            )
            self._episode_context[episode.episode_id] = snapshot
        if not snapshot.compatible:
            self._invalidate(episode, bar.ts_close_ns, "hierarchical_context_incompatible_at_entry")
            return None

        noise = self._causal_noise_buffer()
        original_sweep = episode.sweep_extreme
        original_retest = episode.retest_extreme
        if episode.kind is EpisodeKind.LIQUIDITY_RECLAIM:
            episode.sweep_extreme = (
                original_sweep - max(0.0, noise - self.tick_size)
                if episode.side is Side.LONG
                else original_sweep + max(0.0, noise - self.tick_size)
            )
        elif original_retest is not None:
            episode.retest_extreme = (
                original_retest - max(0.0, noise - self.tick_size)
                if episode.side is Side.LONG
                else original_retest + max(0.0, noise - self.tick_size)
            )
        try:
            plan = super()._build_plan(episode, bar, response_strength)
        finally:
            episode.sweep_extreme = original_sweep
            episode.retest_extreme = original_retest
        if plan is None:
            return None

        context_now = self._context(bar.ts_close_ns, bar.close)
        alignment = context_now.side_alignment(plan.side)
        source_multiplier = (
            1.0
            + max(0.0, alignment) * 0.50
            + snapshot.channel_confluence * 0.75
            + snapshot.source_edge_score * 0.25
        )
        trigger_multiplier = 1.0 + max(0.0, snapshot.common_factor_alignment) * 0.20
        provenance = tuple(plan.rule_provenance) + (
            HIERARCHICAL_DIRECTION_RULE,
            CAUSAL_CHANNEL_RULE,
            CHANNEL_LIQUIDITY_RULE,
            CONTEXT_EVENT_RULE,
            CAUSAL_NOISE_INVALIDATION_RULE,
            INTEGRATED_POLICY_RULE,
            PROJECTED_STRUCTURE_EVENT_RULE,
            f"RESEARCH_STATE:STRUCTURE_60={context_now.structure_60:.6f}",
            f"RESEARCH_STATE:STRUCTURE_15={context_now.structure_15:.6f}",
            f"RESEARCH_STATE:CHANNEL_CONFLUENCE={snapshot.channel_confluence:.6f}",
            f"RESEARCH_STATE:CAUSAL_NOISE_BUFFER={noise:.12g}",
        )
        integrated = replace(
            plan,
            higher_strength_ratio=max(1.0, plan.higher_strength_ratio * source_multiplier),
            lower_strength_ratio=max(1.0, plan.lower_strength_ratio * (1.0 + snapshot.channel_confluence * 0.35)),
            trigger_strength_ratio=max(1.0, plan.trigger_strength_ratio * trigger_multiplier),
            rule_provenance=provenance,
            source_rule_count=len(provenance),
            scale_name=f"{plan.higher_timeframe_minutes}m_HIERARCHICAL_DIRECTION_LIQUIDITY_EVENT_ENTRY",
        )
        if self._plans and self._plans[-1].plan_id == plan.plan_id:
            self._plans[-1] = integrated
        self._cinc("integrated_plan_emitted")
        channel_60 = context_now.channel_60
        channel_15 = context_now.channel_15
        self._audit(
            "integrated_plan_context",
            bar.ts_close_ns,
            plan_id=integrated.plan_id,
            episode_id=episode.episode_id,
            causal_event_id=integrated.causal_event_id,
            side=_side_name(integrated.side),
            family=integrated.family,
            structure_60=context_now.structure_60,
            structure_15=context_now.structure_15,
            signed_direction_score=context_now.signed_direction_score,
            structural_alignment=alignment,
            channel_confluence=snapshot.channel_confluence,
            source_edge_score=snapshot.source_edge_score,
            common_factor_alignment=snapshot.common_factor_alignment,
            causal_noise_buffer=noise,
            channel_60_direction=None if channel_60 is None else channel_60.direction,
            channel_60_quality=None if channel_60 is None else channel_60.quality,
            channel_60_position=None if channel_60 is None else channel_60.position,
            channel_15_direction=None if channel_15 is None else channel_15.direction,
            channel_15_quality=None if channel_15 is None else channel_15.quality,
            channel_15_position=None if channel_15 is None else channel_15.position,
            rule_provenance=(
                HIERARCHICAL_DIRECTION_RULE,
                CAUSAL_CHANNEL_RULE,
                CHANNEL_LIQUIDITY_RULE,
                CONTEXT_EVENT_RULE,
                CAUSAL_NOISE_INVALIDATION_RULE,
                INTEGRATED_POLICY_RULE,
                PROJECTED_STRUCTURE_EVENT_RULE,
            ),
        )
        return integrated

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        book = self._projected_structure.get(timeframe_minutes)
        if book is not None:
            pivots, lines, channels = book.on_bar(bar)
            if pivots or lines or channels:
                self._audit(
                    "projected_structure_updated",
                    bar.ts_close_ns,
                    source_timeframe_minutes=timeframe_minutes,
                    pivots=len(pivots),
                    trend_lines=len(lines),
                    channels=len(channels),
                    rule_provenance=PROJECTED_STRUCTURE_EVENT_RULE,
                )
        plans = super().on_bar(timeframe_minutes, bar)
        # Refresh after every completed structural bar so the latest confirmed
        # state is visible before the next decision bucket.
        if timeframe_minutes in {60, 15, 5}:
            self._context(bar.ts_close_ns, bar.close)
        return plans

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        context = self._last_context
        output["hierarchical_context"] = {
            "counts": dict(sorted(self._context_counts.items())),
            "last": None
            if context is None
            else {
                "time_ns": context.time_ns,
                "price": context.price,
                "structure_60": context.structure_60,
                "structure_15": context.structure_15,
                "signed_direction_score": context.signed_direction_score,
                "macro_side": None if context.macro_side is None else context.macro_side.name,
                "common_factor_side": None
                if context.common_factor_side is None
                else context.common_factor_side.name,
                "channel_60": None
                if context.channel_60 is None
                else {
                    "direction": context.channel_60.direction,
                    "quality": context.channel_60.quality,
                    "position": context.channel_60.position,
                    "lower": context.channel_60.lower,
                    "upper": context.channel_60.upper,
                },
                "channel_15": None
                if context.channel_15 is None
                else {
                    "direction": context.channel_15.direction,
                    "quality": context.channel_15.quality,
                    "position": context.channel_15.position,
                    "lower": context.channel_15.lower,
                    "upper": context.channel_15.upper,
                },
            },
            "rules": (
                HIERARCHICAL_DIRECTION_RULE,
                CAUSAL_CHANNEL_RULE,
                CHANNEL_LIQUIDITY_RULE,
                CONTEXT_EVENT_RULE,
                CAUSAL_NOISE_INVALIDATION_RULE,
                INTEGRATED_POLICY_RULE,
                PROJECTED_STRUCTURE_EVENT_RULE,
            ),
        }
        output["projected_structure"] = {
            str(timeframe): dict(book.diagnostics)
            for timeframe, book in self._projected_structure.items()
        }
        return output


MultiScaleScenarioBundle = IntegratedAuctionBundle

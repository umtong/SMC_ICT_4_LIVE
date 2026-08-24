"""Non-linear latent auction state for one coherent EasyChart policy.

Direction is not collapsed into a vote or a 60m/15m weighted average.  The
policy keeps distinct answers to distinct questions:

* which named source and destination own the current auction episode;
* is structure persistently trending, ranging, or transitioning;
* is price at an external channel/liquidity edge or in ordinary internal noise;
* has common market initiative transferred control;
* is the event a continuation break or a sweep/trap reversal.

The latent measurements describe the environment but do not vote a trade into
existence.  Direction belongs to one named public boundary and its failed or
accepted settlement, with one objective committed when that event begins.
OB/FVG/retest remain execution footprints only.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Candle, Side
from integrated_auction import (
    CONTEXT_EVENT_RULE,
    EpisodeContextSnapshot,
    HierarchicalAuctionContext,
    IntegratedAuctionBundle,
)
from intrinsic_auction import (
    AuctionEpisode,
    EpisodeKind,
    LiquidityPool,
    PoolRole,
    _side_name,
)


LATENT_DIRECTION_RULE = (
    "RESEARCH_HYPOTHESIS:LATENT_TREND_LOCATION_AND_COMMON_FACTOR_"
    "DESCRIBE_CONTEXT_WHILE_THE_NAMED_BOUNDARY_EVENT_OWNS_DIRECTION"
)
EVENT_SPECIFIC_ROUTING_RULE = (
    "RESEARCH_HYPOTHESIS:CONTINUATION_BREAKS_AND_EXTERNAL_LIQUIDITY_RECLAIMS_"
    "ARE_DISTINCT_SETTLEMENTS_WITHIN_ONE_NAMED_BOUNDARY_POLICY"
)
NAMED_EVENT_DIRECTION_RULE = (
    "SOURCE_EXPLICIT:DIRECTION_BELONGS_TO_ONE_NAMED_PUBLIC_BOUNDARY_ATTACK_"
    "AND_ITS_FAILED_OR_ACCEPTED_SETTLEMENT_NOT_AN_AGGREGATE_POOL_VOTE"
)
for _rule in (
    LATENT_DIRECTION_RULE,
    EVENT_SPECIFIC_ROUTING_RULE,
    NAMED_EVENT_DIRECTION_RULE,
):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


class AuctionRegime(str, Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    TRANSITION = "TRANSITION"
    MIXED = "MIXED"


@dataclass(frozen=True, slots=True)
class LatentAuctionState:
    time_ns: int
    regime: AuctionRegime
    trend_60: float
    trend_15: float
    factor_side: Side | None
    channel_location_long: float
    channel_location_short: float

    def trend_alignment(self, side: Side, timeframe: int) -> float:
        score = self.trend_60 if timeframe == 60 else self.trend_15
        return score if side is Side.LONG else -score

    def factor_alignment(self, side: Side) -> float:
        if self.factor_side is None:
            return 0.0
        return 1.0 if self.factor_side is side else -1.0

    def location_alignment(self, side: Side) -> float:
        return (
            self.channel_location_long
            if side is Side.LONG
            else self.channel_location_short
        )


class LatentAuctionBundle(IntegratedAuctionBundle):
    """Integrated auction engine with event-specific latent-state routing."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._latent_episode: dict[str, LatentAuctionState] = {}
        self._latent_last: LatentAuctionState | None = None
        self._latent_counts: dict[str, int] = {}

    def _linc(self, key: str) -> None:
        self._latent_counts[key] = self._latent_counts.get(key, 0) + 1

    @staticmethod
    def _channel_location(context: HierarchicalAuctionContext) -> tuple[float, float]:
        candidates = [
            channel
            for channel in (context.channel_60, context.channel_15)
            if channel is not None and channel.quality >= 0.10
        ]
        if not candidates:
            return 0.0, 0.0
        long_values = []
        short_values = []
        for channel in candidates:
            # +1 at the favorable external edge, -1 at the destination edge.
            long_values.append((0.5 - channel.position) * 2.0 * channel.quality)
            short_values.append((channel.position - 0.5) * 2.0 * channel.quality)
        return (
            max(-1.5, min(1.5, max(long_values, key=abs))),
            max(-1.5, min(1.5, max(short_values, key=abs))),
        )

    def _latent_state(
        self, context: HierarchicalAuctionContext
    ) -> LatentAuctionState:
        t60 = float(context.structure_60)
        t15 = float(context.structure_15)
        channel_long, channel_short = self._channel_location(context)
        strong_up = t60 >= 0.35 and t15 >= 0.25
        strong_down = t60 <= -0.35 and t15 <= -0.25
        conflict = t60 * t15 < -0.10
        channel = context.channel_60 or context.channel_15
        range_state = (
            not strong_up
            and not strong_down
            and not conflict
            and channel is not None
            and channel.quality >= 0.25
            and abs(channel.normalized_slope) < 0.30
        )
        if strong_up:
            regime = AuctionRegime.TREND_UP
        elif strong_down:
            regime = AuctionRegime.TREND_DOWN
        elif conflict:
            regime = AuctionRegime.TRANSITION
        elif range_state:
            regime = AuctionRegime.RANGE
        else:
            regime = AuctionRegime.MIXED
        state = LatentAuctionState(
            time_ns=context.time_ns,
            regime=regime,
            trend_60=t60,
            trend_15=t15,
            factor_side=context.common_factor_side,
            channel_location_long=channel_long,
            channel_location_short=channel_short,
        )
        self._latent_last = state
        return state

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
        state = self._latent_state(context)
        channel_confluence = self._channel_confluence(context, pool, side)
        role_set = set(roles)
        source_edge_score = (
            1.0
            if PoolRole.EXTERNAL_STOP_POOL in role_set
            else 0.5
            if PoolRole.VALUE_BOUNDARY in role_set
            else 0.0
        )
        trend_60 = state.trend_alignment(side, 60)
        trend_15 = state.trend_alignment(side, 15)
        factor = state.factor_alignment(side)
        location = state.location_alignment(side)

        # Direction is established by the named boundary's settlement.  The
        # latent structure and factor remain descriptive
        # context; none may veto or manufacture a complete public-liquidity
        # episode.  A committed destination is enforced by the base episode
        # constructor before the state is allowed to live.
        if kind is EpisodeKind.LIQUIDITY_RECLAIM:
            compatible = PoolRole.EXTERNAL_STOP_POOL in role_set
            reason = (
                "NAMED_EXTERNAL_BOUNDARY_FAILED_AUCTION"
                if compatible
                else "RECLAIM_HAS_NO_PUBLIC_EXTERNAL_BOUNDARY_OWNER"
            )
        else:
            compatible = PoolRole.VALUE_BOUNDARY in role_set
            reason = (
                "NAMED_VALUE_BOUNDARY_ACCEPTANCE_CANDIDATE"
                if compatible
                else "BREAK_HAS_NO_PUBLIC_VALUE_BOUNDARY_OWNER"
            )

        self._linc(f"event_{kind.value.lower()}_{'accepted' if compatible else 'suppressed'}")
        self._audit(
            "latent_event_context",
            bar.ts_close_ns,
            pool_id=pool.pool_id,
            family=kind.value,
            side=_side_name(side),
            compatible=compatible,
            reason=reason,
            regime=state.regime.value,
            trend_60_alignment=trend_60,
            trend_15_alignment=trend_15,
            factor_alignment=factor,
            location_alignment=location,
            channel_confluence=channel_confluence,
            source_edge_score=source_edge_score,
            rule_provenance=(
                LATENT_DIRECTION_RULE,
                EVENT_SPECIFIC_ROUTING_RULE,
                NAMED_EVENT_DIRECTION_RULE,
                CONTEXT_EVENT_RULE,
            ),
        )
        return EpisodeContextSnapshot(
            context=context,
            source_edge_score=source_edge_score,
            channel_confluence=channel_confluence,
            common_factor_alignment=factor,
            structural_alignment=min(trend_60, trend_15),
            compatible=compatible,
            reason=reason,
        )

    def _start_reclaim(self, *args: Any, **kwargs: Any) -> None:
        before = set(self.episodes)
        super()._start_reclaim(*args, **kwargs)
        created = set(self.episodes) - before
        if self._latent_last is not None:
            for episode_id in created:
                self._latent_episode[episode_id] = self._latent_last

    def _start_break(self, *args: Any, **kwargs: Any) -> None:
        before = set(self.episodes)
        super()._start_break(*args, **kwargs)
        created = set(self.episodes) - before
        if self._latent_last is not None:
            for episode_id in created:
                self._latent_episode[episode_id] = self._latent_last

    def _build_plan(
        self,
        episode: AuctionEpisode,
        bar: Candle,
        response_strength: float,
    ) -> V5TradePlan | None:
        plan = super()._build_plan(episode, bar, response_strength)
        if plan is None:
            return None
        state = self._latent_episode.get(episode.episode_id)
        if state is None:
            state = self._latent_state(self._context(bar.ts_close_ns, bar.close))
        trend_60 = state.trend_alignment(plan.side, 60)
        trend_15 = state.trend_alignment(plan.side, 15)
        factor = state.factor_alignment(plan.side)
        location = state.location_alignment(plan.side)
        provenance = tuple(plan.rule_provenance) + (
            LATENT_DIRECTION_RULE,
            EVENT_SPECIFIC_ROUTING_RULE,
            NAMED_EVENT_DIRECTION_RULE,
            f"RESEARCH_STATE:LATENT_REGIME={state.regime.value}",
            f"RESEARCH_STATE:TREND_60_ALIGNMENT={trend_60:.6f}",
            f"RESEARCH_STATE:TREND_15_ALIGNMENT={trend_15:.6f}",
            f"RESEARCH_STATE:FACTOR_ALIGNMENT={factor:.6f}",
            f"RESEARCH_STATE:LOCATION_ALIGNMENT={location:.6f}",
        )
        output = replace(
            plan,
            rule_provenance=provenance,
            source_rule_count=len(provenance),
            scale_name=(
                f"{plan.higher_timeframe_minutes}m_LATENT_{state.regime.value}_"
                f"DIRECTION_LIQUIDITY_EVENT_ENTRY"
            ),
        )
        if self._plans and self._plans[-1].plan_id == plan.plan_id:
            self._plans[-1] = output
        self._audit(
            "latent_plan_context",
            bar.ts_close_ns,
            plan_id=output.plan_id,
            causal_event_id=output.causal_event_id,
            episode_id=episode.episode_id,
            side=_side_name(output.side),
            family=output.family,
            regime=state.regime.value,
            trend_60_alignment=trend_60,
            trend_15_alignment=trend_15,
            factor_alignment=factor,
            location_alignment=location,
            rule_provenance=(
                LATENT_DIRECTION_RULE,
                EVENT_SPECIFIC_ROUTING_RULE,
                NAMED_EVENT_DIRECTION_RULE,
            ),
        )
        self._linc("latent_plan_emitted")
        return output

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        state = self._latent_last
        output["latent_auction_state"] = {
            "counts": dict(sorted(self._latent_counts.items())),
            "last": None
            if state is None
            else {
                "time_ns": state.time_ns,
                "regime": state.regime.value,
                "trend_60": state.trend_60,
                "trend_15": state.trend_15,
                "factor_side": None if state.factor_side is None else state.factor_side.name,
                "channel_location_long": state.channel_location_long,
                "channel_location_short": state.channel_location_short,
            },
            "rules": (
                LATENT_DIRECTION_RULE,
                EVENT_SPECIFIC_ROUTING_RULE,
                NAMED_EVENT_DIRECTION_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = LatentAuctionBundle

"""Non-linear latent auction state for one coherent EasyChart policy.

Direction is not collapsed into a vote or a 60m/15m weighted average.  The
policy keeps distinct answers to distinct questions:

* where is the meaningful unswept liquidity draw;
* is structure persistently trending, ranging, or transitioning;
* is price at an external channel/liquidity edge or in ordinary internal noise;
* has common market initiative transferred control;
* is the event a continuation break or a sweep/trap reversal.

Each event family uses the state relevant to its causal mechanism.  A reversal
is allowed to oppose the old trend precisely at external liquidity, whereas a
continuation break needs either persistent structure, an aligned liquidity draw,
or aligned common initiative.  OB/FVG/retest remain execution footprints only.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import math
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
    "RESEARCH_HYPOTHESIS:DIRECTION_IS_A_JOINT_STATE_OF_LIQUIDITY_DRAW_TREND_"
    "PERSISTENCE_AUCTION_LOCATION_AND_CONTROL_TRANSFER_NOT_ONE_LINEAR_SCORE"
)
EVENT_SPECIFIC_ROUTING_RULE = (
    "RESEARCH_HYPOTHESIS:CONTINUATION_BREAKS_AND_EXTERNAL_LIQUIDITY_RECLAIMS_"
    "USE_DIFFERENT_NONLINEAR_CONTEXT_LOGIC_WITHIN_ONE_ACCOUNT_POLICY"
)
LIQUIDITY_DRAW_RULE = (
    "EXTERNAL_METHOD:NEARBY_ACTIVE_DECISION_SCALE_UNSWEPT_LIQUIDITY_STRENGTH_"
    "AND_DISTANCE_DEFINE_THE_CURRENT_DRAW_WITHOUT_SYMBOL_OR_CLOCK_IDENTITY"
)
for _rule in (
    LATENT_DIRECTION_RULE,
    EVENT_SPECIFIC_ROUTING_RULE,
    LIQUIDITY_DRAW_RULE,
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
    draw_side: Side | None
    draw_balance: float
    trend_60: float
    trend_15: float
    factor_side: Side | None
    channel_location_long: float
    channel_location_short: float

    def trend_alignment(self, side: Side, timeframe: int) -> float:
        score = self.trend_60 if timeframe == 60 else self.trend_15
        return score if side is Side.LONG else -score

    def draw_alignment(self, side: Side) -> float:
        if self.draw_side is None:
            return 0.0
        return 1.0 if self.draw_side is side else -1.0

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

    def _pool_recent(self, pool: LiquidityPool) -> bool:
        checker = getattr(self.liquidity, "is_recent", None)
        return bool(checker(pool)) if checker is not None else bool(pool.active)

    def _liquidity_draw(
        self, time_ns: int, price: float
    ) -> tuple[Side | None, float]:
        scale = max(self.dc[15].prior_range, self.tick_size)
        high_scores: list[float] = []
        low_scores: list[float] = []
        for pool in self.liquidity.pools:
            if (
                not pool.active
                or pool.observed_time_ns >= time_ns
                or not self._pool_recent(pool)
                or (pool.timeframe_minutes < 5 and pool.member_count < 2)
            ):
                continue
            if pool.side == "HIGH" and pool.lower > price:
                distance = max(pool.lower - price, self.tick_size)
                high_scores.append(float(pool.strength) / (1.0 + distance / scale))
            elif pool.side == "LOW" and pool.upper < price:
                distance = max(price - pool.upper, self.tick_size)
                low_scores.append(float(pool.strength) / (1.0 + distance / scale))
        high = sum(sorted(high_scores, reverse=True)[:3])
        low = sum(sorted(low_scores, reverse=True)[:3])
        total = high + low
        if total <= 1e-12:
            return None, 0.0
        balance = (high - low) / total
        if balance >= 0.12:
            return Side.LONG, float(balance)
        if balance <= -0.12:
            return Side.SHORT, float(balance)
        return None, float(balance)

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
        draw_side, draw_balance = self._liquidity_draw(
            context.time_ns, context.price
        )
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
            draw_side=draw_side,
            draw_balance=draw_balance,
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
        draw = state.draw_alignment(side)
        factor = state.factor_alignment(side)
        location = state.location_alignment(side)

        if kind is EpisodeKind.LIQUIDITY_RECLAIM:
            external_edge = (
                source_edge_score >= 1.0
                or pool.timeframe_minutes >= 15
                or channel_confluence >= 0.18
            )
            with_old_trend = trend_60 >= 0.20 or trend_15 >= 0.30
            trap_reversal = external_edge and (
                trend_60 < 0.10
                or state.regime in {AuctionRegime.RANGE, AuctionRegime.TRANSITION}
                or location >= 0.20
            )
            jointly_opposed = draw < 0.0 and factor < 0.0 and trend_60 < -0.45
            compatible = (with_old_trend or trap_reversal) and not (
                jointly_opposed and channel_confluence < 0.35
            )
            reason = (
                "RECLAIM_WITH_TREND"
                if compatible and with_old_trend
                else "EXTERNAL_LIQUIDITY_TRAP_REVERSAL"
                if compatible
                else "RECLAIM_LACKS_EXTERNAL_EVENT_OR_CONTROL_SUPPORT"
            )
        else:
            persistent_trend = trend_60 >= 0.25 and trend_15 >= 0.20
            liquidity_draw = draw > 0.0 and trend_60 > -0.45
            common_impulse = factor > 0.0 and trend_15 > -0.35
            destination_edge = location < -0.55 and channel_confluence >= 0.12
            compatible = (
                persistent_trend or liquidity_draw or common_impulse
            ) and not destination_edge
            reason = (
                "BREAK_WITH_PERSISTENT_STRUCTURE"
                if compatible and persistent_trend
                else "BREAK_TOWARD_ACTIVE_LIQUIDITY_DRAW"
                if compatible and liquidity_draw
                else "BREAK_WITH_COMMON_CONTROL_TRANSFER"
                if compatible
                else "BREAK_HAS_NO_DIRECTIONAL_CAUSAL_OWNER_OR_IS_AT_DESTINATION"
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
            draw_side=None if state.draw_side is None else state.draw_side.name,
            draw_balance=state.draw_balance,
            draw_alignment=draw,
            trend_60_alignment=trend_60,
            trend_15_alignment=trend_15,
            factor_alignment=factor,
            location_alignment=location,
            channel_confluence=channel_confluence,
            source_edge_score=source_edge_score,
            rule_provenance=(
                LATENT_DIRECTION_RULE,
                EVENT_SPECIFIC_ROUTING_RULE,
                LIQUIDITY_DRAW_RULE,
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
        draw_alignment = state.draw_alignment(plan.side)
        trend_60 = state.trend_alignment(plan.side, 60)
        trend_15 = state.trend_alignment(plan.side, 15)
        factor = state.factor_alignment(plan.side)
        location = state.location_alignment(plan.side)
        provenance = tuple(plan.rule_provenance) + (
            LATENT_DIRECTION_RULE,
            EVENT_SPECIFIC_ROUTING_RULE,
            LIQUIDITY_DRAW_RULE,
            f"RESEARCH_STATE:LATENT_REGIME={state.regime.value}",
            f"RESEARCH_STATE:LIQUIDITY_DRAW_ALIGNMENT={draw_alignment:.6f}",
            f"RESEARCH_STATE:LIQUIDITY_DRAW_BALANCE={state.draw_balance:.6f}",
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
            draw_alignment=draw_alignment,
            draw_balance=state.draw_balance,
            trend_60_alignment=trend_60,
            trend_15_alignment=trend_15,
            factor_alignment=factor,
            location_alignment=location,
            rule_provenance=(
                LATENT_DIRECTION_RULE,
                EVENT_SPECIFIC_ROUTING_RULE,
                LIQUIDITY_DRAW_RULE,
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
                "draw_side": None if state.draw_side is None else state.draw_side.name,
                "draw_balance": state.draw_balance,
                "trend_60": state.trend_60,
                "trend_15": state.trend_15,
                "factor_side": None if state.factor_side is None else state.factor_side.name,
                "channel_location_long": state.channel_location_long,
                "channel_location_short": state.channel_location_short,
            },
            "rules": (
                LATENT_DIRECTION_RULE,
                EVENT_SPECIFIC_ROUTING_RULE,
                LIQUIDITY_DRAW_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = LatentAuctionBundle

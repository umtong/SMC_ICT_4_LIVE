"""Residual first pullback in an accepted sixty-minute trend.

The current-leg efficient pullback requires a newly accepted fifteen-minute leg.
During a mature broad trend that can leave long stretches with no local leg
change even though repeated five-minute acceptance/pullback auctions remain
available.  This independent family uses the already accepted sixty-minute side
as its direction source and reuses the same five-minute break, next-bar hold,
first return, flow-confirmed response, structural stop and frozen first target.

It is not a relaxed version of the local family.  The causal state is different:

* local efficient pullback: newly accepted fifteen-minute leg;
* macro trend pullback: established accepted sixty-minute leg while no more
  specific owner has claimed the episode.

Event-local OB/FVG, horizontal flip and local-leg pullback owners run first.  The
macro family receives only residual episodes, and an active opposite common
impulse still vetoes the first touch.  No threshold, session, timeout, partial
exit, stop movement or account slot is added.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import (
    ObjectKind,
    Pivot,
    StructureFamily,
    StructureZone,
    V5TradePlan,
    provenance,
)
from domain import Candle, Side
from easychart_re1_auction_router_v2 import EasyChartRE1AuctionRouterV2Bundle
from easychart_re1_efficient_pullback import (
    EFFICIENT_PULLBACK_IMPULSE_RULE,
    EFFICIENT_PULLBACK_OBJECTIVE_RULE,
    EFFICIENT_PULLBACK_RESPONSE_RULE,
)
from easychart_re1_efficient_pullback_final import (
    CurrentLegEfficientPullbackEngine,
    EasyChartRE1EfficientPullbackFinalBundle,
)
from easychart_re1_local_auction_continuation import (
    COMMON_FACTOR_VETO_ONLY_RULE,
    EasyChartRE1LocalAuctionStrategy,
)
from easychart_zones import ZoneSide
from execution_re1_market_factor import CommonFactorState


MACRO_TREND_PULLBACK_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "ACCEPTED_SIXTY_MINUTE_DIRECTION_MAY_OWN_A_RESIDUAL_FIVE_MINUTE_BREAK_HOLD_FIRST_PULLBACK_WHEN_NO_MORE_SPECIFIC_LOCAL_OWNER_EXISTS"
)
MACRO_TREND_PULLBACK_LIFECYCLE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "A_NEW_ACCEPTED_SIXTY_MINUTE_SIDE_ENDS_PENDING_PULLBACKS_FROM_THE_PRIOR_MACRO_LEG"
)
for _rule in (MACRO_TREND_PULLBACK_RULE, MACRO_TREND_PULLBACK_LIFECYCLE_RULE):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


class MacroTrendPullbackEngine(CurrentLegEfficientPullbackEngine):
    """Efficient-pullback execution whose direction is supplied by the macro router."""

    def set_macro_context(self, side: Side | None, pivot: Pivot | None, time_ns: int) -> None:
        previous = self.local_side
        self.local_side = side
        self.local_pivot = pivot
        if side is None or side is previous:
            return
        for setup in list(self._active.values()):
            if setup.side is side:
                continue
            self._finish(
                setup,
                "macro_trend_pullback_prior_leg_superseded",
                time_ns,
                new_macro_side=side.name,
                new_macro_pivot_id=None if pivot is None else pivot.pivot_id,
                rule_provenance=MACRO_TREND_PULLBACK_LIFECYCLE_RULE,
            )
        self._inc("macro_trend_side_changed")

    def _on_fifteen(self, bar: Candle) -> None:
        # Fifteen-minute structure remains an objective book only.  Direction is
        # the accepted sixty-minute state supplied by the bundle.
        self.local_structure.on_bar(bar)
        self.local_structure.observe_price(bar)

    def _macro_snapshot(self, pivot: Pivot, time_ns: int) -> StructureZone:
        if self.local_side is Side.LONG:
            lower = pivot.price - self.tick_size
            upper = pivot.price
            side = ZoneSide.SUPPORT
            kind = ObjectKind.HORIZONTAL_SUPPORT
            invalidation = lower - self.tick_size
        else:
            lower = pivot.price
            upper = pivot.price + self.tick_size
            side = ZoneSide.RESISTANCE
            kind = ObjectKind.HORIZONTAL_RESISTANCE
            invalidation = upper + self.tick_size
        return StructureZone(
            zone_id=f"MACRO_PULLBACK:{pivot.pivot_id}:SNAP:{time_ns}",
            kind=kind,
            family=StructureFamily.HORIZONTAL,
            side=side,
            timeframe_minutes=60,
            lower=lower,
            upper=upper,
            invalidation=invalidation,
            impulse_extreme=pivot.price,
            formed_index=pivot.index,
            formed_time_ns=pivot.event_time_ns,
            observed_time_ns=pivot.observed_time_ns,
            formation_indices=(),
            strength_ratio=pivot.strength_ratio,
            source_structure_id=f"MACRO_PULLBACK:{pivot.pivot_id}",
            source_pivot_span=pivot.span,
        )

    def _plan(self, setup, bar: Candle, mechanism: str, observation) -> V5TradePlan | None:  # type: ignore[no-untyped-def]
        target = self._target(setup, bar)
        if target is None:
            self._finish(setup, "macro_trend_pullback_no_frozen_target", bar.ts_close_ns)
            return None
        target_zone, target_price = target
        if setup.retest_low is None or setup.retest_high is None or setup.local_pivot is None:
            raise RuntimeError("macro trend pullback lost response geometry")
        if setup.side is Side.LONG:
            stop = min(setup.retest_low, bar.low, setup.break_pivot.price - self.tick_size) - self.tick_size
            risk = bar.close - stop
            reward = target_price - bar.close
        else:
            stop = max(setup.retest_high, bar.high, setup.break_pivot.price + self.tick_size) + self.tick_size
            risk = stop - bar.close
            reward = bar.close - target_price
        if risk <= 0.0 or reward <= 0.0:
            self._finish(setup, "macro_trend_pullback_nonpositive_geometry", bar.ts_close_ns)
            return None
        gross_rr = reward / risk
        if gross_rr + 1e-12 < self.minimum_gross_rr:
            self._finish(
                setup,
                "macro_trend_pullback_below_minimum_gross_rr",
                bar.ts_close_ns,
                gross_rr=gross_rr,
            )
            return None

        higher = self._macro_snapshot(setup.local_pivot, bar.ts_close_ns)
        decision = self.decision_structure._horizontal_snapshot(setup.break_pivot, bar.ts_close_ns)
        self._audit(higher)
        self._audit(decision)
        self.sequence += 1
        plan = V5TradePlan(
            plan_id=f"ec-re1-macro-pullback-{self.symbol}-{self.sequence:08d}",
            causal_event_id=f"MACRO:{setup.setup_id}",
            symbol=self.symbol,
            family="MACRO_60M_TREND_5M_ACCEPTED_BREAK_FIRST_PULLBACK",
            side=setup.side,
            observed_time_ns=bar.ts_close_ns,
            entry=bar.close,
            stop=stop,
            target=target_price,
            gross_rr=gross_rr,
            setup_id=setup.setup_id,
            higher_zone_id=higher.zone_id,
            higher_zone_kind=higher.kind,
            higher_strength_ratio=higher.strength_ratio,
            lower_zone_id=decision.zone_id,
            lower_zone_kind=decision.kind,
            lower_strength_ratio=decision.strength_ratio,
            trigger_zone_id=decision.zone_id,
            trigger_strength_ratio=max(1.0, observation.activity_ratio * observation.delta_ratio),
            target_zone_id=target_zone.zone_id,
            target_zone_kind=target_zone.kind,
            overlap_lower=decision.lower,
            overlap_upper=decision.upper,
            interaction_time_ns=setup.break_time_ns,
            trigger_time_ns=bar.ts_close_ns,
            scenario_path="ACCEPTANCE",
            setup_observed_time_ns=setup.break_pivot.observed_time_ns,
            trigger_zone_kind=f"MACRO_TREND_PULLBACK_{mechanism}",
            source_rule_count=len(provenance()),
            rule_provenance=provenance() + (
                MACRO_TREND_PULLBACK_RULE,
                MACRO_TREND_PULLBACK_LIFECYCLE_RULE,
                EFFICIENT_PULLBACK_IMPULSE_RULE,
                EFFICIENT_PULLBACK_RESPONSE_RULE,
                EFFICIENT_PULLBACK_OBJECTIVE_RULE,
                COMMON_FACTOR_VETO_ONLY_RULE,
            ),
            scale_name="MACRO_TREND_PULLBACK",
            higher_timeframe_minutes=60,
            decision_timeframe_minutes=5,
            trigger_timeframe_minutes=1,
        )
        self.plans.append(plan)
        self._finish(
            setup,
            "macro_trend_pullback_plan_created",
            bar.ts_close_ns,
            plan_id=plan.plan_id,
            mechanism=mechanism,
            entry=plan.entry,
            stop=plan.stop,
            target=plan.target,
            gross_rr=plan.gross_rr,
        )
        return plan

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["macro_direction_source"] = {
            "side": None if self.local_side is None else self.local_side.name,
            "pivot_id": None if self.local_pivot is None else self.local_pivot.pivot_id,
            "rules": (
                MACRO_TREND_PULLBACK_RULE,
                MACRO_TREND_PULLBACK_LIFECYCLE_RULE,
            ),
        }
        return output


class EasyChartRE1MacroTrendOpportunityBundle(EasyChartRE1EfficientPullbackFinalBundle):
    """Specific owners, local first pullback, then residual macro trend pullback."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.macro_trend_pullback = MacroTrendPullbackEngine(
            symbol,
            tick_size,
            minimum_gross_rr,
        )
        self._macro_pullback_counts: dict[str, int] = {}
        self._macro_pullback_trace: list[dict[str, Any]] = []

    def _mpinc(self, key: str) -> None:
        self._macro_pullback_counts[key] = self._macro_pullback_counts.get(key, 0) + 1

    def set_market_factor_state(self, state: CommonFactorState | None) -> None:
        super().set_market_factor_state(state)
        self.macro_trend_pullback.set_market_factor_state(state)

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return super().setups + self.macro_trend_pullback.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return super().plans + self.macro_trend_pullback.plans

    def _route_macro_pullback(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in raw:
            if self._duplicate_episode(plan):
                self._mpinc("macro_trend_pullback_duplicate_episode")
                continue
            factor = self._factor_state
            if factor is not None and factor.side is not plan.side:
                self._mpinc("macro_trend_pullback_rejected_by_common_factor")
                continue
            self._claim_episode(plan)
            output.append(plan)
            self._mpinc("macro_trend_pullback_plan_allowed")
            self._macro_pullback_trace.append(
                {
                    "scenario_kind": "macro_trend_pullback_plan_allowed",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "entry": plan.entry,
                    "stop": plan.stop,
                    "target": plan.target,
                    "gross_rr": plan.gross_rr,
                    "rule_provenance": MACRO_TREND_PULLBACK_RULE,
                }
            )
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        core = EasyChartRE1AuctionRouterV2Bundle.on_bar(self, timeframe_minutes, bar)
        self.macro_trend_pullback.set_macro_context(
            self._macro_side,
            self._last_direction_pivot,
            bar.ts_close_ns,
        )
        local = self._route_pullback(
            self.efficient_pullback.on_bar(timeframe_minutes, bar)
        )
        macro = self._route_macro_pullback(
            self.macro_trend_pullback.on_bar(timeframe_minutes, bar)
        )
        diagonal = self._route_diagonal(
            self.mature_diagonal_acceptance.on_bar(timeframe_minutes, bar)
        )
        return sorted(
            core + local + macro + diagonal,
            key=lambda plan: (
                plan.interaction_time_ns,
                -plan.higher_timeframe_minutes,
                plan.symbol,
                plan.plan_id,
            ),
        )

    def drain_trace(self) -> list[dict[str, Any]]:
        output = (
            super().drain_trace()
            + self.macro_trend_pullback.drain_trace()
            + self._macro_pullback_trace
        )
        self._macro_pullback_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return super().find_zone(zone_id) or self.macro_trend_pullback.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["macro_trend_pullback"] = {
            "bundle_counts": dict(sorted(self._macro_pullback_counts.items())),
            "engine": self.macro_trend_pullback.diagnostics,
            "rules": (
                MACRO_TREND_PULLBACK_RULE,
                MACRO_TREND_PULLBACK_LIFECYCLE_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1MacroTrendOpportunityBundle
StrategyClass = EasyChartRE1LocalAuctionStrategy

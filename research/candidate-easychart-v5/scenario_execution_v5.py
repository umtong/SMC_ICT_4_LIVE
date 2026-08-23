"""Event-local footprint confirmation and immutable plan construction for EasyChart v5."""
from __future__ import annotations

from typing import Any

from domain import Candle, Side
from easychart_zones import PriceZone, ZoneKind, ZoneSide
from contracts_v5 import ScenarioPath, ScenarioSetup, SetupState, StructureFamily, V5TradePlan, provenance, SOURCE_RULES


class ScenarioExecutionMixin:
    def _formation_touches_context(self, zone: PriceZone, setup: ScenarioSetup) -> bool:
        bars = self.trigger_detector.bars
        for item in zone.formation_indices:
            if not 0 <= item < len(bars):
                continue
            formation_bar = bars[item]
            _, lower, upper = self._projected_bounds(setup, formation_bar.ts_close_ns)
            if formation_bar.low <= upper and formation_bar.high >= lower:
                return True
        return False
    def _select_footprint(self, candidates: list[PriceZone], setup: ScenarioSetup) -> PriceZone | None:
        if not candidates:
            return None
        center = (setup.context.lower + setup.context.upper) / 2.0
        return min(
            candidates,
            key=lambda zone: (
                zone.observed_time_ns,
                abs(((zone.lower + zone.upper) / 2.0) - center),
                zone.upper - zone.lower,
                zone.zone_id,
            ),
        )
    def _arm_displacements(self, bar: Candle, index: int, created: list[PriceZone]) -> None:
        for setup in list(self._active.values()):
            if setup.state is not SetupState.WAITING_DISPLACEMENT:
                continue
            if setup.confirmation_time_ns is None or bar.ts_close_ns <= setup.confirmation_time_ns:
                continue
            if self._target_is_spent(setup, bar):
                self._finish(setup, SetupState.TARGET_SPENT, bar.ts_close_ns, "target_spent_before_entry")
                continue
            if self._extreme_breached(setup, bar):
                self._finish(
                    setup,
                    SetupState.INVALIDATED,
                    bar.ts_close_ns,
                    "interaction_extreme_breached_before_displacement",
                )
                continue
            wanted = ZoneSide.SUPPORT if setup.side is Side.LONG else ZoneSide.RESISTANCE
            candidates = [
                zone
                for zone in created
                if zone.side is wanted
                and (zone.kind is ZoneKind.ORDER_BLOCK or zone.high_quality_by_size)
                and zone.observed_time_ns > setup.confirmation_time_ns
                and self._formation_touches_context(zone, setup)
            ]
            trigger = self._select_footprint(candidates, setup)
            if trigger is None:
                continue
            setup.trigger_zone = trigger
            setup.trigger_index = index
            setup.state = SetupState.WAITING_FOOTPRINT_RETEST
            self._audit(trigger)
            self._inc("event_local_footprint_confirmed")
            self._trace(
                "event_local_footprint_confirmed",
                bar.ts_close_ns,
                setup,
                trigger_zone_id=trigger.zone_id,
                trigger_zone_kind=trigger.kind.value,
                trigger_strength_ratio=trigger.strength_ratio,
            )
    def _acceptance_stop(self, setup: ScenarioSetup, time_ns: int) -> float | None:
        members, lower, upper = self._projected_bounds(setup, time_ns)
        if any(member.family is StructureFamily.CHANNEL for member in members):
            return lower - self.tick_size if setup.side is Side.LONG else upper + self.tick_size
        origin = setup.acceptance_origin
        if origin is None:
            return None
        return origin.price - self.tick_size if setup.side is Side.LONG else origin.price + self.tick_size
    def _make_plan(
        self,
        setup: ScenarioSetup,
        bar: Candle,
        *,
        entry: float,
        stop: float,
        trigger_zone: Any,
        trigger_kind: Any,
        trigger_strength: float,
    ) -> V5TradePlan | None:
        dynamic_target = self._channel_target_at(setup, bar.ts_close_ns)
        if dynamic_target is not None:
            setup.target_zone, setup.target_price = dynamic_target
            self._audit(setup.target_zone)
        if setup.target_zone is None or setup.target_price is None:
            self._finish(setup, SetupState.NO_TARGET, bar.ts_close_ns, "plan_lost_target")
            return None
        target = setup.target_price
        valid = stop < entry < target if setup.side is Side.LONG else target < entry < stop
        if not valid:
            self._finish(
                setup,
                SetupState.NO_TRADE_GEOMETRY,
                bar.ts_close_ns,
                "invalid_preentry_geometry",
                entry=entry,
                stop=stop,
                target=target,
            )
            return None
        gross_rr = abs(target - entry) / abs(entry - stop)
        if gross_rr + 1e-12 < self.minimum_gross_rr:
            self._finish(
                setup,
                SetupState.NO_TRADE_GEOMETRY,
                bar.ts_close_ns,
                "gross_rr_below_minimum",
                gross_rr=gross_rr,
            )
            return None
        members = sorted(
            setup.context_members,
            key=lambda item: (
                -item.source_pivot_span,
                -self._family_priority(item),
                item.zone_id,
            ),
        )
        higher = members[0]
        decision = members[1] if len(members) > 1 else higher
        self.sequence += 1
        family = (
            f"{self.scale_name}_CHANNEL_ROTATION_FOOTPRINT_RETEST"
            if setup.path is ScenarioPath.ROTATION
            else f"{self.scale_name}_STRUCTURE_ACCEPTANCE_RETEST"
            if setup.path is ScenarioPath.ACCEPTANCE
            else f"{self.scale_name}_STRUCTURE_{setup.path.value}_FOOTPRINT_RETEST"
        )
        plan = V5TradePlan(
            plan_id=f"ecv5-{self.scale_name.lower()}-{self.symbol}-{self.sequence:08d}",
            causal_event_id=f"{family}:{setup.setup_id}",
            symbol=self.symbol,
            family=family,
            side=setup.side,
            observed_time_ns=bar.ts_close_ns,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
            setup_id=setup.setup_id,
            higher_zone_id=higher.zone_id,
            higher_zone_kind=higher.kind,
            higher_strength_ratio=higher.strength_ratio,
            lower_zone_id=decision.zone_id,
            lower_zone_kind=decision.kind,
            lower_strength_ratio=decision.strength_ratio,
            trigger_zone_id=trigger_zone.zone_id,
            trigger_strength_ratio=trigger_strength,
            target_zone_id=setup.target_zone.zone_id,
            target_zone_kind=setup.target_zone.kind,
            overlap_lower=min(item.lower for item in members),
            overlap_upper=max(item.upper for item in members),
            interaction_time_ns=setup.interaction_time_ns,
            trigger_time_ns=bar.ts_close_ns,
            scenario_path=setup.path.value,
            setup_observed_time_ns=setup.observed_time_ns,
            trigger_zone_kind=trigger_kind.value,
            source_rule_count=len(SOURCE_RULES),
            rule_provenance=provenance(),
            scale_name=self.scale_name,
            higher_timeframe_minutes=self.higher_minutes,
            decision_timeframe_minutes=self.decision_minutes,
            trigger_timeframe_minutes=self.trigger_minutes,
        )
        setup.state = SetupState.PLANNED
        self._active.pop(setup.setup_id, None)
        if setup.trigger_zone is not None:
            setup.trigger_zone.consumed = True
        self.plans.append(plan)
        self._inc("plan_created")
        self._trace(
            "plan_created",
            bar.ts_close_ns,
            setup,
            plan_id=plan.plan_id,
            family=family,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
        )
        return plan
    def _advance_acceptance_retests(self, bar: Candle, index: int) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for setup in list(self._active.values()):
            if setup.state is not SetupState.WAITING_ACCEPTANCE_RETEST:
                continue
            if setup.confirmation_time_ns is None or bar.ts_close_ns <= setup.confirmation_time_ns:
                continue
            if self._target_is_spent(setup, bar):
                self._finish(setup, SetupState.TARGET_SPENT, bar.ts_close_ns, "target_spent_before_entry")
                continue
            projected, lower, upper = self._projected_bounds(setup, bar.ts_close_ns)
            touched = bar.low <= upper and bar.high >= lower
            if not touched:
                continue
            closes_outside = bar.close > upper if setup.side is Side.LONG else bar.close < lower
            if not closes_outside:
                self._finish(
                    setup,
                    SetupState.UNRESOLVED,
                    bar.ts_close_ns,
                    "acceptance_first_retest_failed",
                )
                continue
            stop = self._acceptance_stop(setup, bar.ts_close_ns)
            if stop is None:
                self._finish(setup, SetupState.NO_TRADE_GEOMETRY, bar.ts_close_ns, "acceptance_missing_stop")
                continue
            proxy = self.structure.snapshot_for(setup.context, bar.ts_close_ns)
            self._audit(proxy)
            plan = self._make_plan(
                setup,
                bar,
                entry=bar.close,
                stop=stop,
                trigger_zone=proxy,
                trigger_kind=proxy.kind,
                trigger_strength=proxy.strength_ratio,
            )
            if plan is not None:
                output.append(plan)
        return output
    def _advance_footprint_retests(self, bar: Candle, index: int) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for setup in list(self._active.values()):
            if setup.state is not SetupState.WAITING_FOOTPRINT_RETEST:
                continue
            trigger = setup.trigger_zone
            if trigger is None or setup.trigger_index is None:
                raise RuntimeError("footprint setup lost trigger")
            if self._target_is_spent(setup, bar):
                self._finish(setup, SetupState.TARGET_SPENT, bar.ts_close_ns, "target_spent_before_entry")
                continue
            trigger_invalidated = (
                bar.low <= trigger.invalidation
                if setup.side is Side.LONG
                else bar.high >= trigger.invalidation
            )
            if index > setup.trigger_index and trigger_invalidated:
                trigger.invalidated_index = index
                trigger.invalidated_time_ns = bar.ts_close_ns
                self._finish(
                    setup,
                    SetupState.INVALIDATED,
                    bar.ts_close_ns,
                    "trigger_footprint_invalidated_before_retest",
                )
                continue
            if self._extreme_breached(setup, bar):
                self._finish(
                    setup,
                    SetupState.INVALIDATED,
                    bar.ts_close_ns,
                    "interaction_extreme_breached_before_retest",
                )
                continue
            if index <= setup.trigger_index:
                continue
            touched = bar.low <= trigger.upper and bar.high >= trigger.lower
            if not touched:
                continue
            if setup.first_retest_consumed:
                raise RuntimeError("first footprint retest processed twice")
            setup.first_retest_consumed = True
            trigger.first_touch_index = index
            trigger.first_touch_time_ns = bar.ts_close_ns
            if setup.side is Side.LONG:
                reacted = bar.close > trigger.upper and bar.close > bar.open
                stop = min(setup.interaction_extreme - self.tick_size, trigger.invalidation)
            else:
                reacted = bar.close < trigger.lower and bar.close < bar.open
                stop = max(setup.interaction_extreme + self.tick_size, trigger.invalidation)
            if not reacted:
                self._finish(
                    setup,
                    SetupState.UNRESOLVED,
                    bar.ts_close_ns,
                    "first_footprint_retest_failed",
                )
                continue
            plan = self._make_plan(
                setup,
                bar,
                entry=bar.close,
                stop=stop,
                trigger_zone=trigger,
                trigger_kind=trigger.kind,
                trigger_strength=trigger.strength_ratio,
            )
            if plan is not None:
                output.append(plan)

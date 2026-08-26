"""Confirmed first-return continuation for persistent common initiative.

The first persistent-continuation pass entered on the same one-minute candle
which first touched a five-minute OB/FVG whenever adverse taker flow appeared
absorbed.  That repeated the earlier translation error: potential absorption was
mistaken for completed control transfer.

This family preserves the same causal formation and regime logic but changes two
responsibilities:

* the first touch only defines the pullback extreme; entry waits, without a
  fitted timeout, until a later completed minute closes beyond that extreme
  while directed taker flow is either aligned initiative or adverse flow which
  failed despite intended price progress;
* the first objective is the nearest pre-entry candidate which still offers at
  least 1R: the footprint's own displacement-wave extreme or the nearest
  pre-existing unspent 5m/15m structure.

The setup terminates only on source invalidation, objective consumption,
common-regime change, or a confirmed plan.  It does not add a score, percentile,
clock, session rule, partial exit or moving stop.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ObjectKind, SetupState, StructureFamily, StructureZone, V5TradePlan
from domain import Candle, Side
from easychart_re1_persistent_continuation import (
    PERSISTENT_CONTINUATION_FORMATION_RULE,
    PERSISTENT_REBALANCE_RULE,
    EasyChartRE1PersistentContinuationBundle,
    PersistentContinuationEngine,
    PersistentContinuationMarketStrategy,
    PersistentContinuationSetup,
)
from easychart_zones import ZoneSide
from execution_re1_factor_persistence import PERSISTENT_COMMON_AUCTION_RULE

PERSISTENT_CONFIRMED_RESPONSE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:FIRST_FIVE_MINUTE_FOOTPRINT_TOUCH_ONLY_ARMS_THE_PULLBACK_AND_A_LATER_COMPLETED_MINUTE_MUST_CLOSE_BEYOND_THE_TOUCH_EXTREME_WITH_DIRECTED_FLOW"
)
PERSISTENT_FIRST_ELIGIBLE_OBJECTIVE_RULE = (
    "SOURCE_EXPLICIT:THE_FIRST_PREENTRY_OBJECTIVE_WITH_AT_LEAST_ONE_GROSS_R_IS_THE_FORMATION_WAVE_EXTREME_OR_A_NEARER_PREEXISTING_STRUCTURE"
)
for _rule in (
    PERSISTENT_CONFIRMED_RESPONSE_RULE,
    PERSISTENT_FIRST_ELIGIBLE_OBJECTIVE_RULE,
):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


class PersistentObjectiveKind(str, Enum):
    FORMATION_WAVE_EXTREME = "FORMATION_WAVE_EXTREME"


class ConfirmedPersistentContinuationEngine(PersistentContinuationEngine):
    """Persistent five-minute footprint whose first return must transfer control."""

    def _formation_objective(
        self,
        setup: PersistentContinuationSetup,
        time_ns: int,
    ) -> tuple[StructureZone, float]:
        price = setup.source_zone.impulse_extreme
        if setup.side is Side.LONG:
            side = ZoneSide.RESISTANCE
            kind = ObjectKind.HORIZONTAL_RESISTANCE
            lower, upper = price, price + self.tick_size
            invalidation = upper + self.tick_size
        else:
            side = ZoneSide.SUPPORT
            kind = ObjectKind.HORIZONTAL_SUPPORT
            lower, upper = price - self.tick_size, price
            invalidation = lower - self.tick_size
        source_id = f"{setup.source_zone.zone_id}:FORMATION_WAVE_EXTREME"
        zone = StructureZone(
            zone_id=f"{source_id}:SNAP:{time_ns}",
            kind=PersistentObjectiveKind.FORMATION_WAVE_EXTREME,
            family=StructureFamily.HORIZONTAL,
            side=side,
            timeframe_minutes=setup.source_zone.timeframe_minutes,
            lower=lower,
            upper=upper,
            invalidation=invalidation,
            impulse_extreme=price,
            formed_index=setup.source_zone.formed_index,
            formed_time_ns=setup.source_zone.formed_time_ns,
            observed_time_ns=setup.source_zone.observed_time_ns,
            formation_indices=setup.source_zone.formation_indices,
            strength_ratio=setup.source_zone.strength_ratio,
            source_structure_id=source_id,
            source_pivot_span=setup.source_zone.source_pivot_span,
        )
        return zone, price

    def _select_entry_objective(
        self,
        setup: PersistentContinuationSetup,
        bar: Candle,
        entry: float,
        stop: float,
    ) -> tuple[StructureZone, float, float] | None:
        risk = entry - stop if setup.side is Side.LONG else stop - entry
        if risk <= 0.0:
            return None

        structural = self._nearest_target(
            setup.side,
            time_ns=bar.ts_close_ns,
            high=bar.high,
            low=bar.low,
        )
        formation = self._formation_objective(setup, bar.ts_close_ns)
        candidates: list[tuple[str, StructureZone, float, float]] = []
        for source, value in (("FORMATION_WAVE_EXTREME", formation), ("PREEXISTING_STRUCTURE", structural)):
            if value is None:
                continue
            zone, price = value
            reward = price - entry if setup.side is Side.LONG else entry - price
            if reward <= 0.0:
                continue
            rr = reward / risk
            if rr + 1e-12 < self.minimum_gross_rr:
                self._inc(f"persistent_objective_{source.lower()}_below_one_r")
                continue
            candidates.append((source, zone, price, rr))
        if not candidates:
            return None
        selected = (
            min(candidates, key=lambda item: (item[2], item[0]))
            if setup.side is Side.LONG
            else max(candidates, key=lambda item: (item[2], item[0]))
        )
        source, zone, price, rr = selected
        self._audit(zone)
        self._inc(f"persistent_objective_{source.lower()}_selected")
        self._trace(
            "persistent_first_eligible_objective_selected",
            bar.ts_close_ns,
            setup_id=setup.setup_id,
            side=setup.side.name,
            entry=entry,
            stop=stop,
            selected_source=source,
            selected_zone_id=zone.zone_id,
            selected_price=price,
            selected_gross_rr=rr,
            candidates=[
                {
                    "source": item[0],
                    "zone_id": item[1].zone_id,
                    "price": item[2],
                    "gross_rr": item[3],
                }
                for item in candidates
            ],
            rule_provenance=PERSISTENT_FIRST_ELIGIBLE_OBJECTIVE_RULE,
        )
        return zone, price, rr

    def _make_plan(
        self,
        setup: PersistentContinuationSetup,
        bar: Candle,
        mechanism: str,
    ) -> V5TradePlan | None:
        entry = bar.close
        stop = setup.source_zone.invalidation
        selected = self._select_entry_objective(setup, bar, entry, stop)
        if selected is None:
            self._inc("persistent_continuation_no_eligible_one_r_objective")
            return None
        target_zone, target, gross_rr = selected
        setup.target_zone = target_zone
        setup.target_price = target
        plan_id = f"PLAN:{setup.setup_id}:{bar.ts_close_ns}"
        plan = V5TradePlan(
            plan_id=plan_id,
            causal_event_id=setup.setup_id,
            symbol=self.symbol,
            family="PERSISTENT_COMMON_FLOW_5M_FOOTPRINT_CONFIRMED_RETURN",
            side=setup.side,
            observed_time_ns=bar.ts_close_ns,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
            setup_id=setup.setup_id,
            higher_zone_id=setup.source_zone.zone_id,
            higher_zone_kind=setup.source_zone.kind,
            higher_strength_ratio=setup.source_zone.strength_ratio,
            lower_zone_id=setup.source_zone.zone_id,
            lower_zone_kind=setup.source_zone.kind,
            lower_strength_ratio=setup.source_zone.strength_ratio,
            trigger_zone_id=setup.source_zone.zone_id,
            trigger_strength_ratio=setup.source_zone.strength_ratio,
            target_zone_id=target_zone.zone_id,
            target_zone_kind=target_zone.kind,
            overlap_lower=setup.source_zone.lower,
            overlap_upper=setup.source_zone.upper,
            interaction_time_ns=setup.first_touch_time_ns or setup.source_zone.observed_time_ns,
            trigger_time_ns=bar.ts_close_ns,
            scenario_path="ACCEPTANCE",
            setup_observed_time_ns=setup.source_zone.observed_time_ns,
            trigger_zone_kind=f"PERSISTENT_{mechanism}",
            source_rule_count=4,
            rule_provenance=(
                PERSISTENT_COMMON_AUCTION_RULE,
                PERSISTENT_CONTINUATION_FORMATION_RULE,
                PERSISTENT_REBALANCE_RULE,
                PERSISTENT_CONFIRMED_RESPONSE_RULE,
                PERSISTENT_FIRST_ELIGIBLE_OBJECTIVE_RULE,
            ),
            scale_name="PERSISTENT_CONTINUATION",
            higher_timeframe_minutes=15,
            decision_timeframe_minutes=5,
            trigger_timeframe_minutes=1,
        )
        self.plans.append(plan)
        self._inc("persistent_confirmed_continuation_plan_created")
        return plan

    def _response_mechanism(
        self,
        setup: PersistentContinuationSetup,
        bar: Candle,
        observation: Any,
    ) -> str | None:
        if observation is None or not observation.directed:
            return None
        assert setup.touch_high is not None and setup.touch_low is not None
        price_confirms = (
            bar.close > setup.touch_high
            if setup.side is Side.LONG
            else bar.close < setup.touch_low
        )
        body_confirms = bar.close > bar.open if setup.side is Side.LONG else bar.close < bar.open
        if not price_confirms or not body_confirms:
            return None
        if self._aligned(setup.side, observation.signed_taker_quote):
            return "CONFIRMED_REINITIATIVE"
        if self._opposite_delta(setup.side, observation.signed_taker_quote):
            return "CONFIRMED_ADVERSE_FLOW_ABSORBED"
        return None

    def _advance_setups(self, bar: Candle) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        observation = self._current_flow
        for setup in list(self._active.values()):
            if not self._setup_context_survives(setup):
                self._finish(
                    setup,
                    "persistent_continuation_common_regime_changed",
                    bar.ts_close_ns,
                    regime=self.common_snapshot.regime.value,
                    latest_side=None if self.common_snapshot.side is None else self.common_snapshot.side.name,
                )
                continue
            if bar.ts_close_ns <= setup.source_zone.observed_time_ns:
                continue
            if self._target_touched(setup, bar):
                self._finish(setup, "persistent_continuation_target_spent_before_entry", bar.ts_close_ns)
                continue
            if self._stop_touched(setup, bar):
                self._finish(setup, "persistent_continuation_source_invalidated_before_entry", bar.ts_close_ns)
                continue

            if setup.first_touch_time_ns is None:
                touched = bar.low <= setup.source_zone.upper and bar.high >= setup.source_zone.lower
                if not touched:
                    continue
                setup.first_touch_time_ns = bar.ts_close_ns
                setup.touch_high = bar.high
                setup.touch_low = bar.low
                setup.state = SetupState.WAITING_ACCEPTANCE_RESPONSE
                self._inc("persistent_continuation_first_touch_armed")
                self._trace(
                    "persistent_continuation_first_touch_armed",
                    bar.ts_close_ns,
                    setup_id=setup.setup_id,
                    side=setup.side.name,
                    touch_high=bar.high,
                    touch_low=bar.low,
                    touch_close=bar.close,
                    rule_provenance=PERSISTENT_CONFIRMED_RESPONSE_RULE,
                )
                continue

            if bar.ts_close_ns <= setup.first_touch_time_ns:
                continue
            mechanism = self._response_mechanism(setup, bar, observation)
            if mechanism is None:
                self._inc("persistent_continuation_waiting_control_transfer")
                continue
            plan = self._make_plan(setup, bar, mechanism)
            if plan is None:
                self._finish(setup, "persistent_continuation_no_trade_geometry", bar.ts_close_ns)
                continue
            self._finish(
                setup,
                "persistent_continuation_planned",
                bar.ts_close_ns,
                plan_id=plan.plan_id,
                response_mechanism=mechanism,
            )
            output.append(plan)
        return output

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["confirmed_return_policy"] = {
            "entry": "FIRST_LATER_CLOSE_BEYOND_TOUCH_EXTREME_WITH_DIRECTED_FLOW",
            "target": "NEAREST_FORMATION_WAVE_EXTREME_OR_PREEXISTING_STRUCTURE_WITH_GROSS_R_AT_LEAST_ONE",
            "rules": (
                PERSISTENT_CONFIRMED_RESPONSE_RULE,
                PERSISTENT_FIRST_ELIGIBLE_OBJECTIVE_RULE,
            ),
        }
        return output


class EasyChartRE1ConfirmedPersistentBundle(EasyChartRE1PersistentContinuationBundle):
    """Replace only the persistent pullback engine with confirmed control transfer."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.persistent_continuation = ConfirmedPersistentContinuationEngine(
            symbol,
            tick_size,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["persistent_continuation"] = 0


MultiScaleScenarioBundle = EasyChartRE1ConfirmedPersistentBundle
StrategyClass = PersistentContinuationMarketStrategy

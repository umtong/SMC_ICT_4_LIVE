"""First-return execution for a validated nested local continuation.

The complete continuation thesis is observable before the pullback: causal 15m
direction, a nested 5m structure break, a same-event high-quality engulfing OB,
aligned constituent one-minute aggressor flow and price progress, and an
anchored fair-value reference.  Once the first later pullback touches the source
OB or anchored fair value and closes back on the valid side, that completed
candle is the lower-frame reversal response described by the source material.

Waiting for another one-minute breakout duplicates confirmation, worsens the
entry and often lets the first structural target trade before submission.
Invalidation follows the responsibility of the actual entry location:

* a return into the source OB is invalid only beyond the OB wick and return
  extreme;
* an anchored-fair-value-only return creates a new lower-frame defended swing,
  so its first-return extreme is the structural invalidation.  Waiting for the
  distant source OB to fail would combine two different entry theses and destroy
  the short-stop / nearby-target geometry of a day trade.

The first return remains single-use, a close through the defended area ends the
episode, and the nearest pre-existing 5m/15m/impulse objective must still offer
at least 1R before the order is submitted.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, V5TradePlan
from domain import Candle, Side
from easychart_re1_local_continuation import (
    ANCHORED_FAIR_VALUE_PULLBACK_RULE,
    LOCAL_CONTINUATION_OBJECTIVE_RULE,
    LOCAL_NESTED_INITIATIVE_RULE,
    LocalAuctionContinuationEngine,
    LocalContinuationKind,
    MinuteWeight,
)
from easychart_re1_local_continuation_hold import CLOSE_HELD_PULLBACK_RULE


LOCAL_CONTINUATION_FIRST_RETURN_ENTRY_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:A_FLOW_VALIDATED_NESTED_INITIATIVE_ENTERS_ON_"
    "THE_COMPLETED_FIRST_SOURCE_OB_OR_ANCHORED_FAIR_VALUE_RETURN_WHICH_CLOSES_"
    "BACK_ON_THE_VALID_SIDE_WITHOUT_WAITING_FOR_A_DUPLICATE_SECOND_RESPONSE"
)
PULLBACK_RESPONSIBILITY_STOP_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:A_SOURCE_OB_RETURN_IS_INVALID_BEYOND_THE_OB_"
    "WICK_WHILE_AN_ANCHORED_FAIR_VALUE_ONLY_RETURN_IS_INVALID_BEYOND_ITS_"
    "NEWLY_DEFENDED_LOWER_FRAME_SWING_EXTREME"
)
for _rule in (
    LOCAL_CONTINUATION_FIRST_RETURN_ENTRY_RULE,
    PULLBACK_RESPONSIBILITY_STOP_RULE,
):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


class FirstReturnLocalAuctionContinuationEngine(LocalAuctionContinuationEngine):
    """Create the immutable plan at the first successfully held return close."""

    def _entry_stop(
        self,
        setup: Any,
        bar: Candle,
        kind: LocalContinuationKind,
    ) -> float:
        return_extreme = (
            bar.low - self.tick_size
            if setup.side is Side.LONG
            else bar.high + self.tick_size
        )
        if kind is LocalContinuationKind.ANCHORED_VWAP_PULLBACK:
            return return_extreme
        return (
            min(return_extreme, setup.source_zone.invalidation)
            if setup.side is Side.LONG
            else max(return_extreme, setup.source_zone.invalidation)
        )

    def _advance_setup(
        self,
        bar: Candle,
        minute: MinuteWeight,
    ) -> list[V5TradePlan]:
        setup = self._active
        if setup is None or setup.state != "WAITING_PULLBACK":
            return super()._advance_setup(bar, minute)
        if bar.ts_close_ns <= setup.impulse_time_ns:
            return []

        self._update_vwap(setup, minute)
        if self._target_touched(setup, bar):
            self._finish(
                setup,
                "local_continuation_target_spent_before_entry",
                bar.ts_close_ns,
            )
            return []
        if self._zone_invalidated(setup, bar):
            self._finish(
                setup,
                "local_continuation_source_invalidated_before_entry",
                bar.ts_close_ns,
            )
            return []

        choice = self._pullback_choice(setup, bar)
        if choice is None:
            return []
        kind, lower, upper = choice
        held = bar.close > upper if setup.side is Side.LONG else bar.close < lower
        if not held:
            self._finish(
                setup,
                "local_continuation_first_pullback_failed",
                bar.ts_close_ns,
                pullback_kind=kind.value,
                pullback_lower=lower,
                pullback_upper=upper,
                pullback_open=bar.open,
                pullback_high=bar.high,
                pullback_low=bar.low,
                pullback_close=bar.close,
                rule_provenance=(
                    CLOSE_HELD_PULLBACK_RULE,
                    LOCAL_CONTINUATION_FIRST_RETURN_ENTRY_RULE,
                    PULLBACK_RESPONSIBILITY_STOP_RULE,
                ),
            )
            return []

        stop = self._entry_stop(setup, bar, kind)
        self._refresh_target(setup, bar)
        entry = bar.close
        target = setup.target_price
        risk = entry - stop if setup.side is Side.LONG else stop - entry
        reward = target - entry if setup.side is Side.LONG else entry - target
        if risk <= 0.0 or reward <= 0.0:
            self._finish(
                setup,
                "local_continuation_nonpositive_preentry_geometry",
                bar.ts_close_ns,
                entry=entry,
                stop=stop,
                target=target,
                pullback_kind=kind.value,
            )
            return []
        gross_rr = reward / risk
        if gross_rr + 1e-12 < self.minimum_gross_rr:
            self._finish(
                setup,
                "local_continuation_below_minimum_gross_rr",
                bar.ts_close_ns,
                gross_rr=gross_rr,
                entry=entry,
                stop=stop,
                target=target,
                pullback_kind=kind.value,
            )
            return []

        self._sequence += 1
        plan = V5TradePlan(
            plan_id=f"local-continuation-{self.symbol}-{self._sequence:08d}",
            causal_event_id=setup.setup_id,
            symbol=self.symbol,
            family=f"NESTED_LOCAL_INITIATIVE_{kind.value}_FIRST_HELD_RETURN",
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
            target_zone_id=setup.target_zone.zone_id,
            target_zone_kind=setup.target_zone.kind,
            overlap_lower=setup.source_zone.lower,
            overlap_upper=setup.source_zone.upper,
            interaction_time_ns=setup.impulse_time_ns,
            trigger_time_ns=bar.ts_close_ns,
            scenario_path=ScenarioPath.ACCEPTANCE.value,
            setup_observed_time_ns=setup.source_zone.observed_time_ns,
            trigger_zone_kind=kind.value,
            source_rule_count=5,
            rule_provenance=(
                LOCAL_NESTED_INITIATIVE_RULE,
                ANCHORED_FAIR_VALUE_PULLBACK_RULE,
                CLOSE_HELD_PULLBACK_RULE,
                LOCAL_CONTINUATION_FIRST_RETURN_ENTRY_RULE,
                PULLBACK_RESPONSIBILITY_STOP_RULE,
                LOCAL_CONTINUATION_OBJECTIVE_RULE,
            ),
            scale_name="LOCAL_CONTINUATION",
            higher_timeframe_minutes=15,
            decision_timeframe_minutes=5,
            trigger_timeframe_minutes=1,
        )
        self.plans.append(plan)
        self._finish(
            setup,
            "local_continuation_plan_created",
            bar.ts_close_ns,
            plan_id=plan.plan_id,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
            anchored_vwap=setup.anchored_vwap,
            pullback_kind=kind.value,
            stop_responsibility=(
                "PULLBACK_EXTREME"
                if kind is LocalContinuationKind.ANCHORED_VWAP_PULLBACK
                else "SOURCE_OB_INVALIDATION_AND_PULLBACK_EXTREME"
            ),
            rule_provenance=(
                LOCAL_NESTED_INITIATIVE_RULE,
                ANCHORED_FAIR_VALUE_PULLBACK_RULE,
                CLOSE_HELD_PULLBACK_RULE,
                LOCAL_CONTINUATION_FIRST_RETURN_ENTRY_RULE,
                PULLBACK_RESPONSIBILITY_STOP_RULE,
                LOCAL_CONTINUATION_OBJECTIVE_RULE,
            ),
        )
        return [plan]

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["first_return_execution"] = {
            "entry": "FIRST_HELD_SOURCE_OB_OR_ANCHORED_FAIR_VALUE_RETURN_CLOSE",
            "source_ob_stop": "OB_INVALIDATION_AND_RETURN_EXTREME",
            "anchored_fair_value_stop": "RETURN_EXTREME",
            "rules": (
                LOCAL_CONTINUATION_FIRST_RETURN_ENTRY_RULE,
                PULLBACK_RESPONSIBILITY_STOP_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = FirstReturnLocalAuctionContinuationEngine

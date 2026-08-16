"""First-return execution for a validated nested local continuation.

The complete continuation thesis is already observable before the pullback:
causal 15m direction, a nested 5m structure break, a same-event high-quality
engulfing OB, aligned constituent one-minute aggressor flow and price progress,
and an anchored fair-value reference.  Once the first later pullback touches the
source OB or anchored fair value and closes back on the valid side, that candle
is the lower-frame reversal response described by the source material.

Waiting for yet another one-minute breakout duplicates confirmation, worsens the
entry and frequently lets the first structural target trade before submission.
This engine therefore enters at the completed first-return close.  The first
return remains single-use, a close through the area ends the episode, the stop
stays beyond both the return extreme and source invalidation, and the nearest
pre-existing 5m/15m/impulse objective must still offer at least 1R.
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
    MinuteWeight,
)
from easychart_re1_local_continuation_hold import CLOSE_HELD_PULLBACK_RULE


LOCAL_CONTINUATION_FIRST_RETURN_ENTRY_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:A_FLOW_VALIDATED_NESTED_INITIATIVE_ENTERS_ON_"
    "THE_COMPLETED_FIRST_SOURCE_OB_OR_ANCHORED_FAIR_VALUE_RETURN_WHICH_CLOSES_"
    "BACK_ON_THE_VALID_SIDE_WITHOUT_WAITING_FOR_A_DUPLICATE_SECOND_RESPONSE"
)
if LOCAL_CONTINUATION_FIRST_RETURN_ENTRY_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (LOCAL_CONTINUATION_FIRST_RETURN_ENTRY_RULE,)


class FirstReturnLocalAuctionContinuationEngine(LocalAuctionContinuationEngine):
    """Create the immutable plan at the first successfully held return close."""

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
                ),
            )
            return []

        stop = (
            min(bar.low - self.tick_size, setup.source_zone.invalidation)
            if setup.side is Side.LONG
            else max(bar.high + self.tick_size, setup.source_zone.invalidation)
        )
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
            )
            return []

        self._sequence += 1
        plan = V5TradePlan(
            plan_id=f"local-continuation-{self.symbol}-{self._sequence:08d}",
            causal_event_id=setup.setup_id,
            symbol=self.symbol,
            family="NESTED_LOCAL_INITIATIVE_FIRST_HELD_RETURN",
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
            source_rule_count=4,
            rule_provenance=(
                LOCAL_NESTED_INITIATIVE_RULE,
                ANCHORED_FAIR_VALUE_PULLBACK_RULE,
                CLOSE_HELD_PULLBACK_RULE,
                LOCAL_CONTINUATION_FIRST_RETURN_ENTRY_RULE,
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
            rule_provenance=(
                LOCAL_NESTED_INITIATIVE_RULE,
                ANCHORED_FAIR_VALUE_PULLBACK_RULE,
                CLOSE_HELD_PULLBACK_RULE,
                LOCAL_CONTINUATION_FIRST_RETURN_ENTRY_RULE,
                LOCAL_CONTINUATION_OBJECTIVE_RULE,
            ),
        )
        return [plan]

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["first_return_execution"] = {
            "entry": "FIRST_HELD_SOURCE_OB_OR_ANCHORED_FAIR_VALUE_RETURN_CLOSE",
            "stop": "RETURN_EXTREME_AND_SOURCE_INVALIDATION",
            "rule_provenance": LOCAL_CONTINUATION_FIRST_RETURN_ENTRY_RULE,
        }
        return output


MultiScaleScenarioBundle = FirstReturnLocalAuctionContinuationEngine

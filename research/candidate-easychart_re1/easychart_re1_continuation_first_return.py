"""Enter a validated local continuation on its first reacted pullback close.

A complete local continuation already exists before entry:

* 15m and 5m causal structure break in the same direction;
* a flow-validated 5m engulfing source order block;
* post-break expansion rather than immediate invalidation;
* the first later touch of the source OB or anchored fair value;
* that pullback candle closes back on the intended side with its own body.

The parent policy waits one additional minute and requires a close beyond the
entire pullback extreme.  That turns a confirmed first return into a second
confirmation and systematically sacrifices the price supplied by the OB/FVG
return.  This engine enters at the already completed reacted pullback close.
The stop remains beyond both the pullback wick and source invalidation; the
objective is the nearest pre-existing unspent structure selected at that same
close.  No intrabar decision, later retest selection, partial exit, stop move,
threshold fit, or risk change is introduced.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, V5TradePlan
from domain import Candle, Side
from easychart_re1_continuation_target_refresh import (
    CONTINUATION_TARGET_LIFECYCLE_RULE,
    EasyChartRE1ContinuationTargetRefreshBundle,
    PostImpulseTargetRefreshContinuationEngine,
)
from easychart_re1_local_continuation import (
    ANCHORED_FAIR_VALUE_PULLBACK_RULE,
    LOCAL_CONTINUATION_OBJECTIVE_RULE,
    LOCAL_NESTED_INITIATIVE_RULE,
    LocalContinuationSetup,
)


CONTINUATION_FIRST_RETURN_ENTRY_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:A_FIRST_SOURCE_OB_OR_ANCHORED_FAIR_VALUE_"
    "RETURN_THAT_CLOSES_BACK_ON_THE_INTENDED_SIDE_IS_ITSELF_THE_COMPLETED_"
    "ENTRY_CONFIRMATION_AND_DOES_NOT_REQUIRE_A_SECOND_LATER_MINUTE"
)
if CONTINUATION_FIRST_RETURN_ENTRY_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (CONTINUATION_FIRST_RETURN_ENTRY_RULE,)


class FirstReturnContinuationEngine(PostImpulseTargetRefreshContinuationEngine):
    """Create the immutable trade at the first reacted pullback close."""

    def _advance_setup(
        self,
        bar: Candle,
        minute: Any,
    ) -> list[V5TradePlan]:
        setup = self._active
        if (
            setup is None
            or bar.ts_close_ns <= setup.impulse_time_ns
            or setup.state != "WAITING_PULLBACK"
        ):
            return super()._advance_setup(bar, minute)

        self._update_vwap(setup, minute)
        # Pre-pullback extension is initiative, not a completed trade target.
        self._target_touched(setup, bar)
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
        reacted = (
            bar.close > upper and bar.close > bar.open
            if setup.side is Side.LONG
            else bar.close < lower and bar.close < bar.open
        )
        if not reacted:
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
            )
            return []

        self._sequence += 1
        plan = V5TradePlan(
            plan_id=f"local-continuation-first-return-{self.symbol}-{self._sequence:08d}",
            causal_event_id=setup.setup_id,
            symbol=self.symbol,
            family="NESTED_LOCAL_INITIATIVE_FIRST_RETURN_RESPONSE",
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
                CONTINUATION_FIRST_RETURN_ENTRY_RULE,
                CONTINUATION_TARGET_LIFECYCLE_RULE,
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
            "local_continuation_first_return_plan_created",
            bar.ts_close_ns,
            plan_id=plan.plan_id,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
            anchored_vwap=setup.anchored_vwap,
            pullback_kind=kind.value,
            rule_provenance=plan.rule_provenance,
        )
        return [plan]


class EasyChartRE1ContinuationFirstReturnBundle(
    EasyChartRE1ContinuationTargetRefreshBundle
):
    """Displacement core plus good-price first-return continuation entry."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.continuation = FirstReturnContinuationEngine(
            symbol,
            tick_size,
            minimum_gross_rr,
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["continuation_first_return_entry"] = {
            "rule_provenance": CONTINUATION_FIRST_RETURN_ENTRY_RULE,
            "entry": "FIRST_REACTED_SOURCE_OB_OR_ANCHORED_FAIR_VALUE_RETURN_CLOSE",
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1ContinuationFirstReturnBundle

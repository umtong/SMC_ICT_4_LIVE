"""Complete the breakout/retest sequence before an acceptance entry.

A closed break and hold establishes acceptance; the first valid retest only
confirms that the old boundary has flipped.  The supplied EasyChart cases then
use a lower-timeframe reversal candle, OB/FVG or breakout as the actual entry
cue.  Entering at the retest close alone caused repeated one- or two-minute
stop-outs and omitted that final decision step.

This module routes a successful acceptance retest into the existing event-local
OB/FVG lifecycle.  It does not add a threshold, score, timer or management
layer.  The final plan remains one full entry, one fixed full stop and one fixed
full target at 3% planned account risk.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, ScenarioSetup, SetupState, V5TradePlan
from domain import Candle, Side
from scenario_close_detached_v14 import (
    CLOSE_DETACHED_RETEST_RULE,
    CloseDetachedRetestScenarioEngine,
    close_detached,
)
from scenario_complete_context_v18 import SourceFaithfulHigherTimeframeBundleV18


ACCEPTANCE_FOOTPRINT_RULE = (
    "SOURCE_EXPLICIT:AFTER_BREAK_HOLD_AND_RETEST_THE_ACTUAL_ENTRY_USES_A_"
    "LATER_LOWER_TIMEFRAME_REVERSAL_FOOTPRINT"
)
if ACCEPTANCE_FOOTPRINT_RULE not in _contracts.SOURCE_RULES:
    _contracts.SOURCE_RULES += (ACCEPTANCE_FOOTPRINT_RULE,)


class AcceptanceFootprintScenarioEngine(CloseDetachedRetestScenarioEngine):
    """Use the structure retest as confirmation and OB/FVG as execution."""

    def _advance_acceptance_retests(self, bar: Candle, index: int) -> list[V5TradePlan]:
        del index
        for setup in list(self._active.values()):
            if setup.state is not SetupState.WAITING_ACCEPTANCE_RETEST:
                continue
            if setup.confirmation_time_ns is None or bar.ts_close_ns <= setup.confirmation_time_ns:
                continue
            if self._target_is_spent(setup, bar):
                self._finish(setup, SetupState.TARGET_SPENT, bar.ts_close_ns, "target_spent_before_entry")
                continue

            _, lower, upper = self._projected_bounds(setup, bar.ts_close_ns)
            if setup.setup_id not in self._detached_setup_ids:
                if close_detached(setup.side, lower, upper, bar):
                    self._detached_setup_ids.add(setup.setup_id)
                    self._inc("acceptance_boundary_close_detached")
                    self._trace(
                        "acceptance_boundary_close_detached",
                        bar.ts_close_ns,
                        setup,
                        detached_bar_low=bar.low,
                        detached_bar_high=bar.high,
                        detached_bar_close=bar.close,
                        projected_lower=lower,
                        projected_upper=upper,
                        provenance=CLOSE_DETACHED_RETEST_RULE,
                    )
                continue

            touched = bar.low <= upper and bar.high >= lower
            if not touched:
                continue
            closes_outside = bar.close > upper if setup.side is Side.LONG else bar.close < lower
            if not closes_outside:
                self._finish(
                    setup,
                    SetupState.UNRESOLVED,
                    bar.ts_close_ns,
                    "acceptance_first_detached_retest_failed",
                )
                continue

            # The S/R flip is now confirmed, but this bar is not asked to be
            # location, confirmation and entry simultaneously.  A later
            # event-local footprint must form and survive its own first return.
            setup.confirmation_time_ns = bar.ts_close_ns
            setup.interaction_extreme = (
                min(setup.interaction_extreme, bar.low)
                if setup.side is Side.LONG
                else max(setup.interaction_extreme, bar.high)
            )
            setup.state = SetupState.WAITING_DISPLACEMENT
            self._detached_setup_ids.discard(setup.setup_id)
            self._inc("acceptance_retest_confirmed_waiting_footprint")
            self._trace(
                "acceptance_retest_confirmed_waiting_footprint",
                bar.ts_close_ns,
                setup,
                retest_low=bar.low,
                retest_high=bar.high,
                retest_close=bar.close,
                projected_lower=lower,
                projected_upper=upper,
                provenance=ACCEPTANCE_FOOTPRINT_RULE,
            )
        return []

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
                    "trigger_footprint_invalidated_before_detached_retest",
                )
                continue
            if self._extreme_breached(setup, bar):
                self._finish(
                    setup,
                    SetupState.INVALIDATED,
                    bar.ts_close_ns,
                    "interaction_extreme_breached_before_detached_retest",
                )
                continue
            if index <= setup.trigger_index:
                continue

            if setup.setup_id not in self._detached_setup_ids:
                if close_detached(setup.side, trigger.lower, trigger.upper, bar):
                    self._detached_setup_ids.add(setup.setup_id)
                    self._inc("footprint_close_detached")
                    self._trace(
                        "footprint_close_detached",
                        bar.ts_close_ns,
                        setup,
                        trigger_zone_id=trigger.zone_id,
                        detached_bar_low=bar.low,
                        detached_bar_high=bar.high,
                        detached_bar_close=bar.close,
                        provenance=CLOSE_DETACHED_RETEST_RULE,
                    )
                continue

            touched = bar.low <= trigger.upper and bar.high >= trigger.lower
            if not touched:
                continue
            if setup.first_retest_consumed:
                raise RuntimeError("first detached footprint retest processed twice")
            setup.first_retest_consumed = True
            trigger.first_touch_index = index
            trigger.first_touch_time_ns = bar.ts_close_ns

            if setup.side is Side.LONG:
                reacted = bar.close > trigger.upper and bar.close > bar.open
            else:
                reacted = bar.close < trigger.lower and bar.close < bar.open
            if not reacted:
                self._finish(
                    setup,
                    SetupState.UNRESOLVED,
                    bar.ts_close_ns,
                    "first_detached_footprint_retest_failed",
                )
                continue

            if setup.path is ScenarioPath.ACCEPTANCE:
                structural_stop = self._acceptance_stop(setup, bar.ts_close_ns)
                if structural_stop is None:
                    self._finish(
                        setup,
                        SetupState.NO_TRADE_GEOMETRY,
                        bar.ts_close_ns,
                        "acceptance_missing_stop_after_footprint",
                    )
                    continue
                stop = (
                    min(structural_stop, trigger.invalidation)
                    if setup.side is Side.LONG
                    else max(structural_stop, trigger.invalidation)
                )
            else:
                stop = (
                    min(setup.interaction_extreme - self.tick_size, trigger.invalidation)
                    if setup.side is Side.LONG
                    else max(setup.interaction_extreme + self.tick_size, trigger.invalidation)
                )

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
        return output


class CompleteEasyChartBundleV18(SourceFaithfulHigherTimeframeBundleV18):
    """Active micro policy with meaningful context and complete entry causality."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = AcceptanceFootprintScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["micro"] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["acceptance_execution"] = {
            "name": "BREAK_HOLD_RETEST_THEN_EVENT_LOCAL_FOOTPRINT_AND_FIRST_RETURN",
            "rule_provenance": ACCEPTANCE_FOOTPRINT_RULE,
        }
        return output

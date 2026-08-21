"""Source-consistent departure-then-return semantics for the canonical candidate.

The earlier machine policy could call continued contact with a boundary a
"retest".  A retest in the supplied chart examples is a distinct sequence:
price leaves the structure or event-local footprint, a completed bar closes on
the valid side, and only a later bar returns.  This module changes only that
ambiguous translation.  It does not add a time threshold, score, volatility
multiple, direction filter, risk multiplier, or post-entry management rule.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioSetup, SetupState, V5TradePlan
from domain import Candle, Side
from scenario_micro_nearest_target_v5 import MicroNearestAnyTargetResearchScenarioBundleV5
from scenario_target_ablation_v5 import NearestAnyTargetScenarioEngine


CLOSE_DETACHED_RETEST_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "RETEST_REQUIRES_A_COMPLETED_CLOSE_OUTSIDE_THE_BOUNDARY_BEFORE_RETURN"
)
if CLOSE_DETACHED_RETEST_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (CLOSE_DETACHED_RETEST_RULE,)


def close_detached(side: Side, lower: float, upper: float, bar: Candle) -> bool:
    if lower > upper:
        raise ValueError("detachment bounds are inverted")
    return bar.close > upper if side is Side.LONG else bar.close < lower


class CloseDetachedRetestScenarioEngine(NearestAnyTargetScenarioEngine):
    """Nearest-objective engine which requires a distinct departure and return."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._detached_setup_ids: set[str] = set()

    def _finish(
        self,
        setup: ScenarioSetup,
        state: SetupState,
        time_ns: int,
        reason: str,
        **values: Any,
    ) -> None:
        self._detached_setup_ids.discard(setup.setup_id)
        super()._finish(setup, state, time_ns, reason, **values)

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
        plan = super()._make_plan(
            setup,
            bar,
            entry=entry,
            stop=stop,
            trigger_zone=trigger_zone,
            trigger_kind=trigger_kind,
            trigger_strength=trigger_strength,
        )
        if plan is not None:
            self._detached_setup_ids.discard(setup.setup_id)
        return plan

    def _advance_acceptance_retests(self, bar: Candle, index: int) -> list[V5TradePlan]:
        del index
        output: list[V5TradePlan] = []
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
                # A departure bar cannot also be its own retest.
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
                # A departure bar cannot also be its own retest.
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
                stop = min(setup.interaction_extreme - self.tick_size, trigger.invalidation)
            else:
                reacted = bar.close < trigger.lower and bar.close < bar.open
                stop = max(setup.interaction_extreme + self.tick_size, trigger.invalidation)
            if not reacted:
                self._finish(
                    setup,
                    SetupState.UNRESOLVED,
                    bar.ts_close_ns,
                    "first_detached_footprint_retest_failed",
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
        return output


class MicroCloseDetachedRetestBundleV14(MicroNearestAnyTargetResearchScenarioBundleV5):
    """Micro structure policy with source-consistent close-detached retests."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = CloseDetachedRetestScenarioEngine(
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
        output["retest_policy"] = {
            "name": "CLOSE_DETACH_THEN_FIRST_RETURN",
            "rule_provenance": CLOSE_DETACHED_RETEST_RULE,
        }
        return output

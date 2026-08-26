"""Horizontal S/R-flip continuation with one immediate micro response.

The live EasyChart examples often trade a horizontal resistance/support flip
without requiring a rare multi-family confluence cluster.  The complete
scenario is still strict and causal:

* a pre-existing horizontal level is accepted by a completed body break;
* the required next decision bar holds outside;
* the first later return closes on the new side of the level;
* the first following completed minute must extend beyond that return extreme;
* the stop remains the structural flip invalidation and the objective is the
  nearer of the inherited first obstacle and a pre-existing significant
  one-minute opposing swing.

This family reuses the existing EasyChart horizontal-flip detector.  It changes
only the delayed response responsibility and does not add a score, session,
volatility threshold, fixed-R target, partial exit or stop movement.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, V5TradePlan
from domain import Candle, Side
from easychart_re1_efficient_objective import PivotOnlyObjectiveBook
from easychart_re1_human_policy import EasyChartRE1HumanPolicyBundle
from easychart_re1_local_auction_continuation import (
    COMMON_FACTOR_VETO_ONLY_RULE,
)
from easychart_re1_local_auction_continuation_v2 import (
    EasyChartRE1LocalAuctionContinuationV2Bundle,
    EasyChartRE1LocalAuctionStrategy,
)


HORIZONTAL_FLIP_RESPONSE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "HORIZONTAL_SR_FLIP_ENTERS_ONLY_AFTER_BODY_BREAK_NEXT_BAR_HOLD_FIRST_RETURN_AND_FIRST_LATER_MICRO_CLOSE_BEYOND_THE_RETURN_EXTREME"
)
HORIZONTAL_FLIP_SIGNIFICANT_OBJECTIVE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "HORIZONTAL_FLIP_USES_THE_NEARER_OF_ITS_EXISTING_OBJECTIVE_AND_A_PREEXISTING_UNSPENT_SPAN6_ONE_MINUTE_OPPOSING_SWING"
)
for _rule in (HORIZONTAL_FLIP_RESPONSE_RULE, HORIZONTAL_FLIP_SIGNIFICANT_OBJECTIVE_RULE):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


@dataclass(frozen=True, slots=True)
class PendingHorizontalFlipResponse:
    plan: V5TradePlan
    retest_time_ns: int
    retest_high: float
    retest_low: float
    retest_close: float


class HorizontalFlipResponseFamily:
    """Extract only horizontal acceptance plans and delay them one response bar."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.minimum_gross_rr = minimum_gross_rr
        self.source = EasyChartRE1HumanPolicyBundle(symbol, tick_size, minimum_gross_rr)
        self.micro_objectives = PivotOnlyObjectiveBook(
            symbol,
            1,
            tick_size,
            pivot_spans=(6,),
        )
        self.pending: dict[str, PendingHorizontalFlipResponse] = {}
        self.final_plans: list[V5TradePlan] = []
        self.trace_events: list[dict[str, Any]] = []
        self._zones: dict[str, Any] = {}
        self._counts: dict[str, int] = {}

    def _inc(self, key: str) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1

    @staticmethod
    def _horizontal_acceptance(plan: V5TradePlan) -> bool:
        if plan.scenario_path != ScenarioPath.ACCEPTANCE.value:
            return False
        text = " ".join(
            (
                str(plan.family),
                str(plan.scale_name),
                str(plan.higher_zone_kind),
                str(plan.lower_zone_kind),
                str(plan.higher_zone_id),
                str(plan.lower_zone_id),
            )
        ).upper()
        return "HORIZONTAL" in text or "FLIP" in text

    @staticmethod
    def _stop_touched(plan: V5TradePlan, bar: Candle) -> bool:
        return bar.low <= plan.stop if plan.side is Side.LONG else bar.high >= plan.stop

    @staticmethod
    def _target_touched(plan: V5TradePlan, bar: Candle) -> bool:
        return bar.high >= plan.target if plan.side is Side.LONG else bar.low <= plan.target

    @staticmethod
    def _responded(pending: PendingHorizontalFlipResponse, bar: Candle) -> bool:
        return (
            bar.close > pending.retest_high
            if pending.plan.side is Side.LONG
            else bar.close < pending.retest_low
        )

    @staticmethod
    def _closer(side: Side, candidate: float, existing: float) -> bool:
        return candidate < existing if side is Side.LONG else candidate > existing

    def _target(self, plan: V5TradePlan, bar: Candle) -> tuple[Any | None, float]:
        target = self.micro_objectives.target_for(
            plan.side,
            interaction_time_ns=bar.ts_close_ns,
            source_span=6,
            current_high=bar.high,
            current_low=bar.low,
        )
        if target is None or not self._closer(plan.side, target[1], plan.target):
            return None, plan.target
        zone, price = target
        self._zones[zone.zone_id] = zone
        return zone, price

    def _complete_pending(self, bar: Candle) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for event_id, pending in list(self.pending.items()):
            if bar.ts_close_ns <= pending.retest_time_ns:
                continue
            self.pending.pop(event_id, None)
            plan = pending.plan
            if self._stop_touched(plan, bar):
                self._inc("response_bar_touched_stop_before_entry")
                continue
            if self._target_touched(plan, bar):
                self._inc("response_bar_spent_target_before_entry")
                continue
            if not self._responded(pending, bar):
                self._inc("first_response_failed")
                self.trace_events.append(
                    {
                        "scenario_kind": "horizontal_flip_first_response_failed",
                        "event_time_ns": bar.ts_close_ns,
                        "symbol": self.symbol,
                        "plan_id": plan.plan_id,
                        "side": plan.side.name,
                        "retest_time_ns": pending.retest_time_ns,
                        "retest_high": pending.retest_high,
                        "retest_low": pending.retest_low,
                        "response_close": bar.close,
                        "rule_provenance": HORIZONTAL_FLIP_RESPONSE_RULE,
                    }
                )
                continue

            target_zone, target = self._target(plan, bar)
            entry = bar.close
            risk = entry - plan.stop if plan.side is Side.LONG else plan.stop - entry
            reward = target - entry if plan.side is Side.LONG else entry - target
            if risk <= 0.0 or reward <= 0.0:
                self._inc("response_nonpositive_geometry")
                continue
            gross_rr = reward / risk
            if gross_rr + 1e-12 < self.minimum_gross_rr:
                self._inc("response_below_minimum_gross_rr")
                continue
            final = replace(
                plan,
                plan_id=f"{plan.plan_id}:FIRST_RESPONSE:{bar.ts_close_ns}",
                observed_time_ns=bar.ts_close_ns,
                trigger_time_ns=bar.ts_close_ns,
                entry=entry,
                target=target,
                gross_rr=gross_rr,
                target_zone_id=plan.target_zone_id if target_zone is None else target_zone.zone_id,
                target_zone_kind=plan.target_zone_kind if target_zone is None else target_zone.kind,
                trigger_zone_kind=f"HORIZONTAL_FLIP_FIRST_RESPONSE:{plan.trigger_zone_kind}",
                rule_provenance=plan.rule_provenance + (
                    HORIZONTAL_FLIP_RESPONSE_RULE,
                    HORIZONTAL_FLIP_SIGNIFICANT_OBJECTIVE_RULE,
                ),
            )
            self.final_plans.append(final)
            output.append(final)
            self._inc("horizontal_flip_response_plan_created")
            self.trace_events.append(
                {
                    "scenario_kind": "horizontal_flip_first_response_confirmed",
                    "event_time_ns": bar.ts_close_ns,
                    "symbol": self.symbol,
                    "plan_id": final.plan_id,
                    "side": final.side.name,
                    "retest_time_ns": pending.retest_time_ns,
                    "entry": final.entry,
                    "stop": final.stop,
                    "target": final.target,
                    "gross_rr": final.gross_rr,
                    "rule_provenance": (
                        HORIZONTAL_FLIP_RESPONSE_RULE,
                        HORIZONTAL_FLIP_SIGNIFICANT_OBJECTIVE_RULE,
                    ),
                }
            )
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        if timeframe_minutes == 1:
            self.micro_objectives.on_bar(bar)
            output.extend(self._complete_pending(bar))

        raw = self.source.on_bar(timeframe_minutes, bar)
        for plan in raw:
            if not self._horizontal_acceptance(plan):
                continue
            if plan.causal_event_id in self.pending:
                self._inc("duplicate_pending_horizontal_flip")
                continue
            self.pending[plan.causal_event_id] = PendingHorizontalFlipResponse(
                plan=plan,
                retest_time_ns=bar.ts_close_ns,
                retest_high=bar.high,
                retest_low=bar.low,
                retest_close=bar.close,
            )
            self._inc("horizontal_flip_waiting_first_response")
            self.trace_events.append(
                {
                    "scenario_kind": "horizontal_flip_waiting_first_response",
                    "event_time_ns": bar.ts_close_ns,
                    "symbol": self.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "entry_before_response": plan.entry,
                    "stop": plan.stop,
                    "target": plan.target,
                    "rule_provenance": HORIZONTAL_FLIP_RESPONSE_RULE,
                }
            )

        if timeframe_minutes == 1:
            self.micro_objectives.observe_price(bar)
        return output

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return self.source.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return self.final_plans

    def drain_trace(self) -> list[dict[str, Any]]:
        output = self.source.drain_trace() + self.trace_events
        self.trace_events = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return self._zones.get(zone_id) or self.source.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._counts.items())),
            "pending": len(self.pending),
            "source": self.source.diagnostics,
            "micro_objectives": dict(self.micro_objectives.diagnostics),
            "rules": (
                HORIZONTAL_FLIP_RESPONSE_RULE,
                HORIZONTAL_FLIP_SIGNIFICANT_OBJECTIVE_RULE,
            ),
        }


class EasyChartRE1HorizontalFlipResponseBundle(
    EasyChartRE1LocalAuctionContinuationV2Bundle,
):
    """Rejection, local OB continuation and horizontal flip continuation."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.horizontal_flip_response = HorizontalFlipResponseFamily(
            symbol,
            tick_size,
            minimum_gross_rr,
        )
        self._horizontal_counts: dict[str, int] = {}
        self._horizontal_trace: list[dict[str, Any]] = []

    def _hinc(self, key: str) -> None:
        self._horizontal_counts[key] = self._horizontal_counts.get(key, 0) + 1

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return super().setups + self.horizontal_flip_response.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return super().plans + self.horizontal_flip_response.plans

    def _route_horizontal(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in raw:
            if self._duplicate_episode(plan):
                self._hinc("horizontal_flip_duplicate_episode")
                continue
            macro_side = getattr(self, "_macro_side", None)
            factor = self._market_factor_state
            if macro_side is None or plan.side is macro_side:
                if not self._route_plan(plan):
                    self._hinc("horizontal_flip_rejected_by_macro_router")
                    continue
            elif factor is None or factor.side is not plan.side:
                self._hinc("counter_macro_horizontal_flip_without_common_support")
                continue
            else:
                self._hinc("counter_macro_horizontal_flip_allowed_by_common_factor")
            self._claim_episode(plan)
            output.append(plan)
            self._hinc("horizontal_flip_plan_allowed")
            self._horizontal_trace.append(
                {
                    "scenario_kind": "horizontal_flip_response_plan_allowed",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "entry": plan.entry,
                    "stop": plan.stop,
                    "target": plan.target,
                    "gross_rr": plan.gross_rr,
                    "rule_provenance": (
                        HORIZONTAL_FLIP_RESPONSE_RULE,
                        HORIZONTAL_FLIP_SIGNIFICANT_OBJECTIVE_RULE,
                        COMMON_FACTOR_VETO_ONLY_RULE,
                    ),
                }
            )
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        horizontal = self._route_horizontal(
            self.horizontal_flip_response.on_bar(timeframe_minutes, bar)
        )
        core = super().on_bar(timeframe_minutes, bar)
        return sorted(
            horizontal + core,
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
            + self.horizontal_flip_response.drain_trace()
            + self._horizontal_trace
        )
        self._horizontal_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return super().find_zone(zone_id) or self.horizontal_flip_response.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["horizontal_flip_response"] = {
            "bundle_counts": dict(sorted(self._horizontal_counts.items())),
            "family": self.horizontal_flip_response.diagnostics,
            "rules": (
                HORIZONTAL_FLIP_RESPONSE_RULE,
                HORIZONTAL_FLIP_SIGNIFICANT_OBJECTIVE_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1HorizontalFlipResponseBundle
StrategyClass = EasyChartRE1LocalAuctionStrategy

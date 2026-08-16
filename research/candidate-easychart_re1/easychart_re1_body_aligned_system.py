"""Complete EasyChart scenarios routed by observable multi-timeframe delivery.

A finished trade plan is not enough when the completed higher, decision and
trigger candles are still delivering price against it.  The source material
separates direction from entry timing: the larger chart establishes direction,
while the smaller chart confirms the actual return or break.  This router makes
that relationship explicit without a fitted threshold:

* the latest completed 60m, 15m and 5m candle bodies must all point in the plan
  direction;
* a doji or disagreement is an unresolved auction, not a trade;
* the underlying scenario still owns context, liquidity event, source OB/FVG,
  first return, structural stop and pre-existing target;
* the source-footprint continuation family and every higher auction compete in
  one unchanged global account slot.

The rule uses only closed candles available when the immutable plan is emitted.
It does not alter entry, stop, target, risk, costs or trade management.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Candle, Side
from easychart_re1_continuation_source_return import (
    EasyChartRE1ContinuationSourceReturnBundle,
)


MULTITIMEFRAME_BODY_ALIGNMENT_RULE = (
    "RESEARCH_SYNTHESIS:AN_EXECUTABLE_PLAN_REQUIRES_THE_LATEST_COMPLETED_60M_"
    "15M_AND_5M_CANDLE_BODIES_TO_DELIVER_IN_THE_PLAN_DIRECTION_WHILE_"
    "DISAGREEMENT_REMAINS_UNRESOLVED"
)
if MULTITIMEFRAME_BODY_ALIGNMENT_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (MULTITIMEFRAME_BODY_ALIGNMENT_RULE,)


class EasyChartRE1BodyAlignedSystem:
    """One full scenario stream with causal 60m/15m/5m delivery routing."""

    REQUIRED_TIMEFRAMES = (60, 15, 5)

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.base = EasyChartRE1ContinuationSourceReturnBundle(
            symbol,
            tick_size,
            minimum_gross_rr,
        )
        self.detectors = self.base.detectors
        self._latest: dict[int, Candle] = {}
        self._plans: list[V5TradePlan] = []
        self._trace: list[dict[str, Any]] = []
        self._counts: dict[str, int] = {}

    def _inc(self, key: str) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1

    @staticmethod
    def _body_side(bar: Candle) -> Side | None:
        if bar.close > bar.open:
            return Side.LONG
        if bar.close < bar.open:
            return Side.SHORT
        return None

    def _state_values(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for timeframe in self.REQUIRED_TIMEFRAMES:
            bar = self._latest.get(timeframe)
            side = None if bar is None else self._body_side(bar)
            values[f"body_{timeframe}m_side"] = None if side is None else side.name
            values[f"body_{timeframe}m_close_time_ns"] = (
                None if bar is None else bar.ts_close_ns
            )
        return values

    def _aligned(self, plan: V5TradePlan) -> tuple[bool, str]:
        missing = [tf for tf in self.REQUIRED_TIMEFRAMES if tf not in self._latest]
        if missing:
            return False, "MISSING_COMPLETED_CONTEXT"
        sides = [self._body_side(self._latest[tf]) for tf in self.REQUIRED_TIMEFRAMES]
        if any(side is None for side in sides):
            return False, "DOJI_CONTEXT_UNRESOLVED"
        if all(side is plan.side for side in sides):
            return True, "ALL_COMPLETED_BODIES_ALIGNED"
        return False, "MULTITIMEFRAME_DELIVERY_DISAGREEMENT"

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes in self.REQUIRED_TIMEFRAMES:
            self._latest[timeframe_minutes] = bar

        raw = self.base.on_bar(timeframe_minutes, bar)
        output: list[V5TradePlan] = []
        for plan in raw:
            allowed, reason = self._aligned(plan)
            event = {
                "event_time_ns": plan.observed_time_ns,
                "symbol": plan.symbol,
                "plan_id": plan.plan_id,
                "family": plan.family,
                "scale_name": plan.scale_name,
                "side": plan.side.name,
                "routing_reason": reason,
                **self._state_values(),
                "rule_provenance": MULTITIMEFRAME_BODY_ALIGNMENT_RULE,
            }
            if allowed:
                self._inc("body_aligned_plan_allowed")
                self._trace.append(
                    {"scenario_kind": "body_aligned_plan_allowed", **event},
                )
                output.append(plan)
            else:
                self._inc("body_aligned_plan_rejected")
                self._inc(reason.lower())
                self._trace.append(
                    {"scenario_kind": "body_aligned_plan_rejected", **event},
                )

        unique = {plan.plan_id: plan for plan in output}
        routed = sorted(
            unique.values(),
            key=lambda plan: (
                plan.interaction_time_ns,
                plan.observed_time_ns,
                plan.symbol,
                plan.plan_id,
            ),
        )
        self._plans.extend(routed)
        return routed

    def drain_trace(self) -> list[dict[str, Any]]:
        output = self.base.drain_trace() + self._trace
        self._trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return self.base.find_zone(zone_id)

    @property
    def plans(self) -> list[V5TradePlan]:
        return list(self._plans)

    @property
    def setups(self) -> list[Any]:
        return list(self.base.setups)

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "body_aligned_router": {
                "counts": dict(sorted(self._counts.items())),
                **self._state_values(),
                "rule_provenance": MULTITIMEFRAME_BODY_ALIGNMENT_RULE,
            },
            "base": self.base.diagnostics,
        }


MultiScaleScenarioBundle = EasyChartRE1BodyAlignedSystem

"""Higher direction plus lower response routing for complete EasyChart plans.

The fifteen-minute chart already owns scenario construction: structure break,
liquidity interaction and the institutional footprint are created there and on
its nested five-minute auction.  Requiring the latest 15m candle body to point
with the trade duplicates that responsibility and rejects valid pullbacks whose
15m candle is temporarily counter-directional.

This router separates the two jobs described by the source material:

* the latest completed 60m body supplies broad directional delivery;
* the latest completed 5m body confirms that control has returned at entry;
* the 15m structure remains inside the scenario engine instead of becoming a
  second direction veto.

A doji or disagreement is unresolved.  Entry, stop, target, first-return
ownership, flow evidence, costs, fixed 3% NAV risk and the one global account
slot are unchanged.  Only closed candles available when the plan is emitted are
used.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Candle, Side
from easychart_re1_continuation_source_return import (
    EasyChartRE1ContinuationSourceReturnBundle,
)


DIRECTION_RESPONSE_ALIGNMENT_RULE = (
    "RESEARCH_SYNTHESIS:THE_LATEST_COMPLETED_60M_BODY_SUPPLIES_DIRECTION_AND_"
    "THE_LATEST_COMPLETED_5M_BODY_CONFIRMS_ENTRY_RESPONSE_WHILE_15M_STRUCTURE_"
    "REMAINS_INSIDE_THE_CAUSAL_SCENARIO"
)
if DIRECTION_RESPONSE_ALIGNMENT_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (DIRECTION_RESPONSE_ALIGNMENT_RULE,)


class EasyChartRE1DirectionResponseSystem:
    """Complete source-footprint stream routed by 60m direction and 5m response."""

    REQUIRED_TIMEFRAMES = (60, 5)

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
        if any(tf not in self._latest for tf in self.REQUIRED_TIMEFRAMES):
            return False, "MISSING_COMPLETED_DIRECTION_OR_RESPONSE"
        sides = [self._body_side(self._latest[tf]) for tf in self.REQUIRED_TIMEFRAMES]
        if any(side is None for side in sides):
            return False, "DOJI_DIRECTION_OR_RESPONSE_UNRESOLVED"
        if all(side is plan.side for side in sides):
            return True, "COMPLETED_60M_DIRECTION_AND_5M_RESPONSE_ALIGNED"
        return False, "DIRECTION_RESPONSE_DISAGREEMENT"

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
                "rule_provenance": DIRECTION_RESPONSE_ALIGNMENT_RULE,
            }
            if allowed:
                self._inc("direction_response_plan_allowed")
                self._trace.append(
                    {"scenario_kind": "direction_response_plan_allowed", **event},
                )
                output.append(plan)
            else:
                self._inc("direction_response_plan_rejected")
                self._inc(reason.lower())
                self._trace.append(
                    {"scenario_kind": "direction_response_plan_rejected", **event},
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
            "direction_response_router": {
                "counts": dict(sorted(self._counts.items())),
                **self._state_values(),
                "rule_provenance": DIRECTION_RESPONSE_ALIGNMENT_RULE,
            },
            "base": self.base.diagnostics,
        }


MultiScaleScenarioBundle = EasyChartRE1DirectionResponseSystem

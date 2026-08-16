"""Completed-60m market-structure router for the full skilled opportunity set.

EasyChart assigns direction to market structure and timing to the lower frame.
The existing local engine creates complete 15m/5m/1m plans, but it can still
trade the opposite side of an already observable higher auction structure.
This wrapper converts the human top-down decision into one causal policy:

* the two latest causally confirmed span-2 sixty-minute highs and lows define
  BULL, BEAR or MIXED structure;
* in BULL/BEAR structure, generic local plans must trade with that direction;
* in MIXED structure, only an accepted break/hold/return plan may transfer
  control; generic rejection, bounce and rotation labels abstain;
* daily/H4 auction families and the separately owned anchored local
  continuation family remain independent and unchanged.

No return threshold, moving average, session score, volatility cutoff or future
bar is used.  Entry, stop, target, execution and risk remain inside the existing
immutable plan and NautilusTrader lifecycle.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, V5TradePlan
from domain import Candle, Side
from easychart_re1_skilled_continuation import (
    EasyChartRE1SkilledContinuationBundle,
)
from structure_v5 import CausalStructureBook


COMPLETED_60M_STRUCTURE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:TWO_LATEST_CAUSALLY_CONFIRMED_SPAN2_60M_"
    "HIGHS_AND_LOWS_DEFINE_THE_AVAILABLE_HIGHER_MARKET_STRUCTURE_DIRECTION"
)
LOCAL_STRUCTURE_ROUTER_RULE = (
    "RESEARCH_HYPOTHESIS:DIRECTIONAL_GENERIC_LOCAL_PLANS_MUST_ALIGN_WITH_"
    "COMPLETED_60M_STRUCTURE_WHILE_MIXED_STRUCTURE_ALLOWS_ONLY_ACCEPTED_"
    "CONTROL_TRANSFER"
)
if COMPLETED_60M_STRUCTURE_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (COMPLETED_60M_STRUCTURE_RULE,)
if LOCAL_STRUCTURE_ROUTER_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (LOCAL_STRUCTURE_ROUTER_RULE,)


class HigherStructureState(str, Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    MIXED = "MIXED"


class EasyChartRE1HigherStructureRouterBundle:
    """Full skilled stream with causal 60m/local state arbitration."""

    HIGHER_AUCTION_SCALES = {
        "DAILY_LIQUIDITY",
        "DAILY_ACCEPTANCE",
        "H4_LIQUIDITY",
        "H4_ACCEPTANCE",
    }
    INDEPENDENT_LOCAL_SCALES = {"LOCAL_CONTINUATION"}

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.base = EasyChartRE1SkilledContinuationBundle(
            symbol,
            tick_size,
            minimum_gross_rr,
        )
        self.structure = CausalStructureBook(
            symbol,
            60,
            tick_size,
            pivot_spans=(2,),
        )
        self.detectors = self.base.detectors
        self._plans: list[V5TradePlan] = []
        self._trace: list[dict[str, Any]] = []
        self._counts: dict[str, int] = {}
        self._state = HigherStructureState.MIXED

    def _inc(self, key: str) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1

    @staticmethod
    def _path(plan: V5TradePlan) -> str:
        return str(getattr(plan.scenario_path, "value", plan.scenario_path))

    def _current_state(self) -> HigherStructureState:
        highs = [pivot for pivot in self.structure.pivots if pivot.side == "HIGH"]
        lows = [pivot for pivot in self.structure.pivots if pivot.side == "LOW"]
        if len(highs) < 2 or len(lows) < 2:
            return HigherStructureState.MIXED
        previous_high, current_high = highs[-2], highs[-1]
        previous_low, current_low = lows[-2], lows[-1]
        if (
            current_high.price > previous_high.price
            and current_low.price > previous_low.price
        ):
            return HigherStructureState.BULL
        if (
            current_high.price < previous_high.price
            and current_low.price < previous_low.price
        ):
            return HigherStructureState.BEAR
        return HigherStructureState.MIXED

    def _state_values(self) -> dict[str, Any]:
        highs = [pivot for pivot in self.structure.pivots if pivot.side == "HIGH"]
        lows = [pivot for pivot in self.structure.pivots if pivot.side == "LOW"]
        return {
            "higher_structure_state": self._state.value,
            "latest_high": None if not highs else highs[-1].price,
            "previous_high": None if len(highs) < 2 else highs[-2].price,
            "latest_low": None if not lows else lows[-1].price,
            "previous_low": None if len(lows) < 2 else lows[-2].price,
            "latest_high_observed_time_ns": (
                None if not highs else highs[-1].observed_time_ns
            ),
            "latest_low_observed_time_ns": (
                None if not lows else lows[-1].observed_time_ns
            ),
        }

    def _is_independent(self, plan: V5TradePlan) -> bool:
        return (
            plan.scale_name in self.HIGHER_AUCTION_SCALES
            or plan.scale_name in self.INDEPENDENT_LOCAL_SCALES
            or plan.higher_timeframe_minutes >= 240
        )

    def _local_allowed(self, plan: V5TradePlan) -> tuple[bool, str]:
        if self._state is HigherStructureState.BULL:
            return (
                plan.side is Side.LONG,
                "ALIGNED_WITH_BULL_60M_STRUCTURE"
                if plan.side is Side.LONG
                else "COUNTER_TO_BULL_60M_STRUCTURE",
            )
        if self._state is HigherStructureState.BEAR:
            return (
                plan.side is Side.SHORT,
                "ALIGNED_WITH_BEAR_60M_STRUCTURE"
                if plan.side is Side.SHORT
                else "COUNTER_TO_BEAR_60M_STRUCTURE",
            )
        accepted = self._path(plan) == ScenarioPath.ACCEPTANCE.value
        return (
            accepted,
            "MIXED_60M_ACCEPTED_CONTROL_TRANSFER"
            if accepted
            else "MIXED_60M_GENERIC_REVERSAL_ABSTENTION",
        )

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes == 60:
            self.structure.on_bar(bar)
            previous = self._state
            self._state = self._current_state()
            if self._state is not previous:
                self._inc(f"state_changed_to_{self._state.value.lower()}")
                self._trace.append(
                    {
                        "scenario_kind": "completed_60m_structure_state_changed",
                        "event_time_ns": bar.ts_close_ns,
                        "symbol": self.symbol,
                        "previous_state": previous.value,
                        **self._state_values(),
                        "rule_provenance": COMPLETED_60M_STRUCTURE_RULE,
                    },
                )

        raw = self.base.on_bar(timeframe_minutes, bar)
        output: list[V5TradePlan] = []
        for plan in raw:
            if self._is_independent(plan):
                self._inc("independent_family_preserved")
                output.append(plan)
                continue
            allowed, reason = self._local_allowed(plan)
            if allowed:
                self._inc("generic_local_plan_allowed")
                self._inc(reason.lower())
                output.append(plan)
                self._trace.append(
                    {
                        "scenario_kind": "generic_local_plan_allowed_by_higher_structure",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "plan_id": plan.plan_id,
                        "side": plan.side.name,
                        "scale_name": plan.scale_name,
                        "scenario_path": self._path(plan),
                        "routing_reason": reason,
                        **self._state_values(),
                        "rule_provenance": LOCAL_STRUCTURE_ROUTER_RULE,
                    },
                )
                continue
            self._inc("generic_local_plan_rejected")
            self._inc(reason.lower())
            self._trace.append(
                {
                    "scenario_kind": "generic_local_plan_rejected_by_higher_structure",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "scale_name": plan.scale_name,
                    "scenario_path": self._path(plan),
                    "routing_reason": reason,
                    **self._state_values(),
                    "rule_provenance": LOCAL_STRUCTURE_ROUTER_RULE,
                },
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
            "higher_structure_router": {
                "counts": dict(sorted(self._counts.items())),
                **self._state_values(),
                "confirmed_60m_pivots": len(self.structure.pivots),
                "rules": (
                    COMPLETED_60M_STRUCTURE_RULE,
                    LOCAL_STRUCTURE_ROUTER_RULE,
                ),
            },
            "base": self.base.diagnostics,
        }


MultiScaleScenarioBundle = EasyChartRE1HigherStructureRouterBundle

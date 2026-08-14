"""Sixty-minute structure with one-minute volume-clock entry for EasyChart RE1.

The proven micro core uses a 15m decision boundary. This independent family asks
the same causal auction question one scale higher without introducing new
indicators or fitted filters:

* confirmed 60m wick trend-line/channel defines the liquidity boundary;
* a completed 15m bar classifies rejection or accepted break;
* exact one-minute Binance aggressor flow accumulates until the first typical
  prior-minute quote-volume bucket is complete;
* reversal requires cumulative opposing aggression absorbed at the boundary;
* accepted break requires cumulative aligned initiative and price progress;
* stop and target are the inherited natural 15m/60m invalidation and first
  meaningful objective, fixed before one full-position entry.

Only flow-triggered MACRO_FLOW plans can reach the account. Visual-only plans
cannot claim an episode. This is a distinct higher-scale opportunity family, not
another condition on the 15m micro core.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Candle
from easychart_re1_flow_micro_core import EasyChartRE1VolumeClockMicroCoreBundle
from easychart_re1_flow_routed import EasyChartRE1FlowRoutedBundle
from easychart_re1_flow_volume_clock import VolumeClockMicroEngine


MACRO_FLOW_CORE_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "CONFIRMED_SIXTY_MINUTE_DIAGONAL_BOUNDARY_PLUS_FIRST_TYPICAL_VOLUME_BUCKET_DEFINES_AN_INDEPENDENT_HIGHER_SCALE_AUCTION_FAMILY"
)
if MACRO_FLOW_CORE_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (MACRO_FLOW_CORE_RULE,)


class EasyChartRE1VolumeClockMacroCoreBundle(EasyChartRE1VolumeClockMicroCoreBundle):
    """Only flow-triggered 60m/15m/1m auction plans are executable."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.macro_flow = VolumeClockMicroEngine(
            symbol,
            tick_size,
            scale_name="MACRO_FLOW",
            higher_minutes=60,
            decision_minutes=15,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["macro_flow"] = 0
        self._macro_flow_counts: dict[str, int] = {}
        self._macro_flow_trace: list[dict[str, Any]] = []

    def _mfinc(self, key: str) -> None:
        self._macro_flow_counts[key] = self._macro_flow_counts.get(key, 0) + 1

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return self.macro_flow.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return self.macro_flow.plans

    def _route_macro_flow(self, plan: V5TradePlan) -> bool:
        if plan.scale_name != "MACRO_FLOW":
            self._mfinc("non_macro_flow_plan_suppressed")
            return False
        if not self._flow_plan(plan):
            self._mfinc("macro_visual_only_plan_suppressed")
            return False
        # Reuse the mechanism-aware router: accepted-break initiative must have
        # causal context, while explicit boundary absorption may own the HTF
        # reversal even before the lagging BOS side changes.
        allowed = EasyChartRE1FlowRoutedBundle._route_plan(self, plan)
        self._mfinc(
            "macro_flow_plan_allowed"
            if allowed
            else "macro_flow_plan_rejected_by_context"
        )
        self._macro_flow_trace.append(
            {
                "scenario_kind": (
                    "macro_flow_plan_allowed"
                    if allowed
                    else "macro_flow_plan_rejected_by_context"
                ),
                "event_time_ns": plan.observed_time_ns,
                "symbol": plan.symbol,
                "plan_id": plan.plan_id,
                "side": plan.side.name,
                "scenario_path": plan.scenario_path,
                "trigger_zone_kind": self._trigger_kind(plan),
                "interaction_time_ns": plan.interaction_time_ns,
                "entry": plan.entry,
                "stop": plan.stop,
                "target": plan.target,
                "gross_rr": plan.gross_rr,
                "allowed": allowed,
                "rule_provenance": MACRO_FLOW_CORE_RULE,
            },
        )
        return allowed

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes in self.detectors:
            self.detectors[timeframe_minutes].on_bar(bar)
        if timeframe_minutes == self.CONTEXT_MINUTES:
            self._update_macro_context(bar)
        elif timeframe_minutes == self.LOCAL_CONTEXT_MINUTES:
            self._update_local_direction(bar)
            self._update_decision_footprints(bar)
        if timeframe_minutes not in {60, 15, 1}:
            return []

        raw = self.macro_flow.on_bar(timeframe_minutes, bar)
        self._sync_audit("macro_flow", self.macro_flow)
        output: list[V5TradePlan] = []
        for plan in sorted(
            raw,
            key=lambda item: (
                item.interaction_time_ns,
                item.observed_time_ns,
                item.plan_id,
            ),
        ):
            if self._duplicate_episode(plan):
                self._mfinc("macro_flow_duplicate_episode_suppressed")
                continue
            if not self._route_macro_flow(plan):
                continue
            self._claim_episode(plan)
            output.append(plan)
        return output

    def drain_trace(self) -> list[dict[str, Any]]:
        output = (
            self.macro_flow.drain_trace()
            + self._bundle_trace
            + self._flow_route_trace
            + self._macro_flow_trace
        )
        self._bundle_trace = []
        self._flow_route_trace = []
        self._macro_flow_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return self.macro_flow.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "macro_flow_family": {
                "counts": dict(sorted(self._macro_flow_counts.items())),
                "entry": self.macro_flow.flow_entry_diagnostics,
                "volume_clock": self.macro_flow.volume_clock_diagnostics,
                "engine": self.macro_flow.diagnostics,
                "rule_provenance": MACRO_FLOW_CORE_RULE,
            },
            "top_down_context_router": {
                "macro_side": "NEUTRAL" if self._macro_side is None else self._macro_side.name,
                "local_side": "NEUTRAL" if self._local_side is None else self._local_side.name,
            },
        }


MultiScaleScenarioBundle = EasyChartRE1VolumeClockMacroCoreBundle

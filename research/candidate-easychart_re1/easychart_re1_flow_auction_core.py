"""Two-mechanism EasyChart RE1 auction core.

The first causal flow experiments separated two useful responsibilities from the
failed broad system:

* a 15-minute trend-line/channel boundary plus a first typical-volume bucket
  captures frequent micro absorption and accepted-break initiative;
* a pre-existing high-quality 15-minute engulfing order block plus an explicit
  liquidity-absorption-response sequence captures an independent institutional
  decision-area reversal.

This module combines only those mechanisms in one account. It does not select
families after seeing outcomes. Each family solves a different market problem:

* MICRO_VOLUME_CLOCK: price is at a diagonal auction boundary now; the first
  normal amount of traded volume decides absorption versus initiative;
* DECISION_OB_SEQUENCE: a previously visible large OB is the location; actual
  opposing aggression must be absorbed and reclaimed before entry.

Decision-area plans are routed first when the same causal episode overlaps a
more generic diagonal label. Visual-only plans and all unrelated families cannot
reserve the account slot. Stops, targets, costs and the one-position account
contract are unchanged.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, V5TradePlan
from domain import Candle
from easychart_re1_flow_micro_core import (
    CHANNEL_FADE_MACRO_ALIGNMENT_RULE,
    MICRO_FLOW_CORE_RULE,
    EasyChartRE1VolumeClockMicroCoreBundle,
)
from easychart_re1_flow_sequence import SequenceFlowDecisionAreaEngine


AUCTION_CORE_ROUTING_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "ONE_ACCOUNT_ROUTES_ONLY_MICRO_VOLUME_CLOCK_AUCTIONS_AND_PREEXISTING_FIFTEEN_MINUTE_OB_ABSORPTION_SEQUENCE_EPISODES"
)
DECISION_OB_SEQUENCE_RESPONSIBILITY_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "HIGH_QUALITY_FIFTEEN_MINUTE_OB_REVERSAL_REQUIRES_EXPLICIT_OPPOSING_AGGRESSION_ABSORPTION_AND_RECLAIM_SEQUENCE"
)
for _rule in (AUCTION_CORE_ROUTING_RULE, DECISION_OB_SEQUENCE_RESPONSIBILITY_RULE):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


class EasyChartRE1FlowAuctionCoreBundle(EasyChartRE1VolumeClockMicroCoreBundle):
    """Micro volume-clock and decision-OB sequence routed as one system."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.decision_area = SequenceFlowDecisionAreaEngine(
            symbol,
            tick_size,
            scale_name="DECISION_AREA_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["decision_area"] = 0
        self._auction_core_counts: dict[str, int] = {}
        self._auction_core_trace: list[dict[str, Any]] = []

    def _acinc(self, key: str) -> None:
        self._auction_core_counts[key] = self._auction_core_counts.get(key, 0) + 1

    def _route_decision_ob_sequence(
        self,
        raw: list[V5TradePlan],
    ) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in sorted(
            raw,
            key=lambda item: (
                item.interaction_time_ns,
                item.observed_time_ns,
                item.plan_id,
            ),
        ):
            if plan.scenario_path != ScenarioPath.REJECTION.value:
                self._acinc("decision_ob_non_rejection_suppressed")
                continue
            if not self._flow_plan(plan):
                self._acinc("decision_ob_visual_only_plan_suppressed")
                continue
            if self._duplicate_episode(plan):
                self._acinc("decision_ob_sequence_duplicate_suppressed")
                continue
            self._claim_episode(plan)
            output.append(plan)
            self._acinc("decision_ob_absorption_sequence_allowed")
            self._auction_core_trace.append(
                {
                    "scenario_kind": "decision_ob_absorption_sequence_allowed",
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
                    "rule_provenance": (
                        AUCTION_CORE_ROUTING_RULE,
                        DECISION_OB_SEQUENCE_RESPONSIBILITY_RULE,
                    ),
                },
            )
        return output

    def _route_micro_volume_clock(
        self,
        raw: list[V5TradePlan],
    ) -> list[V5TradePlan]:
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
                self._acinc("micro_volume_clock_duplicate_suppressed")
                continue
            if not self._route_plan(plan):
                continue
            self._claim_episode(plan)
            output.append(plan)
            self._acinc("micro_volume_clock_allowed")
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        # Preserve the complete closed-bar evidence without invoking any
        # unrelated family. Each timeframe is appended exactly once.
        if timeframe_minutes in self.detectors:
            self.detectors[timeframe_minutes].on_bar(bar)

        # Update each causal market-state book exactly once.
        if timeframe_minutes == self.CONTEXT_MINUTES:
            self._update_macro_context(bar)
            return []
        if timeframe_minutes == self.LOCAL_CONTEXT_MINUTES:
            self._update_local_direction(bar)
            self._update_decision_footprints(bar)
        if timeframe_minutes not in {15, 5, 1}:
            return []

        decision_raw = self.decision_area.on_bar(timeframe_minutes, bar)
        micro_raw = self.micro.on_bar(timeframe_minutes, bar)
        self._sync_audit("decision_area", self.decision_area)
        self._sync_audit("micro", self.micro)

        # The explicit 15m OB owns an overlapping episode before a generic
        # diagonal label, mirroring how a human prioritizes the stronger area.
        decision = self._route_decision_ob_sequence(decision_raw)
        micro = self._route_micro_volume_clock(micro_raw)
        return sorted(
            decision + micro,
            key=lambda item: (
                item.interaction_time_ns,
                -item.higher_timeframe_minutes,
                item.symbol,
                item.plan_id,
            ),
        )

    def drain_trace(self) -> list[dict[str, Any]]:
        output = (
            self.micro.drain_trace()
            + self.decision_area.drain_trace()
            + self._bundle_trace
            + self._flow_route_trace
            + self._micro_core_trace
            + self._auction_core_trace
        )
        self._bundle_trace = []
        self._flow_route_trace = []
        self._micro_core_trace = []
        self._auction_core_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return self.micro.find_zone(zone_id) or self.decision_area.find_zone(zone_id)

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return self.micro.setups + self.decision_area.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return self.micro.plans + self.decision_area.plans

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["flow_auction_core"] = {
            "counts": dict(sorted(self._auction_core_counts.items())),
            "micro": self.micro.flow_entry_diagnostics,
            "micro_volume_clock": self.micro.volume_clock_diagnostics,
            "decision_area": self.decision_area.flow_entry_diagnostics,
            "decision_area_sequence": self.decision_area.flow_sequence_diagnostics,
            "rules": (
                AUCTION_CORE_ROUTING_RULE,
                DECISION_OB_SEQUENCE_RESPONSIBILITY_RULE,
                MICRO_FLOW_CORE_RULE,
                CHANNEL_FADE_MACRO_ALIGNMENT_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1FlowAuctionCoreBundle

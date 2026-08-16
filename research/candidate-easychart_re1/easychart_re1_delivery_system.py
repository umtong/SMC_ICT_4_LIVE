"""One coherent EasyChart RE1 system routed by matching-scale liquidity delivery.

The account no longer treats every locally valid pattern as an independent
binary trade.  A confirmed external-liquidity transfer defines the current
price-delivery responsibility:

* sweep/reclaim rejection engines may trade only in the active delivery
  direction, so an internal trend line, channel edge or decision OB is a
  pullback location rather than a reason to reverse matching-scale order flow;
* a flow-validated five-minute OB/FVG continuation family may arm only while
  that same draw remains active, then enters after its first return and a later
  completed control-transfer response;
* the first pre-existing high-quality one-minute opposing OB/FVG remains the
  full-position objective for rejection trades, while continuation uses its
  inherited first eligible formation-wave or 5m/15m structure objective.

All plans retain one full position, immutable entry/stop/target, at least one
gross R before costs, and the existing global one-position account arbitration.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Candle
from easychart_re1_delivery_draw import (
    CausalLiquidityDraw,
    MATCHING_SCALE_LIQUIDITY_DRAW_RULE,
)
from easychart_re1_persistent_confirmed_fixed import (
    FixedConfirmedPersistentContinuationEngine,
)
from easychart_re1_rejection_micro_target_v2 import (
    EasyChartRE1RejectionMicroTargetV2Bundle,
)


DELIVERY_RESPONSIBILITY_ROUTER_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "LOCAL_REJECTION_AND_REBALANCE_CONTINUATION_ARE_EXECUTABLE_ONLY_IN_THE_ACTIVE_MATCHING_SCALE_LIQUIDITY_DRAW_DIRECTION"
)
if DELIVERY_RESPONSIBILITY_ROUTER_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (DELIVERY_RESPONSIBILITY_ROUTER_RULE,)


class EasyChartRE1DeliverySystemBundle(EasyChartRE1RejectionMicroTargetV2Bundle):
    """Draw-routed rejection core plus one independent continuation family."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.delivery_draw = CausalLiquidityDraw(symbol, tick_size)
        self.delivery_continuation = FixedConfirmedPersistentContinuationEngine(
            symbol,
            tick_size,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["delivery_continuation"] = 0
        self._delivery_counts: dict[str, int] = {}
        self._delivery_trace: list[dict[str, Any]] = []

    def _dinc(self, key: str) -> None:
        self._delivery_counts[key] = self._delivery_counts.get(key, 0) + 1

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return super().setups + self.delivery_continuation.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return super().plans + self.delivery_continuation.plans

    def _route_plan(self, plan: V5TradePlan) -> bool:
        if not super()._route_plan(plan):
            return False
        if self.delivery_draw.allows(plan):
            self._dinc("draw_aligned_rejection_allowed")
            return True
        active = self.delivery_draw.active
        self._dinc("rejection_deferred_without_matching_draw")
        self._delivery_trace.append(
            {
                "scenario_kind": "rejection_deferred_without_matching_draw",
                "event_time_ns": plan.observed_time_ns,
                "symbol": plan.symbol,
                "plan_id": plan.plan_id,
                "side": plan.side.name,
                "scale_name": plan.scale_name,
                "scenario_path": plan.scenario_path,
                "interaction_time_ns": plan.interaction_time_ns,
                "active_draw_side": None if active is None else active.side.name,
                "active_draw_target": None if active is None else active.target_price,
                "rule_provenance": (
                    MATCHING_SCALE_LIQUIDITY_DRAW_RULE,
                    DELIVERY_RESPONSIBILITY_ROUTER_RULE,
                ),
            }
        )
        return False

    def _sync_delivery_audit(self) -> None:
        start = self._audit_offsets["delivery_continuation"]
        for zone in self.delivery_continuation.audit_zones[start:]:
            timeframe = getattr(zone, "timeframe_minutes", 5)
            destination = timeframe if timeframe in self.detectors else 5
            self.detectors[destination].register(zone)
        self._audit_offsets["delivery_continuation"] = len(
            self.delivery_continuation.audit_zones
        )

    def _route_continuation(
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
            if not self.delivery_draw.allows(plan):
                self._dinc("continuation_context_ended_before_plan")
                continue
            if self._duplicate_episode(plan):
                self._dinc("continuation_overlapped_existing_episode")
                continue
            self._claim_episode(plan)
            output.append(plan)
            self._dinc("draw_aligned_continuation_allowed")
            active = self.delivery_draw.active
            self._delivery_trace.append(
                {
                    "scenario_kind": "draw_aligned_continuation_allowed",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "entry": plan.entry,
                    "stop": plan.stop,
                    "target": plan.target,
                    "gross_rr": plan.gross_rr,
                    "draw_target": None if active is None else active.target_price,
                    "rule_provenance": DELIVERY_RESPONSIBILITY_ROUTER_RULE,
                }
            )
        return output

    def on_bar(
        self,
        timeframe_minutes: int,
        bar: Candle,
    ) -> list[V5TradePlan]:
        # State is advanced before either family sees the completed bar.  At a
        # shared timestamp the runner supplies 15m, then 5m, then 1m, so a
        # decision-frame shift can causally authorize only later micro entries.
        self.delivery_draw.on_bar(timeframe_minutes, bar)
        self.delivery_continuation.set_common_auction_snapshot(
            self.delivery_draw.common_snapshot
        )

        continuation: list[V5TradePlan] = []
        if timeframe_minutes in {15, 5, 1}:
            raw = self.delivery_continuation.on_bar(timeframe_minutes, bar)
            self._sync_delivery_audit()
            continuation = self._route_continuation(raw)

        # The continuation footprint owns a coincident causal episode before a
        # generic internal rejection label is considered.
        rejection = super().on_bar(timeframe_minutes, bar)
        return sorted(
            continuation + rejection,
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
            + self.delivery_continuation.drain_trace()
            + self.delivery_draw.drain_trace()
            + self._delivery_trace
        )
        self._delivery_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return (
            super().find_zone(zone_id)
            or self.delivery_continuation.find_zone(zone_id)
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["matching_scale_delivery_system"] = {
            "counts": dict(sorted(self._delivery_counts.items())),
            "draw": self.delivery_draw.diagnostics,
            "continuation": self.delivery_continuation.diagnostics,
            "rules": (
                MATCHING_SCALE_LIQUIDITY_DRAW_RULE,
                DELIVERY_RESPONSIBILITY_ROUTER_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1DeliverySystemBundle

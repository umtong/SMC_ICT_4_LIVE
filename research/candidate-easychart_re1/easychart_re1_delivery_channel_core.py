"""Lean EasyChart RE1 day-trade core: delivery, channel S/R flip and pullback.

The mature-balance implementation generated many mechanically valid but
structurally weak boxes.  It is removed from the executable account rather than
patched with more thresholds.  This core keeps the two mechanisms which have a
clear causal role and already supply approximately day-trading frequency:

1. matching-scale external liquidity is acquired and a still-unspent opposite
   external draw remains; constituent aggressor flow and price impact validate
   accepted initiative or control transfer;
2. inside that delivery, either an accepted 15-minute channel edge is bought or
   sold after next-bar hold and first return, or a flow-validated five-minute
   OB/FVG pullback enters immediately or after its first fresh post-touch
   one-minute internal structure shift;
3. a rare local sweep/reclaim rejection may execute only when it agrees with the
   same active delivery.

Channel acceptance owns a coincident episode before generic rejection.  Pullback
continuation is restricted to the source-side half of external delivery and may
not target through the matching-scale draw.  One full position, immutable
structural stop/first obstacle, >=1 gross R and global single-position account
arbitration are unchanged.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, V5TradePlan
from domain import Candle, Side
from easychart_re1_delivery_channel_acceptance import (
    CHANNEL_ACCEPTANCE_EPISODE_PRIORITY_RULE,
    DRAW_ALIGNED_CHANNEL_ACCEPTANCE_RULE,
    DeliveryChannelAcceptanceEngine,
)
from easychart_re1_delivery_channel_acceptance_v3 import (
    EXTERNAL_DRAW_TARGET_CAP_RULE,
    PROXIMAL_DELIVERY_CONTINUATION_RULE,
)
from easychart_re1_delivery_continuation_cisd import (
    DeliveryContinuationCISDEngine,
    FIRST_INTERNAL_SHIFT_CROSS_RULE,
    POST_TOUCH_INTERNAL_SHIFT_RULE,
)
from easychart_re1_delivery_draw_v6 import (
    FlowValidatedLiquidityDrawV6,
    MECHANISM_SPECIFIC_ACTIVE_FLOW_RULE,
)
from easychart_re1_delivery_system_v3 import (
    EasyChartRE1DeliverySystemV3Bundle,
)
from easychart_re1_rejection_micro_target_v2 import (
    EasyChartRE1RejectionMicroTargetV2Bundle,
)


MATURE_BALANCE_DEFERRED_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "MATURE_BALANCE_REMAINS_DIAGNOSTIC_UNTIL_A_CANONICAL_BOX_REPRESENTATION_NO_LONGER_OVERTRADES_NESTED_DEFENSE_LABELS"
)
if MATURE_BALANCE_DEFERRED_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (MATURE_BALANCE_DEFERRED_RULE,)


class EasyChartRE1DeliveryChannelCoreBundle(
    EasyChartRE1DeliverySystemV3Bundle,
):
    """One executable account stream without the weak balance-box family."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.delivery_draw = FlowValidatedLiquidityDrawV6(symbol, tick_size)
        self.delivery_continuation = DeliveryContinuationCISDEngine(
            symbol,
            tick_size,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.delivery_channel_acceptance = DeliveryChannelAcceptanceEngine(
            symbol,
            tick_size,
            scale_name="DELIVERY_CHANNEL_ACCEPTANCE",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["delivery_continuation"] = 0
        self._audit_offsets["delivery_channel_acceptance"] = 0
        self._channel_acceptance_counts: dict[str, int] = {}
        self._channel_acceptance_trace: list[dict[str, Any]] = []

    def _cainc(self, key: str) -> None:
        self._channel_acceptance_counts[key] = (
            self._channel_acceptance_counts.get(key, 0) + 1
        )

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return super().setups + self.delivery_channel_acceptance.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return super().plans + self.delivery_channel_acceptance.plans

    @staticmethod
    def _kind_text(value: Any) -> str:
        return str(getattr(value, "value", value)).upper()

    @classmethod
    def _is_channel_acceptance(cls, plan: V5TradePlan) -> bool:
        if plan.scenario_path != ScenarioPath.ACCEPTANCE.value:
            return False
        values = (
            cls._kind_text(plan.higher_zone_kind),
            cls._kind_text(plan.lower_zone_kind),
            plan.higher_zone_id.upper(),
            plan.lower_zone_id.upper(),
        )
        return any("CHANNEL" in item for item in values)

    def _sync_channel_acceptance_audit(self) -> None:
        start = self._audit_offsets["delivery_channel_acceptance"]
        for zone in self.delivery_channel_acceptance.audit_zones[start:]:
            timeframe = getattr(zone, "timeframe_minutes", 15)
            destination = timeframe if timeframe in self.detectors else 15
            self.detectors[destination].register(zone)
        self._audit_offsets["delivery_channel_acceptance"] = len(
            self.delivery_channel_acceptance.audit_zones
        )

    def _route_channel_acceptance(
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
            if not self._is_channel_acceptance(plan):
                self._cainc("non_channel_acceptance_suppressed")
                continue
            if not self.delivery_draw.allows(plan):
                self._cainc("channel_acceptance_without_active_delivery")
                continue
            if self._duplicate_episode(plan):
                self._cainc("channel_acceptance_overlapped_executable_episode")
                continue
            self._claim_episode(plan)
            output.append(plan)
            self._cainc("draw_aligned_channel_acceptance_allowed")
            active = self.delivery_draw.active
            self._channel_acceptance_trace.append(
                {
                    "scenario_kind": "draw_aligned_channel_acceptance_allowed",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "interaction_time_ns": plan.interaction_time_ns,
                    "entry": plan.entry,
                    "stop": plan.stop,
                    "target": plan.target,
                    "gross_rr": plan.gross_rr,
                    "delivery_source": None
                    if active is None
                    else active.source_pivot_price,
                    "delivery_target": None
                    if active is None
                    else active.target_price,
                    "rule_provenance": (
                        DRAW_ALIGNED_CHANNEL_ACCEPTANCE_RULE,
                        CHANNEL_ACCEPTANCE_EPISODE_PRIORITY_RULE,
                    ),
                }
            )
        return output

    def _route_continuation(
        self,
        raw: list[V5TradePlan],
    ) -> list[V5TradePlan]:
        active = self.delivery_draw.active
        if active is None:
            self._dinc("continuation_without_active_delivery")
            return []
        midpoint = (active.source_pivot_price + active.target_price) / 2.0
        eligible: list[V5TradePlan] = []
        for plan in raw:
            proximal = (
                plan.entry <= midpoint
                if plan.side is Side.LONG
                else plan.entry >= midpoint
            )
            target_within_draw = (
                plan.target <= active.target_price
                if plan.side is Side.LONG
                else plan.target >= active.target_price
            )
            if not proximal:
                self._dinc("continuation_deferred_past_delivery_midpoint")
                self._delivery_trace.append(
                    {
                        "scenario_kind": "continuation_deferred_past_delivery_midpoint",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "plan_id": plan.plan_id,
                        "side": plan.side.name,
                        "entry": plan.entry,
                        "delivery_source": active.source_pivot_price,
                        "delivery_midpoint": midpoint,
                        "delivery_target": active.target_price,
                        "rule_provenance": PROXIMAL_DELIVERY_CONTINUATION_RULE,
                    }
                )
                continue
            if not target_within_draw:
                self._dinc("continuation_target_beyond_external_draw")
                continue
            eligible.append(plan)
        return super()._route_continuation(eligible)

    def on_bar(
        self,
        timeframe_minutes: int,
        bar: Candle,
    ) -> list[V5TradePlan]:
        # One state advance per completed bar.  On one-minute bars this first
        # terminates any delivery whose draw or invalidation traded intrabar.
        self.delivery_draw.on_bar(timeframe_minutes, bar)
        self.delivery_continuation.set_common_auction_snapshot(
            self.delivery_draw.common_snapshot
        )

        channel: list[V5TradePlan] = []
        raw_channel = self.delivery_channel_acceptance.on_bar(
            timeframe_minutes,
            bar,
        )
        self._sync_channel_acceptance_audit()
        channel = self._route_channel_acceptance(raw_channel)

        raw_continuation = self.delivery_continuation.on_bar(
            timeframe_minutes,
            bar,
        )
        self._sync_delivery_audit()
        continuation = self._route_continuation(raw_continuation)

        # Bypass the delivery wrapper to avoid a second draw advance.  Dynamic
        # routing still limits generic rejection to the active draw direction.
        rejection = EasyChartRE1RejectionMicroTargetV2Bundle.on_bar(
            self,
            timeframe_minutes,
            bar,
        )
        return sorted(
            channel + continuation + rejection,
            key=lambda plan: (
                plan.interaction_time_ns,
                -plan.higher_timeframe_minutes,
                plan.symbol,
                plan.plan_id,
            ),
        )

    def drain_trace(self) -> list[dict[str, Any]]:
        output = (
            EasyChartRE1RejectionMicroTargetV2Bundle.drain_trace(self)
            + self.delivery_channel_acceptance.drain_trace()
            + self.delivery_continuation.drain_trace()
            + self.delivery_draw.drain_trace()
            + self._delivery_trace
            + self._channel_acceptance_trace
        )
        self._delivery_trace = []
        self._channel_acceptance_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return (
            EasyChartRE1RejectionMicroTargetV2Bundle.find_zone(self, zone_id)
            or self.delivery_channel_acceptance.find_zone(zone_id)
            or self.delivery_continuation.find_zone(zone_id)
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["lean_delivery_channel_core"] = {
            "channel_acceptance_counts": dict(
                sorted(self._channel_acceptance_counts.items())
            ),
            "channel_acceptance": self.delivery_channel_acceptance.diagnostics,
            "mature_balance_executable": False,
            "rules": (
                MECHANISM_SPECIFIC_ACTIVE_FLOW_RULE,
                DRAW_ALIGNED_CHANNEL_ACCEPTANCE_RULE,
                PROXIMAL_DELIVERY_CONTINUATION_RULE,
                EXTERNAL_DRAW_TARGET_CAP_RULE,
                POST_TOUCH_INTERNAL_SHIFT_RULE,
                FIRST_INTERNAL_SHIFT_CROSS_RULE,
                MATURE_BALANCE_DEFERRED_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1DeliveryChannelCoreBundle

"""Lean core with distinct accepted-break and sweep-pullback responsibilities.

An accepted external break is already a directional continuation event.  Adding
a generic five-minute OB/FVG pullback family inside the same event duplicated
that responsibility and produced late, weak entries.  Accepted-break execution
belongs to the structurally explicit channel S/R-flip family.  The generic
rebalance continuation family is reserved for delivery born from an external
sweep and confirmed control transfer.

This is a mechanism split, not a fitted filter: channel acceptance remains
available in every active delivery, while OB/FVG continuation requires a sweep-
based source mode, proximal location, complete first obstacle and immediate-or-
post-touch internal structure shift.
"""
from __future__ import annotations

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Candle
from easychart_re1_delivery_channel_core import (
    EasyChartRE1DeliveryChannelCoreBundle,
)
from easychart_re1_rejection_micro_target_v2 import (
    EasyChartRE1RejectionMicroTargetV2Bundle,
)


ACCEPTED_BREAK_EXECUTION_RESPONSIBILITY_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "ACCEPTED_EXTERNAL_BREAK_EXECUTION_BELONGS_TO_STRUCTURAL_CHANNEL_SR_FLIP_WHILE_GENERIC_OB_FVG_REBALANCE_CONTINUATION_IS_RESERVED_FOR_SWEEP_BORN_CONTROL_TRANSFER"
)
if ACCEPTED_BREAK_EXECUTION_RESPONSIBILITY_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (
        ACCEPTED_BREAK_EXECUTION_RESPONSIBILITY_RULE,
    )


class EasyChartRE1DeliveryChannelCoreV2Bundle(
    EasyChartRE1DeliveryChannelCoreBundle,
):
    def _route_continuation(
        self,
        raw: list[V5TradePlan],
    ) -> list[V5TradePlan]:
        active = self.delivery_draw.active
        if active is None:
            return super()._route_continuation(raw)
        if active.source_mode == "EXTERNAL_ACCEPTANCE_HELD":
            for plan in raw:
                self._dinc("accepted_break_continuation_deferred_to_channel_sr_flip")
                self._delivery_trace.append(
                    {
                        "scenario_kind": "accepted_break_continuation_deferred_to_channel_sr_flip",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "plan_id": plan.plan_id,
                        "side": plan.side.name,
                        "entry": plan.entry,
                        "delivery_source": active.source_pivot_price,
                        "delivery_target": active.target_price,
                        "rule_provenance": ACCEPTED_BREAK_EXECUTION_RESPONSIBILITY_RULE,
                    }
                )
            return []
        return super()._route_continuation(raw)

    def on_bar(
        self,
        timeframe_minutes: int,
        bar: Candle,
    ) -> list[V5TradePlan]:
        # The account also carries a 60-minute context stream.  Dedicated
        # 15/5/1 engines must not receive unsupported frames, while the shared
        # rejection bundle retains its own 60-minute context handling.
        self.delivery_draw.on_bar(timeframe_minutes, bar)
        self.delivery_continuation.set_common_auction_snapshot(
            self.delivery_draw.common_snapshot
        )

        channel: list[V5TradePlan] = []
        continuation: list[V5TradePlan] = []
        if timeframe_minutes in {15, 5, 1}:
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

    @property
    def diagnostics(self):  # type: ignore[no-untyped-def]
        output = dict(super().diagnostics)
        output["accepted_break_execution_responsibility"] = {
            "accepted_external_break": "CHANNEL_SR_FLIP_ONLY",
            "sweep_born_delivery": "OB_FVG_REBALANCE_CONTINUATION_AVAILABLE",
            "rule_provenance": ACCEPTED_BREAK_EXECUTION_RESPONSIBILITY_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1DeliveryChannelCoreV2Bundle

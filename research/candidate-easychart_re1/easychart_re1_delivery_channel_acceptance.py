"""Draw-aligned accepted channel break and first retest for EasyChart RE1.

The strongest executable acceptance evidence in the existing diagnostics came
from channel S/R flips, while isolated trend-line acceptance and generic
continuation remained unstable.  This family gives the source-defined channel
breakout one precise responsibility inside the matching-scale delivery system:

* a completed 15-minute channel already exists from ordered wick pivots;
* a five-minute body closes outside its projected edge and the next completed
  five-minute bar holds outside;
* entry occurs only on the inherited first detached one-minute return/response;
* the plan side must agree with an already active flow-impact-validated external
  liquidity draw whose target remains unspent;
* the first pre-existing untouched high-quality one-minute OB/FVG, nearer
  5m/15m structure, or first channel-extension midpoint owns the full target.

The dedicated engine is evaluated before the generic rejection core at each
completed bar.  Thus a valid channel S/R flip owns its causal episode instead of
being mislabeled as a local rejection.  No RR cap, fitted distance, clock
expiry, session rule, partial exit or moving stop is introduced.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, V5TradePlan
from domain import Candle
from easychart_re1_delivery_balance_system_v5 import (
    EasyChartRE1DeliveryBalanceSystemV5Bundle,
)
from easychart_re1_rejection_micro_target_v2 import (
    EasyChartRE1RejectionMicroTargetV2Bundle,
    FixedRejectionTargetMicroEngine,
)


DRAW_ALIGNED_CHANNEL_ACCEPTANCE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "AN_ACCEPTED_CHANNEL_BREAK_IS_EXECUTABLE_ONLY_AFTER_NEXT_BAR_HOLD_FIRST_DETACHED_RETURN_AND_ALIGNMENT_WITH_ACTIVE_MATCHING_SCALE_LIQUIDITY_DELIVERY"
)
CHANNEL_ACCEPTANCE_EPISODE_PRIORITY_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "A_DRAW_ALIGNED_CHANNEL_SR_FLIP_OWNS_A_COINCIDENT_LOCAL_EPISODE_BEFORE_GENERIC_REJECTION_LABELS"
)
for _rule in (
    DRAW_ALIGNED_CHANNEL_ACCEPTANCE_RULE,
    CHANNEL_ACCEPTANCE_EPISODE_PRIORITY_RULE,
):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


class DeliveryChannelAcceptanceEngine(FixedRejectionTargetMicroEngine):
    """Existing causal channel geometry with its own acceptance responsibility."""


class EasyChartRE1DeliveryChannelAcceptanceBundle(
    EasyChartRE1DeliveryBalanceSystemV5Bundle,
):
    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.delivery_channel_acceptance = DeliveryChannelAcceptanceEngine(
            symbol,
            tick_size,
            scale_name="DELIVERY_CHANNEL_ACCEPTANCE",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
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
        kinds = (
            cls._kind_text(plan.higher_zone_kind),
            cls._kind_text(plan.lower_zone_kind),
        )
        ids = (plan.higher_zone_id.upper(), plan.lower_zone_id.upper())
        return any("CHANNEL" in item for item in kinds + ids)

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
                self._cainc("channel_acceptance_without_active_draw")
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
                    "channel_zone_id": plan.higher_zone_id,
                    "draw_source": None
                    if active is None
                    else active.source_pivot_id,
                    "draw_target": None
                    if active is None
                    else active.target_price,
                    "rule_provenance": (
                        DRAW_ALIGNED_CHANNEL_ACCEPTANCE_RULE,
                        CHANNEL_ACCEPTANCE_EPISODE_PRIORITY_RULE,
                    ),
                }
            )
        return output

    def on_bar(
        self,
        timeframe_minutes: int,
        bar: Candle,
    ) -> list[V5TradePlan]:
        # Advance delivery exactly once.  On a one-minute close this also ends
        # a state whose target or invalidation traded during the current bar,
        # so that bar can never use stale delivery permission.
        self.delivery_draw.on_bar(timeframe_minutes, bar)
        self.delivery_continuation.set_common_auction_snapshot(
            self.delivery_draw.common_snapshot
        )

        channel: list[V5TradePlan] = []
        if timeframe_minutes in {15, 5, 1}:
            raw_channel = self.delivery_channel_acceptance.on_bar(
                timeframe_minutes,
                bar,
            )
            self._sync_channel_acceptance_audit()
            channel = self._route_channel_acceptance(raw_channel)

        continuation: list[V5TradePlan] = []
        if timeframe_minutes in {15, 5, 1}:
            raw_continuation = self.delivery_continuation.on_bar(
                timeframe_minutes,
                bar,
            )
            self._sync_delivery_audit()
            continuation = self._route_continuation(raw_continuation)

        # Call the rejection-core bar router directly so delivery is not
        # advanced a second time.  Dynamic _route_plan and _claim_episode still
        # apply the current delivery policy.
        rejection = EasyChartRE1RejectionMicroTargetV2Bundle.on_bar(
            self,
            timeframe_minutes,
            bar,
        )

        self.mature_balance.set_directional_draw(
            self.delivery_draw.active is not None,
            bar.ts_close_ns,
        )
        balance: list[V5TradePlan] = []
        if timeframe_minutes in {5, 1}:
            raw_balance = self.mature_balance.on_bar(timeframe_minutes, bar)
            self._sync_balance_audit()
            balance = self._route_balance(raw_balance)

        return sorted(
            channel + continuation + rejection + balance,
            key=lambda plan: (
                plan.interaction_time_ns,
                -plan.higher_timeframe_minutes,
                plan.symbol,
                plan.plan_id,
            ),
        )

    def drain_trace(self) -> list[dict[str, Any]]:
        # Do not call the delivery-system wrapper because this class owns the
        # draw/continuation drain after bypassing its on_bar wrapper.
        output = (
            EasyChartRE1RejectionMicroTargetV2Bundle.drain_trace(self)
            + self.delivery_channel_acceptance.drain_trace()
            + self.delivery_continuation.drain_trace()
            + self.delivery_draw.drain_trace()
            + self.mature_balance.drain_trace()
            + self._delivery_trace
            + self._balance_trace
            + self._channel_acceptance_trace
        )
        self._delivery_trace = []
        self._balance_trace = []
        self._channel_acceptance_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return (
            EasyChartRE1RejectionMicroTargetV2Bundle.find_zone(self, zone_id)
            or self.delivery_channel_acceptance.find_zone(zone_id)
            or self.delivery_continuation.find_zone(zone_id)
            or self.mature_balance.find_zone(zone_id)
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["draw_aligned_channel_acceptance"] = {
            "counts": dict(sorted(self._channel_acceptance_counts.items())),
            "engine": self.delivery_channel_acceptance.diagnostics,
            "rules": (
                DRAW_ALIGNED_CHANNEL_ACCEPTANCE_RULE,
                CHANNEL_ACCEPTANCE_EPISODE_PRIORITY_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1DeliveryChannelAcceptanceBundle

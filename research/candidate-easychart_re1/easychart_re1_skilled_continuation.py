"""Full skilled opportunity router with nested anchored pullback continuation.

Higher daily/H4 auctions keep their established ownership.  The local
continuation family owns a nested 5m initiative, its anchored fair-value/source
OB pullback and first response.  Generic local structure labels at the same
price episode are suppressed while that specific continuation is pending or on
its terminal response bar.  Distinct causal locations remain independent and
are arbitrated by the one global account slot.
"""
from __future__ import annotations

from typing import Any

from contracts_v5 import V5TradePlan
from domain import Candle
from easychart_re1_local_continuation import LocalAuctionContinuationEngine
from easychart_re1_skilled_h4_auction import EasyChartRE1SkilledH4AuctionBundle


class EasyChartRE1SkilledContinuationBundle:
    HIGHER_AUCTION_SCALES = {
        "DAILY_LIQUIDITY",
        "DAILY_ACCEPTANCE",
        "H4_LIQUIDITY",
        "H4_ACCEPTANCE",
    }

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.base = EasyChartRE1SkilledH4AuctionBundle(
            symbol,
            tick_size,
            minimum_gross_rr,
        )
        self.continuation = LocalAuctionContinuationEngine(
            symbol,
            tick_size,
            minimum_gross_rr,
        )
        self.detectors = self.base.detectors
        self._plans: list[V5TradePlan] = []
        self._trace: list[dict[str, Any]] = []
        self._counts: dict[str, int] = {}

    def _inc(self, key: str) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1

    def _overlap_prices(
        self,
        plan: V5TradePlan,
        lower: float,
        upper: float,
    ) -> bool:
        return (
            max(plan.overlap_lower, lower)
            <= min(plan.overlap_upper, upper) + self.tick_size
        )

    def _same_episode(self, left: V5TradePlan, right: V5TradePlan) -> bool:
        return (
            left.interaction_time_ns == right.interaction_time_ns
            and self._overlap_prices(left, right.overlap_lower, right.overlap_upper)
        )

    @classmethod
    def _higher(cls, plan: V5TradePlan) -> bool:
        return plan.scale_name in cls.HIGHER_AUCTION_SCALES

    @staticmethod
    def _continuation(plan: V5TradePlan) -> bool:
        return plan.scale_name == "LOCAL_CONTINUATION"

    def _continuation_context_owns(self, plan: V5TradePlan, setup: Any) -> bool:
        if setup is None or plan.observed_time_ns < setup.impulse_time_ns:
            return False
        return self._overlap_prices(
            plan,
            setup.source_zone.lower,
            setup.source_zone.upper,
        )

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        continuation_before = self.continuation._active
        continuation_raw = self.continuation.on_bar(timeframe_minutes, bar)
        continuation_resolved = (
            continuation_before
            if continuation_before is not None
            and self.continuation._active is not continuation_before
            else None
        )
        base_raw = self.base.on_bar(timeframe_minutes, bar)

        higher = [plan for plan in base_raw if self._higher(plan)]
        local = [plan for plan in base_raw if not self._higher(plan)]
        routed: list[V5TradePlan] = list(higher)

        for plan in continuation_raw:
            owner = next(
                (existing for existing in higher if self._same_episode(plan, existing)),
                None,
            )
            if owner is not None:
                self._inc("local_continuation_suppressed_by_higher_auction")
                self._trace.append(
                    {
                        "scenario_kind": "local_continuation_owned_by_higher_auction",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "suppressed_plan_id": plan.plan_id,
                        "owner_plan_id": owner.plan_id,
                    },
                )
                continue
            routed.append(plan)

        owners = list(routed)
        for plan in local:
            owner = next(
                (existing for existing in owners if self._same_episode(plan, existing)),
                None,
            )
            if owner is not None:
                self._inc("generic_local_plan_suppressed_by_specific_owner")
                self._trace.append(
                    {
                        "scenario_kind": "generic_local_episode_owned_by_specific_auction",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "suppressed_plan_id": plan.plan_id,
                        "owner_plan_id": owner.plan_id,
                    },
                )
                continue
            pending = self.continuation._active
            if self._continuation_context_owns(plan, pending):
                self._inc("generic_local_plan_suppressed_during_pending_continuation")
                self._trace.append(
                    {
                        "scenario_kind": "generic_local_episode_owned_by_pending_continuation",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "suppressed_plan_id": plan.plan_id,
                        "continuation_setup_id": pending.setup_id,
                        "impulse_time_ns": pending.impulse_time_ns,
                    },
                )
                continue
            if (
                continuation_resolved is not None
                and plan.observed_time_ns == bar.ts_close_ns
                and self._continuation_context_owns(plan, continuation_resolved)
            ):
                self._inc("generic_local_plan_suppressed_on_continuation_terminal_bar")
                self._trace.append(
                    {
                        "scenario_kind": "generic_local_episode_owned_by_terminal_continuation",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "suppressed_plan_id": plan.plan_id,
                        "continuation_setup_id": continuation_resolved.setup_id,
                        "continuation_terminal_reason": continuation_resolved.terminal_reason,
                    },
                )
                continue
            routed.append(plan)

        unique = {plan.plan_id: plan for plan in routed}
        output = sorted(
            unique.values(),
            key=lambda plan: (
                plan.interaction_time_ns,
                0
                if self._higher(plan)
                else 1
                if self._continuation(plan)
                else 2,
                plan.observed_time_ns,
                plan.symbol,
                plan.plan_id,
            ),
        )
        self._plans.extend(output)
        return output

    def drain_trace(self) -> list[dict[str, Any]]:
        output = self.base.drain_trace() + self.continuation.drain_trace() + self._trace
        self._trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return self.base.find_zone(zone_id) or self.continuation.find_zone(zone_id)

    @property
    def plans(self) -> list[V5TradePlan]:
        return list(self._plans)

    @property
    def setups(self) -> list[Any]:
        # The legacy result writer summarizes standard ScenarioSetup.state enums.
        # Daily/H4/local-continuation machines expose their own richer lifecycle
        # counts in diagnostics and intentionally use dedicated setup contracts.
        values = list(self.base.setups) + list(self.continuation.setups)
        return [
            setup
            for setup in values
            if hasattr(getattr(setup, "state", None), "value")
        ]

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "skilled_continuation_router": {
                "counts": dict(sorted(self._counts.items())),
                "priority": (
                    "DAILY_OR_H4_AUCTION",
                    "NESTED_LOCAL_CONTINUATION",
                    "GENERIC_LOCAL_STRUCTURE",
                ),
            },
            "base": self.base.diagnostics,
            "local_continuation": self.continuation.diagnostics,
        }


MultiScaleScenarioBundle = EasyChartRE1SkilledContinuationBundle

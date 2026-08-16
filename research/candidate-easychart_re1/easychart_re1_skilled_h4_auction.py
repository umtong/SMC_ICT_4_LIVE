"""Complete previous-H4 auction router: rejection and accepted continuation.

The previous-H4 extreme can end in only one of two executable states.  A sweep
and close back through the boundary belongs to the rejection engine.  A body
break, required next-bar hold, first return and first response belongs to the
acceptance engine.  Both are independent of the local structure families and
are evaluated under the same single-account constraint.

Previous-day auctions keep highest ownership.  A pending or just-resolved H4
acceptance owns an overlapping local/H4-rejection label because accepting and
rejecting the same completed boundary on the same causal episode is incoherent.
Different boundaries remain independent opportunities.
"""
from __future__ import annotations

from typing import Any

from contracts_v5 import V5TradePlan
from domain import Candle
from easychart_re1_h4_acceptance import H4AcceptanceEngine
from easychart_re1_skilled_h4 import EasyChartRE1SkilledH4Bundle


class EasyChartRE1SkilledH4AuctionBundle:
    DAILY_SCALES = {"DAILY_LIQUIDITY", "DAILY_ACCEPTANCE"}

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.base = EasyChartRE1SkilledH4Bundle(
            symbol,
            tick_size,
            minimum_gross_rr,
        )
        self.acceptance = H4AcceptanceEngine(
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
    def _daily(cls, plan: V5TradePlan) -> bool:
        return plan.scale_name in cls.DAILY_SCALES

    @staticmethod
    def _h4_acceptance(plan: V5TradePlan) -> bool:
        return plan.scale_name == "H4_ACCEPTANCE"

    @staticmethod
    def _h4_rejection(plan: V5TradePlan) -> bool:
        return plan.scale_name == "H4_LIQUIDITY"

    def _acceptance_context_owns(self, plan: V5TradePlan, setup: Any) -> bool:
        if setup is None or plan.observed_time_ns < setup.break_time_ns:
            return False
        return self._overlap_prices(
            plan,
            setup.level_zone.lower,
            setup.level_zone.upper,
        )

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        acceptance_before = self.acceptance._active
        acceptance_raw = self.acceptance.on_bar(timeframe_minutes, bar)
        acceptance_resolved = (
            acceptance_before
            if acceptance_before is not None
            and self.acceptance._active is not acceptance_before
            else None
        )
        base_raw = self.base.on_bar(timeframe_minutes, bar)

        daily = [plan for plan in base_raw if self._daily(plan)]
        other = [plan for plan in base_raw if not self._daily(plan)]
        routed: list[V5TradePlan] = list(daily)

        for plan in acceptance_raw:
            owner = next(
                (existing for existing in daily if self._same_episode(plan, existing)),
                None,
            )
            if owner is not None:
                self._inc("h4_acceptance_suppressed_by_previous_day_auction")
                self._trace.append(
                    {
                        "scenario_kind": "h4_acceptance_owned_by_previous_day_auction",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "suppressed_plan_id": plan.plan_id,
                        "owner_plan_id": owner.plan_id,
                    },
                )
                continue
            routed.append(plan)

        higher = list(routed)
        for plan in other:
            owner = next(
                (existing for existing in higher if self._same_episode(plan, existing)),
                None,
            )
            if owner is not None:
                self._inc("base_plan_suppressed_by_higher_auction")
                self._trace.append(
                    {
                        "scenario_kind": "base_episode_owned_by_higher_auction",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "suppressed_plan_id": plan.plan_id,
                        "owner_plan_id": owner.plan_id,
                    },
                )
                continue
            pending = self.acceptance._active
            if self._acceptance_context_owns(plan, pending):
                self._inc("base_plan_suppressed_during_pending_h4_acceptance")
                self._trace.append(
                    {
                        "scenario_kind": "base_episode_owned_by_pending_h4_acceptance",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "suppressed_plan_id": plan.plan_id,
                        "h4_acceptance_setup_id": pending.setup_id,
                        "h4_break_time_ns": pending.break_time_ns,
                    },
                )
                continue
            if (
                acceptance_resolved is not None
                and plan.observed_time_ns == bar.ts_close_ns
                and self._acceptance_context_owns(plan, acceptance_resolved)
            ):
                self._inc("base_plan_suppressed_on_resolved_h4_acceptance_bar")
                self._trace.append(
                    {
                        "scenario_kind": "base_episode_owned_by_resolved_h4_acceptance",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "suppressed_plan_id": plan.plan_id,
                        "h4_acceptance_setup_id": acceptance_resolved.setup_id,
                        "h4_terminal_reason": acceptance_resolved.terminal_reason,
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
                if self._daily(plan)
                else 1
                if self._h4_acceptance(plan)
                else 2
                if self._h4_rejection(plan)
                else 3,
                plan.observed_time_ns,
                plan.symbol,
                plan.plan_id,
            ),
        )
        self._plans.extend(output)
        return output

    def drain_trace(self) -> list[dict[str, Any]]:
        output = self.base.drain_trace() + self.acceptance.drain_trace() + self._trace
        self._trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return self.base.find_zone(zone_id) or self.acceptance.find_zone(zone_id)

    @property
    def plans(self) -> list[V5TradePlan]:
        return list(self._plans)

    @property
    def setups(self) -> list[Any]:
        return list(self.base.setups) + list(self.acceptance.setups)

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "skilled_h4_auction_router": {
                "counts": dict(sorted(self._counts.items())),
                "priority": (
                    "PREVIOUS_DAY_AUCTION",
                    "H4_ACCEPTANCE",
                    "H4_REJECTION",
                    "LOCAL_STRUCTURE",
                ),
            },
            "base": self.base.diagnostics,
            "h4_acceptance": self.acceptance.diagnostics,
        }


MultiScaleScenarioBundle = EasyChartRE1SkilledH4AuctionBundle

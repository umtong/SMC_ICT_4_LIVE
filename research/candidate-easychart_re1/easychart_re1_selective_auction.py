"""Accepted-control auction routers after rejecting generic H4 sweep repetition.

The immediately preceding H4 high/low is not automatically a meaningful
liquidity event.  The broad H4 sweep family repeatedly traded every completed
auction edge and dominated the account with near-identical reversal attempts.
This module removes that family categorically instead of adding another fitted
threshold.

Two executable opportunity sets are kept separate for matched diagnosis:

* ``SelectiveAuction``: previous-day sweep/reclaim, completed-H4 accepted
  control transfer, and the response-confirmed local mechanisms;
* ``AcceptedControl``: completed-H4 accepted control transfer and the local
  mechanisms, with previous-day reversal removed as well.

An accepted H4 break/hold/return owns an overlapping local label while pending
or on its terminal response bar.  Different causal locations remain independent
and are arbitrated by the one global account slot.  Entry, stop, objective,
risk, fees and execution are unchanged.
"""
from __future__ import annotations

from typing import Any, Type

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Candle
from easychart_re1_h4_acceptance import H4AcceptanceEngine
from easychart_re1_skilled_daily import EasyChartRE1SkilledDailyBundle
from easychart_re1_skilled_integrated import EasyChartRE1SkilledIntegratedBundle


H4_REJECTION_FAMILY_RETIREMENT_RULE = (
    "RESEARCH_SYNTHESIS:THE_IMMEDIATELY_PRECEDING_H4_EXTREME_IS_NOT_BY_ITSELF_"
    "A_DISTINCT_SIGNIFICANT_LIQUIDITY_EVENT_SO_GENERIC_H4_SWEEP_REJECTION_IS_"
    "RETIRED_WHILE_ACCEPTED_CONTROL_TRANSFER_REMAINS_EXECUTABLE"
)
ACCEPTED_CONTROL_OWNERSHIP_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:A_PENDING_OR_COMPLETED_H4_ACCEPTANCE_"
    "BREAK_HOLD_RETURN_OWNS_AN_OVERLAPPING_LOCAL_LABEL_WHILE_DISTINCT_PRICE_"
    "LOCATIONS_REMAIN_INDEPENDENT"
)
for _rule in (
    H4_REJECTION_FAMILY_RETIREMENT_RULE,
    ACCEPTED_CONTROL_OWNERSHIP_RULE,
):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


class _AcceptedControlRouter:
    """Common categorical router for H4 acceptance plus a chosen local base."""

    BASE_CLASS: Type[Any] = EasyChartRE1SkilledIntegratedBundle
    DAILY_SCALES: frozenset[str] = frozenset()
    POLICY_NAME = "ACCEPTED_CONTROL"

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.base = self.BASE_CLASS(symbol, tick_size, minimum_gross_rr)
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

    def _daily(self, plan: V5TradePlan) -> bool:
        return plan.scale_name in self.DAILY_SCALES

    @staticmethod
    def _h4_acceptance(plan: V5TradePlan) -> bool:
        return plan.scale_name == "H4_ACCEPTANCE"

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
        local = [plan for plan in base_raw if not self._daily(plan)]
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
        for plan in local:
            owner = next(
                (existing for existing in higher if self._same_episode(plan, existing)),
                None,
            )
            if owner is not None:
                self._inc("local_plan_suppressed_by_higher_auction")
                self._trace.append(
                    {
                        "scenario_kind": "local_episode_owned_by_higher_auction",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "suppressed_plan_id": plan.plan_id,
                        "owner_plan_id": owner.plan_id,
                    },
                )
                continue
            pending = self.acceptance._active
            if self._acceptance_context_owns(plan, pending):
                self._inc("local_plan_suppressed_during_pending_h4_acceptance")
                self._trace.append(
                    {
                        "scenario_kind": "local_episode_owned_by_pending_h4_acceptance",
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
                self._inc("local_plan_suppressed_on_h4_acceptance_terminal_bar")
                self._trace.append(
                    {
                        "scenario_kind": "local_episode_owned_by_terminal_h4_acceptance",
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
                else 2,
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
            "accepted_control_router": {
                "policy": self.POLICY_NAME,
                "counts": dict(sorted(self._counts.items())),
                "priority": (
                    "PREVIOUS_DAY_AUCTION" if self.DAILY_SCALES else None,
                    "H4_ACCEPTANCE",
                    "LOCAL_MECHANISM",
                ),
                "retired_family": "H4_LIQUIDITY_REJECTION",
                "rules": (
                    H4_REJECTION_FAMILY_RETIREMENT_RULE,
                    ACCEPTED_CONTROL_OWNERSHIP_RULE,
                ),
            },
            "base": self.base.diagnostics,
            "h4_acceptance": self.acceptance.diagnostics,
        }


class EasyChartRE1AcceptedControlBundle(_AcceptedControlRouter):
    """Local response mechanisms plus completed-H4 accepted control transfer."""

    BASE_CLASS = EasyChartRE1SkilledIntegratedBundle
    POLICY_NAME = "H4_ACCEPTANCE_PLUS_LOCAL_ONLY"


class EasyChartRE1SelectiveAuctionBundle(_AcceptedControlRouter):
    """Previous-day auctions, H4 acceptance and local response mechanisms."""

    BASE_CLASS = EasyChartRE1SkilledDailyBundle
    DAILY_SCALES = frozenset({"DAILY_LIQUIDITY"})
    POLICY_NAME = "DAILY_AUCTION_PLUS_H4_ACCEPTANCE_PLUS_LOCAL"


MultiScaleScenarioBundle = EasyChartRE1SelectiveAuctionBundle

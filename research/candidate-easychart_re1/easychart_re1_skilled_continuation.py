"""Coherent EasyChart day-trading policy for rejection, acceptance and pullback.

The broad previous-day and previous-H4 sweep engines produced many trades but
mistook every adjacent range extreme for mature liquidity.  Those labels are no
longer executable here.  Higher-timeframe execution is reserved for the complete
accepted-H4 auction: body break, immediate hold, first return and first response.

The executable opportunity set is now three complete mechanisms:

* local sweep/reclaim rejection, with visual first return or completed 5m
  control transfer for flow-only evidence;
* local/H4 accepted transfer, with first return and first response;
* aligned nested 5m initiative, source-OB / anchored-fair-value pullback and
  immediate response.

Higher accepted auctions own an overlapping episode, then the specific nested
continuation, then generic local structure.  Distinct price locations remain
independent and the existing NautilusTrader strategy arbitrates the single
account slot.  No score, PnL router, trade cap, partial exit, stop movement or
fitted time window is introduced.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Candle
from easychart_re1_h4_acceptance import H4AcceptanceEngine
from easychart_re1_local_continuation_hold import (
    CloseHeldLocalAuctionContinuationEngine,
)
from easychart_re1_skilled_integrated import EasyChartRE1SkilledIntegratedBundle


COMPLETE_MECHANISM_OPPORTUNITY_POLICY = (
    "RESEARCH_SYNTHESIS:EXECUTABLE_OPPORTUNITIES_ARE_LOCAL_REJECTION_LOCAL_OR_"
    "H4_ACCEPTANCE_AND_NESTED_LOCAL_CONTINUATION_NOT_GENERIC_ADJACENT_RANGE_SWEEPS"
)
if COMPLETE_MECHANISM_OPPORTUNITY_POLICY not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (COMPLETE_MECHANISM_OPPORTUNITY_POLICY,)


class EasyChartRE1SkilledContinuationBundle:
    """One plan stream for three mutually owned auction mechanisms."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.local = EasyChartRE1SkilledIntegratedBundle(
            symbol,
            tick_size,
            minimum_gross_rr,
        )
        self.h4_acceptance = H4AcceptanceEngine(
            symbol,
            tick_size,
            minimum_gross_rr,
        )
        self.continuation = CloseHeldLocalAuctionContinuationEngine(
            symbol,
            tick_size,
            minimum_gross_rr,
        )
        self.detectors = self.local.detectors
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

    def _same_emission_location(
        self,
        left: V5TradePlan,
        right: V5TradePlan,
    ) -> bool:
        return (
            left.observed_time_ns == right.observed_time_ns
            and self._overlap_prices(left, right.overlap_lower, right.overlap_upper)
        )

    def _h4_context_owns(self, plan: V5TradePlan, setup: Any) -> bool:
        if setup is None or plan.observed_time_ns < setup.break_time_ns:
            return False
        return self._overlap_prices(
            plan,
            setup.level_zone.lower,
            setup.level_zone.upper,
        )

    def _continuation_context_owns(self, plan: V5TradePlan, setup: Any) -> bool:
        if setup is None or plan.observed_time_ns < setup.impulse_time_ns:
            return False
        return self._overlap_prices(
            plan,
            setup.source_zone.lower,
            setup.source_zone.upper,
        )

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        h4_before = self.h4_acceptance._active
        h4_raw = self.h4_acceptance.on_bar(timeframe_minutes, bar)
        h4_resolved = (
            h4_before
            if h4_before is not None
            and self.h4_acceptance._active is not h4_before
            else None
        )

        continuation_before = self.continuation._active
        continuation_raw = self.continuation.on_bar(timeframe_minutes, bar)
        continuation_resolved = (
            continuation_before
            if continuation_before is not None
            and self.continuation._active is not continuation_before
            else None
        )
        local_raw = self.local.on_bar(timeframe_minutes, bar)

        routed: list[V5TradePlan] = list(h4_raw)
        for plan in continuation_raw:
            owner = next(
                (
                    existing
                    for existing in h4_raw
                    if self._same_emission_location(plan, existing)
                ),
                None,
            )
            if owner is not None:
                self._inc("continuation_suppressed_by_h4_acceptance")
                self._trace.append(
                    {
                        "scenario_kind": "continuation_owned_by_h4_acceptance",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "suppressed_plan_id": plan.plan_id,
                        "owner_plan_id": owner.plan_id,
                    },
                )
                continue
            routed.append(plan)

        owners = list(routed)
        for plan in local_raw:
            owner = next(
                (
                    existing
                    for existing in owners
                    if self._same_emission_location(plan, existing)
                ),
                None,
            )
            if owner is not None:
                self._inc("generic_local_suppressed_by_specific_plan")
                self._trace.append(
                    {
                        "scenario_kind": "generic_local_owned_by_specific_plan",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "suppressed_plan_id": plan.plan_id,
                        "owner_plan_id": owner.plan_id,
                    },
                )
                continue

            h4_pending = self.h4_acceptance._active
            if self._h4_context_owns(plan, h4_pending):
                self._inc("generic_local_suppressed_during_h4_acceptance")
                self._trace.append(
                    {
                        "scenario_kind": "generic_local_owned_by_pending_h4_acceptance",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "suppressed_plan_id": plan.plan_id,
                        "h4_setup_id": h4_pending.setup_id,
                    },
                )
                continue
            if (
                h4_resolved is not None
                and plan.observed_time_ns == bar.ts_close_ns
                and self._h4_context_owns(plan, h4_resolved)
            ):
                self._inc("generic_local_suppressed_on_h4_terminal_bar")
                continue

            pending = self.continuation._active
            if self._continuation_context_owns(plan, pending):
                self._inc("generic_local_suppressed_during_nested_continuation")
                self._trace.append(
                    {
                        "scenario_kind": "generic_local_owned_by_pending_continuation",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "suppressed_plan_id": plan.plan_id,
                        "continuation_setup_id": pending.setup_id,
                    },
                )
                continue
            if (
                continuation_resolved is not None
                and plan.observed_time_ns == bar.ts_close_ns
                and self._continuation_context_owns(plan, continuation_resolved)
            ):
                self._inc("generic_local_suppressed_on_continuation_terminal_bar")
                continue
            routed.append(plan)

        unique = {plan.plan_id: plan for plan in routed}
        output = sorted(
            unique.values(),
            key=lambda plan: (
                plan.interaction_time_ns,
                0
                if plan.scale_name == "H4_ACCEPTANCE"
                else 1
                if plan.scale_name == "LOCAL_CONTINUATION"
                else 2,
                plan.observed_time_ns,
                plan.symbol,
                plan.plan_id,
            ),
        )
        self._plans.extend(output)
        return output

    def drain_trace(self) -> list[dict[str, Any]]:
        output = (
            self.h4_acceptance.drain_trace()
            + self.continuation.drain_trace()
            + self.local.drain_trace()
            + self._trace
        )
        self._trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return (
            self.h4_acceptance.find_zone(zone_id)
            or self.continuation.find_zone(zone_id)
            or self.local.find_zone(zone_id)
        )

    @property
    def plans(self) -> list[V5TradePlan]:
        return list(self._plans)

    @property
    def setups(self) -> list[Any]:
        # Standard setup enums feed the legacy generic summary.  The two custom
        # causal machines expose their complete lifecycle in diagnostics below.
        return [
            setup
            for setup in self.local.setups
            if hasattr(getattr(setup, "state", None), "value")
        ]

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "complete_mechanism_router": {
                "counts": dict(sorted(self._counts.items())),
                "priority": (
                    "ACCEPTED_H4_AUCTION",
                    "NESTED_LOCAL_CONTINUATION",
                    "LOCAL_ACCEPTANCE_OR_REJECTION",
                ),
                "generic_previous_day_sweep_executable": False,
                "generic_previous_h4_sweep_executable": False,
                "rule_provenance": COMPLETE_MECHANISM_OPPORTUNITY_POLICY,
            },
            "local": self.local.diagnostics,
            "h4_acceptance": self.h4_acceptance.diagnostics,
            "local_continuation": self.continuation.diagnostics,
        }


MultiScaleScenarioBundle = EasyChartRE1SkilledContinuationBundle

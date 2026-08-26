"""Skilled day-trading policy plus completed-H4 liquidity auctions.

The existing skilled daily bundle owns completed previous-day extremes and the
local response-confirmed structure mechanisms.  This layer adds one independent
opportunity family: rejection of the immediately preceding completed four-hour
auction extreme.  It does not relax a local setup.

Episode ownership is categorical and causal: previous-day evidence owns an
overlap first, then the completed-H4 auction, then local structure labels.  A
pending or just-failed H4 first-response episode suppresses an overlapping local
entry because both labels describe the same traded boundary.  Distinct price
areas remain independent and are left to the single global account slot.
"""
from __future__ import annotations

from typing import Any

from contracts_v5 import V5TradePlan
from domain import Candle
from easychart_re1_h4_liquidity import H4LiquiditySweepEngine
from easychart_re1_skilled_daily import EasyChartRE1SkilledDailyBundle


class EasyChartRE1SkilledH4Bundle:
    """One routed plan stream with daily, H4 and local mechanism ownership."""

    DAILY_SCALES = {"DAILY_LIQUIDITY", "DAILY_ACCEPTANCE"}

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.base = EasyChartRE1SkilledDailyBundle(
            symbol,
            tick_size,
            minimum_gross_rr,
        )
        self.h4 = H4LiquiditySweepEngine(
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

    def _same_completed_plan_episode(
        self,
        left: V5TradePlan,
        right: V5TradePlan,
    ) -> bool:
        return (
            left.interaction_time_ns == right.interaction_time_ns
            and self._overlap_prices(left, right.overlap_lower, right.overlap_upper)
        )

    @classmethod
    def _daily(cls, plan: V5TradePlan) -> bool:
        return plan.scale_name in cls.DAILY_SCALES

    @staticmethod
    def _h4_plan(plan: V5TradePlan) -> bool:
        return plan.scale_name == "H4_LIQUIDITY"

    def _h4_context_owns_local(self, plan: V5TradePlan) -> bool:
        setup = self.h4._active
        if setup is None:
            return False
        if plan.observed_time_ns < setup.sweep_time_ns:
            return False
        return self._overlap_prices(
            plan,
            setup.level_zone.lower,
            setup.level_zone.upper,
        )

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        # Both engines receive the same completed bar. H4 is processed first so
        # its pending ownership is visible when local plans from this close are
        # routed, while previous-day ownership remains explicit below.
        h4_raw = self.h4.on_bar(timeframe_minutes, bar)
        base_raw = self.base.on_bar(timeframe_minutes, bar)

        daily = [plan for plan in base_raw if self._daily(plan)]
        local = [plan for plan in base_raw if not self._daily(plan)]

        routed: list[V5TradePlan] = list(daily)
        for plan in h4_raw:
            owner = next(
                (
                    existing
                    for existing in daily
                    if self._same_completed_plan_episode(plan, existing)
                ),
                None,
            )
            if owner is not None:
                self._inc("h4_plan_suppressed_by_previous_day_auction")
                self._trace.append(
                    {
                        "scenario_kind": "h4_episode_owned_by_previous_day_auction",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "suppressed_plan_id": plan.plan_id,
                        "owner_plan_id": owner.plan_id,
                        "interaction_time_ns": plan.interaction_time_ns,
                    },
                )
                continue
            routed.append(plan)

        higher = list(routed)
        for plan in local:
            owner = next(
                (
                    existing
                    for existing in higher
                    if self._same_completed_plan_episode(plan, existing)
                ),
                None,
            )
            if owner is not None:
                self._inc("local_plan_suppressed_by_higher_liquidity_auction")
                self._trace.append(
                    {
                        "scenario_kind": "local_episode_owned_by_higher_liquidity_auction",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "suppressed_plan_id": plan.plan_id,
                        "owner_plan_id": owner.plan_id,
                        "interaction_time_ns": plan.interaction_time_ns,
                    },
                )
                continue
            if self._h4_context_owns_local(plan):
                setup = self.h4._active
                assert setup is not None
                self._inc("local_plan_suppressed_during_pending_h4_response")
                self._trace.append(
                    {
                        "scenario_kind": "local_episode_owned_by_pending_h4_response",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "suppressed_plan_id": plan.plan_id,
                        "h4_setup_id": setup.setup_id,
                        "h4_sweep_time_ns": setup.sweep_time_ns,
                    },
                )
                continue
            routed.append(plan)

        unique = {plan.plan_id: plan for plan in routed}
        output = sorted(
            unique.values(),
            key=lambda plan: (
                plan.interaction_time_ns,
                0 if self._daily(plan) else 1 if self._h4_plan(plan) else 2,
                plan.observed_time_ns,
                plan.symbol,
                plan.plan_id,
            ),
        )
        self._plans.extend(output)
        return output

    def drain_trace(self) -> list[dict[str, Any]]:
        output = self.base.drain_trace() + self.h4.drain_trace() + self._trace
        self._trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return self.base.find_zone(zone_id) or self.h4.find_zone(zone_id)

    @property
    def plans(self) -> list[V5TradePlan]:
        return list(self._plans)

    @property
    def setups(self) -> list[Any]:
        return list(self.base.setups) + list(self.h4.setups)

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "skilled_h4_router": {
                "counts": dict(sorted(self._counts.items())),
                "priority": (
                    "PREVIOUS_DAY_AUCTION",
                    "COMPLETED_H4_AUCTION",
                    "LOCAL_STRUCTURE",
                ),
            },
            "base": self.base.diagnostics,
            "h4_liquidity": self.h4.diagnostics,
        }


MultiScaleScenarioBundle = EasyChartRE1SkilledH4Bundle

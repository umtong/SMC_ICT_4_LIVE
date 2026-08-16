"""Unified directional-delivery and mature-balance EasyChart RE1 system.

The router has two mutually exclusive local auction states:

* a confirmed matching-scale external liquidity draw routes pullback rejection
  and flow-validated rebalance continuation in that direction;
* when no draw is active, one canonical two-sided mature balance may trade its
  first outside sweep and reclaim toward the opposite defense.

This is one plan stream and one account, not an ensemble sum.  The existing
single global position arbitration decides among simultaneous symbols.
"""
from __future__ import annotations

from typing import Any

from contracts_v5 import V5TradePlan
from domain import Candle
from easychart_re1_delivery_system_v3 import EasyChartRE1DeliverySystemV3Bundle
from easychart_re1_mature_balance import MatureBalanceEngine
from scenario_bundle_v5 import ResearchScenarioBundleV5


class EasyChartRE1DeliveryBalanceSystemBundle(
    EasyChartRE1DeliverySystemV3Bundle,
):
    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.mature_balance = MatureBalanceEngine(
            symbol,
            tick_size,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["mature_balance"] = 0
        self._balance_counts: dict[str, int] = {}
        self._balance_trace: list[dict[str, Any]] = []

    def _binc(self, key: str) -> None:
        self._balance_counts[key] = self._balance_counts.get(key, 0) + 1

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return super().setups + self.mature_balance.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return super().plans + self.mature_balance.plans

    def _claim_episode(self, plan: V5TradePlan) -> None:
        if plan.scale_name == "MATURE_BALANCE":
            ResearchScenarioBundleV5._claim_episode(self, plan)
            return
        super()._claim_episode(plan)

    def _sync_balance_audit(self) -> None:
        start = self._audit_offsets["mature_balance"]
        for zone in self.mature_balance.audit_zones[start:]:
            timeframe = getattr(zone, "timeframe_minutes", 5)
            destination = timeframe if timeframe in self.detectors else 5
            self.detectors[destination].register(zone)
        self._audit_offsets["mature_balance"] = len(
            self.mature_balance.audit_zones
        )

    def _route_balance(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in raw:
            if self.delivery_draw.active is not None:
                self._binc("balance_plan_suppressed_by_directional_draw")
                continue
            if self._duplicate_episode(plan):
                self._binc("balance_plan_overlapped_executable_episode")
                continue
            self._claim_episode(plan)
            output.append(plan)
            self._binc("mature_balance_plan_allowed")
            self._balance_trace.append(
                {
                    "scenario_kind": "mature_balance_plan_allowed",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "entry": plan.entry,
                    "stop": plan.stop,
                    "target": plan.target,
                    "gross_rr": plan.gross_rr,
                    "rule_provenance": plan.rule_provenance,
                }
            )
        return output

    def on_bar(
        self,
        timeframe_minutes: int,
        bar: Candle,
    ) -> list[V5TradePlan]:
        directional = super().on_bar(timeframe_minutes, bar)
        self.mature_balance.set_directional_draw(
            self.delivery_draw.active is not None,
            bar.ts_close_ns,
        )
        balance: list[V5TradePlan] = []
        if timeframe_minutes in {5, 1}:
            raw = self.mature_balance.on_bar(timeframe_minutes, bar)
            self._sync_balance_audit()
            balance = self._route_balance(raw)
        return sorted(
            directional + balance,
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
            + self.mature_balance.drain_trace()
            + self._balance_trace
        )
        self._balance_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return super().find_zone(zone_id) or self.mature_balance.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["mature_balance_family"] = {
            "routing_counts": dict(sorted(self._balance_counts.items())),
            "engine": self.mature_balance.diagnostics,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1DeliveryBalanceSystemBundle

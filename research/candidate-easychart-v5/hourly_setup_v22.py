"""One-hour/15-minute/5-minute EasyChart scenario scale.

This is the same integrated structure-first policy at the intermediate scale
EasyChart explicitly assigns to trend and important support/resistance.  A
completed one-hour structure supplies context, the 15-minute chart resolves the
interaction, and the 5-minute chart supplies the event-local retest or
footprint.  Execution remains on the 1-minute stream through the unchanged
NautilusTrader bracket and account implementation.

The module changes no execution, risk, management, daily, time, or trade-count
rule.  It is a scale diagnostic intended for later integration with the lower
15m/5m/1m family if its independent contribution is real.
"""
from __future__ import annotations

from typing import Any

from contracts_v5 import V5TradePlan
from domain import Candle
from hourly_direction_v21 import PersistentChannelTargetScenarioEngine
from scenario_bundle_v5 import ResearchScenarioBundleV5


HOURLY_SETUP_RULE = (
    "SOURCE_EXPLICIT:ONE_HOUR_STRUCTURE_WITH_FIFTEEN_MINUTE_INTERACTION_AND_FIVE_MINUTE_ENTRY_CONFIRMATION"
)


class HourlySetupBundleV22(ResearchScenarioBundleV5):
    """Emit only 60m/15m/5m plans while retaining all audit frames."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.macro = PersistentChannelTargetScenarioEngine(
            symbol,
            tick_size,
            scale_name="HOURLY_SETUP",
            higher_minutes=60,
            decision_minutes=15,
            trigger_minutes=5,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["macro"] = 0

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes in self.detectors:
            self.detectors[timeframe_minutes].on_bar(bar)
        plans: list[V5TradePlan] = []
        if timeframe_minutes in {60, 15, 5}:
            plans.extend(self.macro.on_bar(timeframe_minutes, bar))
        self._sync_audit("macro", self.macro)

        ranked = sorted(
            plans,
            key=lambda plan: (
                plan.interaction_time_ns,
                -plan.higher_timeframe_minutes,
                plan.symbol,
                plan.plan_id,
            ),
        )
        independent: list[V5TradePlan] = []
        for plan in ranked:
            if self._duplicate_episode(plan):
                self._bundle_trace.append(
                    {
                        "scenario_kind": "causal_episode_duplicate_suppressed",
                        "event_time_ns": plan.observed_time_ns,
                        "scale_name": plan.scale_name,
                        "plan_id": plan.plan_id,
                        "side": plan.side.name,
                        "interaction_time_ns": plan.interaction_time_ns,
                    },
                )
                continue
            self._claim_episode(plan)
            independent.append(plan)
        return independent

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["scale_policy"] = {
            "name": "ONE_HOUR_CONTEXT_FIFTEEN_MINUTE_DECISION_FIVE_MINUTE_TRIGGER",
            "rule_provenance": HOURLY_SETUP_RULE,
        }
        return output

"""Route local rejection only after completed hourly delivery turns with it.

A local rejection and an accepted control transfer solve different problems.
An acceptance break/hold/retest proves its own direction change.  A rejection at
a projected 15-minute line or channel, however, should not fight the still-open
higher delivery merely because a one-minute footprint appeared.

This policy therefore gives the last *completed* 60-minute candle one precise
responsibility:

* MICRO rejection requires that completed hourly body direction already agrees
  with the planned side;
* MICRO acceptance remains executable because its break, hold and retest prove
  control transfer directly;
* horizontal, major-liquidity and flow-validated decision-OB families are not
  redefined;
* entry, stop, objective, risk, costs, account arbitration and execution remain
  unchanged.

No magnitude threshold, moving average, score, clock session, fitted lookback or
PnL-dependent decision is introduced.  A flat hourly body is an unresolved
higher delivery and cannot sponsor a local rejection.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, V5TradePlan
from domain import Candle, Side
from easychart_re1_significant_response import (
    EasyChartRE1SignificantResponseBundle,
)


COMPLETED_HOURLY_DELIVERY_OWNERSHIP_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:A_LOCAL_REJECTION_ENTRY_REQUIRES_THE_LAST_"
    "COMPLETED_HOURLY_CANDLE_BODY_TO_DELIVER_IN_THE_INTENDED_DIRECTION_WHILE_"
    "AN_ACCEPTED_BREAK_HOLD_RETEST_PROVES_ITS_OWN_CONTROL_TRANSFER"
)
if COMPLETED_HOURLY_DELIVERY_OWNERSHIP_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (COMPLETED_HOURLY_DELIVERY_OWNERSHIP_RULE,)


class EasyChartRE1DeliveryAlignedResponseBundle(
    EasyChartRE1SignificantResponseBundle
):
    """Significant-response core with causal completed-hour rejection routing."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self._last_completed_hour: Candle | None = None
        self._delivery_counts: dict[str, int] = {}
        self._delivery_trace: list[dict[str, Any]] = []

    def _dinc(self, key: str) -> None:
        self._delivery_counts[key] = self._delivery_counts.get(key, 0) + 1

    @staticmethod
    def _hour_side(bar: Candle) -> Side | None:
        if bar.close > bar.open:
            return Side.LONG
        if bar.close < bar.open:
            return Side.SHORT
        return None

    @staticmethod
    def _requires_hour_owner(plan: V5TradePlan) -> bool:
        return (
            plan.scale_name == "MICRO"
            and plan.scenario_path == ScenarioPath.REJECTION.value
        )

    def _route_completed_hour(
        self,
        raw: list[V5TradePlan],
    ) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        hour = self._last_completed_hour
        for plan in raw:
            if not self._requires_hour_owner(plan):
                output.append(plan)
                continue
            hour_side = None if hour is None else self._hour_side(hour)
            if hour_side is plan.side:
                output.append(plan)
                self._dinc("micro_rejection_allowed_by_completed_hour_delivery")
                self._delivery_trace.append(
                    {
                        "scenario_kind": "micro_rejection_owned_by_completed_hour_delivery",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "plan_id": plan.plan_id,
                        "side": plan.side.name,
                        "hour_close_time_ns": hour.ts_close_ns,
                        "hour_open": hour.open,
                        "hour_close": hour.close,
                        "rule_provenance": COMPLETED_HOURLY_DELIVERY_OWNERSHIP_RULE,
                    },
                )
                continue
            reason = (
                "missing_completed_hour"
                if hour is None
                else "flat_completed_hour"
                if hour_side is None
                else "completed_hour_delivery_opposed_rejection"
            )
            self._dinc(f"micro_rejection_suppressed_{reason}")
            self._delivery_trace.append(
                {
                    "scenario_kind": "micro_rejection_suppressed_without_hourly_delivery",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "reason": reason,
                    "hour_close_time_ns": None if hour is None else hour.ts_close_ns,
                    "hour_open": None if hour is None else hour.open,
                    "hour_close": None if hour is None else hour.close,
                    "rule_provenance": COMPLETED_HOURLY_DELIVERY_OWNERSHIP_RULE,
                },
            )
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes == 60:
            self._last_completed_hour = bar
        return self._route_completed_hour(super().on_bar(timeframe_minutes, bar))

    def drain_trace(self) -> list[dict[str, Any]]:
        output = super().drain_trace() + self._delivery_trace
        self._delivery_trace = []
        return output

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["completed_hour_delivery_router"] = {
            "counts": dict(sorted(self._delivery_counts.items())),
            "last_completed_hour": (
                None
                if self._last_completed_hour is None
                else {
                    "ts_close_ns": self._last_completed_hour.ts_close_ns,
                    "open": self._last_completed_hour.open,
                    "close": self._last_completed_hour.close,
                    "side": (
                        None
                        if self._hour_side(self._last_completed_hour) is None
                        else self._hour_side(self._last_completed_hour).name
                    ),
                }
            ),
            "rule_provenance": COMPLETED_HOURLY_DELIVERY_OWNERSHIP_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1DeliveryAlignedResponseBundle

"""One auction system gated by completed higher delivery and 5m displacement.

The broad sweep/reclaim engines were not failing because their levels were
unknown; they were failing because a reclaim plus any small response was treated
as a completed transfer of control.  A response-confirmed human entry needs two
separate facts which are both available before the order:

* the completed higher frame already delivers in the planned direction;
* the completed five-minute response is body-dominant, so directional delivery
  is larger than its combined wicks rather than a weak close inside noise.

This policy assigns those facts without inventing a strength score:

* DAILY/H4 liquidity rejection and MICRO rejection require completed 60m and 5m
  bodies in the intended direction, with the 5m body larger than both wicks
  combined;
* H4 and MICRO acceptance require completed 60m, 15m and 5m bodies in the
  intended direction, again with a body-dominant 5m response;
* generic decision-OB and isolated horizontal labels are retired rather than
  repeatedly patched: they do not establish the full higher-delivery scenario;
* anchored local continuation and any other independently completed mechanism
  retain their existing entry, invalidation and objective.

``body > upper_wick + lower_wick`` is the candle's own categorical geometry
(body occupies more than half of its full range), not an optimized magnitude,
ATR, percentile or fitted score.  No session, symbol exception, trade cap,
partial exit, stop movement, target movement or PnL-dependent routing is added.
Entry, stop, objective, costs, current-NAV 3% risk and the single global account
slot remain unchanged.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, V5TradePlan
from domain import Candle
from easychart_re1_delivery_confirmed_auction import (
    EasyChartRE1DeliveryConfirmedAuctionBundle,
)


BODY_DOMINANT_DELIVERY_RULE = (
    "RESEARCH_SYNTHESIS:A_COMPLETED_FIVE_MINUTE_RESPONSE_CONFIRMS_DIRECTIONAL_"
    "DELIVERY_ONLY_WHEN_ITS_BODY_EXCEEDS_ITS_COMBINED_UPPER_AND_LOWER_WICKS"
)
DISPLACEMENT_CONFIRMED_AUCTION_RULE = (
    "RESEARCH_SYNTHESIS:LIQUIDITY_AND_LOCAL_REJECTION_REQUIRE_COMPLETED_60M_"
    "DIRECTION_PLUS_BODY_DOMINANT_5M_DELIVERY_WHILE_H4_AND_LOCAL_ACCEPTANCE_"
    "REQUIRE_COMPLETED_60M_15M_AND_BODY_DOMINANT_5M_DELIVERY"
)
WEAK_LOCAL_FAMILY_RETIREMENT_RULE = (
    "RESEARCH_SYNTHESIS:GENERIC_FLOW_DECISION_OB_AND_ISOLATED_HORIZONTAL_LABELS_"
    "ARE_RETIRED_BECAUSE_THEY_LACK_A_COMPLETE_HIGHER_DELIVERY_CONTROL_SCENARIO"
)
for _rule in (
    BODY_DOMINANT_DELIVERY_RULE,
    DISPLACEMENT_CONFIRMED_AUCTION_RULE,
    WEAK_LOCAL_FAMILY_RETIREMENT_RULE,
):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


class EasyChartRE1DisplacementConfirmedAuctionBundle(
    EasyChartRE1DeliveryConfirmedAuctionBundle
):
    """Full auction stream with categorical body-dominant response ownership."""

    RETIRED_LOCAL_SCALES = frozenset({"FLOW_DECISION_OB", "HORIZONTAL"})

    @staticmethod
    def _body_dominant(bar: Candle | None) -> bool:
        if bar is None:
            return False
        body = abs(bar.close - bar.open)
        upper_wick = bar.high - max(bar.open, bar.close)
        lower_wick = min(bar.open, bar.close) - bar.low
        return body > upper_wick + lower_wick

    @classmethod
    def _retired_weak_local(cls, plan: V5TradePlan) -> bool:
        return plan.scale_name in cls.RETIRED_LOCAL_SCALES

    def _displacement_aligns(
        self,
        plan: V5TradePlan,
        higher_frames: tuple[int, ...],
    ) -> bool:
        five = self._last_completed.get(5)
        return (
            self._frames_align(plan.side, (*higher_frames, 5))
            and self._body_dominant(five)
        )

    def _record_displacement_route(
        self,
        plan: V5TradePlan,
        *,
        allowed: bool,
        higher_frames: tuple[int, ...],
        mechanism: str,
    ) -> None:
        five = self._last_completed.get(5)
        frame_values: dict[str, Any] = {}
        for timeframe in (*higher_frames, 5):
            bar = self._last_completed.get(timeframe)
            frame_values[str(timeframe)] = (
                None
                if bar is None
                else {
                    "ts_close_ns": bar.ts_close_ns,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "side": (
                        None
                        if self._bar_side(bar) is None
                        else self._bar_side(bar).name
                    ),
                    "body_dominant": (
                        self._body_dominant(bar) if timeframe == 5 else None
                    ),
                }
            )
        self._delivery_trace.append(
            {
                "scenario_kind": (
                    "auction_plan_allowed_by_displacement_delivery"
                    if allowed
                    else "auction_plan_suppressed_without_displacement_delivery"
                ),
                "event_time_ns": plan.observed_time_ns,
                "symbol": plan.symbol,
                "plan_id": plan.plan_id,
                "family": plan.family,
                "scale_name": plan.scale_name,
                "scenario_path": plan.scenario_path,
                "side": plan.side.name,
                "mechanism": mechanism,
                "required_higher_frames": higher_frames,
                "completed_frame_states": frame_values,
                "five_minute_body_dominant": self._body_dominant(five),
                "rule_provenance": (
                    BODY_DOMINANT_DELIVERY_RULE,
                    DISPLACEMENT_CONFIRMED_AUCTION_RULE,
                ),
            },
        )

    @staticmethod
    def _acceptance_requires_displacement(plan: V5TradePlan) -> bool:
        return (
            plan.scenario_path == ScenarioPath.ACCEPTANCE.value
            and plan.scale_name in {"H4_ACCEPTANCE", "MICRO"}
        )

    def _route_delivery(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in raw:
            if self._retired_weak_local(plan):
                self._dinc("weak_local_family_retired")
                self._delivery_trace.append(
                    {
                        "scenario_kind": "weak_local_family_retired",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "plan_id": plan.plan_id,
                        "family": plan.family,
                        "scale_name": plan.scale_name,
                        "scenario_path": plan.scenario_path,
                        "side": plan.side.name,
                        "rule_provenance": WEAK_LOCAL_FAMILY_RETIREMENT_RULE,
                    },
                )
                continue
            if self._rejection_requires_delivery(plan):
                higher_frames = (60,)
                allowed = self._displacement_aligns(plan, higher_frames)
                self._dinc(
                    "rejection_allowed_completed_60m_body_dominant_5m"
                    if allowed
                    else "rejection_suppressed_without_60m_body_dominant_5m"
                )
                self._record_displacement_route(
                    plan,
                    allowed=allowed,
                    higher_frames=higher_frames,
                    mechanism="REJECTION_AFTER_SWEEP_OR_PROJECTED_BOUNDARY",
                )
                if allowed:
                    output.append(plan)
                continue
            if self._acceptance_requires_displacement(plan):
                higher_frames = (60, 15)
                allowed = self._displacement_aligns(plan, higher_frames)
                self._dinc(
                    "acceptance_allowed_completed_60m_15m_body_dominant_5m"
                    if allowed
                    else "acceptance_suppressed_without_60m_15m_body_dominant_5m"
                )
                self._record_displacement_route(
                    plan,
                    allowed=allowed,
                    higher_frames=higher_frames,
                    mechanism="ACCEPTED_CONTROL_TRANSFER",
                )
                if allowed:
                    output.append(plan)
                continue
            output.append(plan)
            self._dinc("distinct_local_mechanism_unchanged")
        return output

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        five = self._last_completed.get(5)
        output["body_dominant_delivery"] = {
            "five_minute_body_dominant": self._body_dominant(five),
            "five_minute_bar": (
                None
                if five is None
                else {
                    "ts_close_ns": five.ts_close_ns,
                    "open": five.open,
                    "high": five.high,
                    "low": five.low,
                    "close": five.close,
                }
            ),
            "retired_local_scales": tuple(sorted(self.RETIRED_LOCAL_SCALES)),
            "rules": (
                BODY_DOMINANT_DELIVERY_RULE,
                DISPLACEMENT_CONFIRMED_AUCTION_RULE,
                WEAK_LOCAL_FAMILY_RETIREMENT_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1DisplacementConfirmedAuctionBundle

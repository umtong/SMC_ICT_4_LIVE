"""One auction system whose reversal entries wait for completed delivery.

The broad previous-day and previous-H4 sweep families failed because a sweep,
reclaim and one-minute response were allowed to fight delivery still moving in
the opposite direction.  The same error appeared in local line/channel
rejections.  An accepted break is different: it proves a change of control, but
its first return still needs the completed decision and trigger frames to be
delivering with the new side.

This router gives each completed frame one causal responsibility:

* DAILY/H4 liquidity rejection and MICRO rejection require both the last
  completed 60-minute and 5-minute candle bodies to agree with the trade;
* H4 accepted control transfer requires both the last completed 15-minute and
  5-minute candle bodies to agree with the trade;
* local accepted breaks retain their own break/hold/retest/first-response proof;
* flow-validated decision OBs and other distinct local mechanisms are not
  silently redefined.

This is a state router, not a strength score.  There is no body-size threshold,
ATR rule, moving average, fitted lookback, clock session, symbol exception,
trade cap, partial exit, stop move or PnL-dependent choice.  Entry, invalidation,
objective, fees, current-NAV 3% risk and the one global account slot are
unchanged.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, V5TradePlan
from domain import Candle, Side
from easychart_re1_skilled_continuation import (
    EasyChartRE1SkilledContinuationBundle,
)


MULTIFRAME_DELIVERY_CONFIRMATION_RULE = (
    "RESEARCH_SYNTHESIS:LIQUIDITY_AND_LOCAL_REJECTION_REQUIRE_COMPLETED_60M_"
    "AND_5M_DELIVERY_IN_THE_INTENDED_DIRECTION_WHILE_H4_ACCEPTANCE_REQUIRES_"
    "COMPLETED_15M_AND_5M_DELIVERY_AFTER_BREAK_HOLD_RETURN"
)
if MULTIFRAME_DELIVERY_CONFIRMATION_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (MULTIFRAME_DELIVERY_CONFIRMATION_RULE,)


class EasyChartRE1DeliveryConfirmedAuctionBundle(
    EasyChartRE1SkilledContinuationBundle
):
    """Full opportunity stream with causal multi-frame delivery ownership."""

    REVERSAL_SCALES = frozenset({
        "DAILY_LIQUIDITY",
        "H4_LIQUIDITY",
    })

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self._last_completed: dict[int, Candle] = {}
        self._delivery_counts: dict[str, int] = {}
        self._delivery_trace: list[dict[str, Any]] = []

    def _dinc(self, key: str) -> None:
        self._delivery_counts[key] = self._delivery_counts.get(key, 0) + 1

    @staticmethod
    def _bar_side(bar: Candle | None) -> Side | None:
        if bar is None or bar.close == bar.open:
            return None
        return Side.LONG if bar.close > bar.open else Side.SHORT

    def _frames_align(
        self,
        side: Side,
        frames: tuple[int, ...],
    ) -> bool:
        return all(
            self._bar_side(self._last_completed.get(timeframe)) is side
            for timeframe in frames
        )

    @classmethod
    def _rejection_requires_delivery(cls, plan: V5TradePlan) -> bool:
        return (
            plan.scale_name in cls.REVERSAL_SCALES
            or (
                plan.scale_name == "MICRO"
                and plan.scenario_path == ScenarioPath.REJECTION.value
            )
        )

    @staticmethod
    def _h4_acceptance_requires_delivery(plan: V5TradePlan) -> bool:
        return plan.scale_name == "H4_ACCEPTANCE"

    def _record_route(
        self,
        plan: V5TradePlan,
        *,
        allowed: bool,
        frames: tuple[int, ...],
        mechanism: str,
    ) -> None:
        states = {
            str(timeframe): (
                None
                if (bar := self._last_completed.get(timeframe)) is None
                else {
                    "ts_close_ns": bar.ts_close_ns,
                    "open": bar.open,
                    "close": bar.close,
                    "side": (
                        None
                        if self._bar_side(bar) is None
                        else self._bar_side(bar).name
                    ),
                }
            )
            for timeframe in frames
        }
        self._delivery_trace.append(
            {
                "scenario_kind": (
                    "auction_plan_allowed_by_multiframe_delivery"
                    if allowed
                    else "auction_plan_suppressed_without_multiframe_delivery"
                ),
                "event_time_ns": plan.observed_time_ns,
                "symbol": plan.symbol,
                "plan_id": plan.plan_id,
                "family": plan.family,
                "scale_name": plan.scale_name,
                "scenario_path": plan.scenario_path,
                "side": plan.side.name,
                "mechanism": mechanism,
                "required_frames": frames,
                "completed_frame_states": states,
                "rule_provenance": MULTIFRAME_DELIVERY_CONFIRMATION_RULE,
            },
        )

    def _route_delivery(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in raw:
            if self._rejection_requires_delivery(plan):
                frames = (60, 5)
                allowed = self._frames_align(plan.side, frames)
                self._dinc(
                    "rejection_allowed_completed_60m_5m"
                    if allowed
                    else "rejection_suppressed_completed_60m_5m_not_aligned"
                )
                self._record_route(
                    plan,
                    allowed=allowed,
                    frames=frames,
                    mechanism="REJECTION_AFTER_SWEEP_OR_PROJECTED_BOUNDARY",
                )
                if allowed:
                    output.append(plan)
                continue
            if self._h4_acceptance_requires_delivery(plan):
                frames = (15, 5)
                allowed = self._frames_align(plan.side, frames)
                self._dinc(
                    "h4_acceptance_allowed_completed_15m_5m"
                    if allowed
                    else "h4_acceptance_suppressed_completed_15m_5m_not_aligned"
                )
                self._record_route(
                    plan,
                    allowed=allowed,
                    frames=frames,
                    mechanism="H4_ACCEPTED_CONTROL_TRANSFER",
                )
                if allowed:
                    output.append(plan)
                continue
            output.append(plan)
            self._dinc("distinct_local_mechanism_unchanged")
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes in {60, 15, 5}:
            self._last_completed[timeframe_minutes] = bar
        return self._route_delivery(super().on_bar(timeframe_minutes, bar))

    def drain_trace(self) -> list[dict[str, Any]]:
        output = super().drain_trace() + self._delivery_trace
        self._delivery_trace = []
        return output

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["multiframe_delivery_confirmation"] = {
            "counts": dict(sorted(self._delivery_counts.items())),
            "completed_frames": {
                str(timeframe): {
                    "ts_close_ns": bar.ts_close_ns,
                    "open": bar.open,
                    "close": bar.close,
                    "side": (
                        None
                        if self._bar_side(bar) is None
                        else self._bar_side(bar).name
                    ),
                }
                for timeframe, bar in sorted(self._last_completed.items())
            },
            "rule_provenance": MULTIFRAME_DELIVERY_CONFIRMATION_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1DeliveryConfirmedAuctionBundle

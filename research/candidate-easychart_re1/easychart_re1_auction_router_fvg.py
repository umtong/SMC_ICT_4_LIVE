"""Add flow-validated five-minute FVG continuation to the auction router.

The OB-only continuation family captured inventory transfer expressed as an
engulfing source candle.  It omitted the other footprint used throughout the
EasyChart material: a conspicuous displacement imbalance which price later
rebalances.  This module treats OB and FVG as two representations of the same
local continuation auction, not as independent overlapping strategies.

A five-minute FVG is eligible only when:

* the causally confirmed fifteen-minute BOS direction agrees;
* its middle displacement candle has aligned cumulative one-minute taker flow,
  net intended price progress and at least one active directed minute;
* it is high-quality by the existing displacement geometry;
* its first later touch is consumed once and the first completed response shows
  aligned initiative or adverse-flow absorption;
* no active BTC/ETH-led common impulse is opposite.

OB and FVG setups share the same episode router, structural invalidation,
nearest significant objective and global account slot.  No score, session,
volatility threshold, fitted timeout, partial exit or stop movement is added.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from domain import Candle, Side
from easychart_re1_auction_router import (
    MECHANISM_SPECIFIC_CONTEXT_ROUTER_RULE,
    EasyChartRE1AuctionRouterBundle,
)
from easychart_re1_local_auction_continuation import (
    LOCAL_AUCTION_CONTINUATION_RULE,
    EasyChartRE1LocalAuctionStrategy,
)
from easychart_re1_local_auction_continuation_v2 import (
    LOCAL_CONTINUATION_RESPONSE_FLOW_RULE,
    FlowResponseLocalContinuationEngine,
)
from easychart_re1_flow import FlowObservation
from easychart_zones import PriceZone, ZoneKind


LOCAL_FVG_CONTINUATION_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "HIGH_QUALITY_FIVE_MINUTE_FVG_WITH_ALIGNED_MIDDLE_CANDLE_FLOW_SHARES_THE_LOCAL_BOS_FIRST_RETURN_CONTINUATION_RESPONSIBILITY_WITH_ENGULFING_OB"
)
if LOCAL_FVG_CONTINUATION_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (LOCAL_FVG_CONTINUATION_RULE,)


class LocalFootprintContinuationEngine(FlowResponseLocalContinuationEngine):
    """One local continuation state machine for either OB or FVG footprints."""

    def _on_five(self, bar: Candle) -> None:
        self.decision_structure.on_bar(bar)
        created = self.footprints.on_bar(bar)
        for zone in created:
            self._audit(zone)
            if zone.kind not in {ZoneKind.ORDER_BLOCK, ZoneKind.FVG}:
                continue
            if not zone.high_quality_by_size:
                self._inc("local_footprint_rejected_by_existing_quality_geometry")
                continue
            side = self._zone_side(zone)
            state = self.factor_state
            if state is None or state.side is not side:
                self._inc("five_minute_footprint_without_aligned_local_state")
                continue
            if self.local_side is not side or self.last_direction_pivot is None:
                self._inc("five_minute_footprint_without_aligned_local_bos")
                continue
            self._pending_formations[zone.zone_id] = zone
            self._inc(
                "five_minute_ob_waiting_complete_constituent_flow"
                if zone.kind is ZoneKind.ORDER_BLOCK
                else "five_minute_fvg_waiting_middle_candle_flow"
            )
            self._trace(
                "local_continuation_footprint_waiting_complete_flow",
                bar.ts_close_ns,
                zone_id=zone.zone_id,
                zone_kind=zone.kind.value,
                side=side.name,
                local_state_time_ns=state.event_time_ns,
                local_state_sequence=state.sequence,
                local_direction_pivot_id=self.last_direction_pivot.pivot_id,
                rule_provenance=(
                    LOCAL_AUCTION_CONTINUATION_RULE,
                    LOCAL_FVG_CONTINUATION_RULE,
                ),
            )
        self.decision_structure.observe_price(bar)

    def _formation_observations(self, zone: PriceZone) -> list[FlowObservation]:
        if zone.kind is ZoneKind.ORDER_BLOCK:
            start = zone.formed_time_ns
            end = zone.observed_time_ns
        elif zone.kind is ZoneKind.FVG:
            indices = tuple(zone.formation_indices)
            if len(indices) != 3:
                return []
            middle_index = indices[1]
            bars = self.footprints.bars
            if middle_index <= 0 or middle_index >= len(bars):
                return []
            start = bars[middle_index - 1].ts_close_ns
            end = bars[middle_index].ts_close_ns
        else:
            return []
        return [
            item
            for item in self.flow_analyzer.history
            if start < item.ts_close_ns <= end
        ]

    def _formation_flow(
        self,
        zone: PriceZone,
        side: Side,
    ) -> tuple[list[FlowObservation], float, float] | None:
        observations = self._formation_observations(zone)
        if not observations:
            return None
        cumulative = sum(item.signed_taker_quote for item in observations)
        progress = self._progress(side, observations[0].open, observations[-1].close)
        aligned = [
            item
            for item in observations
            if item.active
            and item.directed
            and item.material_progress
            and self._aligned(side, item.signed_taker_quote)
            and (item.body > 0.0 if side is Side.LONG else item.body < 0.0)
        ]
        if not self._aligned(side, cumulative) or progress <= 0.0 or not aligned:
            self._inc(
                "local_ob_rejected_without_aligned_formation_flow"
                if zone.kind is ZoneKind.ORDER_BLOCK
                else "local_fvg_rejected_without_aligned_middle_flow"
            )
            return None
        self._inc(
            "local_ob_flow_validated"
            if zone.kind is ZoneKind.ORDER_BLOCK
            else "local_fvg_middle_flow_validated"
        )
        return observations, cumulative, progress

    @property
    def local_footprint_diagnostics(self) -> dict[str, Any]:
        return {
            "footprints": (ZoneKind.ORDER_BLOCK.value, ZoneKind.FVG.value),
            "rule_provenance": LOCAL_FVG_CONTINUATION_RULE,
        }


class EasyChartRE1AuctionRouterFVGBundle(EasyChartRE1AuctionRouterBundle):
    """Integrated rejection, OB/FVG continuation and horizontal flip policy."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.local_continuation = LocalFootprintContinuationEngine(
            symbol,
            tick_size,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["local_continuation"] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["local_ob_fvg_continuation"] = {
            "engine": self.local_continuation.local_footprint_diagnostics,
            "rules": (
                LOCAL_AUCTION_CONTINUATION_RULE,
                LOCAL_FVG_CONTINUATION_RULE,
                LOCAL_CONTINUATION_RESPONSE_FLOW_RULE,
                MECHANISM_SPECIFIC_CONTEXT_ROUTER_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1AuctionRouterFVGBundle
StrategyClass = EasyChartRE1LocalAuctionStrategy

"""Channel-phase correction for the response-confirmed RE1 candidate.

A parallel channel is not merely two simultaneously tradable edges.  The source
material's four-point sequence is ordered: a channel is first defined by three
alternating pivots, and the next valid interaction is the opposite edge.  The
machine previously exposed both edges immediately after construction.  It could
therefore buy the lower edge of a newly formed ascending channel (or sell the
upper edge of a newly formed descending channel) before price had completed the
intervening trip across the channel.  That is not a fourth point; it is a failed
channel trying to repeat its third point.

The main channel edge is represented twice in the inherited structure book:
once as the original trend line and once as the channel edge.  Hiding only the
channel label therefore left the same invalid point-three repeat tradable under
the trend-line label.  This module treats those two labels as one price fact.

Policy
------
* ascending channel: upper edge first, then lower/main edge;
* descending channel: lower edge first, then upper/main edge;
* before the opposite fourth point, both the channel main-edge snapshot and its
  coincident source trend line are unavailable to setup discovery;
* each projected boundary remains first-interaction only under the inherited
  lifecycle.

No clock, volatility, R-multiple, score, outcome information or trade-count
rule is introduced. Entry confirmation, stops, targets, macro routing,
execution, costs and post-entry management are unchanged.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import StructureFamily
from diagonal_core_v20 import DiagonalOnlyContextStructureBook
from domain import Candle
from easychart_re1_confirmed import (
    ConfirmedSelectiveScenarioEngine,
    EasyChartRE1ConfirmedBundle,
)


CHANNEL_PHASE_RULE = (
    "SOURCE_EXPLICIT:"
    "CHANNEL_THREE_ANCHORS_REQUIRE_OPPOSITE_FOURTH_POINT_BEFORE_MAIN_EDGE_CAN_TRADE_AGAIN"
)
CHANNEL_LABEL_UNIFICATION_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "CHANNEL_MAIN_EDGE_AND_ITS_SOURCE_TREND_LINE_ARE_ONE_PRICE_BOUNDARY_FOR_PHASE_ROUTING"
)
if CHANNEL_PHASE_RULE not in _contracts.SOURCE_RULES:
    _contracts.SOURCE_RULES += (CHANNEL_PHASE_RULE,)
if CHANNEL_LABEL_UNIFICATION_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (CHANNEL_LABEL_UNIFICATION_RULE,)


class ChannelPhaseStructureBook(DiagonalOnlyContextStructureBook):
    """Expose channel boundaries in their causal alternating order."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._opposite_edge_reached: set[str] = set()

    @staticmethod
    def _first_edge(channel: Any) -> str:
        return "UPPER" if channel.direction == "ASCENDING" else "LOWER"

    @staticmethod
    def _main_edge(channel: Any) -> str:
        return "LOWER" if channel.direction == "ASCENDING" else "UPPER"

    def _channel_for_main_line(self, source_structure_id: str, time_ns: int) -> Any | None:
        line = next(
            (item for item in self.trend_lines if item.structure_id == source_structure_id),
            None,
        )
        if line is None:
            return None
        candidates = [
            channel
            for channel in self.active_channels(time_ns)
            if channel.main_first_pivot_id == line.first_pivot_id
            and channel.main_second_pivot_id == line.second_pivot_id
        ]
        return max(
            candidates,
            key=lambda item: (item.observed_time_ns, item.channel_id),
            default=None,
        )

    def boundaries_at(self, time_ns: int):  # type: ignore[no-untyped-def]
        output = super().boundaries_at(time_ns)
        filtered = []
        for zone in output:
            if zone.family is StructureFamily.TREND_LINE:
                channel = self._channel_for_main_line(zone.source_structure_id, time_ns)
                if (
                    channel is not None
                    and channel.channel_id not in self._opposite_edge_reached
                ):
                    self._inc("channel_source_trend_line_hidden_before_opposite_fourth_point")
                    continue
                filtered.append(zone)
                continue

            if zone.family is not StructureFamily.CHANNEL:
                filtered.append(zone)
                continue
            channel = self.channel_for_boundary(zone.source_structure_id)
            if channel is None:
                filtered.append(zone)
                continue
            edge = zone.source_structure_id.rsplit(":", 1)[-1]
            if (
                edge == self._main_edge(channel)
                and channel.channel_id not in self._opposite_edge_reached
            ):
                self._inc("channel_main_edge_hidden_before_opposite_fourth_point")
                continue
            filtered.append(zone)
        return filtered

    def observe_price(self, bar: Candle) -> None:
        # Detect the ordered fourth point before the inherited first-touch
        # lifecycle retires the touched edge. The current completed decision bar
        # has already been classified, so an unlocked main edge can only affect
        # a later bar.
        for channel in list(self.active_channels(bar.ts_close_ns)):
            if (
                channel.channel_id in self._opposite_edge_reached
                or bar.ts_close_ns <= channel.observed_time_ns
            ):
                continue
            edge = self._first_edge(channel)
            snapshot = self.channel_edge_snapshot(channel, edge, bar.ts_close_ns)
            if bar.low <= snapshot.upper and bar.high >= snapshot.lower:
                self._opposite_edge_reached.add(channel.channel_id)
                self._inc("channel_opposite_fourth_point_reached")
        super().observe_price(bar)

    @property
    def phase_diagnostics(self) -> dict[str, Any]:
        return {
            "channels_with_opposite_fourth_point": len(self._opposite_edge_reached),
            "rules": (CHANNEL_PHASE_RULE, CHANNEL_LABEL_UNIFICATION_RULE),
        }


class PhaseConfirmedSelectiveScenarioEngine(ConfirmedSelectiveScenarioEngine):
    """Confirmed diagonal engine with ordered channel-edge availability."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = ChannelPhaseStructureBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
        )


class EasyChartRE1PhaseBundle(EasyChartRE1ConfirmedBundle):
    """Confirmed candidate with the source's ordered four-point channel phase."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = PhaseConfirmedSelectiveScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["micro"] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["channel_phase_policy"] = self.micro.structure.phase_diagnostics
        return output


MultiScaleScenarioBundle = EasyChartRE1PhaseBundle

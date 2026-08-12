"""Causal lifecycle and human-to-program translations for EasyChart v5.

The source repeatedly uses trend lines and channels as structures which may
survive a successful touch, but cease to support the original interpretation
when price closes through the projected boundary. It also distinguishes a fast
fakeout from an ordinary close-back-inside by the conspicuous rejection tail.
A charting human updates those states naturally; software must state them.

This module changes semantic state only. NautilusTrader remains responsible
for orders, fills, positions, fees and NAV.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import StructureFamily
from domain import Candle
from easychart_zones import ZoneSide
from structure_v5 import CausalStructureBook


DIAGONAL_INVALIDATION_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "PROJECTED_DIAGONAL_RETIRES_AFTER_BODY_CLOSE_THROUGH_ITS_INVALIDATION_SIDE"
)
ACCEPTANCE_STOP_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "ACCEPTANCE_HARD_STOP_MUST_BE_BEYOND_THE_COMPLETED_RETEST_EXTREME"
)
FAST_FAKEOUT_WICK_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "FAST_FAKEOUT_EXCURSION_WICK_EXCEEDS_REAL_BODY"
)

for _rule in (
    DIAGONAL_INVALIDATION_RULE,
    ACCEPTANCE_STOP_RULE,
    FAST_FAKEOUT_WICK_RULE,
):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


class LifecycleAwareStructureBook(CausalStructureBook):
    """Remove broken projected boundaries without expiring valid bounces.

    The prior diagnostic translation retired a line or channel edge on any
    touch. That was too strong: the material explicitly depicts structures
    surviving bounces. The executable lifecycle is now narrower:

    * a support diagonal retires only after a completed bar closes below it;
    * a resistance diagonal retires only after a completed bar closes above it;
    * channel edges retire independently;
    * an already selected structure is still protected from duplicate trading
      by the scenario engine's causal-episode registry.

    Thus a valid bounce does not erase the structure, while a body-confirmed
    break cannot reappear later as an untouched opportunity.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._retired_line_ids: set[str] = set()
        self._retired_channel_edges: set[str] = set()
        self._boundary_retired_time_ns: dict[str, int] = {}

    def active_trend_lines(self, time_ns: int):  # type: ignore[no-untyped-def]
        return [
            line
            for line in super().active_trend_lines(time_ns)
            if line.structure_id not in self._retired_line_ids
        ]

    def active_channels(self, time_ns: int):  # type: ignore[no-untyped-def]
        output = []
        for channel in super().active_channels(time_ns):
            lower_id = f"{channel.channel_id}:LOWER"
            upper_id = f"{channel.channel_id}:UPPER"
            if (
                lower_id not in self._retired_channel_edges
                or upper_id not in self._retired_channel_edges
            ):
                output.append(channel)
        return output

    def boundaries_at(self, time_ns: int):  # type: ignore[no-untyped-def]
        output = super().boundaries_at(time_ns)
        return [
            zone
            for zone in output
            if not (
                zone.family is StructureFamily.CHANNEL
                and zone.source_structure_id in self._retired_channel_edges
            )
        ]

    @staticmethod
    def _closed_through(bar: Candle, lower: float, upper: float, side: ZoneSide) -> bool:
        if side is ZoneSide.SUPPORT:
            return bar.close < lower
        return bar.close > upper

    def _retire_line(self, structure_id: str, time_ns: int) -> None:
        if structure_id in self._retired_line_ids:
            return
        self._retired_line_ids.add(structure_id)
        self._boundary_retired_time_ns[structure_id] = time_ns
        self._inc("trend_line_body_close_invalidated")

    def _retire_channel_edge(self, source_structure_id: str, time_ns: int) -> None:
        if source_structure_id in self._retired_channel_edges:
            return
        self._retired_channel_edges.add(source_structure_id)
        self._boundary_retired_time_ns[source_structure_id] = time_ns
        edge = source_structure_id.rsplit(":", 1)[-1].lower()
        self._inc(f"channel_{edge}_body_close_invalidated")

    def observe_price(self, bar: Candle) -> None:
        """Apply body-close invalidation after the current interaction is read.

        The scenario engine invokes this after classifying the completed
        decision bar. An accepted break can therefore arm one retest episode,
        but the broken diagonal is removed from the future fresh-opportunity
        set. A wick excursion which closes back on the valid side remains a
        rejection/fakeout rather than a structural deletion.
        """
        for line in CausalStructureBook.active_trend_lines(self, bar.ts_close_ns):
            if line.structure_id in self._retired_line_ids:
                continue
            snapshot = self._line_snapshot(line, bar.ts_close_ns)
            if self._closed_through(bar, snapshot.lower, snapshot.upper, snapshot.side):
                self._retire_line(line.structure_id, bar.ts_close_ns)

        for channel in CausalStructureBook.active_channels(self, bar.ts_close_ns):
            for edge in ("LOWER", "UPPER"):
                source_id = f"{channel.channel_id}:{edge}"
                if source_id in self._retired_channel_edges:
                    continue
                snapshot = self.channel_edge_snapshot(channel, edge, bar.ts_close_ns)
                if self._closed_through(bar, snapshot.lower, snapshot.upper, snapshot.side):
                    self._retire_channel_edge(source_id, bar.ts_close_ns)

        # Preserve the audited horizontal-pivot first-touch lifecycle.
        super().observe_price(bar)

    def boundary_retired_time_ns(self, source_structure_id: str) -> int | None:
        return self._boundary_retired_time_ns.get(source_structure_id)

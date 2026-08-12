"""Causal lifecycle and acceptance-stop translations for EasyChart v5.

The source treats a trend line or channel edge as a pre-existing market
structure whose next interaction is meaningful.  A charting human naturally
stops treating that exact projected boundary as fresh after price has already
met or crossed it.  Software must state that lifecycle explicitly; otherwise
an old broken diagonal can remain a trade candidate for days.

This module deliberately changes only semantic state.  NautilusTrader remains
responsible for orders, fills, positions, fees and NAV.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioSetup, StructureFamily
from domain import Candle, Side
from structure_v5 import CausalStructureBook


FIRST_INTERACTION_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "FIRST_PROJECTED_STRUCTURE_INTERACTION_RETIRES_THAT_BOUNDARY"
)
ACCEPTANCE_STOP_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "ACCEPTANCE_HARD_STOP_BEYOND_RETEST_EXTREME_AND_CAUSAL_ORIGIN"
)

# ``provenance()`` reads the module globals at plan-construction time.  Extend
# the auditable translation ledger once, without relabelling either rule as an
# explicit statement by the source.
for _rule in (FIRST_INTERACTION_RULE, ACCEPTANCE_STOP_RULE):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


class LifecycleAwareStructureBook(CausalStructureBook):
    """A structure book whose projected boundaries have a first-touch life.

    Horizontal pivots already had a first-touch lifecycle in v5.  Trend lines
    and channel edges did not: every non-superseded diagonal was emitted
    forever, even after a decisive break.  This subclass gives each projected
    line or edge the same causal property while retaining the immutable
    snapshots already attached to an armed setup.

    Channel edges are retired independently.  Touching the lower edge does not
    destroy the opposite upper edge, which can still be the same-leg rotation
    objective.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._retired_line_ids: set[str] = set()
        self._retired_channel_edges: set[str] = set()
        self._boundary_retired_time_ns: dict[str, int] = {}

    @staticmethod
    def _bar_touches_band(bar: Candle, lower: float, upper: float) -> bool:
        return bar.low <= upper and bar.high >= lower

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
        # The base method dynamically dispatches ``active_trend_lines`` and
        # ``active_channels``.  A channel with one live edge still appears, so
        # remove the independently retired edge from its two generated
        # snapshots here.
        output = super().boundaries_at(time_ns)
        return [
            zone
            for zone in output
            if not (
                zone.family is StructureFamily.CHANNEL
                and zone.source_structure_id in self._retired_channel_edges
            )
        ]

    def _retire_line(self, structure_id: str, time_ns: int) -> None:
        if structure_id in self._retired_line_ids:
            return
        self._retired_line_ids.add(structure_id)
        self._boundary_retired_time_ns[structure_id] = time_ns
        self._inc("trend_line_first_interaction_retired")

    def _retire_channel_edge(self, source_structure_id: str, time_ns: int) -> None:
        if source_structure_id in self._retired_channel_edges:
            return
        self._retired_channel_edges.add(source_structure_id)
        self._boundary_retired_time_ns[source_structure_id] = time_ns
        edge = source_structure_id.rsplit(":", 1)[-1].lower()
        self._inc(f"channel_{edge}_first_interaction_retired")

    def observe_price(self, bar: Candle) -> None:
        """Retire every diagonal boundary touched by this completed bar.

        The scenario engine calls this *after* classifying the current decision
        bar.  Consequently the first interaction is still available to create
        one setup, but neither the selected boundary nor another crossed
        lookalike can masquerade as fresh on a later bar.
        """
        for line in CausalStructureBook.active_trend_lines(self, bar.ts_close_ns):
            if line.structure_id in self._retired_line_ids:
                continue
            snapshot = self._line_snapshot(line, bar.ts_close_ns)
            if self._bar_touches_band(bar, snapshot.lower, snapshot.upper):
                self._retire_line(line.structure_id, bar.ts_close_ns)

        for channel in CausalStructureBook.active_channels(self, bar.ts_close_ns):
            for edge in ("LOWER", "UPPER"):
                source_id = f"{channel.channel_id}:{edge}"
                if source_id in self._retired_channel_edges:
                    continue
                snapshot = self.channel_edge_snapshot(channel, edge, bar.ts_close_ns)
                if self._bar_touches_band(bar, snapshot.lower, snapshot.upper):
                    self._retire_channel_edge(source_id, bar.ts_close_ns)

        # Preserve the already-audited horizontal-pivot lifecycle.
        super().observe_price(bar)

    def boundary_retired_time_ns(self, source_structure_id: str) -> int | None:
        return self._boundary_retired_time_ns.get(source_structure_id)


class CausalAcceptanceGeometryMixin:
    """Translate accepted-break retest failure into an executable hard stop.

    A one-tick stop behind a projected zero-width line can lie inside the very
    candle used to confirm the retest.  That creates artificial 20R--100R
    plans which are stopped by already-observed price.  The protective stop is
    therefore placed beyond the retest candle's opposite wick.  For a
    non-channel breakout the pre-existing causal origin remains authoritative,
    so the farther of origin and retest extreme is used.
    """

    def _acceptance_stop(self, setup: ScenarioSetup, time_ns: int) -> float | None:
        if not self.trigger_detector.bars:
            raise RuntimeError("acceptance stop requested before a trigger bar")
        current = self.trigger_detector.bars[-1]
        if current.ts_close_ns != time_ns:
            raise RuntimeError("acceptance stop must use the current completed retest bar")

        members, lower, upper = self._projected_bounds(setup, time_ns)
        retest_stop = (
            current.low - self.tick_size
            if setup.side is Side.LONG
            else current.high + self.tick_size
        )
        has_channel = any(member.family is StructureFamily.CHANNEL for member in members)

        if has_channel:
            projected_stop = (
                lower - self.tick_size
                if setup.side is Side.LONG
                else upper + self.tick_size
            )
            stop = (
                min(projected_stop, retest_stop)
                if setup.side is Side.LONG
                else max(projected_stop, retest_stop)
            )
            basis = "CHANNEL_RETEST_EXTREME"
        else:
            origin = setup.acceptance_origin
            if origin is None:
                return None
            origin_stop = (
                origin.price - self.tick_size
                if setup.side is Side.LONG
                else origin.price + self.tick_size
            )
            projected_stop = origin_stop
            stop = (
                min(origin_stop, retest_stop)
                if setup.side is Side.LONG
                else max(origin_stop, retest_stop)
            )
            basis = "CAUSAL_ORIGIN_AND_RETEST_EXTREME"

        projected_inside_retest = (
            projected_stop >= current.low
            if setup.side is Side.LONG
            else projected_stop <= current.high
        )
        if projected_inside_retest:
            self._inc("acceptance_projected_stop_was_inside_retest_bar")

        if setup.side is Side.LONG and not stop < current.low:
            raise RuntimeError("long acceptance stop was not moved beyond retest low")
        if setup.side is Side.SHORT and not stop > current.high:
            raise RuntimeError("short acceptance stop was not moved beyond retest high")

        self._trace(
            "acceptance_stop_fixed_before_submission",
            time_ns,
            setup,
            stop=stop,
            projected_stop=projected_stop,
            retest_extreme=current.low if setup.side is Side.LONG else current.high,
            basis=basis,
            rule_provenance=ACCEPTANCE_STOP_RULE,
        )
        return stop

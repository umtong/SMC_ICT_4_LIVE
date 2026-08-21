"""Confluence S/R flip defined by one breakout body clearing a structure stack.

The live example's three resistances need not be mathematically overlapping at
one tick.  They are one causal fact because the same completed five-minute body
crosses all of them, the next bar holds beyond the outermost barrier, and the
whole cleared stack is later retested as support.  This is a stronger and less
arbitrary grouping rule than a fitted price-distance tolerance.
"""
from __future__ import annotations

from contracts_v5 import StructureFamily, StructureZone
from domain import Candle, Side
from easychart_re1_confluence_flip import ConfluenceAcceptanceEngine
from easychart_re1_confluence_flip_v2 import (
    EasyChartRE1ConfluenceFlipBundle as _BaseBundle,
)
from easychart_zones import ZoneSide


class BreakoutStackConfluenceAcceptanceEngine(ConfluenceAcceptanceEngine):
    """Group independent structures only when one 5m body clears all of them."""

    def _candidate_clusters(
        self,
        bar: Candle,
        previous: Candle,
    ) -> list[tuple[Side, tuple[StructureZone, ...], float, float]]:
        available = [
            zone
            for zone in self.structure.boundaries_at(bar.ts_close_ns)
            if zone.observed_time_ns < bar.ts_close_ns
            and zone.source_structure_id not in self._claimed_structures
        ]
        output: list[tuple[Side, tuple[StructureZone, ...], float, float]] = []
        for zone_side, side in (
            (ZoneSide.RESISTANCE, Side.LONG),
            (ZoneSide.SUPPORT, Side.SHORT),
        ):
            raw: list[StructureZone] = []
            for zone in available:
                if zone.side is not zone_side:
                    continue
                if side is Side.LONG:
                    crossed = (
                        previous.close <= zone.upper
                        and bar.close > zone.upper
                        and bar.close > bar.open
                        and bar.low <= zone.upper
                    )
                else:
                    crossed = (
                        previous.close >= zone.lower
                        and bar.close < zone.lower
                        and bar.close < bar.open
                        and bar.high >= zone.lower
                    )
                if crossed:
                    raw.append(zone)
            members = self._distinct_members(tuple(raw), bar.ts_close_ns)
            families = {zone.family for zone in members}
            if len(members) < 2 or len(families) < 2:
                if raw:
                    self._cinc("body_crossed_stack_lacked_distinct_multi_family_confluence")
                continue
            lower = min(zone.lower for zone in members)
            upper = max(zone.upper for zone in members)
            output.append((side, members, lower, upper))
            self._cinc("multi_family_breakout_stack_identified")
        return output


class EasyChartRE1BreakoutStackConfluenceBundle(_BaseBundle):
    """Prior reversal/OB account plus causal same-body structure-stack flips."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.confluence_flip = BreakoutStackConfluenceAcceptanceEngine(
            symbol,
            tick_size,
            scale_name="CONFLUENCE_FLIP",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["confluence_flip"] = 0


MultiScaleScenarioBundle = EasyChartRE1BreakoutStackConfluenceBundle

"""Event-local OB/FVG detection without a global zone-lifecycle scan.

The source-defined detection formulas are inherited unchanged from the v3
component. v5 only asks newly created footprints to confirm an already-open
structure event, so scanning every historical zone on every one-minute bar is
both unnecessary and quadratic. Selected footprint lifecycle is owned by the
scenario setup and remains fully auditable.
"""
from __future__ import annotations

from domain import Candle
from easychart_zones import EasyChartZoneDetector, PriceZone


class EventLocalZoneDetector(EasyChartZoneDetector):
    def on_bar(self, bar: Candle) -> list[PriceZone]:
        if self.bars and bar.ts_close_ns <= self.bars[-1].ts_close_ns:
            raise ValueError("bars must arrive in strictly increasing close time")
        self.bars.append(bar)
        index = len(self.bars) - 1
        created: list[PriceZone] = []
        detect_order_block = getattr(self, "_detect_order_block", None)
        if detect_order_block is None:
            detect_order_block = self._ob
        order_block = detect_order_block(index)
        if order_block is not None:
            self.zones.append(order_block)
            created.append(order_block)
        detect_fvg = getattr(self, "_detect_fvg", None)
        if detect_fvg is None:
            detect_fvg = self._fvg
        fvg = detect_fvg(index)
        if fvg is not None:
            self.zones.append(fvg)
            created.append(fvg)
        return created

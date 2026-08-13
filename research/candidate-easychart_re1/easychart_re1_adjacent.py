"""Source-candle location translation for mechanism-complete RE1.

An order block body does not always straddle a one-tick projected trend line.
The source examples require the *order-block candle* to occur at the structure:
its wick can touch the line while the body closes away.  The stricter exact-body
overlap variant is retained as an ablation, while this candidate uses a
price-factual adjacency rule with no tolerance: the footprint's source candle
range itself must intersect the projected structure at that completed candle.
Other formation candles cannot qualify it by proxy.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioSetup
from easychart_re1_complete import EasyChartRE1CompleteBundle
from easychart_re1_impulse import ImpulseHorizontalScenarioEngine, ImpulseNaturalScenarioEngine
from easychart_re1_liquidity import MajorLiquidityScenarioEngine
from easychart_zones import PriceZone


SOURCE_CANDLE_LOCATION_RULE = (
    "SOURCE_EXPLICIT:"
    "THE_OB_OR_FVG_SOURCE_CANDLE_RANGE_ITSELF_INTERSECTS_THE_TRADED_STRUCTURE_AND_CLOSES_AWAY"
)
if SOURCE_CANDLE_LOCATION_RULE not in _contracts.SOURCE_RULES:
    _contracts.SOURCE_RULES += (SOURCE_CANDLE_LOCATION_RULE,)


class SourceCandleLocatedMixin:
    """Use only the footprint's own source candle for structure location."""

    def _formation_touches_context(self, zone: PriceZone, setup: ScenarioSetup) -> bool:
        detector = self.detectors[self.trigger_minutes]
        if zone.formed_index < 0 or zone.formed_index >= len(detector.bars):
            return False
        source = detector.bars[zone.formed_index]
        _, lower, upper = self._projected_bounds(setup, source.ts_close_ns)
        touches = source.low <= upper and source.high >= lower
        if not touches:
            self._inc("footprint_source_candle_not_at_structure_deferred")
        return touches


class AdjacentNaturalScenarioEngine(SourceCandleLocatedMixin, ImpulseNaturalScenarioEngine):
    pass


class AdjacentHorizontalScenarioEngine(SourceCandleLocatedMixin, ImpulseHorizontalScenarioEngine):
    pass


class AdjacentMajorLiquidityScenarioEngine(SourceCandleLocatedMixin, MajorLiquidityScenarioEngine):
    pass


class EasyChartRE1AdjacentCompleteBundle(EasyChartRE1CompleteBundle):
    """Complete mechanism router with source-candle rather than body overlap."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = AdjacentNaturalScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.horizontal = AdjacentHorizontalScenarioEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.liquidity = AdjacentMajorLiquidityScenarioEngine(
            symbol,
            tick_size,
            scale_name="MAJOR_LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.horizontal_flip = AdjacentHorizontalScenarioEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL_SR_FLIP",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        for key in ("micro", "horizontal", "liquidity", "horizontal_flip"):
            self._audit_offsets[key] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["footprint_location_policy"] = {
            "name": "SOURCE_CANDLE_RANGE_INTERSECTS_PROJECTED_STRUCTURE",
            "rule_provenance": SOURCE_CANDLE_LOCATION_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1AdjacentCompleteBundle

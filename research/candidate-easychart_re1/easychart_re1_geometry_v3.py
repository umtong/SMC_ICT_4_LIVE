"""Natural geometry with the original accepted horizontal S/R-flip structure.

The complete router owns a distinct accepted-break family whose structure book
turns a genuinely broken repeated-defense level into a later first-retest S/R
flip.  It must not be replaced by a second generic horizontal detector.  This
module preserves that specialized structure while applying the same natural
stop/target geometry used by the other families.
"""
from __future__ import annotations

from typing import Any

from easychart_re1_adjacent import SourceCandleLocatedMixin
from easychart_re1_complete import AcceptedHorizontalFlipScenarioEngine
from easychart_re1_geometry_v2 import (
    EasyChartRE1GeometryV2Bundle,
    NaturalGeometryV2Mixin,
)


class GeometryV3HorizontalFlipScenarioEngine(
    NaturalGeometryV2Mixin,
    SourceCandleLocatedMixin,
    AcceptedHorizontalFlipScenarioEngine,
):
    pass


class EasyChartRE1GeometryV3Bundle(EasyChartRE1GeometryV2Bundle):
    """Proven router and natural geometry with non-duplicated S/R-flip family."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.horizontal_flip = GeometryV3HorizontalFlipScenarioEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL_SR_FLIP",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["horizontal_flip"] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["natural_geometry_v3"] = {
            "horizontal_flip": self.horizontal_flip.natural_geometry_diagnostics,
            "structure_policy": "BROKEN_REPEATED_DEFENSE_FIRST_RETEST_SR_FLIP",
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1GeometryV3Bundle

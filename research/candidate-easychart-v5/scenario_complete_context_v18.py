"""Complete source-faithful higher-timeframe context for the active policy."""
from __future__ import annotations

from scenario_higher_timeframe_v15 import HigherTimeframeAcceptanceBundleV15
from structure_admission_v5 import SourceFaithfulStructureBook


class SourceFaithfulHigherTimeframeBundleV18(HigherTimeframeAcceptanceBundleV15):
    """Replace only the two higher-timeframe location books before data arrives."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.higher_structure = SourceFaithfulStructureBook(symbol, 60, tick_size)
        self._higher_snapshot_time_ns = None
        self._higher_boundaries_before_retirement = ()
        self.four_hour_structure = SourceFaithfulStructureBook(symbol, 240, tick_size)
        self._four_hour_snapshot_time_ns = None
        self._four_hour_boundaries_before_retirement = ()

"""Acceptance-gated EasyChart v4 runtime with horizontal box ranges."""
from __future__ import annotations

from scenario_bundle_v4 import _EvidenceDetectorView
from scenario_runtime_v4_acceptance_gate import (
    SourceFaithfulRetestEntryGatedBundle,
    SourceFaithfulRetestEntryGatedEngine,
)
from market_structure_horizontal_range_v4 import (
    HorizontalRangeMarketStructureDetector,
)


class HorizontalRangeStructuralScenarioEngine(
    SourceFaithfulRetestEntryGatedEngine,
):
    """Use the frozen trade grammar on sloped structures and box ranges."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.structure = HorizontalRangeMarketStructureDetector(
            self.symbol,
            self.context_minutes,
            self.tick_size,
            pivot_spans=(2, 6),
        )


class HorizontalRangeResearchBundle(SourceFaithfulRetestEntryGatedBundle):
    """Strict 1h event routing plus source-defined horizontal ranges."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        self.symbol = symbol
        self.macro = HorizontalRangeStructuralScenarioEngine(
            symbol,
            tick_size,
            scale_name="MACRO",
            context_minutes=60,
            trigger_minutes=5,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.micro = HorizontalRangeStructuralScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            context_minutes=15,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.detectors = _EvidenceDetectorView(
            {
                60: self.macro.structure,
                15: self.micro.structure,
                5: self.macro.trigger_detector,
            },
            (self.micro.trigger_detector,),
        )
        self._claimed_episodes = set()
        self._bundle_trace = []
        self._routing_diagnostics: dict[str, int] = {}
        self._last_context_key: tuple[str | None, str] | None = None


__all__ = [
    "HorizontalRangeResearchBundle",
    "HorizontalRangeStructuralScenarioEngine",
]

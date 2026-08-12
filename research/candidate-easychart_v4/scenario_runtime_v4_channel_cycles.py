"""Acceptance-gated EasyChart v4 runtime with independent channel cycles."""
from __future__ import annotations

from scenario_bundle_v4 import _EvidenceDetectorView
from scenario_runtime_v4_acceptance_gate import (
    SourceFaithfulRetestEntryGatedBundle,
    SourceFaithfulRetestEntryGatedEngine,
)
from market_structure_channel_cycles_v4 import (
    CyclicSourceFaithfulMarketStructureDetector,
)


class ChannelCycleStructuralScenarioEngine(
    SourceFaithfulRetestEntryGatedEngine,
):
    """Use the frozen setup grammar with complete channel-wave rearming."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.structure = CyclicSourceFaithfulMarketStructureDetector(
            self.symbol,
            self.context_minutes,
            self.tick_size,
            pivot_spans=(2, 6),
        )


class ChannelCycleResearchBundle(SourceFaithfulRetestEntryGatedBundle):
    """Strict 1h context and source-faithful alternating channel waves."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        self.symbol = symbol
        self.macro = ChannelCycleStructuralScenarioEngine(
            symbol,
            tick_size,
            scale_name="MACRO",
            context_minutes=60,
            trigger_minutes=5,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.micro = ChannelCycleStructuralScenarioEngine(
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
    "ChannelCycleResearchBundle",
    "ChannelCycleStructuralScenarioEngine",
]

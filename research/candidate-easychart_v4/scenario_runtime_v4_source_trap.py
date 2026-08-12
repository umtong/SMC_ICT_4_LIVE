"""EasyChart v4 runtimes with source-faithful Fakeout/Trap semantics."""
from __future__ import annotations

from scenario_bundle_v4 import _EvidenceDetectorView
from scenario_runtime_v4_preserved import (
    ResearchScenarioBundleV4 as _BasePreservedBundle,
    SameSidePreservingStructuralScenarioEngine,
)
from scenario_runtime_v4_refined import (
    ResearchScenarioBundleV4 as _BaseRetestBundle,
    RetestConfirmedStructuralScenarioEngine,
)

from market_structure_trap_v4 import SourceFaithfulMarketStructureDetector


class SourceFaithfulRetestStructuralScenarioEngine(
    RetestConfirmedStructuralScenarioEngine,
):
    """Retest-confirmed context with corrected Fakeout versus Trap detection."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.structure = SourceFaithfulMarketStructureDetector(
            self.symbol,
            self.context_minutes,
            self.tick_size,
            pivot_spans=(2, 6),
        )


class SourceFaithfulSameSideStructuralScenarioEngine(
    SameSidePreservingStructuralScenarioEngine,
):
    """Same-side context continuity with corrected Trap state sequence."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.structure = SourceFaithfulMarketStructureDetector(
            self.symbol,
            self.context_minutes,
            self.tick_size,
            pivot_spans=(2, 6),
        )


def _initialize_bundle(
    bundle,
    *,
    engine_type,
    symbol: str,
    tick_size: float,
    minimum_gross_rr: float,
) -> None:
    bundle.symbol = symbol
    bundle.macro = engine_type(
        symbol,
        tick_size,
        scale_name="MACRO",
        context_minutes=60,
        trigger_minutes=5,
        minimum_gross_rr=minimum_gross_rr,
    )
    bundle.micro = engine_type(
        symbol,
        tick_size,
        scale_name="MICRO",
        context_minutes=15,
        trigger_minutes=1,
        minimum_gross_rr=minimum_gross_rr,
    )
    bundle.detectors = _EvidenceDetectorView(
        {
            60: bundle.macro.structure,
            15: bundle.micro.structure,
            5: bundle.macro.trigger_detector,
        },
        (bundle.micro.trigger_detector,),
    )
    bundle._claimed_episodes = set()
    bundle._bundle_trace = []
    bundle._routing_diagnostics = {}
    bundle._last_context_key = None


class SourceFaithfulRetestBundle(_BaseRetestBundle):
    """Conservative accepted-break retest hierarchy with corrected Trap."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        _initialize_bundle(
            self,
            engine_type=SourceFaithfulRetestStructuralScenarioEngine,
            symbol=symbol,
            tick_size=tick_size,
            minimum_gross_rr=minimum_gross_rr,
        )


class SourceFaithfulSameSideBundle(_BasePreservedBundle):
    """Same-side context continuity hierarchy with corrected Trap."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        _initialize_bundle(
            self,
            engine_type=SourceFaithfulSameSideStructuralScenarioEngine,
            symbol=symbol,
            tick_size=tick_size,
            minimum_gross_rr=minimum_gross_rr,
        )


__all__ = [
    "SourceFaithfulRetestBundle",
    "SourceFaithfulRetestStructuralScenarioEngine",
    "SourceFaithfulSameSideBundle",
    "SourceFaithfulSameSideStructuralScenarioEngine",
]

"""Current full skilled router with persistent one-bar absorption entries.

The higher daily/H4 auction and nested local continuation families remain
unchanged.  Only the local rejection owner is replaced: single-bar current
boundary absorption must survive its first later completed-minute response.
Accepted transfers, visual footprint first returns and repeated absorption keep
their existing causal policies.
"""
from __future__ import annotations

from typing import Any

from easychart_re1_absorption_response import (
    CURRENT_ABSORPTION_FIRST_RESPONSE_RULE,
    EasyChartRE1PersistentAbsorptionResponseBundle,
)
from easychart_re1_skilled_continuation import (
    EasyChartRE1SkilledContinuationBundle,
)
from easychart_re1_skilled_integrated import (
    EasyChartRE1SkilledIntegratedBundle,
)


class PersistentResponseSkilledIntegratedBundle(
    EasyChartRE1SkilledIntegratedBundle,
):
    """Mechanism router whose rejection owner requires persistent absorption."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.reversal = EasyChartRE1PersistentAbsorptionResponseBundle(
            symbol,
            tick_size,
            minimum_gross_rr,
        )
        self.detectors = self.reversal.detectors

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["persistent_absorption_rejection_owner"] = {
            "owner": type(self.reversal).__name__,
            "rule": CURRENT_ABSORPTION_FIRST_RESPONSE_RULE,
        }
        return output


class EasyChartRE1SkilledContinuationPersistentResponseBundle(
    EasyChartRE1SkilledContinuationBundle,
):
    """Full opportunity stream with one causal change to local absorption."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)

        # Chain: continuation -> H4 auction -> H4 router -> daily router ->
        # local mechanism router.  Replace only the local owner, then propagate
        # its deterministic audit registry back through the wrappers.
        daily = self.base.base.base
        daily.local = PersistentResponseSkilledIntegratedBundle(
            symbol,
            tick_size,
            minimum_gross_rr,
        )
        daily.detectors = daily.local.detectors
        self.base.base.detectors = daily.detectors
        self.base.detectors = self.base.base.detectors
        self.detectors = self.base.detectors

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["persistent_absorption_integration"] = {
            "scope": "LOCAL_REJECTION_CURRENT_BOUNDARY_ABSORPTION_ONLY",
            "rule": CURRENT_ABSORPTION_FIRST_RESPONSE_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1SkilledContinuationPersistentResponseBundle

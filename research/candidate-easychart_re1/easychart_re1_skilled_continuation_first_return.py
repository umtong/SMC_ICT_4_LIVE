"""Complete mechanism policy using first-held-return local continuation entry."""
from __future__ import annotations

from typing import Any

from easychart_re1_local_continuation_first_return import (
    FirstReturnLocalAuctionContinuationEngine,
)
from easychart_re1_skilled_continuation import (
    EasyChartRE1SkilledContinuationBundle,
)


class EasyChartRE1SkilledContinuationFirstReturnBundle(
    EasyChartRE1SkilledContinuationBundle
):
    """Keep rejection/H4-acceptance routing and replace only continuation entry."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.continuation = FirstReturnLocalAuctionContinuationEngine(
            symbol,
            tick_size,
            minimum_gross_rr,
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["local_continuation_entry_owner"] = (
            "FIRST_HELD_SOURCE_OB_OR_ANCHORED_FAIR_VALUE_RETURN"
        )
        return output


MultiScaleScenarioBundle = EasyChartRE1SkilledContinuationFirstReturnBundle

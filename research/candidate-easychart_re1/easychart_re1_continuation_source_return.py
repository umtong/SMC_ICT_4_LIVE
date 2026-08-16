"""Local continuation entered only on the first return to its visual footprint.

The first-return experiment showed that an anchored-VWAP touch can create a
trade even when price never revisits the event-local institutional footprint.
The supplied EasyChart material consistently assigns entry responsibility to
an OB or FVG created by the liquidity/structure event; it does not assign that
responsibility to an average-price line.

This policy therefore keeps the complete aligned impulse and flow evidence but
accepts only the first reacted return to the selected source OB, or to the
source FVG when no event-local OB exists.  Anchored VWAP remains descriptive
context and is not an alternative entry location.  Stop, target, >=1R geometry,
fees, 3% NAV risk and one account position remain unchanged.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from domain import Candle
from easychart_re1_continuation_footprint import (
    EasyChartRE1ContinuationFootprintBundle,
    OBOrFVGFallbackContinuationEngine,
)
from easychart_re1_local_continuation import (
    LocalContinuationKind,
    LocalContinuationSetup,
)


CONTINUATION_SOURCE_RETURN_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:THE_FIRST_REACTED_RETURN_TO_THE_EVENT_LOCAL_"
    "OB_OR_RESPONSIBLE_FVG_OWNS_CONTINUATION_ENTRY_WHILE_ANCHORED_VWAP_IS_"
    "CONTEXT_ONLY"
)
if CONTINUATION_SOURCE_RETURN_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (CONTINUATION_SOURCE_RETURN_RULE,)


class SourceFootprintContinuationEngine(OBOrFVGFallbackContinuationEngine):
    """Reject abstract fair-value touches which miss the responsible footprint."""

    def _pullback_choice(
        self,
        setup: LocalContinuationSetup,
        bar: Candle,
    ) -> tuple[LocalContinuationKind, float, float] | None:
        touched = (
            bar.low <= setup.source_zone.upper
            and bar.high >= setup.source_zone.lower
        )
        if not touched:
            return None
        return (
            LocalContinuationKind.SOURCE_ORDER_BLOCK_PULLBACK,
            setup.source_zone.lower,
            setup.source_zone.upper,
        )


class EasyChartRE1ContinuationSourceReturnBundle(
    EasyChartRE1ContinuationFootprintBundle
):
    """Displacement core plus one source-footprint first-return continuation."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.continuation = SourceFootprintContinuationEngine(
            symbol,
            tick_size,
            minimum_gross_rr,
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["continuation_source_return"] = {
            "entry_owner": "EVENT_LOCAL_ORDER_BLOCK_OR_RESPONSIBLE_FVG",
            "anchored_vwap": "CONTEXT_ONLY",
            "rule_provenance": CONTINUATION_SOURCE_RETURN_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1ContinuationSourceReturnBundle

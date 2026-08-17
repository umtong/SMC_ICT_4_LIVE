"""Wide causal EasyChart candidate generator for ML2.

ML2 reuses ML1's separation between structural opportunity generation and
context-quality selection. It also repairs one implementation bug exposed by
the first ML1 shadow run: the 15/5/1 mature-diagonal engine was accidentally fed
60-minute bars by the enclosing multi-scale bundle.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from candidate_bundle_ml1 import EasyChartML1CandidateBundle
from easychart_re1_auction_router_v3 import MatureDiagonalResponseFamily


ML2_DIAGONAL_TIMEFRAME_CONTRACT_RULE = (
    "IMPLEMENTATION_REPAIR:MATURE_DIAGONAL_ACCEPTANCE_CONSUMES_ONLY_15_5_1_MINUTE_BARS;"
    "ENCLOSING_60_MINUTE_CONTEXT_BAR_IS_NOT_FORWARDED_TO_THE_MICRO_ENGINE"
)
if ML2_DIAGONAL_TIMEFRAME_CONTRACT_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (ML2_DIAGONAL_TIMEFRAME_CONTRACT_RULE,)


class ML2MatureDiagonalResponseFamily(MatureDiagonalResponseFamily):
    """Mature diagonal family with its declared timeframe contract enforced."""

    SUPPORTED_TIMEFRAMES = frozenset((15, 5, 1))

    def on_bar(self, timeframe_minutes: int, bar: Any):  # type: ignore[no-untyped-def]
        if timeframe_minutes not in self.SUPPORTED_TIMEFRAMES:
            self._inc("nonmember_timeframe_ignored")
            return []
        return super().on_bar(timeframe_minutes, bar)


class EasyChartML2CandidateBundle(EasyChartML1CandidateBundle):
    """ML1 structural candidate set with the diagonal feed contract repaired."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.mature_diagonal_acceptance = ML2MatureDiagonalResponseFamily(
            symbol,
            tick_size,
            minimum_gross_rr,
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["ml2_implementation_repairs"] = {
            "mature_diagonal_supported_timeframes": [15, 5, 1],
            "rule": ML2_DIAGONAL_TIMEFRAME_CONTRACT_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartML2CandidateBundle

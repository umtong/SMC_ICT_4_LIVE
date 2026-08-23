"""Fresh-cross delivery with executable-only causal episode ownership.

A plan rejected by the matching-scale delivery router is a diagnostic pattern,
not an account decision.  It must not claim the causal episode and suppress a
later draw-aligned continuation response.  This binding therefore uses the
fresh post-sweep cross state and records episode ownership only for plans which
are executable in the active draw direction.
"""
from __future__ import annotations

from contracts_v5 import V5TradePlan
from easychart_re1_delivery_draw_v3 import CausalLiquidityDrawV3
from easychart_re1_delivery_system import EasyChartRE1DeliverySystemBundle


class EasyChartRE1DeliverySystemV3Bundle(EasyChartRE1DeliverySystemBundle):
    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.delivery_draw = CausalLiquidityDrawV3(symbol, tick_size)

    def _claim_episode(self, plan: V5TradePlan) -> None:
        if self.delivery_draw.allows(plan):
            super()._claim_episode(plan)
            return
        self._dinc("nonexecutable_pattern_did_not_claim_episode")


MultiScaleScenarioBundle = EasyChartRE1DeliverySystemV3Bundle

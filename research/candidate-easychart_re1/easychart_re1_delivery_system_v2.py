"""Strict-causal matching-scale delivery system.

This binding keeps the integrated rejection/continuation policy intact and
replaces only the liquidity-draw state with the version whose internal shift is
strictly later than the external sweep and whose target remains outside the
entire activation-bar range.
"""
from __future__ import annotations

from easychart_re1_delivery_draw_v2 import CausalLiquidityDrawV2
from easychart_re1_delivery_system import EasyChartRE1DeliverySystemBundle


class EasyChartRE1DeliverySystemV2Bundle(EasyChartRE1DeliverySystemBundle):
    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.delivery_draw = CausalLiquidityDrawV2(symbol, tick_size)


MultiScaleScenarioBundle = EasyChartRE1DeliverySystemV2Bundle

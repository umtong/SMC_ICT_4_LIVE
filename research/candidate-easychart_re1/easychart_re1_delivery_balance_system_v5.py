"""Unified system with one-minute delivery lifecycle completion."""
from __future__ import annotations

from easychart_re1_delivery_balance_system_v3 import (
    EasyChartRE1DeliveryBalanceSystemV3Bundle,
)
from easychart_re1_delivery_draw_v5 import FlowValidatedLiquidityDrawV5


class EasyChartRE1DeliveryBalanceSystemV5Bundle(
    EasyChartRE1DeliveryBalanceSystemV3Bundle,
):
    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.delivery_draw = FlowValidatedLiquidityDrawV5(symbol, tick_size)


MultiScaleScenarioBundle = EasyChartRE1DeliveryBalanceSystemV5Bundle

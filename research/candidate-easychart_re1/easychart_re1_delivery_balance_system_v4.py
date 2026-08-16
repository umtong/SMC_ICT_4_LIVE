"""Unified system with flow-impact validated directional delivery."""
from __future__ import annotations

from easychart_re1_delivery_balance_system_v3 import (
    EasyChartRE1DeliveryBalanceSystemV3Bundle,
)
from easychart_re1_delivery_draw_v4_fixed import (
    FlowValidatedLiquidityDrawFixed,
)


class EasyChartRE1DeliveryBalanceSystemV4Bundle(
    EasyChartRE1DeliveryBalanceSystemV3Bundle,
):
    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.delivery_draw = FlowValidatedLiquidityDrawFixed(symbol, tick_size)


MultiScaleScenarioBundle = EasyChartRE1DeliveryBalanceSystemV4Bundle

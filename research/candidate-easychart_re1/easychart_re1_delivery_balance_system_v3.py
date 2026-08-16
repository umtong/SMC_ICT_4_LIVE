"""Unified delivery/balance system with true first-obstacle continuation."""
from __future__ import annotations

from easychart_re1_delivery_balance_system_v2 import (
    EasyChartRE1DeliveryBalanceSystemV2Bundle,
)
from easychart_re1_delivery_continuation import DeliveryContinuationEngine


class EasyChartRE1DeliveryBalanceSystemV3Bundle(
    EasyChartRE1DeliveryBalanceSystemV2Bundle,
):
    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.delivery_continuation = DeliveryContinuationEngine(
            symbol,
            tick_size,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["delivery_continuation"] = 0


MultiScaleScenarioBundle = EasyChartRE1DeliveryBalanceSystemV3Bundle

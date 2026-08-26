"""Strict delivery plus single-use mature balance."""
from __future__ import annotations

from easychart_re1_delivery_balance_system import (
    EasyChartRE1DeliveryBalanceSystemBundle,
)
from easychart_re1_mature_balance_v2 import MatureBalanceEngineV2


class EasyChartRE1DeliveryBalanceSystemV2Bundle(
    EasyChartRE1DeliveryBalanceSystemBundle,
):
    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.mature_balance = MatureBalanceEngineV2(
            symbol,
            tick_size,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["mature_balance"] = 0


MultiScaleScenarioBundle = EasyChartRE1DeliveryBalanceSystemV2Bundle

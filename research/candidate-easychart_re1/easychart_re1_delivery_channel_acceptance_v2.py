"""Draw-aligned channel acceptance with complete micro continuation geometry."""
from __future__ import annotations

from easychart_re1_delivery_channel_acceptance import (
    EasyChartRE1DeliveryChannelAcceptanceBundle,
)
from easychart_re1_delivery_continuation_v2 import DeliveryContinuationEngineV2


class EasyChartRE1DeliveryChannelAcceptanceV2Bundle(
    EasyChartRE1DeliveryChannelAcceptanceBundle,
):
    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.delivery_continuation = DeliveryContinuationEngineV2(
            symbol,
            tick_size,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["delivery_continuation"] = 0


MultiScaleScenarioBundle = EasyChartRE1DeliveryChannelAcceptanceV2Bundle

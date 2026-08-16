"""Current integrated policy: channel S/R flip, first-response continuation and mature balance."""
from __future__ import annotations

from easychart_re1_delivery_channel_acceptance_v3 import (
    EasyChartRE1DeliveryChannelAcceptanceV3Bundle,
)
from easychart_re1_delivery_continuation_v3 import DeliveryContinuationEngineV3


class EasyChartRE1DeliveryChannelAcceptanceV4Bundle(
    EasyChartRE1DeliveryChannelAcceptanceV3Bundle,
):
    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.delivery_continuation = DeliveryContinuationEngineV3(
            symbol,
            tick_size,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["delivery_continuation"] = 0


MultiScaleScenarioBundle = EasyChartRE1DeliveryChannelAcceptanceV4Bundle

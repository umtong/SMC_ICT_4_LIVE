"""Current delivery policy with structural channel-break invalidation.

The channel S/R-flip family keeps the accepted-break, next-bar hold and first
return sequence, but its immutable stop now belongs to the breakout wave rather
than a one-tick projection beyond the moving line.  Continuation remains the
complete-micro-obstacle, proximal-half policy; mature balance remains single-use.
"""
from __future__ import annotations

from easychart_re1_delivery_channel_acceptance_v3 import (
    EasyChartRE1DeliveryChannelAcceptanceV3Bundle,
)
from easychart_re1_delivery_channel_geometry import (
    StructuralDeliveryChannelAcceptanceEngine,
)


class EasyChartRE1DeliveryChannelAcceptanceV5Bundle(
    EasyChartRE1DeliveryChannelAcceptanceV3Bundle,
):
    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.delivery_channel_acceptance = (
            StructuralDeliveryChannelAcceptanceEngine(
                symbol,
                tick_size,
                scale_name="DELIVERY_CHANNEL_ACCEPTANCE",
                higher_minutes=15,
                decision_minutes=5,
                trigger_minutes=1,
                minimum_gross_rr=minimum_gross_rr,
            )
        )
        self._audit_offsets["delivery_channel_acceptance"] = 0


MultiScaleScenarioBundle = EasyChartRE1DeliveryChannelAcceptanceV5Bundle

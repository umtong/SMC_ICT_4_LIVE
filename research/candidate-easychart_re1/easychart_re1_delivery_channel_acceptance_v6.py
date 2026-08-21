"""Draw-aligned channel acceptance with event-driven continuation response.

This binding keeps channel accepted-break ownership, proximal delivery routing,
complete first-obstacle geometry and single-use mature balance.  The only
change is the continuation response: immediate first-touch control transfer or
the first fresh post-touch span-2 one-minute structure shift.
"""
from __future__ import annotations

from easychart_re1_delivery_channel_acceptance_v3 import (
    EasyChartRE1DeliveryChannelAcceptanceV3Bundle,
)
from easychart_re1_delivery_continuation_cisd import (
    DeliveryContinuationCISDEngine,
)


class EasyChartRE1DeliveryChannelAcceptanceV6Bundle(
    EasyChartRE1DeliveryChannelAcceptanceV3Bundle,
):
    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.delivery_continuation = DeliveryContinuationCISDEngine(
            symbol,
            tick_size,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["delivery_continuation"] = 0


MultiScaleScenarioBundle = EasyChartRE1DeliveryChannelAcceptanceV6Bundle

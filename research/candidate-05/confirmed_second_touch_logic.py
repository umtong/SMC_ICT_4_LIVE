"""Pure geometry for a post-confirmation CHoCH second-touch entry."""
from __future__ import annotations

from dataclasses import dataclass
import math

from logic import net_r_at_price
from logic import planned_loss_per_unit


@dataclass(frozen=True, slots=True)
class ConfirmedSecondTouchGeometry:
    entry: float
    planned_loss_per_unit: float
    target_net_r: float


def confirmed_second_touch_geometry(
    *,
    side: int,
    choch_reference: float,
    confirmed_response_price: float,
    stop: float,
    target: float,
    cost_rate: float,
    adverse_slippage_rate: float,
    minimum_net_r: float,
) -> ConfirmedSecondTouchGeometry | None:
    """Validate a passive return to CHoCH after an actual retest response.

    This is deliberately not the earlier blind retrace order. The completed
    response must already be beyond the CHoCH reference in the scenario
    direction, making the reference passive. The original structural stop and
    frozen liquidity target must still bracket that reference and preserve the
    existing post-cost minimum R using the same adverse-slippage assumption.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    values = (
        choch_reference,
        confirmed_response_price,
        stop,
        target,
        cost_rate,
        adverse_slippage_rate,
        minimum_net_r,
    )
    if not all(math.isfinite(float(value)) for value in values):
        return None
    if (
        choch_reference <= 0.0
        or confirmed_response_price <= 0.0
        or stop <= 0.0
        or target <= 0.0
        or cost_rate < 0.0
        or adverse_slippage_rate < 0.0
        or minimum_net_r < 0.0
    ):
        return None

    passive_after_response = (
        confirmed_response_price > choch_reference
        if side > 0
        else confirmed_response_price < choch_reference
    )
    bracketed = (
        stop < choch_reference < target
        if side > 0
        else target < choch_reference < stop
    )
    if not passive_after_response or not bracketed:
        return None

    planned_loss = planned_loss_per_unit(
        choch_reference,
        stop,
        side,
        cost_rate,
        adverse_slippage_rate,
    )
    if not math.isfinite(planned_loss) or planned_loss <= 0.0:
        return None
    target_net_r = net_r_at_price(
        choch_reference,
        target,
        side,
        planned_loss,
        cost_rate,
    )
    if target_net_r + 1e-9 < minimum_net_r:
        return None
    return ConfirmedSecondTouchGeometry(
        entry=choch_reference,
        planned_loss_per_unit=planned_loss,
        target_net_r=target_net_r,
    )


__all__ = [
    "ConfirmedSecondTouchGeometry",
    "confirmed_second_touch_geometry",
]

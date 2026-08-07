"""v32 funded source-equilibrium risk transfer.

At the midpoint of the pre-existing source dealing range, close the minimum
position fraction whose modeled all-cost profit funds the complete original-stop
loss of the residual runner.  The fraction is solved from current price, fees,
impact and the original loss budget; it is never a tuned fixed percentage.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
import os

from c10_v30_overlay import (  # re-export frozen v29/v28/v27 layers
    CostAwareRiskSizer,
    LiveImpactLedger,
    apply_cost_overlay,
    build_leadership_gate,
    certify_plan,
    equilibrium_enabled,
    equilibrium_reached,
    far_only_enabled,
    repair_kline_flow_frame,
    source_midpoint,
)


@dataclass(frozen=True, slots=True)
class FundedReduction:
    partial_quantity: Decimal
    residual_quantity: Decimal
    fraction: Decimal
    expected_exit_price: Decimal
    gain_per_unit: Decimal
    original_loss_per_unit: Decimal
    locked_profit: Decimal
    residual_max_loss: Decimal


def funded_partial_enabled() -> bool:
    return os.environ.get("C10_V32_FUNDED_PARTIAL", "0") == "1"


def solve_funded_reduction(
    *,
    direction: str,
    total_quantity: Decimal,
    entry_price: Decimal,
    current_price: Decimal,
    original_loss_per_unit: Decimal,
    maker_fee: Decimal,
    taker_fee: Decimal,
    impact_per_side: Decimal,
    tick_size: Decimal,
    quantity_increment: Decimal,
    min_quantity: Decimal,
) -> FundedReduction | None:
    values = (
        total_quantity,
        entry_price,
        current_price,
        original_loss_per_unit,
        tick_size,
        quantity_increment,
        min_quantity,
    )
    if any(value <= 0 for value in values):
        raise ValueError("funded reduction price, quantity and loss inputs must be positive")
    if maker_fee < 0 or taker_fee < 0 or impact_per_side < 0:
        raise ValueError("funded reduction costs must be nonnegative")

    if direction == "LONG":
        expected_exit = current_price - tick_size
        favorable_move = expected_exit - entry_price
    elif direction == "SHORT":
        expected_exit = current_price + tick_size
        favorable_move = entry_price - expected_exit
    else:
        raise ValueError(f"unsupported direction: {direction}")

    gain = (
        favorable_move
        - entry_price * maker_fee
        - expected_exit * taker_fee
        - Decimal("2") * impact_per_side
    )
    if gain <= 0:
        return None

    fraction = original_loss_per_unit / (original_loss_per_unit + gain)
    raw_partial = total_quantity * fraction
    partial = (
        raw_partial / quantity_increment
    ).to_integral_value(rounding=ROUND_CEILING) * quantity_increment
    partial = min(total_quantity, partial)
    residual = total_quantity - partial
    if Decimal("0") < residual < min_quantity:
        partial = total_quantity
        residual = Decimal("0")

    locked = partial * gain
    residual_loss = residual * original_loss_per_unit
    tolerance = max(Decimal("0.01"), residual_loss * Decimal("1e-9"))
    if locked + tolerance < residual_loss:
        raise AssertionError("rounded funded reduction does not cover residual stop risk")
    return FundedReduction(
        partial_quantity=partial,
        residual_quantity=residual,
        fraction=partial / total_quantity,
        expected_exit_price=expected_exit,
        gain_per_unit=gain,
        original_loss_per_unit=original_loss_per_unit,
        locked_profit=locked,
        residual_max_loss=residual_loss,
    )

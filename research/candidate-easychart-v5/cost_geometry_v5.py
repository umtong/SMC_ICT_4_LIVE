"""Pre-entry after-cost geometry for EasyChart v5.

Gross chart RR is necessary but not sufficient when stops are very tight.  A
planned target can be above the entry for a long (or below it for a short) and
still lose money after the market-entry reserve, entry/exit fees, and funding.
Such a plan has no positive executable reward and therefore cannot be a valid
trade, regardless of its pattern label.

This module intentionally introduces no optimized reward threshold.  It only
rejects the logically impossible case where the conservative target outcome is
non-positive after already-declared costs.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


NONPOSITIVE_TARGET_RULE = (
    "VALIDITY_INVARIANT:CONSERVATIVE_TARGET_FILL_MUST_HAVE_POSITIVE_AFTER_COST_PNL"
)


@dataclass(frozen=True, slots=True)
class AfterCostTargetGeometry:
    entry: Decimal
    conservative_entry_fill: Decimal
    target: Decimal
    gross_target_per_unit: Decimal
    entry_slippage_reserve: Decimal
    entry_fee: Decimal
    target_fee: Decimal
    funding_reserve: Decimal
    net_target_per_unit: Decimal

    @property
    def positive(self) -> bool:
        return self.net_target_per_unit > 0

    def to_float_dict(self) -> dict[str, float]:
        return {
            "planned_entry": float(self.entry),
            "conservative_entry_fill": float(self.conservative_entry_fill),
            "planned_target": float(self.target),
            "gross_target_per_unit": float(self.gross_target_per_unit),
            "estimated_entry_slippage": float(self.entry_slippage_reserve),
            "estimated_entry_fee": float(self.entry_fee),
            "estimated_target_fee": float(self.target_fee),
            "estimated_funding": float(self.funding_reserve),
            "estimated_net_target_per_unit": float(self.net_target_per_unit),
        }


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def conservative_after_cost_target(
    *,
    is_long: bool,
    entry: Any,
    target: Any,
    price_increment: Any,
    entry_slippage_ticks: int,
    entry_fee_rate: Any,
    target_fee_rate: Any,
    funding_rate: Any,
) -> AfterCostTargetGeometry:
    """Return the conservative per-unit PnL if the declared target is reached.

    The entry is a market order, so the configured adverse entry-slippage
    reserve is applied.  The target has no favorable fill assumption and is
    charged the conservative exit fee rate already used for stop sizing.
    Funding is reserved once, matching the existing fixed-risk contract.
    """
    if entry_slippage_ticks < 0:
        raise ValueError("entry slippage ticks cannot be negative")
    entry_value = _decimal(entry)
    target_value = _decimal(target)
    tick = _decimal(price_increment)
    if entry_value <= 0 or target_value <= 0 or tick <= 0:
        raise ValueError("entry, target and price increment must be positive")
    fee_in = _decimal(entry_fee_rate)
    fee_out = _decimal(target_fee_rate)
    funding = _decimal(funding_rate)
    if min(fee_in, fee_out, funding) < 0:
        raise ValueError("cost rates cannot be negative")

    slippage = tick * Decimal(entry_slippage_ticks)
    conservative_entry = entry_value + slippage if is_long else entry_value - slippage
    if conservative_entry <= 0:
        raise ValueError("conservative entry fill must remain positive")
    gross = (
        target_value - conservative_entry
        if is_long
        else conservative_entry - target_value
    )
    entry_fee = conservative_entry * fee_in
    target_fee = target_value * fee_out
    funding_reserve = conservative_entry * funding
    net = gross - entry_fee - target_fee - funding_reserve
    return AfterCostTargetGeometry(
        entry=entry_value,
        conservative_entry_fill=conservative_entry,
        target=target_value,
        gross_target_per_unit=gross,
        entry_slippage_reserve=slippage,
        entry_fee=entry_fee,
        target_fee=target_fee,
        funding_reserve=funding_reserve,
        net_target_per_unit=net,
    )

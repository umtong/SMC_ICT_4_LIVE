"""Execution-valid positive after-cost protection for Candidate 15.

The trigger is derived from the *actual* average opening fill and budgets the
frozen adverse stop-market slippage before requiring one full price tick of net
profit per unit after entry and exit commissions.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Any


@dataclass(frozen=True)
class PositiveCostCover:
    trigger_price: Decimal
    expected_adverse_fill: Decimal
    expected_net_gain_per_unit: Decimal
    minimum_net_gain_per_unit: Decimal
    adverse_slippage_ticks: int


def _d(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def positive_cost_cover_trigger(
    *,
    direction: str,
    actual_average_entry: Decimal,
    price_increment: Decimal,
    entry_fee_rate: Decimal,
    exit_fee_rate: Decimal,
    adverse_slippage_ticks: int = 2,
    minimum_net_ticks: int = 1,
) -> PositiveCostCover:
    """Return a stop trigger with positive net payoff under frozen costs.

    For a LONG stop, the modeled market fill is ``trigger - slippage``.
    For a SHORT stop, it is ``trigger + slippage``.  The trigger is rounded in
    the conservative direction and must leave at least ``minimum_net_ticks``
    after both commissions at that adverse fill.
    """
    side = str(direction).upper()
    entry = _d(actual_average_entry)
    tick = _d(price_increment)
    maker = _d(entry_fee_rate)
    taker = _d(exit_fee_rate)
    if side not in {"LONG", "SHORT"}:
        raise ValueError(f"unsupported direction {direction!r}")
    if entry <= 0 or tick <= 0:
        raise ValueError("entry and price increment must be positive")
    if not (Decimal("0") <= maker < Decimal("1")):
        raise ValueError("entry fee rate must be in [0, 1)")
    if not (Decimal("0") <= taker < Decimal("1")):
        raise ValueError("exit fee rate must be in [0, 1)")
    if isinstance(adverse_slippage_ticks, bool) or int(adverse_slippage_ticks) != adverse_slippage_ticks:
        raise ValueError("adverse_slippage_ticks must be an integer")
    if isinstance(minimum_net_ticks, bool) or int(minimum_net_ticks) != minimum_net_ticks:
        raise ValueError("minimum_net_ticks must be an integer")
    slippage_ticks = int(adverse_slippage_ticks)
    net_ticks = int(minimum_net_ticks)
    if slippage_ticks < 0 or net_ticks < 1:
        raise ValueError("slippage must be non-negative and minimum net ticks positive")

    slippage = tick * slippage_ticks
    minimum_gain = tick * net_ticks
    if side == "LONG":
        minimum_exit_fill = (entry * (Decimal("1") + maker) + minimum_gain) / (
            Decimal("1") - taker
        )
        raw_trigger = minimum_exit_fill + slippage
        units = (raw_trigger / tick).to_integral_value(rounding=ROUND_CEILING)
        trigger = units * tick
        adverse_fill = trigger - slippage
        net = (
            adverse_fill * (Decimal("1") - taker)
            - entry * (Decimal("1") + maker)
        )
    else:
        maximum_exit_fill = (entry * (Decimal("1") - maker) - minimum_gain) / (
            Decimal("1") + taker
        )
        raw_trigger = maximum_exit_fill - slippage
        units = (raw_trigger / tick).to_integral_value(rounding=ROUND_FLOOR)
        trigger = units * tick
        adverse_fill = trigger + slippage
        net = (
            entry * (Decimal("1") - maker)
            - adverse_fill * (Decimal("1") + taker)
        )

    if trigger <= 0 or adverse_fill <= 0:
        raise ValueError("cost-cover geometry produced a non-positive price")
    if net < minimum_gain:
        raise ArithmeticError(
            f"rounded cost-cover contract failed: net={net}, minimum={minimum_gain}",
        )
    return PositiveCostCover(
        trigger_price=trigger,
        expected_adverse_fill=adverse_fill,
        expected_net_gain_per_unit=net,
        minimum_net_gain_per_unit=minimum_gain,
        adverse_slippage_ticks=slippage_ticks,
    )

"""Exact single-entry position sizing for a three-percent structural stop."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_DOWN
import math

RISK_FRACTION = 0.03


@dataclass(frozen=True, slots=True)
class RiskSizing:
    quantity: float
    notional: float
    required_leverage: float
    unit_stop_loss: float
    planned_cash_loss: float
    planned_risk_fraction: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


def _floor_step(value: float, step: float) -> float:
    if value <= 0.0 or step <= 0.0:
        return 0.0
    units = (Decimal(str(value)) / Decimal(str(step))).to_integral_value(rounding=ROUND_DOWN)
    return float(units * Decimal(str(step)))


def size_three_percent_risk(
    *,
    nav: float,
    entry: float,
    stop: float,
    tick_size: float,
    quantity_step: float,
    min_quantity: float = 0.0,
    risk_fraction: float = RISK_FRACTION,
    entry_fee_rate: float = 0.0002,
    stop_fee_rate: float = 0.0005,
    stop_slippage_ticks: int = 2,
) -> RiskSizing:
    """Use the whole account as margin and derive leverage from structural risk.

    Quantity is the only free variable. Fees and expected stop-market slippage are
    included in the unit loss, so the rounded order remains approximately a true
    three-percent NAV risk rather than a cosmetic price-distance calculation.
    """
    values = (nav, entry, stop, tick_size, quantity_step, risk_fraction)
    if not all(math.isfinite(float(value)) for value in values):
        raise ValueError("all sizing inputs must be finite")
    if nav <= 0.0 or entry <= 0.0 or stop <= 0.0:
        raise ValueError("nav, entry and stop must be positive")
    if tick_size <= 0.0 or quantity_step <= 0.0 or not 0.0 < risk_fraction < 1.0:
        raise ValueError("invalid precision or risk_fraction")
    direction = 1.0 if stop < entry else -1.0
    stop_fill = stop - direction * stop_slippage_ticks * tick_size
    price_loss = abs(entry - stop_fill)
    fee_loss = entry_fee_rate * entry + stop_fee_rate * abs(stop_fill)
    unit_loss = price_loss + fee_loss
    if unit_loss <= 0.0:
        raise ValueError("stop geometry has no positive loss")
    cash_budget = nav * risk_fraction
    quantity = _floor_step(cash_budget / unit_loss, quantity_step)
    if quantity < min_quantity or quantity <= 0.0:
        raise ValueError("risk-sized quantity is below instrument minimum")
    planned_cash_loss = quantity * unit_loss
    notional = quantity * entry
    return RiskSizing(
        quantity=float(quantity),
        notional=float(notional),
        required_leverage=float(notional / nav),
        unit_stop_loss=float(unit_loss),
        planned_cash_loss=float(planned_cash_loss),
        planned_risk_fraction=float(planned_cash_loss / nav),
    )


__all__ = ["RISK_FRACTION", "RiskSizing", "size_three_percent_risk"]

"""Deterministic 3% stop-risk sizing for native Nautilus instruments.

The sizing contract deliberately does not clip to quantity, notional, leverage,
or margin limits.  A trade either has the nearest representable quantity whose
all-in stop loss is approximately 3% of current marked equity, or it is rejected
with an explicit reason.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from enum import Enum
from typing import Any, Mapping

from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Money, Price, Quantity


THREE_PERCENT = Decimal("0.03")
DEFAULT_RISK_TOLERANCE = Decimal("0.0005")  # five NAV basis points


class SizingRejectionReason(str, Enum):
    INVALID_INPUT = "INVALID_INPUT"
    UNSUPPORTED_INSTRUMENT = "UNSUPPORTED_INSTRUMENT"
    INVALID_PRICE_GEOMETRY = "INVALID_PRICE_GEOMETRY"
    QUANTITY_ROUNDED_TO_ZERO = "QUANTITY_ROUNDED_TO_ZERO"
    MIN_QUANTITY = "MIN_QUANTITY"
    MAX_QUANTITY = "MAX_QUANTITY"
    MIN_NOTIONAL = "MIN_NOTIONAL"
    MAX_NOTIONAL = "MAX_NOTIONAL"
    PRICE_BOUNDS = "PRICE_BOUNDS"
    RISK_TOLERANCE = "RISK_TOLERANCE"
    MAX_LEVERAGE = "MAX_LEVERAGE"
    INSUFFICIENT_FREE_MARGIN = "INSUFFICIENT_FREE_MARGIN"


@dataclass(frozen=True, slots=True)
class SizingRejected:
    reason: SizingRejectionReason
    details: Mapping[str, str] = field(default_factory=dict)
    accepted: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class SizingAccepted:
    quantity: Quantity
    entry_price: Price
    stop_trigger_price: Price
    adverse_stop_fill_price: Price
    notional: Money
    nav: Decimal
    free_margin: Decimal
    risk_budget: Decimal
    planned_stop_loss: Decimal
    planned_risk_fraction: Decimal
    effective_leverage: Decimal
    account_leverage: Decimal
    initial_margin_required: Decimal
    entry_fee_rate: Decimal
    stop_fee_rate: Decimal
    accepted: bool = field(default=True, init=False)


SizingResult = SizingAccepted | SizingRejected


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, Money | Price | Quantity):
        return value.as_decimal()
    return Decimal(str(value))


def _reject(reason: SizingRejectionReason, **details: Any) -> SizingRejected:
    return SizingRejected(reason, {key: str(value) for key, value in details.items()})


def _round_increment(value: Decimal, increment: Decimal, rounding: str) -> Decimal:
    units = (value / increment).to_integral_value(rounding=rounding)
    return units * increment


def size_three_percent_stop_risk(
    instrument: Instrument,
    *,
    side: str,
    entry: Decimal | Price | float | str,
    stop: Decimal | Price | float | str,
    nav: Decimal | Money | float | str,
    free_margin: Decimal | Money | float | str,
    max_leverage: Decimal | float | str = Decimal("20"),
    account_leverage: Decimal | float | str | None = None,
    entry_post_only_guaranteed: bool = False,
    risk_fraction: Decimal = THREE_PERCENT,
    risk_tolerance: Decimal = DEFAULT_RISK_TOLERANCE,
    stop_slippage_ticks: int = 2,
    stop_slippage_bps: Decimal = Decimal("1"),
) -> SizingResult:
    """Return the exact native quantity or a structured rejection.

    ``nav`` is current mark-to-market account equity. ``free_margin`` is only a
    feasibility constraint; it never replaces NAV as the risk base.  Entry fees
    are taker fees unless the caller guarantees a post-only entry.  This function
    supports the linear USD-M instruments used by the episode policy; inverse
    contracts are rejected rather than approximated.
    """

    try:
        if instrument is None:
            return _reject(SizingRejectionReason.INVALID_INPUT, field="instrument")
        side = str(side).upper()
        if side not in {"LONG", "SHORT"}:
            return _reject(SizingRejectionReason.INVALID_INPUT, field="side", value=side)
        if instrument.is_inverse:
            return _reject(
                SizingRejectionReason.UNSUPPORTED_INSTRUMENT,
                instrument_id=instrument.id,
                reason="inverse sizing requires a different PnL formula",
            )

        settlement_currency = instrument.settlement_currency or instrument.quote_currency
        for label, amount in {"nav": nav, "free_margin": free_margin}.items():
            if isinstance(amount, Money) and amount.currency != settlement_currency:
                return _reject(
                    SizingRejectionReason.INVALID_INPUT,
                    field=label,
                    currency=amount.currency,
                    expected_currency=settlement_currency,
                )

        entry_value = _decimal(entry)
        stop_value = _decimal(stop)
        nav_value = _decimal(nav)
        free_margin_value = _decimal(free_margin)
        max_leverage_value = _decimal(max_leverage)
        risk_fraction = _decimal(risk_fraction)
        risk_tolerance = _decimal(risk_tolerance)
        stop_slippage_bps = _decimal(stop_slippage_bps)
        tick = instrument.price_increment.as_decimal()
        quantity_step = instrument.size_increment.as_decimal()
        multiplier = instrument.multiplier.as_decimal()

        if account_leverage is None:
            margin_init = _decimal(instrument.margin_init)
            account_leverage_value = (
                Decimal(1) / margin_init if margin_init > 0 else max_leverage_value
            )
        else:
            account_leverage_value = _decimal(account_leverage)

        positive_values = {
            "entry": entry_value,
            "stop": stop_value,
            "nav": nav_value,
            "max_leverage": max_leverage_value,
            "account_leverage": account_leverage_value,
            "risk_fraction": risk_fraction,
            "tick": tick,
            "quantity_step": quantity_step,
            "multiplier": multiplier,
        }
        for name, value in positive_values.items():
            if value <= 0 or not value.is_finite():
                return _reject(SizingRejectionReason.INVALID_INPUT, field=name, value=value)
        if free_margin_value < 0 or not free_margin_value.is_finite():
            return _reject(
                SizingRejectionReason.INVALID_INPUT,
                field="free_margin",
                value=free_margin_value,
            )
        if risk_tolerance < 0 or not risk_tolerance.is_finite():
            return _reject(
                SizingRejectionReason.INVALID_INPUT,
                field="risk_tolerance",
                value=risk_tolerance,
            )
        if stop_slippage_ticks < 0 or stop_slippage_bps < 0:
            return _reject(SizingRejectionReason.INVALID_INPUT, field="stop_slippage")
        if account_leverage_value > max_leverage_value:
            return _reject(
                SizingRejectionReason.INVALID_INPUT,
                field="account_leverage",
                value=account_leverage_value,
                max_leverage=max_leverage_value,
            )

        entry_rounded = _round_increment(entry_value, tick, ROUND_HALF_UP)
        if side == "LONG":
            stop_trigger = _round_increment(stop_value, tick, ROUND_FLOOR)
            valid_geometry = stop_trigger < entry_rounded
            direction = Decimal(1)
        else:
            stop_trigger = _round_increment(stop_value, tick, ROUND_CEILING)
            valid_geometry = stop_trigger > entry_rounded
            direction = Decimal(-1)
        if not valid_geometry:
            return _reject(
                SizingRejectionReason.INVALID_PRICE_GEOMETRY,
                side=side,
                entry=entry_rounded,
                stop=stop_trigger,
            )

        stop_slippage = (
            Decimal(stop_slippage_ticks) * tick
            + stop_trigger * stop_slippage_bps / Decimal(10_000)
        )
        adverse_raw = stop_trigger - direction * stop_slippage
        adverse_stop_fill = _round_increment(
            adverse_raw,
            tick,
            ROUND_FLOOR if side == "LONG" else ROUND_CEILING,
        )
        if adverse_stop_fill <= 0:
            return _reject(
                SizingRejectionReason.INVALID_PRICE_GEOMETRY,
                adverse_stop_fill=adverse_stop_fill,
            )

        entry_price = instrument.make_price(entry_rounded)
        stop_trigger_price = instrument.make_price(stop_trigger)
        adverse_stop_fill_price = instrument.make_price(adverse_stop_fill)
        for label, price in {
            "entry": entry_price,
            "stop_trigger": stop_trigger_price,
            "adverse_stop_fill": adverse_stop_fill_price,
        }.items():
            if instrument.min_price is not None and price < instrument.min_price:
                return _reject(
                    SizingRejectionReason.PRICE_BOUNDS,
                    field=label,
                    value=price,
                    minimum=instrument.min_price,
                )
            if instrument.max_price is not None and price > instrument.max_price:
                return _reject(
                    SizingRejectionReason.PRICE_BOUNDS,
                    field=label,
                    value=price,
                    maximum=instrument.max_price,
                )

        entry_fee_rate = _decimal(
            instrument.maker_fee if entry_post_only_guaranteed else instrument.taker_fee
        )
        stop_fee_rate = _decimal(instrument.taker_fee)
        per_unit_loss = multiplier * (
            abs(entry_rounded - adverse_stop_fill)
            + entry_rounded * entry_fee_rate
            + adverse_stop_fill * stop_fee_rate
        )
        if per_unit_loss <= 0 or not per_unit_loss.is_finite():
            return _reject(
                SizingRejectionReason.INVALID_INPUT,
                field="per_unit_loss",
                value=per_unit_loss,
            )

        risk_budget = nav_value * risk_fraction
        raw_quantity = risk_budget / per_unit_loss
        quantity_value = _round_increment(raw_quantity, quantity_step, ROUND_HALF_UP)
        if quantity_value <= 0:
            return _reject(
                SizingRejectionReason.QUANTITY_ROUNDED_TO_ZERO,
                raw_quantity=raw_quantity,
                quantity_step=quantity_step,
            )
        quantity = instrument.make_qty(quantity_value)

        if instrument.min_quantity is not None and quantity < instrument.min_quantity:
            return _reject(
                SizingRejectionReason.MIN_QUANTITY,
                quantity=quantity,
                minimum=instrument.min_quantity,
            )
        if instrument.max_quantity is not None and quantity > instrument.max_quantity:
            return _reject(
                SizingRejectionReason.MAX_QUANTITY,
                quantity=quantity,
                maximum=instrument.max_quantity,
                raw_quantity=raw_quantity,
            )

        planned_stop_loss = quantity.as_decimal() * per_unit_loss
        planned_risk_fraction = planned_stop_loss / nav_value
        if abs(planned_risk_fraction - risk_fraction) > risk_tolerance:
            return _reject(
                SizingRejectionReason.RISK_TOLERANCE,
                planned_risk_fraction=planned_risk_fraction,
                target=risk_fraction,
                tolerance=risk_tolerance,
                quantity=quantity,
            )

        notional = instrument.notional_value(quantity, entry_price)
        notional_value = notional.as_decimal()
        for label, bound in {
            "min_notional": instrument.min_notional,
            "max_notional": instrument.max_notional,
        }.items():
            if bound is not None and bound.currency != notional.currency:
                return _reject(
                    SizingRejectionReason.UNSUPPORTED_INSTRUMENT,
                    field=label,
                    currency=bound.currency,
                    expected_currency=notional.currency,
                )
        if (
            instrument.min_notional is not None
            and instrument.min_notional.currency == notional.currency
            and notional_value < instrument.min_notional.as_decimal()
        ):
            return _reject(
                SizingRejectionReason.MIN_NOTIONAL,
                notional=notional,
                minimum=instrument.min_notional,
            )
        if (
            instrument.max_notional is not None
            and instrument.max_notional.currency == notional.currency
            and notional_value > instrument.max_notional.as_decimal()
        ):
            return _reject(
                SizingRejectionReason.MAX_NOTIONAL,
                notional=notional,
                maximum=instrument.max_notional,
                raw_quantity=raw_quantity,
            )

        effective_leverage = notional_value / nav_value
        if effective_leverage > max_leverage_value:
            return _reject(
                SizingRejectionReason.MAX_LEVERAGE,
                effective_leverage=effective_leverage,
                maximum=max_leverage_value,
                quantity=quantity,
            )

        # Nautilus MarginAccount.balance_impact uses notional/account leverage.
        # This preflight mirrors that free-balance check without mutating account
        # state or silently reducing the requested risk quantity.
        initial_margin_required = notional_value / account_leverage_value
        if initial_margin_required > free_margin_value:
            return _reject(
                SizingRejectionReason.INSUFFICIENT_FREE_MARGIN,
                required=initial_margin_required,
                available=free_margin_value,
                quantity=quantity,
            )

        return SizingAccepted(
            quantity=quantity,
            entry_price=entry_price,
            stop_trigger_price=stop_trigger_price,
            adverse_stop_fill_price=adverse_stop_fill_price,
            notional=notional,
            nav=nav_value,
            free_margin=free_margin_value,
            risk_budget=risk_budget,
            planned_stop_loss=planned_stop_loss,
            planned_risk_fraction=planned_risk_fraction,
            effective_leverage=effective_leverage,
            account_leverage=account_leverage_value,
            initial_margin_required=initial_margin_required,
            entry_fee_rate=entry_fee_rate,
            stop_fee_rate=stop_fee_rate,
        )
    except (ArithmeticError, InvalidOperation, TypeError, ValueError) as exc:
        return _reject(
            SizingRejectionReason.INVALID_INPUT,
            error=type(exc).__name__,
            message=exc,
        )

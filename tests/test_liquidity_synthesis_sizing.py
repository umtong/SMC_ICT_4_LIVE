from __future__ import annotations

from decimal import Decimal

import pytest
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency, Money, Price, Quantity

from smc_ict_4.episode_policy_live.nautilus_data import make_binance_usdm_instruments
from smc_ict_4.episode_policy_live.sizing import (
    SizingAccepted,
    SizingRejected,
    SizingRejectionReason,
    nautilus_account_leverage,
    size_three_percent_stop_risk,
)


def _custom_instrument(
    *,
    size_increment: str = "1",
    min_quantity: str = "1",
    max_quantity: str | None = None,
    min_notional: str = "0",
    max_notional: str | None = None,
) -> CryptoPerpetual:
    usdt = Currency.from_str("USDT")
    kwargs = {
        "instrument_id": InstrumentId(Symbol("TESTUSDT-PERP"), Venue("BINANCE")),
        "raw_symbol": Symbol("TESTUSDT"),
        "base_currency": Currency.from_str("TEST"),
        "quote_currency": usdt,
        "settlement_currency": usdt,
        "is_inverse": False,
        "price_precision": 2,
        "size_precision": max(0, -Decimal(size_increment).normalize().as_tuple().exponent),
        "price_increment": Price.from_str("0.01"),
        "size_increment": Quantity.from_str(size_increment),
        "min_quantity": Quantity.from_str(min_quantity),
        "min_notional": Money(Decimal(min_notional), usdt),
        "margin_init": Decimal("0.05"),
        "margin_maint": Decimal("0.025"),
        "maker_fee": Decimal("0.0002"),
        "taker_fee": Decimal("0.0005"),
        "ts_event": 0,
        "ts_init": 0,
    }
    if max_quantity is not None:
        kwargs["max_quantity"] = Quantity.from_str(max_quantity)
    if max_notional is not None:
        kwargs["max_notional"] = Money(Decimal(max_notional), usdt)
    return CryptoPerpetual(**kwargs)


@pytest.mark.parametrize(
    ("symbol", "entry"),
    (("BTCUSDT", "100000"), ("ETHUSDT", "5000"), ("SOLUSDT", "200"), ("XRPUSDT", "1")),
)
def test_all_four_native_quantity_steps_land_near_three_percent(symbol: str, entry: str) -> None:
    instrument = make_binance_usdm_instruments()[symbol]
    entry_value = Decimal(entry)
    result = size_three_percent_stop_risk(
        instrument,
        side="LONG",
        entry=entry_value,
        stop=entry_value * Decimal("0.99"),
        nav=Decimal("100000"),
    )

    assert isinstance(result, SizingAccepted), result
    assert (
        abs(result.planned_structural_risk_fraction - Decimal("0.03"))
        <= Decimal("0.0005")
    )
    assert result.quantity.as_decimal() % instrument.size_increment.as_decimal() == 0


def test_stop_distance_derives_effective_leverage_from_whole_nav() -> None:
    instrument = make_binance_usdm_instruments()["BTCUSDT"]
    common = {
        "instrument": instrument,
        "side": "LONG",
        "entry": Decimal("100000"),
        "nav": Decimal("100000"),
    }
    narrow = size_three_percent_stop_risk(stop=Decimal("99500"), **common)
    wide = size_three_percent_stop_risk(stop=Decimal("98000"), **common)

    assert isinstance(narrow, SizingAccepted)
    assert isinstance(wide, SizingAccepted)
    assert Decimal("5.9") < narrow.effective_leverage < Decimal("6.1")
    assert Decimal("1.3") < wide.effective_leverage < Decimal("1.6")
    assert narrow.required_exchange_leverage == 6
    assert wide.required_exchange_leverage == 2
    narrow_account_leverage = nautilus_account_leverage(narrow.effective_leverage)
    wide_account_leverage = nautilus_account_leverage(wide.effective_leverage)
    assert narrow_account_leverage == Decimal("6.000")
    assert wide_account_leverage == Decimal("1.50000")
    assert narrow.notional.as_decimal() / narrow_account_leverage == Decimal("100000")
    assert wide.notional.as_decimal() / wide_account_leverage == Decimal("100000")
    assert narrow.nav == wide.nav == Decimal("100000")


@pytest.mark.parametrize(
    ("stop", "expected_fraction"),
    (("99.01", "0.0297"), ("98.99", "0.0303")),
)
def test_native_quantity_rounding_near_three_percent_is_not_overfit(
    stop: str,
    expected_fraction: str,
) -> None:
    result = size_three_percent_stop_risk(
        _custom_instrument(size_increment="1", min_quantity="1"),
        side="LONG",
        entry=Decimal("100"),
        stop=Decimal(stop),
        nav=Decimal("100"),
    )

    assert isinstance(result, SizingAccepted)
    assert result.quantity.as_decimal() == Decimal("3")
    assert result.planned_structural_risk_fraction == Decimal(expected_fraction)


def test_costs_and_adverse_stop_are_evidence_on_top_of_structural_three_percent() -> None:
    instrument = make_binance_usdm_instruments()["BTCUSDT"]
    common = {
        "instrument": instrument,
        "entry": Decimal("100000"),
        "nav": Decimal("100000"),
    }
    long = size_three_percent_stop_risk(side="LONG", stop=Decimal("99500.07"), **common)
    short = size_three_percent_stop_risk(side="SHORT", stop=Decimal("100500.03"), **common)
    post_only = size_three_percent_stop_risk(
        side="LONG",
        stop=Decimal("99500.07"),
        entry_post_only_guaranteed=True,
        **common,
    )

    assert isinstance(long, SizingAccepted)
    assert isinstance(short, SizingAccepted)
    assert isinstance(post_only, SizingAccepted)
    assert long.stop_trigger_price.as_decimal() == Decimal("99500.0")
    assert long.adverse_stop_fill_price.as_decimal() == Decimal("99489.8")
    assert short.stop_trigger_price.as_decimal() == Decimal("100500.1")
    assert short.adverse_stop_fill_price.as_decimal() == Decimal("100510.4")
    assert long.entry_fee_rate == instrument.taker_fee
    assert post_only.entry_fee_rate == instrument.maker_fee
    assert long.quantity == post_only.quantity
    assert short.quantity.as_decimal() == Decimal("5.999")
    assert (
        abs(short.planned_structural_risk_fraction - Decimal("0.03"))
        <= Decimal("0.0005")
    )
    assert long.planned_structural_stop_loss == Decimal("3000.0")
    assert long.planned_structural_risk_fraction == Decimal("0.030")
    assert long.estimated_adverse_price_loss == Decimal("3061.20")
    assert long.estimated_entry_fee == Decimal("300.0000")
    assert long.estimated_stop_fee == Decimal("298.46940")
    assert long.estimated_all_in_stop_loss == Decimal("3659.66940")
    assert long.estimated_all_in_risk_fraction == Decimal("0.036596694")
    assert post_only.estimated_entry_fee < long.estimated_entry_fee
    assert post_only.estimated_all_in_stop_loss < long.estimated_all_in_stop_loss


def test_fee_and_stop_slippage_assumptions_never_change_structural_quantity() -> None:
    instrument = make_binance_usdm_instruments()["ETHUSDT"]
    common = {
        "instrument": instrument,
        "side": "LONG",
        "entry": Decimal("5000"),
        "stop": Decimal("4950"),
        "nav": Decimal("100000"),
    }
    ordinary = size_three_percent_stop_risk(**common)
    extreme_cost_evidence = size_three_percent_stop_risk(
        **common,
        entry_post_only_guaranteed=True,
        stop_slippage_ticks=100,
        stop_slippage_bps=Decimal("25"),
    )

    assert isinstance(ordinary, SizingAccepted)
    assert isinstance(extreme_cost_evidence, SizingAccepted)
    assert ordinary.quantity == extreme_cost_evidence.quantity
    assert (
        ordinary.planned_structural_stop_loss
        == extreme_cost_evidence.planned_structural_stop_loss
        == Decimal("3000.00")
    )
    assert (
        extreme_cost_evidence.estimated_all_in_stop_loss
        > ordinary.estimated_all_in_stop_loss
    )


@pytest.mark.parametrize(
    ("instrument", "kwargs", "reason"),
    (
        (
            _custom_instrument(size_increment="1", min_quantity="1"),
            {"entry": "100", "stop": "99", "nav": "1"},
            SizingRejectionReason.QUANTITY_ROUNDED_TO_ZERO,
        ),
        (
            _custom_instrument(size_increment="0.01", min_quantity="0.01", max_quantity="1"),
            {"entry": "100", "stop": "99", "nav": "10000"},
            SizingRejectionReason.MAX_QUANTITY,
        ),
        (
            _custom_instrument(size_increment="0.01", min_quantity="0.01", max_notional="100"),
            {"entry": "100", "stop": "99", "nav": "10000"},
            SizingRejectionReason.MAX_NOTIONAL,
        ),
    ),
)
def test_exchange_limits_reject_without_clipping(
    instrument: CryptoPerpetual,
    kwargs: dict[str, str],
    reason: SizingRejectionReason,
) -> None:
    result = size_three_percent_stop_risk(
        instrument,
        side="LONG",
        **kwargs,
    )

    assert isinstance(result, SizingRejected)
    assert result.reason is reason


def test_unrepresentable_risk_is_rejected_but_leverage_is_not_an_alpha_cap() -> None:
    coarse = _custom_instrument(size_increment="1", min_quantity="1")
    risk_error = size_three_percent_stop_risk(
        coarse,
        side="LONG",
        entry=Decimal("1"),
        stop=Decimal("0.50"),
        nav=Decimal("10"),
    )
    high_leverage = size_three_percent_stop_risk(
        make_binance_usdm_instruments()["BTCUSDT"],
        side="LONG",
        entry=Decimal("100000"),
        stop=Decimal("99990"),
        nav=Decimal("100000"),
    )

    assert isinstance(risk_error, SizingRejected)
    assert risk_error.reason is SizingRejectionReason.RISK_TOLERANCE
    assert isinstance(high_leverage, SizingAccepted)
    assert high_leverage.effective_leverage == Decimal("300.000")
    assert high_leverage.required_exchange_leverage == 300


def test_wrong_nav_currency_fails_closed() -> None:
    instrument = make_binance_usdm_instruments()["BTCUSDT"]
    wrong_currency = size_three_percent_stop_risk(
        instrument,
        side="LONG",
        entry=Decimal("100000"),
        stop=Decimal("99500"),
        nav=Money(Decimal("100000"), Currency.from_str("USD")),
    )

    assert isinstance(wrong_currency, SizingRejected)
    assert wrong_currency.reason is SizingRejectionReason.INVALID_INPUT
    assert wrong_currency.details["field"] == "nav"

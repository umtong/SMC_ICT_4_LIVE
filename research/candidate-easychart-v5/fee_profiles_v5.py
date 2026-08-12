"""Explicit Binance USD-M fee profiles for causal cost sensitivity.

The legacy research instrument used 7.5 bps for both maker and taker fills. That
number is the familiar spot BNB-discount rate, not an account-specific USD-M
commission quote.  This module keeps the legacy profile as a control and adds a
regular-tier USD-M profile (2 bps maker, 5 bps taker) for a transparent
sensitivity run.  Production must query the authenticated venue/account rate;
this module never claims a static table is the user's exact live fee.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from instruments import CONTRACTS, _precision
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency, Money, Price, Quantity


@dataclass(frozen=True, slots=True)
class FeeProfile:
    name: str
    maker_rate: Decimal
    taker_rate: Decimal
    provenance: str


FEE_PROFILES: dict[str, FeeProfile] = {
    "legacy_7_5bps": FeeProfile(
        name="legacy_7_5bps",
        maker_rate=Decimal("0.00075"),
        taker_rate=Decimal("0.00075"),
        provenance="PROJECT_LEGACY_CONTROL:SPOT_BNB_DISCOUNT_LIKE_RATE",
    ),
    "usd_m_vip0": FeeProfile(
        name="usd_m_vip0",
        maker_rate=Decimal("0.00020"),
        taker_rate=Decimal("0.00050"),
        provenance="EXTERNAL_METHOD:BINANCE_USD_M_REGULAR_TIER_FEE_SENSITIVITY",
    ),
}


def make_instrument_with_fee_profile(symbol: str, profile: FeeProfile) -> CryptoPerpetual:
    contract = CONTRACTS[symbol]
    base = Currency.from_str(contract.base)
    usdt = Currency.from_str("USDT")
    return CryptoPerpetual(
        instrument_id=InstrumentId(Symbol(f"{symbol}-PERP"), Venue("BINANCE")),
        raw_symbol=Symbol(symbol),
        base_currency=base,
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        price_precision=_precision(contract.price_increment),
        price_increment=Price.from_str(contract.price_increment),
        size_precision=_precision(contract.size_increment),
        size_increment=Quantity.from_str(contract.size_increment),
        max_quantity=Quantity.from_str(contract.max_quantity),
        min_quantity=Quantity.from_str(contract.min_quantity),
        max_notional=None,
        min_notional=Money(Decimal(contract.min_notional), usdt),
        max_price=Price.from_str(contract.max_price),
        min_price=Price.from_str(contract.min_price),
        margin_init=Decimal("0.01"),
        margin_maint=Decimal("0.005"),
        maker_fee=profile.maker_rate,
        taker_fee=profile.taker_rate,
        ts_event=0,
        ts_init=0,
    )

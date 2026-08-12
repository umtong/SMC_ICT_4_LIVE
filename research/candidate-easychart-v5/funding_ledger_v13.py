"""Causal external funding ledger for the pinned NautilusTrader engine.

The pinned 1.230 backtest wheel publishes funding and mark-price updates but the
native cash settlement path did not change account NAV in a deterministic
position-held-across-boundary smoke test. Funding is therefore accounted once in
an explicit strategy ledger while the engine remains authoritative for orders,
fills, fees, positions, and native account state.

Ledger-only FundingRateUpdate objects carry no native settlement boundary. The
realized rate becomes visible at its Binance archive timestamp, the latest
completed mark price is already cached, and the open linear-perpetual notional is
charged immediately. The adjusted NAV is used for every subsequent risk size.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from nautilus_trader.model.data import FundingRateUpdate, MarkPriceUpdate
from nautilus_trader.model.events import PositionClosed
from nautilus_trader.model.identifiers import InstrumentId

from mtf_strategy_half_be_v12 import HalfThenBreakevenStrategy


FUNDING_LEDGER_PROVENANCE = (
    "EXTERNAL_METHOD:CAUSAL_BINANCE_REALIZED_FUNDING_LEDGER_FOR_PINNED_ENGINE"
)
NATIVE_FUNDING_LIMITATION = (
    "PINNED_NAUTILUS_1_230_NATIVE_FUNDING_SMOKE_PUBLISHED_DATA_BUT_DID_NOT_CHANGE_NAV"
)


def linear_funding_cash_flow(
    signed_quantity: Decimal,
    mark_price: Decimal,
    funding_rate: Decimal,
) -> Decimal:
    """Return quote-currency cash flow for a linear perpetual position.

    Positive ``signed_quantity`` is long. A positive funding rate therefore
    produces a negative cash flow for longs and a positive one for shorts.
    """
    if mark_price <= 0:
        raise ValueError("mark price must be positive")
    if signed_quantity == 0:
        return Decimal("0")
    return -(signed_quantity * mark_price * funding_rate)


class ExternalFundingLedgerMixin:
    """Apply one causal funding cash flow and expose funding-adjusted NAV."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._external_funding_balance = Decimal("0")
        self._latest_mark_by_instrument: dict[InstrumentId, MarkPriceUpdate] = {}
        self._processed_funding_keys: set[tuple[InstrumentId, int]] = set()

    @property
    def external_funding_balance(self) -> Decimal:
        return self._external_funding_balance

    def _current_nav(self) -> Decimal:
        native_nav = super()._current_nav()
        adjusted = native_nav + self._external_funding_balance
        if adjusted <= 0:
            raise RuntimeError("funding-adjusted NAV is non-positive")
        return adjusted

    def on_start(self) -> None:
        super().on_start()
        if len(self.instruments) != len(self.config.instrument_ids):
            return
        for instrument_id in self.config.instrument_ids:
            self.subscribe_mark_prices(instrument_id)
            self.subscribe_funding_rates(instrument_id)

    def on_mark_price(self, update: MarkPriceUpdate) -> None:
        previous = self._latest_mark_by_instrument.get(update.instrument_id)
        if previous is not None and update.ts_event <= previous.ts_event:
            raise RuntimeError("mark prices must arrive in strictly increasing time")
        self._latest_mark_by_instrument[update.instrument_id] = update

    def on_funding_rate(self, update: FundingRateUpdate) -> None:
        key = (update.instrument_id, int(update.ts_event))
        if key in self._processed_funding_keys:
            raise RuntimeError(f"duplicate funding observation: {key}")
        self._processed_funding_keys.add(key)

        mark = self._latest_mark_by_instrument.get(update.instrument_id)
        if mark is None:
            cached = self.cache.mark_price(update.instrument_id)
            if cached is None:
                raise RuntimeError(f"funding arrived without a mark price: {update.instrument_id}")
            mark = cached
        if mark.ts_event > update.ts_event:
            raise RuntimeError("future mark price would leak into funding settlement")

        positions = self.cache.positions_open(
            instrument_id=update.instrument_id,
            strategy_id=self.id,
        )
        if len(positions) > 1:
            raise RuntimeError("single-strategy funding ledger found multiple open positions")
        if not positions:
            self._record(
                "external_funding_observed_flat",
                instrument_id=str(update.instrument_id),
                event_time_ns=update.ts_event,
                mark_time_ns=mark.ts_event,
                mark_price=str(mark.value),
                funding_rate=str(update.rate),
                provenance=FUNDING_LEDGER_PROVENANCE,
            )
            return

        position = positions[0]
        signed_quantity = Decimal(str(position.signed_qty))
        mark_price = Decimal(str(mark.value))
        funding_rate = Decimal(str(update.rate))
        cash_flow = linear_funding_cash_flow(
            signed_quantity,
            mark_price,
            funding_rate,
        )
        before = self._external_funding_balance
        self._external_funding_balance += cash_flow
        self._record(
            "external_funding_settlement",
            plan_id=None if self.active_plan is None else self.active_plan.plan_id,
            position_id=str(position.id),
            instrument_id=str(update.instrument_id),
            event_time_ns=update.ts_event,
            mark_time_ns=mark.ts_event,
            mark_age_ns=update.ts_event - mark.ts_event,
            mark_price=str(mark_price),
            funding_rate=str(funding_rate),
            signed_quantity=str(signed_quantity),
            notional=str(abs(signed_quantity) * mark_price),
            funding_cash_flow=str(cash_flow),
            funding_balance_before=str(before),
            funding_balance_after=str(self._external_funding_balance),
            provenance=FUNDING_LEDGER_PROVENANCE,
            native_engine_limitation=NATIVE_FUNDING_LIMITATION,
        )

    def on_position_closed(self, event: PositionClosed) -> None:
        super().on_position_closed(event)

    def on_stop(self) -> None:
        for instrument_id in self.config.instrument_ids:
            self.unsubscribe_mark_prices(instrument_id)
            self.unsubscribe_funding_rates(instrument_id)
        super().on_stop()


class FundingAdjustedHalfThenBreakevenStrategy(
    ExternalFundingLedgerMixin,
    HalfThenBreakevenStrategy,
):
    """Current source policy with funding-adjusted sizing and accounting."""

    pass

"""Historical perpetual-funding settlement inside NautilusTrader's legacy simulator.

NautilusTrader 1.230.0 exposes two backtest implementations.  The project uses
``nautilus_trader.backtest.engine.BacktestEngine`` because the existing strategy
and order lifecycle are Cython components.  The newer Rust backtest has native
``FundingRateUpdate`` settlement, but feeding those objects to the legacy engine
only publishes them as ordinary data and does not adjust the exchange account.

This module reuses the legacy engine's supported ``SimulationModule`` extension
point and the same ``exchange.adjust_account`` path used by NautilusTrader's own
``FXRolloverInterestModule``.  It is not a portfolio/account simulator: matching,
positions, commissions, account events and continuous NAV remain owned by
NautilusTrader.  The module only supplies the missing venue-specific periodic
cash flow from checksum-verified historical funding boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from nautilus_trader.backtest.config import SimulationModuleConfig
from nautilus_trader.backtest.modules import SimulationModule
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Money

NS_PER_MINUTE = 60_000_000_000


@dataclass(frozen=True, slots=True)
class HistoricalFundingBoundary:
    symbol: str
    instrument_id: InstrumentId
    # Exact Binance archive calc_time. Some historical rows are 1 ms after the
    # nominal clock boundary; preserving this value keeps source provenance.
    funding_time_ns: int
    interval_minutes: int
    rate: Decimal
    mark_price: Decimal

    def __post_init__(self) -> None:
        if self.funding_time_ns <= 0:
            raise ValueError("funding_time_ns must be positive")
        if self.interval_minutes <= 0:
            raise ValueError("interval_minutes must be positive")
        if not self.rate.is_finite():
            raise ValueError("funding rate must be finite")
        if not self.mark_price.is_finite() or self.mark_price <= 0:
            raise ValueError("mark price must be finite and positive")

    @property
    def settlement_time_ns(self) -> int:
        """Minute boundary on which a 1-minute simulation can causally settle.

        Binance occasionally records ``calc_time`` a few milliseconds after the
        nominal boundary.  The project has one-minute execution data, so the
        module runs after strategy callbacks at the containing minute boundary:
        old positions pay, while orders emitted at that boundary cannot fill
        until later data.  This is the closest causal representation available
        without inventing intraminute order events.
        """
        return self.funding_time_ns - self.funding_time_ns % NS_PER_MINUTE


class HistoricalPerpetualFundingModule(SimulationModule):
    """Apply realized linear-perpetual funding through Nautilus account events.

    A positive rate debits longs and credits shorts.  A negative rate reverses
    those cash flows.  Each archive boundary is consumed exactly once, whether
    or not a position is open, which prevents a later position from receiving a
    settlement that occurred before it existed.
    """

    def __init__(self, boundaries: list[HistoricalFundingBoundary]) -> None:
        super().__init__(SimulationModuleConfig())
        ordered = sorted(
            boundaries,
            key=lambda item: (
                item.settlement_time_ns,
                item.funding_time_ns,
                str(item.instrument_id),
            ),
        )
        source_keys = [(item.instrument_id, item.funding_time_ns) for item in ordered]
        settlement_keys = [(item.instrument_id, item.settlement_time_ns) for item in ordered]
        if len(source_keys) != len(set(source_keys)):
            raise ValueError("duplicate instrument funding source boundary")
        if len(settlement_keys) != len(set(settlement_keys)):
            raise ValueError("multiple instrument funding rows map to one simulation minute")
        self.boundaries = tuple(ordered)
        self.cursor = 0
        self.ledger: list[dict[str, Any]] = []
        self.processed_boundaries = 0
        self.settled_positions = 0
        self.total_by_currency: dict[str, Decimal] = {}

    def process(self, ts_now: int) -> None:
        while self.cursor < len(self.boundaries):
            boundary = self.boundaries[self.cursor]
            if boundary.settlement_time_ns > ts_now:
                break
            self.cursor += 1
            self.processed_boundaries += 1
            self._settle_boundary(boundary, ts_now)

    def _settle_boundary(
        self,
        boundary: HistoricalFundingBoundary,
        processed_time_ns: int,
    ) -> None:
        positions = self.exchange.cache.positions_open(
            instrument_id=boundary.instrument_id,
        )
        for position in positions:
            signed_qty = Decimal(str(position.signed_qty))
            if signed_qty == 0:
                continue
            notional = abs(signed_qty) * boundary.mark_price
            side_sign = Decimal("1") if signed_qty > 0 else Decimal("-1")
            amount = -(side_sign * notional * boundary.rate)
            currency = position.settlement_currency
            self.exchange.adjust_account(Money(float(amount), currency))
            currency_code = str(currency.code)
            self.total_by_currency[currency_code] = (
                self.total_by_currency.get(currency_code, Decimal("0")) + amount
            )
            self.settled_positions += 1
            self.ledger.append(
                {
                    "symbol": boundary.symbol,
                    "instrument_id": str(boundary.instrument_id),
                    "position_id": str(position.id),
                    "account_id": str(position.account_id),
                    "strategy_id": str(position.strategy_id),
                    "funding_time_ns": boundary.funding_time_ns,
                    "settlement_time_ns": boundary.settlement_time_ns,
                    "processed_time_ns": int(processed_time_ns),
                    "interval_minutes": boundary.interval_minutes,
                    "rate": str(boundary.rate),
                    "mark_price": str(boundary.mark_price),
                    "signed_qty": str(signed_qty),
                    "notional": str(notional),
                    "currency": currency_code,
                    "amount": str(amount),
                },
            )

    def log_diagnostics(self, logger: Any) -> None:
        totals = ", ".join(
            f"{currency}={amount}"
            for currency, amount in sorted(self.total_by_currency.items())
        ) or "none"
        logger.info(
            "Historical perpetual funding: "
            f"boundaries={self.processed_boundaries}, "
            f"position settlements={self.settled_positions}, totals={totals}",
        )

    def reset(self) -> None:
        self.cursor = 0
        self.ledger = []
        self.processed_boundaries = 0
        self.settled_positions = 0
        self.total_by_currency = {}

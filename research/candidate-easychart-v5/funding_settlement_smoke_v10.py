"""Deterministic native Nautilus funding-settlement smoke test.

NautilusTrader 1.230 initializes a process-global Rust logger, so control and
funded engines are intentionally executed in separate Python processes by the
workflow. This module emits one isolated account result per invocation and
records the data/cache/position state visible when the funding update arrives.

The Binance archive publishes the realized rate at the settlement timestamp.
The pinned engine settles immediately when ``next_funding_ns`` is equal to the
current event time. This adapter translation therefore exposes no rate early and
avoids relying on a later timer for an already-completed historical boundary.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import json
from typing import Any

from nautilus_trader.model.data import Bar, BarType, FundingRateUpdate, MarkPriceUpdate
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Currency
from nautilus_trader.trading.strategy import Strategy

from backtest_support import final_nav, make_engine
from instruments import make_instrument


FUNDING_TS_NS = 1_704_096_000_000_000_000  # 2024-01-01 08:00:00 UTC
SETTLEMENT_TS_NS = FUNDING_TS_NS
MARK_TS_NS = FUNDING_TS_NS - 1_000_000
RATE = Decimal("0.001")
PRICE = Decimal("100.0")
QUANTITY = Decimal("1.000")
EXPECTED_FUNDING_DELTA = -float(PRICE * QUANTITY * RATE)


class HoldAcrossFunding(Strategy):
    def __init__(self, instrument_id, bar_type) -> None:  # type: ignore[no-untyped-def]
        super().__init__()
        self.instrument_id = instrument_id
        self.bar_type = bar_type
        self.bars_seen = 0
        self.mark_callbacks = 0
        self.funding_callbacks = 0
        self.funding_observations: list[dict[str, Any]] = []
        self.bar_observations: list[dict[str, Any]] = []

    def _account_total(self) -> float | None:
        account = self.portfolio.account(Venue("BINANCE"))
        if account is None:
            return None
        money = account.balance_total(Currency.from_str("USDT"))
        return None if money is None else float(money.as_double())

    def _position_snapshot(self) -> dict[str, Any]:
        positions = self.cache.positions_open(
            instrument_id=self.instrument_id,
            strategy_id=self.id,
        )
        return {
            "open_positions": len(positions),
            "position_signed_qty": None if not positions else float(positions[0].signed_qty),
            "position_realized_pnl": (
                None
                if not positions or positions[0].realized_pnl is None
                else str(positions[0].realized_pnl)
            ),
        }

    def on_start(self) -> None:
        self.subscribe_bars(self.bar_type)
        self.subscribe_mark_prices(self.instrument_id)
        self.subscribe_funding_rates(self.instrument_id)

    def on_mark_price(self, update: MarkPriceUpdate) -> None:
        self.mark_callbacks += 1

    def on_funding_rate(self, update: FundingRateUpdate) -> None:
        self.funding_callbacks += 1
        cached_mark = self.cache.mark_price(self.instrument_id)
        cached_funding = self.cache.funding_rate(self.instrument_id)
        self.funding_observations.append(
            {
                "event_ts_ns": update.ts_event,
                "next_funding_ns": update.next_funding_ns,
                "rate": str(update.rate),
                "interval": update.interval,
                "cached_mark": None if cached_mark is None else str(cached_mark.value),
                "cached_funding": (
                    None if cached_funding is None else str(cached_funding.rate)
                ),
                "account_total_after_exchange_route": self._account_total(),
                **self._position_snapshot(),
            },
        )

    def on_bar(self, bar: Bar) -> None:
        self.bar_observations.append(
            {
                "bar_ts_ns": bar.ts_event,
                "bars_seen_before": self.bars_seen,
                "account_total": self._account_total(),
                **self._position_snapshot(),
            },
        )
        instrument = self.cache.instrument(self.instrument_id)
        if instrument is None:
            raise RuntimeError("smoke-test instrument unavailable")
        if self.bars_seen == 0:
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.BUY,
                quantity=instrument.make_qty(QUANTITY),
                time_in_force=TimeInForce.GTC,
            )
            self.submit_order(order)
        elif self.bars_seen == 2:
            self.close_all_positions(self.instrument_id)
        self.bars_seen += 1

    def on_stop(self) -> None:
        self.cancel_all_orders(self.instrument_id)
        if not self.portfolio.is_flat(self.instrument_id):
            self.close_all_positions(self.instrument_id)
        self.unsubscribe_bars(self.bar_type)
        self.unsubscribe_mark_prices(self.instrument_id)
        self.unsubscribe_funding_rates(self.instrument_id)


def _bars(instrument, bar_type):  # type: ignore[no-untyped-def]
    timestamps = [
        FUNDING_TS_NS - 120_000_000_000,
        FUNDING_TS_NS - 60_000_000_000,
        FUNDING_TS_NS + 60_000_000_000,
        FUNDING_TS_NS + 120_000_000_000,
        FUNDING_TS_NS + 180_000_000_000,
    ]
    return [
        Bar(
            bar_type=bar_type,
            open=instrument.make_price(PRICE),
            high=instrument.make_price(PRICE),
            low=instrument.make_price(PRICE),
            close=instrument.make_price(PRICE),
            volume=instrument.make_qty(Decimal("1000")),
            ts_event=timestamp,
            ts_init=timestamp,
        )
        for timestamp in timestamps
    ]


def run_smoke(include_funding: bool) -> dict[str, Any]:
    engine = make_engine()
    instrument = make_instrument("BTCUSDT")
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    engine.add_instrument(instrument)
    engine.add_data(_bars(instrument, bar_type), sort=False)
    if include_funding:
        engine.add_data(
            [
                MarkPriceUpdate(
                    instrument_id=instrument.id,
                    value=instrument.make_price(PRICE),
                    ts_event=MARK_TS_NS,
                    ts_init=MARK_TS_NS,
                ),
            ],
            sort=False,
        )
        engine.add_data(
            [
                FundingRateUpdate(
                    instrument_id=instrument.id,
                    rate=RATE,
                    interval=480,
                    next_funding_ns=SETTLEMENT_TS_NS,
                    ts_event=FUNDING_TS_NS,
                    ts_init=FUNDING_TS_NS,
                ),
            ],
            sort=False,
        )
    engine.sort_data()
    strategy = HoldAcrossFunding(instrument.id, bar_type)
    engine.add_strategy(strategy)
    try:
        engine.run()
        if not engine.portfolio.is_flat(instrument.id):
            raise RuntimeError("funding smoke test did not finish flat")
        positions = engine.trader.generate_positions_report()
        account = engine.trader.generate_account_report(Venue("BINANCE"))
        return {
            "final_nav": final_nav(engine),
            "mark_callbacks": strategy.mark_callbacks,
            "funding_callbacks": strategy.funding_callbacks,
            "funding_observations": strategy.funding_observations,
            "bar_observations": strategy.bar_observations,
            "positions_report_rows": int(len(positions.index)),
            "account_report_rows": int(len(account.index)),
            "closed_position_realized_pnl": (
                None
                if positions.empty
                else str(positions.iloc[-1].get("realized_pnl"))
            ),
        }
    finally:
        engine.dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("control", "funded"), required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    include_funding = args.mode == "funded"
    result = run_smoke(include_funding)
    result.update(
        {
            "mode": args.mode,
            "include_funding": include_funding,
            "expected_funding_delta": EXPECTED_FUNDING_DELTA,
            "settlement_delay_ns": SETTLEMENT_TS_NS - FUNDING_TS_NS,
        },
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

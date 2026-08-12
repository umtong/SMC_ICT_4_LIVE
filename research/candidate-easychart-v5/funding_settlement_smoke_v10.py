"""Deterministic native Nautilus funding-settlement smoke test.

NautilusTrader 1.230 initializes a process-global Rust logger, so control and
funded engines are intentionally executed in separate Python processes by the
workflow. This module emits one isolated account result per invocation.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import json

from nautilus_trader.model.data import Bar, BarType, FundingRateUpdate, MarkPriceUpdate
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.trading.strategy import Strategy

from backtest_support import final_nav, make_engine
from instruments import make_instrument


FUNDING_TS_NS = 1_704_096_000_000_000_000  # 2024-01-01 08:00:00 UTC
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

    def on_start(self) -> None:
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar) -> None:
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


def run_smoke(include_funding: bool) -> float:
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
                    next_funding_ns=None,
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
        return final_nav(engine)
    finally:
        engine.dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("control", "funded"), required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    include_funding = args.mode == "funded"
    nav = run_smoke(include_funding)
    print(
        json.dumps(
            {
                "mode": args.mode,
                "include_funding": include_funding,
                "final_nav": nav,
                "expected_funding_delta": EXPECTED_FUNDING_DELTA,
            },
            indent=2,
            sort_keys=True,
        ),
    )


if __name__ == "__main__":
    main()

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType, FundingRateUpdate, MarkPriceUpdate
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Currency
from nautilus_trader.trading.strategy import Strategy

from backtest_support import make_engine
from funding_data import _read_funding_month, _read_mark_month, load_funding_history
from instruments import make_instrument

NS_PER_MINUTE = 60_000_000_000


class FundingProbeConfig(StrategyConfig, frozen=True):
    instrument_ids: tuple[InstrumentId, ...]
    bar_types: tuple[BarType, ...]
    sides: tuple[OrderSide, ...]
    clock_instrument_id: InstrumentId
    capture_times_ns: tuple[int, ...]


class FundingProbeStrategy(Strategy):
    def __init__(self, config: FundingProbeConfig) -> None:
        super().__init__(config)
        if not len(config.instrument_ids) == len(config.bar_types) == len(config.sides):
            raise ValueError("probe routes must have equal lengths")
        self.route = {
            bar_type.id_spec_key(): (instrument_id, side)
            for instrument_id, bar_type, side in zip(
                config.instrument_ids,
                config.bar_types,
                config.sides,
                strict=True,
            )
        }
        self.instruments = {}
        self.submitted: set[InstrumentId] = set()
        self.balances: dict[int, float] = {}

    def on_start(self) -> None:
        for instrument_id, bar_type in zip(
            self.config.instrument_ids,
            self.config.bar_types,
            strict=True,
        ):
            instrument = self.cache.instrument(instrument_id)
            if instrument is None:
                raise RuntimeError("probe instrument unavailable")
            self.instruments[instrument_id] = instrument
            self.subscribe_bars(bar_type)

    def _balance(self) -> float:
        account = self.portfolio.account(self.instruments[self.config.clock_instrument_id].venue)
        if account is None:
            raise RuntimeError("probe account unavailable")
        value = account.balance_total(Currency.from_str("USDT"))
        if value is None:
            raise RuntimeError("probe USDT balance unavailable")
        return float(value.as_double())

    def on_bar(self, bar: Bar) -> None:
        route = self.route.get(bar.bar_type.id_spec_key())
        if route is None:
            return
        instrument_id, side = route
        if instrument_id not in self.submitted:
            self.submitted.add(instrument_id)
            self.submit_order(
                self.order_factory.market(
                    instrument_id=instrument_id,
                    order_side=side,
                    quantity=self.instruments[instrument_id].make_qty(1),
                ),
            )
        if (
            instrument_id == self.config.clock_instrument_id
            and bar.ts_event in self.config.capture_times_ns
        ):
            if any(self.portfolio.is_flat(item) for item in self.config.instrument_ids):
                raise RuntimeError("probe funding capture occurred before both entries filled")
            self.balances[bar.ts_event] = self._balance()

    def on_stop(self) -> None:
        # Leave constant-price positions open so balance differences between
        # captures are funding only, not exit commissions.
        for bar_type in self.config.bar_types:
            self.unsubscribe_bars(bar_type)


class FundingHistoryTests(unittest.TestCase):
    def test_archive_parsers_use_interval_rate_and_boundary_mark_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            funding_archive = root / "funding.zip"
            mark_archive = root / "mark.zip"
            with ZipFile(funding_archive, "w") as archive:
                archive.writestr(
                    "BTCUSDT-fundingRate-2024-02.csv",
                    "calc_time,funding_interval_hours,last_funding_rate\n"
                    "1706745600000,8,0.00050000\n",
                )
            mark_row = [
                "1706745600000", "101.25", "102", "100", "101.5", "0",
                "1706745659999", "0", "0", "0", "0", "0",
            ]
            with ZipFile(mark_archive, "w") as archive:
                archive.writestr(
                    "BTCUSDT-1m-2024-02.csv",
                    ",".join(mark_row) + "\n",
                )
            with patch("funding_data._verified_archive", return_value=funding_archive):
                funding = _read_funding_month("BTCUSDT", "2024-02", root)
            with patch("funding_data._verified_archive", return_value=mark_archive):
                marks = _read_mark_month("BTCUSDT", "2024-02", root)
        self.assertEqual(funding[0]["fundingRate"], Decimal("0.00050000"))
        self.assertEqual(funding[0]["intervalMinutes"], 480)
        self.assertEqual(marks[1706745600000], Decimal("101.25"))

    def test_history_joins_same_boundary_mark_and_includes_end_flatten_boundary(self) -> None:
        funding_rows = [
            {
                "symbol": "BTCUSDT",
                "fundingTime": 1706745600000,
                "fundingRate": Decimal("0.0005"),
                "intervalMinutes": 480,
            },
            {
                "symbol": "BTCUSDT",
                "fundingTime": 1706832000000,
                "fundingRate": Decimal("-0.0001"),
                "intervalMinutes": 480,
            },
        ]
        marks = {
            1706745600000: Decimal("101.0"),
            1706832000000: Decimal("102.0"),
        }
        with tempfile.TemporaryDirectory() as directory, patch(
            "funding_data._read_funding_month",
            return_value=funding_rows,
        ), patch(
            "funding_data._read_mark_month",
            return_value=marks,
        ):
            rows = load_funding_history(
                "BTCUSDT",
                date(2024, 2, 1),
                date(2024, 2, 1),
                Path(directory),
            )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["markPrice"], Decimal("101.0"))
        self.assertEqual(rows[-1]["fundingTime"], 1706832000000)

    def test_native_funding_debits_long_then_credits_short_in_one_engine(self) -> None:
        engine = make_engine()
        btc = make_instrument("BTCUSDT")
        eth = make_instrument("ETHUSDT")
        engine.add_instrument(btc)
        engine.add_instrument(eth)
        btc_type = BarType.from_str(f"{btc.id}-1-MINUTE-LAST-EXTERNAL")
        eth_type = BarType.from_str(f"{eth.id}-1-MINUTE-LAST-EXTERNAL")
        bar_times = (1, 2, 3, 5, 7)
        for instrument, bar_type in ((btc, btc_type), (eth, eth_type)):
            engine.add_data(
                [
                    Bar(
                        bar_type=bar_type,
                        open=instrument.make_price(100.0),
                        high=instrument.make_price(100.0),
                        low=instrument.make_price(100.0),
                        close=instrument.make_price(100.0),
                        volume=instrument.make_qty(100),
                        ts_event=index * NS_PER_MINUTE,
                        ts_init=index * NS_PER_MINUTE,
                    )
                    for index in bar_times
                ],
                sort=False,
            )

        long_boundary = 4 * NS_PER_MINUTE
        short_boundary = 6 * NS_PER_MINUTE
        engine.add_data(
            [
                MarkPriceUpdate(
                    instrument_id=btc.id,
                    value=btc.make_price(100.0),
                    ts_event=long_boundary,
                    ts_init=long_boundary + 1,
                ),
                MarkPriceUpdate(
                    instrument_id=eth.id,
                    value=eth.make_price(100.0),
                    ts_event=short_boundary,
                    ts_init=short_boundary + 1,
                ),
            ],
            sort=False,
        )
        engine.add_data(
            [
                FundingRateUpdate(
                    instrument_id=btc.id,
                    rate=Decimal("0.01"),
                    ts_event=long_boundary,
                    ts_init=long_boundary + 2,
                    interval=1,
                    next_funding_ns=long_boundary,
                ),
                FundingRateUpdate(
                    instrument_id=eth.id,
                    rate=Decimal("0.02"),
                    ts_event=short_boundary,
                    ts_init=short_boundary + 2,
                    interval=1,
                    next_funding_ns=short_boundary,
                ),
            ],
            sort=False,
        )
        engine.sort_data()
        strategy = FundingProbeStrategy(
            FundingProbeConfig(
                instrument_ids=(btc.id, eth.id),
                bar_types=(btc_type, eth_type),
                sides=(OrderSide.BUY, OrderSide.SELL),
                clock_instrument_id=btc.id,
                capture_times_ns=(
                    3 * NS_PER_MINUTE,
                    5 * NS_PER_MINUTE,
                    7 * NS_PER_MINUTE,
                ),
            ),
        )
        engine.add_strategy(strategy)
        try:
            engine.run()
        finally:
            engine.dispose()

        before = strategy.balances[3 * NS_PER_MINUTE]
        after_long = strategy.balances[5 * NS_PER_MINUTE]
        after_short = strategy.balances[7 * NS_PER_MINUTE]
        self.assertAlmostEqual(after_long - before, -1.0, places=6)
        self.assertAlmostEqual(after_short - after_long, 2.0, places=6)


if __name__ == "__main__":
    unittest.main()

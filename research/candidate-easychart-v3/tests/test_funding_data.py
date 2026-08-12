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
from nautilus_trader.trading.strategy import Strategy

from backtest_support import final_nav, make_engine
from funding_data import (
    NS_PER_MS,
    _read_funding_month,
    _read_mark_month,
    load_funding_history,
)
from instruments import make_instrument

NS_PER_MINUTE = 60_000_000_000


class FundingProbeConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    side: OrderSide


class FundingProbeStrategy(Strategy):
    def __init__(self, config: FundingProbeConfig) -> None:
        super().__init__(config)
        self.submitted = False
        self.instrument = None

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            raise RuntimeError("probe instrument unavailable")
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        if self.submitted:
            return
        self.submitted = True
        self.submit_order(
            self.order_factory.market(
                instrument_id=self.config.instrument_id,
                order_side=self.config.side,
                quantity=self.instrument.make_qty(1),
            ),
        )

    def on_stop(self) -> None:
        # Leave the constant-price position open so paired runs differ only by
        # native funding, not by another close commission.
        self.unsubscribe_bars(self.config.bar_type)


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

    def _run_probe(self, side: OrderSide, funding_rate: Decimal | None) -> float:
        engine = make_engine()
        instrument = make_instrument("BTCUSDT")
        engine.add_instrument(instrument)
        bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
        bars = [
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
            for index in (1, 2, 4)
        ]
        engine.add_data(bars, sort=False)
        boundary = 3 * NS_PER_MINUTE
        engine.add_data(
            [
                MarkPriceUpdate(
                    instrument_id=instrument.id,
                    value=instrument.make_price(100.0),
                    ts_event=boundary,
                    ts_init=boundary + 1,
                ),
            ],
            sort=False,
        )
        if funding_rate is not None:
            engine.add_data(
                [
                    FundingRateUpdate(
                        instrument_id=instrument.id,
                        rate=funding_rate,
                        ts_event=boundary,
                        ts_init=boundary + 2,
                        interval=1,
                        next_funding_ns=boundary,
                    ),
                ],
                sort=False,
            )
        engine.sort_data()
        engine.add_strategy(
            FundingProbeStrategy(
                FundingProbeConfig(
                    instrument_id=instrument.id,
                    bar_type=bar_type,
                    side=side,
                ),
            ),
        )
        try:
            engine.run()
            return final_nav(engine)
        finally:
            engine.dispose()

    def test_native_funding_debits_long_and_credits_short(self) -> None:
        rate = Decimal("0.01")
        baseline_long = self._run_probe(OrderSide.BUY, None)
        funded_long = self._run_probe(OrderSide.BUY, rate)
        baseline_short = self._run_probe(OrderSide.SELL, None)
        funded_short = self._run_probe(OrderSide.SELL, rate)
        self.assertAlmostEqual(funded_long - baseline_long, -1.0, places=6)
        self.assertAlmostEqual(funded_short - baseline_short, 1.0, places=6)


if __name__ == "__main__":
    unittest.main()

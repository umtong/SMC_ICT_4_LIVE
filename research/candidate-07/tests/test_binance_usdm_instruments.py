from __future__ import annotations

from decimal import Decimal
import unittest

from binance_usdm_instruments import (
    CONTRACT_GRIDS,
    binance_usdm_perpetual,
    validate_observed_grid,
)


class BinanceUsdmInstrumentTests(unittest.TestCase):
    def test_btc_matches_frozen_nautilus_grid_and_fees(self) -> None:
        instrument = binance_usdm_perpetual("BTCUSDT")
        self.assertEqual(str(instrument.id), "BTCUSDT-PERP.BINANCE")
        self.assertEqual(str(instrument.price_increment), "0.1")
        self.assertEqual(str(instrument.size_increment), "0.001")
        self.assertEqual(instrument.taker_fee, Decimal("0.000180"))
        self.assertIsNone(instrument.max_quantity)
        self.assertIsNone(instrument.max_notional)

    def test_eth_uses_official_linear_perpetual_grid(self) -> None:
        instrument = binance_usdm_perpetual("ETHUSDT")
        self.assertEqual(str(instrument.id), "ETHUSDT-PERP.BINANCE")
        self.assertEqual(instrument.price_precision, 2)
        self.assertEqual(instrument.size_precision, 3)
        self.assertEqual(str(instrument.price_increment), "0.01")
        self.assertEqual(str(instrument.size_increment), "0.001")

    def test_project_symbol_grids_are_positive(self) -> None:
        self.assertEqual(set(CONTRACT_GRIDS), {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"})
        for grid in CONTRACT_GRIDS.values():
            self.assertGreater(Decimal(grid.price_increment), 0)
            self.assertGreater(Decimal(grid.size_increment), 0)

    def test_observed_grid_validation_fails_closed(self) -> None:
        validate_observed_grid(
            symbol="ETHUSDT",
            prices=[Decimal("3000.01"), Decimal("3000.02")],
            quantities=[Decimal("0.001"), Decimal("1.234")],
        )
        with self.assertRaises(RuntimeError):
            validate_observed_grid(
                symbol="ETHUSDT",
                prices=[Decimal("3000.001")],
                quantities=[Decimal("0.001")],
            )


if __name__ == "__main__":
    unittest.main()

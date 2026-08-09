from __future__ import annotations

from decimal import Decimal
import unittest

from instrument_contracts import ALLOWED_SYMBOLS
from instrument_contracts import instrument_contract


class InstrumentContractsTest(unittest.TestCase):
    def test_project_symbol_universe_is_exact(self) -> None:
        self.assertEqual(
            ALLOWED_SYMBOLS,
            ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"),
        )

    def test_btc_contract_remains_exact_v26_control(self) -> None:
        contract = instrument_contract("btcusdt")
        self.assertEqual(contract.price_precision, 1)
        self.assertEqual(contract.price_increment, "0.1")
        self.assertEqual(contract.size_precision, 3)
        self.assertEqual(contract.size_increment, "0.001")
        self.assertEqual(contract.min_quantity, "0.001")
        self.assertEqual(contract.min_notional, 10.0)
        self.assertEqual(contract.metadata_source, "FROZEN_CANDIDATE_05_V26")

    def test_other_contracts_match_frozen_official_probe(self) -> None:
        expected = {
            "ETHUSDT": (2, "0.01", 3, "0.001", "0.001", 20.0),
            "SOLUSDT": (4, "0.0100", 2, "0.01", "0.01", 5.0),
            "XRPUSDT": (4, "0.0001", 1, "0.1", "0.1", 5.0),
        }
        for symbol, values in expected.items():
            with self.subTest(symbol=symbol):
                contract = instrument_contract(symbol)
                self.assertEqual(
                    (
                        contract.price_precision,
                        contract.price_increment,
                        contract.size_precision,
                        contract.size_increment,
                        contract.min_quantity,
                        contract.min_notional,
                    ),
                    values,
                )
                self.assertIn(
                    "BINANCE_OFFICIAL_USDM_TESTNET",
                    contract.metadata_source,
                )

    def test_ids_are_derived_without_symbol_specific_strategy_logic(self) -> None:
        for symbol in ALLOWED_SYMBOLS:
            with self.subTest(symbol=symbol):
                contract = instrument_contract(symbol)
                self.assertEqual(
                    contract.instrument_id,
                    f"{symbol}-PERP.BINANCE",
                )
                self.assertEqual(
                    contract.bar_type,
                    f"{symbol}-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL",
                )

    def test_increment_precision_matches_declared_contract(self) -> None:
        for symbol in ALLOWED_SYMBOLS:
            with self.subTest(symbol=symbol):
                contract = instrument_contract(symbol)
                price_decimals = max(
                    0,
                    -Decimal(contract.price_increment).as_tuple().exponent,
                )
                size_decimals = max(
                    0,
                    -Decimal(contract.size_increment).as_tuple().exponent,
                )
                self.assertLessEqual(price_decimals, contract.price_precision)
                self.assertLessEqual(size_decimals, contract.size_precision)

    def test_unknown_symbol_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            instrument_contract("DOGEUSDT")


if __name__ == "__main__":
    unittest.main()

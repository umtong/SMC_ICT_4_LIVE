from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
import zipfile

from probe_observed_contract_grid import (
    DecimalGridAccumulator,
    _archive_grid,
    _merge_accumulators,
)


class ObservedContractGridTests(unittest.TestCase):
    def test_dynamic_scale_preserves_exact_decimal_gcd(self) -> None:
        accumulator = DecimalGridAccumulator()
        for raw in ("0.1", "0.04", "0.003"):
            accumulator.add(raw)
        self.assertEqual(accumulator.scale, 3)
        self.assertEqual(accumulator.quantum, Decimal("0.001"))
        self.assertEqual(accumulator.rows, 3)

    def test_daily_accumulators_merge_without_precision_loss(self) -> None:
        first = DecimalGridAccumulator()
        second = DecimalGridAccumulator()
        for raw in ("0.02", "0.04"):
            first.add(raw)
        for raw in ("0.003", "0.009"):
            second.add(raw)
        merged = _merge_accumulators((first, second))
        self.assertEqual(merged.quantum, Decimal("0.001"))
        self.assertEqual(merged.rows, 4)

    def test_header_archive_is_streamed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trades.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "SOLUSDT-aggTrades.csv",
                    "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker\n"
                    "1,125.001,0.04,1,1,1766361600000,false\n"
                    "2,125.002,0.01,2,2,1766361600001,true\n",
                )
            prices, quantities, rows = _archive_grid(path)
        self.assertEqual(rows, 2)
        self.assertEqual(prices.quantum, Decimal("0.001"))
        self.assertEqual(quantities.quantum, Decimal("0.01"))

    def test_headerless_archive_is_streamed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trades.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "XRPUSDT-aggTrades.csv",
                    "1,2.1234,1.2,1,1,1766361600000,false\n"
                    "2,2.1235,0.1,2,2,1766361600001,true\n",
                )
            prices, quantities, rows = _archive_grid(path)
        self.assertEqual(rows, 2)
        self.assertEqual(prices.quantum, Decimal("0.0001"))
        self.assertEqual(quantities.quantum, Decimal("0.1"))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from nautilus_trader.test_kit.providers import TestInstrumentProvider

from index_reference import IndexPriceReference


class IndexPriceReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.instrument_id = TestInstrumentProvider.btcusdt_perp_binance().id

    def test_completed_index_bar_is_immutable_data_payload(self) -> None:
        payload = IndexPriceReference(
            instrument_id=self.instrument_id,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            ts_event=300_000_000_000,
            ts_init=300_000_000_000,
        )
        self.assertEqual(payload.instrument_id, self.instrument_id)
        self.assertEqual(payload.close, 100.5)
        self.assertEqual(payload.ts_event, 300_000_000_000)
        self.assertEqual(payload.ts_init, 300_000_000_000)

    def test_rejects_inconsistent_ohlc(self) -> None:
        with self.assertRaises(ValueError):
            IndexPriceReference(
                instrument_id=self.instrument_id,
                open=100.0,
                high=99.0,
                low=98.0,
                close=100.5,
                ts_event=1,
                ts_init=1,
            )

    def test_rejects_nonpositive_price(self) -> None:
        with self.assertRaises(ValueError):
            IndexPriceReference(
                instrument_id=self.instrument_id,
                open=100.0,
                high=101.0,
                low=0.0,
                close=100.5,
                ts_event=1,
                ts_init=1,
            )


if __name__ == "__main__":
    unittest.main()

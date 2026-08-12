from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd

from nautilus_trader.model.data import FundingRateUpdate, MarkPriceUpdate

from funding_data_v10 import (
    FundingObservation,
    MarkObservation,
    add_symbol_funding_data,
    align_preceding_marks,
    epoch_values_to_ns,
    parse_funding_frame,
    parse_mark_frame,
)
from instruments import make_instrument


class FakeEngine:
    def __init__(self) -> None:
        self.batches: list[list[object]] = []

    def add_data(self, data, sort=False):  # type: ignore[no-untyped-def]
        self.batches.append(list(data))


class FundingDataTests(unittest.TestCase):
    def test_epoch_units_are_normalized_without_rounding(self) -> None:
        milliseconds = pd.Series([1_700_000_000_000, 1_700_000_000_001])
        microseconds = pd.Series([1_700_000_000_000_000, 1_700_000_000_000_001])
        nanoseconds = pd.Series([1_700_000_000_000_000_000])
        self.assertEqual(
            epoch_values_to_ns(milliseconds).tolist(),
            [1_700_000_000_000_000_000, 1_700_000_000_001_000_000],
        )
        self.assertEqual(
            epoch_values_to_ns(microseconds).tolist(),
            [1_700_000_000_000_000_000, 1_700_000_000_000_001_000],
        )
        self.assertEqual(
            epoch_values_to_ns(nanoseconds).tolist(),
            [1_700_000_000_000_000_000],
        )

    def test_funding_parser_preserves_decimal_rate_and_variable_interval(self) -> None:
        frame = pd.DataFrame(
            {
                "calc_time": ["1704067200000", "1704096000000"],
                "funding_interval_hours": ["8", "4"],
                "last_funding_rate": ["0.00010000", "-0.00002550"],
            },
        )
        rows = parse_funding_frame(frame)
        self.assertEqual(rows[0].interval_minutes, 480)
        self.assertEqual(rows[1].interval_minutes, 240)
        self.assertEqual(rows[0].rate, Decimal("0.00010000"))
        self.assertEqual(rows[1].rate, Decimal("-0.00002550"))
        self.assertLess(rows[0].timestamp_ns, rows[1].timestamp_ns)

    def test_mark_alignment_uses_only_preceding_completed_price(self) -> None:
        funding = [FundingObservation(10_000, 480, Decimal("0.0001"))]
        marks = [
            MarkObservation(9_000, Decimal("100")),
            MarkObservation(10_001, Decimal("999")),
        ]
        pair = align_preceding_marks(funding, marks, maximum_age_ns=2_000)[0]
        self.assertEqual(pair.mark.timestamp_ns, 9_000)
        self.assertEqual(pair.mark.value, Decimal("100"))
        with self.assertRaisesRegex(ValueError, "stale mark"):
            align_preceding_marks(funding, marks, maximum_age_ns=999)

    def test_mark_parser_rejects_nonpositive_prices(self) -> None:
        frame = pd.DataFrame({"close_time": ["1704067199999"], "close": ["0"]})
        with self.assertRaisesRegex(ValueError, "positive"):
            parse_mark_frame(frame)

    def test_native_nautilus_events_are_emitted_in_causal_order(self) -> None:
        engine = FakeEngine()
        instrument = make_instrument("BTCUSDT")
        funding = [
            FundingObservation(
                1_704_096_000_000_000_000,
                480,
                Decimal("0.0001"),
            ),
        ]
        marks = [
            MarkObservation(
                1_704_095_999_999_000_000,
                Decimal("42000.1"),
            ),
        ]
        with (
            patch("funding_data_v10.load_funding_observations", return_value=funding),
            patch("funding_data_v10.load_mark_observations", return_value=marks),
        ):
            metadata = add_symbol_funding_data(
                engine,
                "BTCUSDT",
                instrument,
                date(2024, 1, 1),
                date(2024, 1, 2),
                Path("unused"),
            )
        self.assertEqual(len(engine.batches), 2)
        mark = engine.batches[0][0]
        funding_event = engine.batches[1][0]
        self.assertIsInstance(mark, MarkPriceUpdate)
        self.assertIsInstance(funding_event, FundingRateUpdate)
        self.assertLess(mark.ts_event, funding_event.ts_event)
        self.assertEqual(str(funding_event.rate), "0.0001")
        self.assertEqual(funding_event.interval, 480)
        self.assertEqual(metadata["funding_updates"], 1)


if __name__ == "__main__":
    unittest.main()

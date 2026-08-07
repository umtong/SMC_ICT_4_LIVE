"""Regression tests for natural no-trade interval handling in v24."""
from __future__ import annotations

import unittest

from c10_v24_dense_alignment import align_cross_market_rows_dense
from c10_v24_research import _AggBucket


def _bucket(
    *,
    close: float,
    first_ts: int,
    last_ts: int,
    volume: float = 1_000.0,
) -> _AggBucket:
    return _AggBucket(
        open=close,
        high=close,
        low=close,
        close=close,
        quote_volume=volume,
        taker_buy_quote=0.6 * volume,
        trade_count=10,
        first_trade_ts_ns=first_ts,
        last_trade_ts_ns=last_ts,
    )


class DenseAlignmentTests(unittest.TestCase):
    def test_one_sided_no_trade_interval_is_zero_flow_not_gap(self) -> None:
        spot = {
            5_000_000_000: _bucket(
                close=100.0,
                first_ts=1_000_000_000,
                last_ts=4_000_000_000,
            ),
            10_000_000_000: _bucket(
                close=101.0,
                first_ts=6_000_000_000,
                last_ts=9_000_000_000,
            ),
        }
        perp = {
            5_000_000_000: _bucket(
                close=100.1,
                first_ts=1_500_000_000,
                last_ts=4_500_000_000,
            ),
            # No perpetual trade in [5s,10s); this is a valid market state.
            15_000_000_000: _bucket(
                close=101.2,
                first_ts=11_000_000_000,
                last_ts=14_000_000_000,
            ),
        }
        rows, quality = align_cross_market_rows_dense(
            spot,
            perp,
            bucket_seconds=5,
        )
        self.assertEqual(len(rows), 2)
        second = rows[1]
        self.assertFalse(second["spot_empty_interval"])
        self.assertTrue(second["perp_empty_interval"])
        self.assertEqual(second["perp_close"], 100.1)
        self.assertEqual(second["perp_open"], 100.1)
        self.assertEqual(second["perp_quote_volume"], 0.0)
        self.assertEqual(second["perp_taker_buy_quote"], 0.0)
        self.assertEqual(second["perp_trade_count"], 0)
        self.assertLess(second["perp_last_trade_ts_ns"], second["ts_ns"])
        self.assertEqual(quality["gap_count"], 0)
        self.assertEqual(quality["perp_empty_interval_count"], 1)

    def test_both_empty_interval_uses_only_strictly_prior_prices(self) -> None:
        spot = {
            5_000_000_000: _bucket(
                close=100.0,
                first_ts=1_000_000_000,
                last_ts=4_000_000_000,
            ),
            15_000_000_000: _bucket(
                close=102.0,
                first_ts=11_000_000_000,
                last_ts=14_000_000_000,
            ),
        }
        perp = {
            5_000_000_000: _bucket(
                close=100.2,
                first_ts=1_000_000_000,
                last_ts=4_500_000_000,
            ),
            15_000_000_000: _bucket(
                close=102.2,
                first_ts=11_000_000_000,
                last_ts=14_500_000_000,
            ),
        }
        rows, quality = align_cross_market_rows_dense(
            spot,
            perp,
            bucket_seconds=5,
        )
        self.assertEqual(len(rows), 3)
        middle = rows[1]
        self.assertTrue(middle["spot_empty_interval"])
        self.assertTrue(middle["perp_empty_interval"])
        self.assertEqual(middle["spot_close"], 100.0)
        self.assertEqual(middle["perp_close"], 100.2)
        self.assertNotEqual(middle["spot_close"], 102.0)
        self.assertNotEqual(middle["perp_close"], 102.2)
        self.assertEqual(quality["both_empty_interval_count"], 1)

    def test_dense_grid_rejects_noncausal_source_timestamp(self) -> None:
        spot = {
            5_000_000_000: _bucket(
                close=100.0,
                first_ts=1_000_000_000,
                last_ts=5_000_000_000,
            ),
        }
        perp = {
            5_000_000_000: _bucket(
                close=100.1,
                first_ts=1_000_000_000,
                last_ts=4_000_000_000,
            ),
        }
        with self.assertRaisesRegex(RuntimeError, "noncausal"):
            align_cross_market_rows_dense(
                spot,
                perp,
                bucket_seconds=5,
            )


if __name__ == "__main__":
    unittest.main()

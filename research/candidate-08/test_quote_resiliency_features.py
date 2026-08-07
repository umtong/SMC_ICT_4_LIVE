"""Pure causal contracts for top-of-book quote resiliency features."""

from __future__ import annotations

from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from quote_resiliency_features_v2 import (
    IMPLEMENTATION_REVISION,
    QuoteResiliencyConfig,
    aggregate_quote_events,
    build_quote_resiliency_features,
    quote_event_rows,
    validate_exact_cadence,
)


class QuoteEventContracts(unittest.TestCase):
    @staticmethod
    def _quotes() -> pd.DataFrame:
        index = pd.date_range("2023-10-15T00:00:01Z", periods=6, freq="1s")
        return pd.DataFrame(
            {
                "best_bid_price": [100.0, 100.0, 99.9, 99.9, 100.0, 100.0],
                "best_bid_qty": [12.0, 12.0, 9.0, 9.0, 5.0, 5.0],
                "best_ask_price": [100.1, 100.1, 100.1, 100.2, 100.2, 100.1],
                "best_ask_qty": [8.0, 11.0, 11.0, 7.0, 7.0, 6.0],
            },
            index=index,
        )

    def test_order_flow_decomposition_matches_economic_signs(self) -> None:
        events, final = quote_event_rows(
            self._quotes(),
            previous_quote={
                "best_bid_price": 100.0,
                "best_bid_qty": 10.0,
                "best_ask_price": 100.1,
                "best_ask_qty": 8.0,
            },
        )
        self.assertEqual(events["bid_add_qty"].tolist(), [2.0, 0.0, 0.0, 0.0, 5.0, 0.0])
        self.assertEqual(events["bid_remove_qty"].tolist(), [0.0, 0.0, 12.0, 0.0, 0.0, 0.0])
        self.assertEqual(events["ask_add_qty"].tolist(), [0.0, 3.0, 0.0, 0.0, 0.0, 6.0])
        self.assertEqual(events["ask_remove_qty"].tolist(), [0.0, 0.0, 0.0, 11.0, 0.0, 0.0])
        self.assertEqual(events["quote_ofi_qty"].tolist(), [2.0, -3.0, -12.0, 11.0, 5.0, -6.0])
        self.assertEqual(final["best_bid_price"], 100.0)
        self.assertEqual(final["best_ask_qty"], 6.0)

    def test_cross_chunk_previous_state_exactly_matches_single_pass(self) -> None:
        quotes = self._quotes()
        previous = {
            "best_bid_price": 100.0,
            "best_bid_qty": 10.0,
            "best_ask_price": 100.1,
            "best_ask_qty": 8.0,
        }
        whole, _ = quote_event_rows(quotes, previous_quote=previous)
        first, state = quote_event_rows(quotes.iloc[:3], previous_quote=previous)
        second, _ = quote_event_rows(quotes.iloc[3:], previous_quote=state)
        combined = pd.concat([first, second])
        pd.testing.assert_frame_equal(whole, combined)

    def test_completed_bucket_is_right_labeled_and_preserves_flows(self) -> None:
        events, _ = quote_event_rows(
            self._quotes(),
            previous_quote={
                "best_bid_price": 100.0,
                "best_bid_qty": 10.0,
                "best_ask_price": 100.1,
                "best_ask_qty": 8.0,
            },
        )
        bucket = aggregate_quote_events(events, cadence_seconds=10)
        self.assertEqual(bucket.index.tolist(), [pd.Timestamp("2023-10-15T00:00:10Z")])
        self.assertEqual(float(bucket.iloc[0]["quote_update_count"]), 6.0)
        self.assertEqual(float(bucket.iloc[0]["bid_add_qty"]), 7.0)
        self.assertEqual(float(bucket.iloc[0]["bid_remove_qty"]), 12.0)
        self.assertEqual(float(bucket.iloc[0]["ask_add_qty"]), 9.0)
        self.assertEqual(float(bucket.iloc[0]["ask_remove_qty"]), 11.0)
        self.assertEqual(float(bucket.iloc[0]["quote_ofi_qty"]), -3.0)
        self.assertEqual(float(bucket.iloc[0]["mid_open"]), 100.05)
        self.assertEqual(float(bucket.iloc[0]["mid_close"]), 100.05)

    def test_invalid_crossed_or_nonpositive_quotes_are_rejected(self) -> None:
        crossed = self._quotes().copy()
        crossed.loc[crossed.index[0], "best_bid_price"] = 101.0
        with self.assertRaisesRegex(ValueError, "crossed quote"):
            quote_event_rows(crossed)
        nonpositive = self._quotes().copy()
        nonpositive.loc[nonpositive.index[0], "best_ask_qty"] = 0.0
        with self.assertRaisesRegex(ValueError, "must be positive"):
            quote_event_rows(nonpositive)


class JoinedFeatureContracts(unittest.TestCase):
    @staticmethod
    def _inputs(rows: int = 120) -> tuple[pd.DataFrame, pd.DataFrame, QuoteResiliencyConfig]:
        index = pd.date_range("2023-10-15T00:00:10Z", periods=rows, freq="10s")
        close = 100.0 + np.arange(rows, dtype=float) * 0.01
        signed = np.where(np.arange(rows) % 2 == 0, 2.0, -2.0)
        trade = pd.DataFrame(
            {
                "open": close - 0.01,
                "high": close + 0.02,
                "low": close - 0.02,
                "close": close,
                "volume": np.full(rows, 10.0),
                "signed_volume": signed,
                "trade_count": np.full(rows, 20.0),
            },
            index=index,
        )
        bid = close - 0.05
        ask = close + 0.05
        quote = pd.DataFrame(
            {
                "bid_open": bid - 0.01,
                "bid_close": bid,
                "bid_qty_open": np.full(rows, 8.0),
                "bid_qty_close": np.full(rows, 9.0),
                "ask_open": ask - 0.01,
                "ask_close": ask,
                "ask_qty_open": np.full(rows, 7.0),
                "ask_qty_close": np.full(rows, 6.0),
                "mid_open": close - 0.01,
                "mid_high": close + 0.01,
                "mid_low": close - 0.02,
                "mid_close": close,
                "microprice_close": close + 0.005,
                "quote_imbalance_close": np.full(rows, 0.2),
                "spread_open": np.full(rows, 0.1),
                "spread_max": np.full(rows, 0.2),
                "spread_median": np.full(rows, 0.1),
                "spread_close": np.full(rows, 0.1),
                "bid_add_qty": np.full(rows, 3.0),
                "bid_remove_qty": np.full(rows, 1.0),
                "ask_add_qty": np.full(rows, 1.0),
                "ask_remove_qty": np.full(rows, 2.0),
                "quote_ofi_qty": np.where(np.arange(rows) % 2 == 0, 3.0, -3.0),
                "quote_update_count": np.full(rows, 100.0),
                "quote_price_change_count": np.full(rows, 2.0),
                "quote_size_only_change_count": np.full(rows, 98.0),
            },
            index=index,
        )
        config = QuoteResiliencyConfig(
            baseline_bars=20,
            minimum_history_bars=10,
        )
        return trade, quote, config

    def test_current_extreme_cannot_change_its_own_causal_scales(self) -> None:
        trade, quote, config = self._inputs()
        baseline = build_quote_resiliency_features(
            trade_bars=trade,
            quote_buckets=quote,
            tick=0.1,
            config=config,
        )
        changed_trade = trade.copy()
        changed_quote = quote.copy()
        position = 50
        changed_trade.iloc[position, changed_trade.columns.get_loc("signed_volume")] = 10000.0
        changed_quote.iloc[position, changed_quote.columns.get_loc("quote_ofi_qty")] = -20000.0
        changed = build_quote_resiliency_features(
            trade_bars=changed_trade,
            quote_buckets=changed_quote,
            tick=0.1,
            config=config,
        )
        self.assertEqual(
            baseline.iloc[position]["aggressive_flow_scale"],
            changed.iloc[position]["aggressive_flow_scale"],
        )
        self.assertEqual(
            baseline.iloc[position]["quote_ofi_scale"],
            changed.iloc[position]["quote_ofi_scale"],
        )
        self.assertGreater(abs(float(changed.iloc[position]["aggressive_pressure_ratio"])), 100.0)
        self.assertGreater(abs(float(changed.iloc[position]["quote_ofi_ratio"])), 100.0)

    def test_future_rows_do_not_change_completed_feature_history(self) -> None:
        trade, quote, config = self._inputs()
        first = build_quote_resiliency_features(
            trade_bars=trade,
            quote_buckets=quote,
            tick=0.1,
            config=config,
        )
        future_index = pd.date_range(trade.index[-1] + pd.Timedelta(seconds=10), periods=3, freq="10s")
        future_trade = pd.concat(
            [
                trade,
                pd.DataFrame(
                    {
                        "open": [50.0, 150.0, 1.0],
                        "high": [200.0, 300.0, 400.0],
                        "low": [1.0, 2.0, 3.0],
                        "close": [180.0, 4.0, 350.0],
                        "volume": [1e6, 1e6, 1e6],
                        "signed_volume": [1e6, -1e6, 1e6],
                        "trade_count": [1e6, 1e6, 1e6],
                    },
                    index=future_index,
                ),
            ]
        )
        future_quote_rows = quote.iloc[[-1]].copy()
        future_quote = pd.concat([quote, pd.concat([future_quote_rows] * 3).set_axis(future_index)])
        future_quote.loc[future_index, "quote_ofi_qty"] = [1e6, -1e6, 1e6]
        second = build_quote_resiliency_features(
            trade_bars=future_trade,
            quote_buckets=future_quote,
            tick=0.1,
            config=config,
        )
        pd.testing.assert_frame_equal(first, second.loc[first.index])

    def test_response_ratios_and_revision_are_dimensionally_stable(self) -> None:
        trade, quote, config = self._inputs()
        result = build_quote_resiliency_features(
            trade_bars=trade,
            quote_buckets=quote,
            tick=0.1,
            config=config,
        )
        self.assertAlmostEqual(float(result.iloc[-1]["bid_response_ratio"]), 3.0, places=10)
        self.assertAlmostEqual(float(result.iloc[-1]["ask_response_ratio"]), 0.5, places=10)
        self.assertEqual(result.attrs["implementation_revision"], IMPLEMENTATION_REVISION)
        self.assertEqual(
            result.attrs["split_bucket_contract"],
            "PRODUCTION_LOADER_CARRIES_RAW_OPEN_BUCKET",
        )
        self.assertTrue(bool(result.iloc[-1]["quote_resiliency_observable"]))

    def test_missing_or_jittered_completed_bucket_is_rejected(self) -> None:
        trade, quote, config = self._inputs()
        broken = trade.drop(trade.index[10])
        with self.assertRaisesRegex(ValueError, "cadence is not exact"):
            build_quote_resiliency_features(
                trade_bars=broken,
                quote_buckets=quote,
                tick=0.1,
                config=config,
            )
        jittered = trade.copy()
        jittered.index = jittered.index.to_list()[:20] + [
            jittered.index[20] + pd.Timedelta(milliseconds=1),
            *jittered.index.to_list()[21:],
        ]
        with self.assertRaisesRegex(ValueError, "cadence is not exact"):
            validate_exact_cadence(jittered.index, seconds=10)

    def test_warmup_is_explicitly_unobservable(self) -> None:
        trade, quote, config = self._inputs()
        result = build_quote_resiliency_features(
            trade_bars=trade,
            quote_buckets=quote,
            tick=0.1,
            config=config,
        )
        self.assertFalse(result.iloc[:10]["quote_resiliency_observable"].any())
        self.assertTrue(result.iloc[10:]["quote_resiliency_observable"].all())

    def test_source_contains_no_outcome_or_execution_proxy(self) -> None:
        source = Path(__file__).with_name("quote_resiliency_features.py").read_text(
            encoding="utf-8"
        ) + Path(__file__).with_name("quote_resiliency_features_v2.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "realized_pnl",
            "future_high",
            "future_low",
            "win_rate",
            "profit_factor",
            "model_score",
            "risk_multiplier",
            "BacktestEngine(",
            "submit_order",
            "order_factory",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

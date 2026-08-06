"""Causal contracts for candidate-08 aggressive-flow price-response primitives."""

from __future__ import annotations

from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from aggtrade_flow_response import (
    FlowResponseConfig,
    FlowResponseState,
    _causal_window_impact_response,
    _classify_frame_states,
    causal_flow_response_frame,
    classify_flow_response,
)


def _history(rows: int = 130) -> pd.DataFrame:
    index = pd.date_range("2024-01-01T00:00:10Z", periods=rows, freq="10s")
    signs = np.where(np.arange(rows) % 2 == 0, 1.0, -1.0)
    close = 100.0 + np.cumsum(signs * 0.05)
    open_ = np.r_[100.0, close[:-1]]
    high = np.maximum(open_, close) + 0.05
    low = np.minimum(open_, close) - 0.05
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(rows, 100.0),
            "signed_volume": signs * 25.0,
            "trade_count": np.full(rows, 100.0),
        },
        index=index,
    )


def _feature_row(**overrides: float) -> dict[str, float]:
    result = {
        "causal_impact_beta": 0.8,
        "flow_direction": 1.0,
        "flow_consistency": 0.9,
        "window_pressure_ratio": 1.2,
        "progress_noise": 1.3,
        "excursion_noise": 1.5,
        "retention": 0.8,
        "response_surprise": 0.2,
    }
    result.update(overrides)
    return result


class FlowResponseCausalityContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.config = FlowResponseConfig(
            impact_lookback_bars=100,
            minimum_history_bars=40,
            response_window_bars=3,
        )

    def test_future_rows_do_not_change_an_already_completed_feature_row(self) -> None:
        initial = _history(130)
        first = causal_flow_response_frame(
            initial,
            tick=0.01,
            config=self.config,
        )
        future = _history(6)
        future.index = pd.date_range(
            initial.index[-1] + pd.Timedelta(seconds=10),
            periods=len(future),
            freq="10s",
        )
        future.loc[:, "close"] = [500.0, 10.0, 700.0, 5.0, 900.0, 1.0]
        future.loc[:, "open"] = future["close"].shift(1).fillna(initial.iloc[-1]["close"])
        future.loc[:, "high"] = np.maximum(future["open"], future["close"]) + 50.0
        future.loc[:, "low"] = np.minimum(future["open"], future["close"]) - 50.0
        future.loc[:, "signed_volume"] = [1e9, -1e9, 1e9, -1e9, 1e9, -1e9]
        second = causal_flow_response_frame(
            pd.concat([initial, future]),
            tick=0.01,
            config=self.config,
        )

        pd.testing.assert_series_equal(
            first.loc[initial.index[-1]],
            second.loc[initial.index[-1]],
            check_names=False,
        )

    def test_current_extreme_cannot_change_its_own_causal_baselines(self) -> None:
        ordinary = _history(130)
        extreme = ordinary.copy()
        timestamp = ordinary.index[-1]
        extreme.loc[timestamp, "signed_volume"] = 1e12
        extreme.loc[timestamp, "volume"] = 1e12
        extreme.loc[timestamp, "open"] = ordinary.loc[timestamp, "open"]
        extreme.loc[timestamp, "close"] = 1000.0
        extreme.loc[timestamp, "high"] = 1001.0
        extreme.loc[timestamp, "low"] = 99.0

        first = causal_flow_response_frame(
            ordinary,
            tick=0.01,
            config=self.config,
        ).loc[timestamp]
        second = causal_flow_response_frame(
            extreme,
            tick=0.01,
            config=self.config,
        ).loc[timestamp]

        for field in (
            "causal_noise_reserve",
            "causal_volume_baseline",
            "causal_impact_beta",
        ):
            self.assertEqual(first[field], second[field], field)

    def test_window_impact_response_sums_each_causal_bucket_in_matching_units(self) -> None:
        index = pd.RangeIndex(3)
        realized, expected, surprise = _causal_window_impact_response(
            signed_activity=pd.Series([1.0, 2.0, 3.0], index=index),
            normalized_price_change=pd.Series([0.2, 0.4, 0.5], index=index),
            impact_beta=pd.Series([0.1, 0.2, 0.3], index=index),
            flow_direction=pd.Series([1.0, 1.0, 1.0], index=index),
            window=3,
        )
        self.assertAlmostEqual(realized.iloc[-1], 1.1)
        self.assertAlmostEqual(expected.iloc[-1], 1.4)
        self.assertAlmostEqual(surprise.iloc[-1], -0.3)

        realized_short, expected_short, surprise_short = _causal_window_impact_response(
            signed_activity=pd.Series([-1.0, -2.0, -3.0], index=index),
            normalized_price_change=pd.Series([-0.2, -0.4, -0.5], index=index),
            impact_beta=pd.Series([0.1, 0.2, 0.3], index=index),
            flow_direction=pd.Series([-1.0, -1.0, -1.0], index=index),
            window=3,
        )
        self.assertAlmostEqual(realized_short.iloc[-1], 1.1)
        self.assertAlmostEqual(expected_short.iloc[-1], 1.4)
        self.assertAlmostEqual(surprise_short.iloc[-1], -0.3)

    def test_endpoint_beta_reprices_only_its_own_completed_bucket(self) -> None:
        index = pd.RangeIndex(3)
        common = {
            "signed_activity": pd.Series([1.0, 2.0, 3.0], index=index),
            "normalized_price_change": pd.Series([0.2, 0.4, 0.5], index=index),
            "flow_direction": pd.Series([1.0, 1.0, 1.0], index=index),
            "window": 3,
        }
        _, first_expected, _ = _causal_window_impact_response(
            impact_beta=pd.Series([0.1, 0.2, 0.3], index=index),
            **common,
        )
        _, second_expected, _ = _causal_window_impact_response(
            impact_beta=pd.Series([0.1, 0.2, 0.5], index=index),
            **common,
        )
        self.assertAlmostEqual(
            second_expected.iloc[-1] - first_expected.iloc[-1],
            (0.5 - 0.3) * 3.0,
        )

    def test_future_impact_rows_do_not_change_completed_window_response(self) -> None:
        initial_index = pd.RangeIndex(3)
        first = _causal_window_impact_response(
            signed_activity=pd.Series([1.0, 2.0, 3.0], index=initial_index),
            normalized_price_change=pd.Series([0.2, 0.4, 0.5], index=initial_index),
            impact_beta=pd.Series([0.1, 0.2, 0.3], index=initial_index),
            flow_direction=pd.Series([1.0, 1.0, 1.0], index=initial_index),
            window=3,
        )
        extended_index = pd.RangeIndex(5)
        second = _causal_window_impact_response(
            signed_activity=pd.Series([1.0, 2.0, 3.0, -1e9, 1e9], index=extended_index),
            normalized_price_change=pd.Series([0.2, 0.4, 0.5, -1e6, 1e6], index=extended_index),
            impact_beta=pd.Series([0.1, 0.2, 0.3, 100.0, 100.0], index=extended_index),
            flow_direction=pd.Series([1.0, 1.0, 1.0, -1.0, 1.0], index=extended_index),
            window=3,
        )
        for first_series, second_series in zip(first, second):
            self.assertEqual(first_series.iloc[2], second_series.iloc[2])

    def test_flow_direction_and_window_response_use_only_completed_current_and_past(self) -> None:
        data = _history(130)
        for position in range(127, 130):
            previous = float(data.iloc[position - 1]["close"])
            data.iloc[position, data.columns.get_loc("open")] = previous
            data.iloc[position, data.columns.get_loc("close")] = previous + 0.40
            data.iloc[position, data.columns.get_loc("high")] = previous + 0.45
            data.iloc[position, data.columns.get_loc("low")] = previous - 0.02
            data.iloc[position, data.columns.get_loc("signed_volume")] = 90.0
        features = causal_flow_response_frame(
            data,
            tick=0.01,
            config=self.config,
        )
        last = features.iloc[-1]
        self.assertEqual(last["flow_direction"], 1.0)
        self.assertGreater(last["flow_consistency"], 0.99)
        self.assertGreater(last["window_pressure_ratio"], 1.0)
        self.assertGreater(last["directional_progress"], 1.0)
        self.assertGreaterEqual(last["retention"], 0.5)
        self.assertTrue(np.isfinite(last["directional_normalized_progress"]))
        self.assertTrue(np.isfinite(last["expected_response"]))
        self.assertAlmostEqual(
            last["response_surprise"],
            last["directional_normalized_progress"] - last["expected_response"],
        )

    def test_vectorized_and_scalar_states_match_every_completed_feature_row(self) -> None:
        data = _history(170)
        for position in range(150, 170):
            direction = 1.0 if position % 7 < 4 else -1.0
            previous = float(data.iloc[position - 1]["close"])
            close = previous + direction * (0.15 + 0.04 * (position % 3))
            data.iloc[position, data.columns.get_loc("open")] = previous
            data.iloc[position, data.columns.get_loc("close")] = close
            data.iloc[position, data.columns.get_loc("high")] = max(previous, close) + 0.08
            data.iloc[position, data.columns.get_loc("low")] = min(previous, close) - 0.08
            data.iloc[position, data.columns.get_loc("signed_volume")] = direction * (
                40.0 + 10.0 * (position % 5)
            )

        features = causal_flow_response_frame(
            data,
            tick=0.01,
            config=self.config,
        )
        scalar = pd.Series(
            [
                classify_flow_response(row, config=self.config).value
                for _, row in features.iterrows()
            ],
            index=features.index,
            dtype="string",
        )
        pd.testing.assert_series_equal(
            features["flow_response_state"],
            scalar,
            check_names=False,
        )

    def test_input_contract_rejects_missing_or_noncausal_index(self) -> None:
        data = _history(50)
        with self.assertRaises(KeyError):
            causal_flow_response_frame(
                data.drop(columns=["signed_volume"]),
                tick=0.01,
                config=self.config,
            )
        naive = data.copy()
        naive.index = naive.index.tz_localize(None)
        with self.assertRaises(TypeError):
            causal_flow_response_frame(naive, tick=0.01, config=self.config)
        duplicated = pd.concat([data, data.iloc[[-1]]])
        with self.assertRaises(ValueError):
            causal_flow_response_frame(duplicated, tick=0.01, config=self.config)

    def test_source_contains_no_outcome_or_trade_execution_proxy(self) -> None:
        source = (Path(__file__).resolve().parent / "aggtrade_flow_response.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "realized_pnl",
            "future_high",
            "future_low",
            "target_hit",
            "stop_hit",
            "win_rate",
            "order_factory",
            "submit_order",
        ):
            self.assertNotIn(forbidden, source)


class FlowResponseStateContracts(unittest.TestCase):
    def test_initiative_requires_tail_pressure_progress_retention_and_positive_surprise(self) -> None:
        self.assertEqual(
            classify_flow_response(_feature_row()),
            FlowResponseState.INITIATIVE_RESPONSE,
        )
        self.assertEqual(
            classify_flow_response(_feature_row(response_surprise=-0.01)),
            FlowResponseState.BALANCED_OR_UNRESOLVED,
        )
        self.assertEqual(
            classify_flow_response(_feature_row(retention=0.49)),
            FlowResponseState.BALANCED_OR_UNRESOLVED,
        )

    def test_absorption_requires_tail_pressure_excursion_giveback_and_negative_surprise(self) -> None:
        row = _feature_row(
            progress_noise=0.1,
            excursion_noise=0.8,
            retention=0.2,
            response_surprise=-0.7,
        )
        self.assertEqual(
            classify_flow_response(row),
            FlowResponseState.ABSORBED_RESPONSE,
        )
        self.assertEqual(
            classify_flow_response({**row, "excursion_noise": 0.49}),
            FlowResponseState.BALANCED_OR_UNRESOLVED,
        )
        self.assertEqual(
            classify_flow_response({**row, "response_surprise": 0.0}),
            FlowResponseState.BALANCED_OR_UNRESOLVED,
        )

    def test_vectorized_boundaries_exactly_match_scalar_boundaries(self) -> None:
        rows = pd.DataFrame(
            [
                _feature_row(),
                _feature_row(progress_noise=0.1, excursion_noise=0.8, retention=0.2, response_surprise=-0.7),
                _feature_row(window_pressure_ratio=0.99),
                _feature_row(causal_impact_beta=float("nan")),
                _feature_row(progress_noise=1.0, retention=0.5, response_surprise=0.0),
                _feature_row(progress_noise=0.5, excursion_noise=0.5, retention=0.5, response_surprise=-0.1),
            ],
            index=pd.RangeIndex(6),
        )
        vectorized = _classify_frame_states(rows, config=FlowResponseConfig())
        scalar = pd.Series(
            [classify_flow_response(row).value for _, row in rows.iterrows()],
            index=rows.index,
            dtype="string",
        )
        pd.testing.assert_series_equal(vectorized, scalar, check_names=False)

    def test_ordinary_pressure_is_unresolved_not_forced_into_a_trade_state(self) -> None:
        self.assertEqual(
            classify_flow_response(_feature_row(window_pressure_ratio=0.99)),
            FlowResponseState.BALANCED_OR_UNRESOLVED,
        )
        self.assertEqual(
            classify_flow_response(_feature_row(flow_consistency=0.5)),
            FlowResponseState.BALANCED_OR_UNRESOLVED,
        )

    def test_missing_nonfinite_or_invalid_impact_state_is_unobservable(self) -> None:
        self.assertEqual(
            classify_flow_response({}),
            FlowResponseState.UNOBSERVABLE,
        )
        self.assertEqual(
            classify_flow_response(_feature_row(causal_impact_beta=float("nan"))),
            FlowResponseState.UNOBSERVABLE,
        )
        self.assertEqual(
            classify_flow_response(_feature_row(causal_impact_beta=-0.1)),
            FlowResponseState.UNOBSERVABLE,
        )
        self.assertEqual(
            classify_flow_response(_feature_row(flow_direction=0.0)),
            FlowResponseState.UNOBSERVABLE,
        )

    def test_configuration_regions_cannot_overlap(self) -> None:
        with self.assertRaises(ValueError):
            FlowResponseConfig(
                initiative_progress_noise=0.5,
                absorption_maximum_progress_noise=0.5,
            ).validate()
        with self.assertRaises(ValueError):
            FlowResponseConfig(minimum_history_bars=1).validate()
        with self.assertRaises(ValueError):
            FlowResponseConfig(pressure_quantile=1.0).validate()


if __name__ == "__main__":
    unittest.main(verbosity=2)

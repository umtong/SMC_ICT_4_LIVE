"""Causal contracts for the candidate-08 intrinsic response-clock foundation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from aggtrade_flow_response import FlowResponseConfig, FlowResponseState
from aggtrade_intrinsic_response_clock import (
    IntrinsicEventStatus,
    IntrinsicResponseClockConfig,
    build_intrinsic_response_event,
    causal_activity_budget_series,
)


def _raw(closes: list[float], *, highs: list[float] | None = None, lows: list[float] | None = None):
    rows = len(closes)
    index = pd.date_range("2024-01-01T00:00:10Z", periods=rows, freq="10s")
    opens = [closes[0], *closes[:-1]]
    highs = highs or [max(open_, close) + 0.1 for open_, close in zip(opens, closes)]
    lows = lows or [min(open_, close) - 0.1 for open_, close in zip(opens, closes)]
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": np.full(rows, 100.0),
            "signed_volume": np.zeros(rows),
            "trade_count": np.full(rows, 100.0),
        },
        index=index,
    )


def _features(
    index: pd.DatetimeIndex,
    signed_activity: list[float],
    *,
    noise: float = 0.5,
    beta: float = 0.1,
):
    return pd.DataFrame(
        {
            "signed_activity": signed_activity,
            "causal_noise_reserve": np.full(len(index), noise),
            "causal_impact_beta": np.full(len(index), beta),
        },
        index=index,
    )


def _budgets(index: pd.DatetimeIndex, values: list[float] | float):
    if isinstance(values, list):
        data = values
    else:
        data = [float(values)] * len(index)
    return pd.Series(data, index=index, dtype="float64")


class IntrinsicActivityBudgetContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.config = IntrinsicResponseClockConfig(
            response=FlowResponseConfig(
                impact_lookback_bars=12,
                minimum_history_bars=4,
                response_window_bars=3,
            ),
            maximum_event_bars=6,
        )

    def test_current_activity_cannot_change_its_own_causal_budget(self) -> None:
        index = pd.date_range("2024-01-01T00:00:10Z", periods=30, freq="10s")
        ordinary = pd.DataFrame(
            {"signed_activity": np.tile([0.5, -0.4, 0.6], 10)},
            index=index,
        )
        extreme = ordinary.copy()
        extreme.iloc[-1, 0] = 1e12
        first = causal_activity_budget_series(ordinary, config=self.config)
        second = causal_activity_budget_series(extreme, config=self.config)
        self.assertEqual(first.iloc[-1], second.iloc[-1])

    def test_future_rows_do_not_change_an_already_frozen_budget(self) -> None:
        index = pd.date_range("2024-01-01T00:00:10Z", periods=30, freq="10s")
        initial = pd.DataFrame(
            {"signed_activity": np.tile([0.5, -0.4, 0.6], 10)},
            index=index,
        )
        first = causal_activity_budget_series(initial, config=self.config)
        future_index = pd.date_range(index[-1] + pd.Timedelta(seconds=10), periods=5, freq="10s")
        future = pd.DataFrame(
            {"signed_activity": [1e9, -1e9, 2e9, -2e9, 3e9]},
            index=future_index,
        )
        second = causal_activity_budget_series(
            pd.concat([initial, future]),
            config=self.config,
        )
        pd.testing.assert_series_equal(first, second.loc[index])


class IntrinsicResponseEventContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.config = IntrinsicResponseClockConfig(
            response=replace(
                FlowResponseConfig(),
                impact_lookback_bars=20,
                minimum_history_bars=5,
                response_window_bars=3,
            ),
            maximum_event_bars=6,
        )

    def test_high_activity_completes_the_same_budget_in_less_physical_time(self) -> None:
        data = _raw([100.0, 100.5, 101.3, 101.6, 101.9, 102.2, 102.5])
        fast = _features(data.index, [0.0, 1.5, 1.5, 0.1, 0.1, 0.1, 0.1])
        slow = _features(data.index, [0.0, 0.75, 0.75, 0.75, 0.75, 0.1, 0.1])
        budget = _budgets(data.index, 3.0)

        fast_event = build_intrinsic_response_event(
            data,
            start_position=1,
            tick=0.1,
            config=self.config,
            flow_response_features=fast,
            activity_budgets=budget,
        )
        slow_event = build_intrinsic_response_event(
            data,
            start_position=1,
            tick=0.1,
            config=self.config,
            flow_response_features=slow,
            activity_budgets=budget,
        )

        self.assertEqual(fast_event.status, IntrinsicEventStatus.COMPLETE)
        self.assertEqual(slow_event.status, IntrinsicEventStatus.COMPLETE)
        self.assertEqual(fast_event.physical_bars, 2)
        self.assertEqual(slow_event.physical_bars, 4)
        self.assertEqual(fast_event.frozen_activity_budget, slow_event.frozen_activity_budget)

    def test_completed_retained_progress_is_initiative_response(self) -> None:
        data = _raw(
            [100.0, 100.8, 101.3, 101.4],
            highs=[100.1, 100.9, 101.4, 101.5],
            lows=[99.9, 99.9, 100.7, 101.2],
        )
        features = _features(data.index, [0.0, 1.5, 1.5, 0.1])
        event = build_intrinsic_response_event(
            data,
            start_position=1,
            tick=0.1,
            config=self.config,
            flow_response_features=features,
            activity_budgets=_budgets(data.index, 3.0),
        )

        self.assertEqual(event.status, IntrinsicEventStatus.COMPLETE)
        self.assertEqual(event.response_state, FlowResponseState.INITIATIVE_RESPONSE)
        self.assertEqual(event.end_position, 2)
        self.assertEqual(event.flow_direction, 1)
        self.assertGreaterEqual(event.flow_consistency, 0.99)
        self.assertGreaterEqual(event.progress_noise, 1.0)
        self.assertGreaterEqual(event.retention, 0.5)
        self.assertGreaterEqual(event.response_surprise, 0.0)

    def test_completed_excursion_giveback_is_absorbed_response(self) -> None:
        data = _raw(
            [100.0, 100.3, 100.2, 100.05, 100.0],
            highs=[100.1, 100.5, 100.45, 100.3, 100.2],
            lows=[99.9, 99.9, 100.0, 99.95, 99.9],
        )
        features = _features(data.index, [0.0, 1.0, 1.0, 1.0, 0.1])
        event = build_intrinsic_response_event(
            data,
            start_position=1,
            tick=0.1,
            config=self.config,
            flow_response_features=features,
            activity_budgets=_budgets(data.index, 3.0),
        )

        self.assertEqual(event.status, IntrinsicEventStatus.COMPLETE)
        self.assertEqual(event.response_state, FlowResponseState.ABSORBED_RESPONSE)
        self.assertGreaterEqual(event.excursion_noise, 0.5)
        self.assertLess(event.progress_noise, 0.5)
        self.assertLess(event.retention, 0.5)
        self.assertLess(event.response_surprise, 0.0)

    def test_budget_is_frozen_at_start_and_later_threshold_changes_have_no_effect(self) -> None:
        data = _raw([100.0, 100.4, 100.8, 101.2, 101.4, 101.5])
        features = _features(data.index, [0.0, 1.0, 1.0, 1.0, 0.1, 0.1])
        first_budget = _budgets(data.index, [99.0, 3.0, 1000.0, 1000.0, 1000.0, 1000.0])
        second_budget = _budgets(data.index, [99.0, 3.0, 0.01, 0.01, 0.01, 0.01])

        first = build_intrinsic_response_event(
            data,
            start_position=1,
            tick=0.1,
            config=self.config,
            flow_response_features=features,
            activity_budgets=first_budget,
        )
        second = build_intrinsic_response_event(
            data,
            start_position=1,
            tick=0.1,
            config=self.config,
            flow_response_features=features,
            activity_budgets=second_budget,
        )
        self.assertEqual(first, second)
        self.assertEqual(first.end_position, 3)

    def test_timeout_is_not_relabelled_as_a_tradeable_response(self) -> None:
        config = replace(self.config, maximum_event_bars=3)
        data = _raw([100.0, 100.1, 100.2, 100.3, 100.4])
        features = _features(data.index, [0.0, 0.4, 0.4, 0.4, 0.4])
        event = build_intrinsic_response_event(
            data,
            start_position=1,
            tick=0.1,
            config=config,
            flow_response_features=features,
            activity_budgets=_budgets(data.index, 5.0),
        )
        self.assertEqual(event.status, IntrinsicEventStatus.TIMEOUT)
        self.assertEqual(event.response_state, FlowResponseState.BALANCED_OR_UNRESOLVED)
        self.assertLess(event.activity_budget_fraction, 1.0)

    def test_future_rows_do_not_change_a_completed_intrinsic_event(self) -> None:
        initial = _raw([100.0, 100.8, 101.3, 101.4])
        features = _features(initial.index, [0.0, 1.5, 1.5, 0.1])
        budget = _budgets(initial.index, 3.0)
        first = build_intrinsic_response_event(
            initial,
            start_position=1,
            tick=0.1,
            config=self.config,
            flow_response_features=features,
            activity_budgets=budget,
        )

        extended = _raw([100.0, 100.8, 101.3, 101.4, 80.0, 130.0])
        extended_features = _features(
            extended.index,
            [0.0, 1.5, 1.5, 0.1, -1e9, 1e9],
        )
        second = build_intrinsic_response_event(
            extended,
            start_position=1,
            tick=0.1,
            config=self.config,
            flow_response_features=extended_features,
            activity_budgets=_budgets(extended.index, 3.0),
        )
        self.assertEqual(first, second)

    def test_nonpositive_causal_impact_is_unobservable_not_balanced(self) -> None:
        data = _raw([100.0, 100.5, 101.0])
        features = _features(data.index, [0.0, 1.5, 1.5], beta=0.0)
        event = build_intrinsic_response_event(
            data,
            start_position=1,
            tick=0.1,
            config=self.config,
            flow_response_features=features,
            activity_budgets=_budgets(data.index, 3.0),
        )
        self.assertEqual(event.status, IntrinsicEventStatus.UNOBSERVABLE)
        self.assertEqual(event.response_state, FlowResponseState.UNOBSERVABLE)

    def test_source_has_no_strategy_execution_or_outcome_proxy(self) -> None:
        source = (
            Path(__file__).resolve().parent / "aggtrade_intrinsic_response_clock.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "realized_pnl",
            "future_high",
            "future_low",
            "target_hit",
            "stop_hit",
            "win_rate",
            "order_factory",
            "submit_order",
            "risk_multiplier",
            "model_score",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

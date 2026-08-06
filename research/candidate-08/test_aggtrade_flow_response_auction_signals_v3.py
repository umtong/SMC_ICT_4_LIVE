"""Exact cadence and revision contracts for flow-response auction V3."""

from __future__ import annotations

import unittest

import pandas as pd

from aggtrade_flow_response import FlowResponseState
from aggtrade_flow_response_auction_signals_v3 import (
    IMPLEMENTATION_REVISION,
    TEN_SECOND_NS,
    build_flow_response_auction_signals,
    validate_exact_ten_second_cadence,
)
from test_aggtrade_flow_response_auction_signals import (
    _bar,
    _feature,
    _inputs,
    _signals,
)


class ExactTenSecondCadenceContracts(unittest.TestCase):
    def test_exact_consecutive_series_is_accepted(self) -> None:
        index = pd.date_range("2024-01-01T00:00:10Z", periods=5, freq="10s")
        frame = pd.DataFrame({"close": range(5)}, index=index)
        validate_exact_ten_second_cadence(frame)
        deltas = frame.index.as_unit("ns").asi8[1:] - frame.index.as_unit("ns").asi8[:-1]
        self.assertTrue((deltas == TEN_SECOND_NS).all())

    def test_missing_or_jittered_bucket_is_rejected(self) -> None:
        missing = pd.to_datetime(
            [
                "2024-01-01T00:00:10Z",
                "2024-01-01T00:00:20Z",
                "2024-01-01T00:00:40Z",
            ],
            utc=True,
        )
        with self.assertRaisesRegex(ValueError, "not an exact consecutive ten-second series"):
            validate_exact_ten_second_cadence(pd.DataFrame({"close": [1, 2, 3]}, index=missing))

        jittered = pd.to_datetime(
            [
                "2024-01-01T00:00:10Z",
                "2024-01-01T00:00:20Z",
                "2024-01-01T00:00:29Z",
            ],
            utc=True,
        )
        with self.assertRaisesRegex(ValueError, "delta_ns=9000000000"):
            validate_exact_ten_second_cadence(pd.DataFrame({"close": [1, 2, 3]}, index=jittered))

    def test_duplicate_naive_and_too_short_inputs_are_rejected(self) -> None:
        one = pd.DataFrame(
            {"close": [1]},
            index=pd.date_range("2024-01-01", periods=1, freq="10s", tz="UTC"),
        )
        with self.assertRaises(ValueError):
            validate_exact_ten_second_cadence(one)
        naive = one.copy()
        naive.index = naive.index.tz_localize(None)
        with self.assertRaises(TypeError):
            validate_exact_ten_second_cadence(naive)
        duplicated = pd.concat([one, one])
        with self.assertRaises(ValueError):
            validate_exact_ten_second_cadence(duplicated)


class V3RevisionStampingContracts(unittest.TestCase):
    def test_every_signal_event_and_rejection_is_stamped_v3(self) -> None:
        bars = [
            _bar(open_=99.4, high=99.6, low=99.3, close=99.5),
            _bar(open_=99.5, high=100.3, low=99.5, close=100.1),
            _bar(open_=100.1, high=100.7, low=100.0, close=100.5),
            _bar(open_=100.5, high=101.2, low=100.4, close=101.0),
            _bar(open_=101.0, high=101.4, low=100.9, close=101.2),
        ]
        features = [_feature() for _ in bars]
        features[4] = _feature(state=FlowResponseState.INITIATIVE_RESPONSE)
        bundle = build_flow_response_auction_signals(**_inputs(bars, features))
        signal = _signals(bundle)[0]

        self.assertEqual(signal.details["implementation_revision"], IMPLEMENTATION_REVISION)
        self.assertEqual(
            signal.details["ten_second_cadence_contract"],
            "EXACT_CONSECUTIVE_10_SECONDS",
        )
        self.assertTrue(
            all(
                event.details["implementation_revision"] == IMPLEMENTATION_REVISION
                for event in signal.events
            )
        )
        self.assertEqual(
            bundle.diagnostics["EXACT_TEN_SECOND_CADENCE_VERIFIED"],
            len(bars),
        )
        self.assertTrue(
            all(
                value["implementation_revision"] == IMPLEMENTATION_REVISION
                for value in bundle.rejected_scenarios
            )
        )

    def test_gap_fails_before_any_scenario_can_be_evaluated(self) -> None:
        bars = [
            _bar(open_=99.4, high=99.6, low=99.3, close=99.5),
            _bar(open_=99.5, high=100.3, low=99.5, close=100.1),
            _bar(open_=100.1, high=100.7, low=100.0, close=100.5),
        ]
        inputs = _inputs(bars, [_feature() for _ in bars])
        index = inputs["data"].index.to_list()
        index[-1] = index[-1] + pd.Timedelta(seconds=10)
        inputs["data"].index = pd.DatetimeIndex(index)
        inputs["flow_response_features"].index = pd.DatetimeIndex(index)
        with self.assertRaisesRegex(ValueError, "exact consecutive ten-second"):
            build_flow_response_auction_signals(**inputs)


if __name__ == "__main__":
    unittest.main(verbosity=2)

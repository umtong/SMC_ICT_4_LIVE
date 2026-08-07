"""Causal contracts for delayed boundary reacceptance V3."""

from __future__ import annotations

from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from aggtrade_delayed_reacceptance_signals_v3 import (
    ABLATION_INITIAL_MODE,
    BASE_INITIAL_MODE,
    IMPLEMENTATION_REVISION,
    REACCEPTANCE_FAMILY,
    build_delayed_reacceptance_signals,
)
from aggtrade_flow_response import FlowResponseState
from range_fvg_logic import ExternalLevel, FiveMinuteBar, LevelKind, LevelSource


def _level(level_id: str, kind: LevelKind, value: float) -> ExternalLevel:
    return ExternalLevel(
        level_id=level_id,
        kind=kind,
        source=LevelSource.FOUR_HOUR,
        level=value,
        formed_index=0,
        formed_time_ns=1,
        period_key="p0",
    )


def _context_bar(ts_event_ns: int) -> FiveMinuteBar:
    return FiveMinuteBar(
        index=0,
        ts_event_ns=ts_event_ns,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1000.0,
        trade_count=1000.0,
        taker_buy_volume=500.0,
        imbalance=0.0,
        atr=1.0,
        volume_ratio=1.0,
        trade_ratio=1.0,
        efficiency_60m=0.1,
        direction_60m=1.0,
        session_key="s0",
        day_key="d0",
        week_key="w0",
    )


def _bar(*, open_: float, high: float, low: float, close: float) -> dict[str, float]:
    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100.0,
        "signed_volume": 20.0,
        "trade_count": 100.0,
    }


def _feature(
    *,
    state: FlowResponseState = FlowResponseState.BALANCED_OR_UNRESOLVED,
    direction: int = 1,
    noise: float = 0.2,
) -> dict[str, float | str]:
    initiative = state is FlowResponseState.INITIATIVE_RESPONSE
    return {
        "flow_response_state": state.value,
        "flow_direction": float(direction),
        "flow_consistency": 0.9,
        "window_pressure_ratio": 1.3 if initiative else 0.8,
        "progress_noise": 1.4 if initiative else 0.2,
        "excursion_noise": 1.6 if initiative else 0.4,
        "retention": 0.8 if initiative else 0.5,
        "response_surprise": 0.3 if initiative else 0.0,
        "causal_noise_reserve": noise,
    }


def _inputs(
    bars: list[dict[str, float]],
    features: list[dict[str, float | str]],
    *,
    target: float = 110.0,
):
    index = pd.date_range("2024-01-01T00:00:10Z", periods=len(bars), freq="10s")
    data = pd.DataFrame(bars, index=index)
    response = pd.DataFrame(features, index=index)
    before = int((index[0] - pd.Timedelta(seconds=10)).as_unit("ns").value)
    after = int((index[-1] + pd.Timedelta(minutes=5)).as_unit("ns").value)
    levels = (
        _level("completed-high-boundary", LevelKind.HIGH, 100.0),
        _level("completed-high-target", LevelKind.HIGH, target),
        _level("completed-low-target", LevelKind.LOW, 90.0),
    )
    return {
        "data": data,
        "context_times": np.asarray([before, after], dtype=np.int64),
        "context_bars": (_context_bar(before), _context_bar(after)),
        "snapshots": (levels, levels),
        "symbol": "BTCUSDT",
        "instrument_id": "BTCUSDT-PERP.BINANCE",
        "tick": 0.1,
        "fee_rate": 0.0006,
        "minimum_net_reward_risk": 1.2,
        "flow_response_features": response,
    }


def _base_bars() -> list[dict[str, float]]:
    return [
        _bar(open_=99.4, high=99.6, low=99.3, close=99.5),
        _bar(open_=99.5, high=100.3, low=99.5, close=100.1),
        _bar(open_=100.1, high=100.6, low=100.0, close=100.4),
        _bar(open_=100.4, high=100.9, low=100.3, close=100.8),
        _bar(open_=100.8, high=101.2, low=100.7, close=101.1),
        _bar(open_=101.1, high=101.2, low=99.6, close=99.8),
        _bar(open_=99.8, high=100.4, low=99.7, close=100.2),
        _bar(open_=100.2, high=100.9, low=100.1, close=100.7),
        _bar(open_=100.7, high=101.5, low=100.6, close=101.4),
    ]


def _base_features(*, initial_state: FlowResponseState = FlowResponseState.INITIATIVE_RESPONSE):
    values = [_feature() for _ in _base_bars()]
    values[4] = _feature(state=initial_state, direction=1)
    values[8] = _feature(state=FlowResponseState.INITIATIVE_RESPONSE, direction=1)
    return values


def _signals(bundle):
    return [signal for values in bundle.signals_by_time_ns.values() for signal in values]


class DelayedReacceptanceSequenceContracts(unittest.TestCase):
    def test_trade_requires_initial_response_reclaim_and_separate_reacceptance(self) -> None:
        inputs = _inputs(_base_bars(), _base_features())
        signal = _signals(build_delayed_reacceptance_signals(**inputs))[0]

        self.assertEqual(signal.signal_index, 8)
        self.assertEqual(signal.details["scenario_family"], REACCEPTANCE_FAMILY)
        self.assertEqual(signal.details["implementation_revision"], IMPLEMENTATION_REVISION)
        self.assertEqual(signal.details["initial_mode"], BASE_INITIAL_MODE)
        self.assertEqual(signal.details["initial_response_time_ns"], int(inputs["data"].index[4].value))
        self.assertEqual(signal.details["reclaim_time_ns"], int(inputs["data"].index[5].value))
        self.assertEqual(signal.retest_high, 101.2)
        self.assertEqual(signal.retest_low, 99.6)
        self.assertLess(signal.structural_stop, 99.6)
        self.assertEqual(signal.target_id, "completed-high-target")
        self.assertGreaterEqual(signal.net_reward_risk, 1.2)
        self.assertEqual(
            [event.event_type for event in signal.events],
            [
                "EXTERNAL_LIQUIDITY_INTERACTION_ARMED",
                "INITIAL_OUTWARD_RESPONSE_CONFIRMED_NO_ENTRY",
                "INITIAL_RESPONSE_RECLAIMED",
                "DELAYED_OUTWARD_REACCEPTANCE_CONFIRMED",
            ],
        )
        self.assertEqual(
            [(event.previous_state, event.next_state) for event in signal.events],
            [
                ("IDLE", "INTERACTION_ARMED"),
                ("INTERACTION_ARMED", "INITIAL_OUTWARD_RESPONSE"),
                ("INITIAL_OUTWARD_RESPONSE", "BOUNDARY_RECLAIMED"),
                ("BOUNDARY_RECLAIMED", "CONFIRMED"),
            ],
        )
        self.assertEqual(
            {event.details["implementation_revision"] for event in signal.events},
            {IMPLEMENTATION_REVISION},
        )
        event_times = [event.event_time_ns for event in signal.events]
        self.assertEqual(event_times, sorted(event_times))
        self.assertEqual(
            signal.details["event_chain_contract"],
            "IDLE->INTERACTION_ARMED->INITIAL_OUTWARD_RESPONSE"
            "->BOUNDARY_RECLAIMED->CONFIRMED",
        )

    def test_reacceptance_before_a_full_post_reclaim_window_cannot_trade(self) -> None:
        bars = _base_bars()[:8]
        features = _base_features()[:8]
        features[6] = _feature(state=FlowResponseState.INITIATIVE_RESPONSE)
        features[7] = _feature(state=FlowResponseState.INITIATIVE_RESPONSE)
        bundle = build_delayed_reacceptance_signals(**_inputs(bars, features))
        self.assertEqual(_signals(bundle), [])

    def test_target_reached_before_reacceptance_rejects_the_setup(self) -> None:
        bars = _base_bars()
        bars[6] = _bar(open_=99.8, high=101.6, low=99.7, close=100.2)
        bundle = build_delayed_reacceptance_signals(
            **_inputs(bars, _base_features(), target=101.5)
        )
        self.assertEqual(_signals(bundle), [])
        self.assertEqual(bundle.diagnostics["TARGET_REACHED_BEFORE_REACCEPTANCE"], 1)

    def test_initial_initiative_ablation_changes_only_the_initial_state_gate(self) -> None:
        bars = _base_bars()
        features = _base_features(initial_state=FlowResponseState.BALANCED_OR_UNRESOLVED)
        base_bundle = build_delayed_reacceptance_signals(
            **_inputs(bars, features),
            initial_mode=BASE_INITIAL_MODE,
        )
        diagnostic = build_delayed_reacceptance_signals(
            **_inputs(bars, features),
            initial_mode=ABLATION_INITIAL_MODE,
        )

        self.assertEqual(_signals(base_bundle), [])
        diagnostic_signal = _signals(diagnostic)[0]
        self.assertEqual(diagnostic_signal.details["initial_mode"], ABLATION_INITIAL_MODE)
        self.assertEqual(diagnostic_signal.signal_index, 8)
        self.assertEqual(
            diagnostic_signal.structural_stop,
            _signals(
                build_delayed_reacceptance_signals(
                    **_inputs(bars, _base_features()),
                    initial_mode=BASE_INITIAL_MODE,
                )
            )[0].structural_stop,
        )

    def test_unobservable_warmup_rows_are_ignored_without_exception(self) -> None:
        features = _base_features()
        features[4] = {
            **features[4],
            "flow_response_state": FlowResponseState.UNOBSERVABLE.value,
            "flow_direction": float("nan"),
            "causal_noise_reserve": float("nan"),
        }
        bundle = build_delayed_reacceptance_signals(
            **_inputs(_base_bars(), features)
        )
        self.assertEqual(_signals(bundle), [])

    def test_future_rows_do_not_change_an_emitted_signal(self) -> None:
        bars = _base_bars()
        features = _base_features()
        first = build_delayed_reacceptance_signals(**_inputs(bars, features))
        signal = _signals(first)[0]
        extended_bars = bars + [
            _bar(open_=101.4, high=140.0, low=70.0, close=80.0),
            _bar(open_=80.0, high=160.0, low=60.0, close=150.0),
        ]
        extended_features = features + [
            _feature(state=FlowResponseState.INITIATIVE_RESPONSE, direction=-1),
            _feature(state=FlowResponseState.INITIATIVE_RESPONSE, direction=1),
        ]
        second = build_delayed_reacceptance_signals(
            **_inputs(extended_bars, extended_features)
        )
        self.assertEqual(signal, second.signals_by_time_ns[signal.signal_time_ns][0])

    def test_exact_ten_second_cadence_is_mandatory(self) -> None:
        inputs = _inputs(_base_bars(), _base_features())
        broken = inputs["data"].copy()
        broken.index = broken.index.to_list()[:4] + [
            broken.index[4] + pd.Timedelta(seconds=1),
            *broken.index.to_list()[5:],
        ]
        shifted_features = inputs["flow_response_features"].copy()
        shifted_features.index = broken.index
        with self.assertRaises(ValueError):
            build_delayed_reacceptance_signals(
                **{
                    **inputs,
                    "data": broken,
                    "flow_response_features": shifted_features,
                }
            )

    def test_source_contains_no_outcome_model_or_execution_engine(self) -> None:
        source = (
            Path(__file__).resolve().parent
            / "aggtrade_delayed_reacceptance_signals.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "realized_pnl",
            "future_high",
            "future_low",
            "win_rate",
            "model_score",
            "risk_multiplier",
            "BacktestEngine(",
            "order_factory",
            "submit_order",
            "fixed_r",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Causal contracts for the candidate-08 intrinsic repricing successor."""

from __future__ import annotations

from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from aggtrade_intrinsic_repricing_signals import (
    DIRECT_PERSISTENCE_PATH,
    IMPLEMENTATION_REVISION,
    INTRINSIC_REPRICING_FAMILY,
    REPRICE_RESUMPTION_PATH,
    build_intrinsic_repricing_signals,
)
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


def _bar(
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    signed: float,
) -> dict[str, float]:
    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100.0,
        "signed_volume": signed * 100.0,
        "trade_count": 100.0,
    }


def _inputs(bars: list[dict[str, float]]):
    index = pd.date_range("2024-01-01T00:00:10Z", periods=len(bars), freq="10s")
    data = pd.DataFrame(bars, index=index)
    features = pd.DataFrame(
        {
            "signed_activity": [row["signed_volume"] / 100.0 for row in bars],
            "causal_noise_reserve": np.full(len(bars), 0.5),
            "causal_impact_beta": np.full(len(bars), 0.1),
        },
        index=index,
    )
    budgets = pd.Series(np.full(len(bars), 3.0), index=index, dtype="float64")
    before = int((index[0] - pd.Timedelta(seconds=10)).as_unit("ns").value)
    after = int((index[-1] + pd.Timedelta(minutes=5)).as_unit("ns").value)
    levels = (
        _level("completed-high-boundary", LevelKind.HIGH, 100.0),
        _level("completed-high-target", LevelKind.HIGH, 110.0),
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
        "flow_response_features": features,
        "activity_budgets": budgets,
    }


def _signals(bundle):
    return [signal for values in bundle.signals_by_time_ns.values() for signal in values]


class IntrinsicRepricingSequenceContracts(unittest.TestCase):
    def test_direct_path_requires_two_separate_complete_activity_events(self) -> None:
        bars = [
            _bar(open_=99.4, high=99.6, low=99.3, close=99.5, signed=0.1),
            _bar(open_=99.5, high=100.3, low=99.5, close=100.1, signed=0.2),
            _bar(open_=100.1, high=100.6, low=100.0, close=100.5, signed=1.0),
            _bar(open_=100.5, high=101.0, low=100.4, close=100.9, signed=1.0),
            _bar(open_=100.9, high=101.4, low=100.8, close=101.3, signed=1.0),
            _bar(open_=101.3, high=101.7, low=101.2, close=101.6, signed=1.0),
            _bar(open_=101.6, high=102.0, low=101.5, close=101.9, signed=1.0),
            _bar(open_=101.9, high=102.3, low=101.8, close=102.2, signed=1.0),
        ]
        inputs = _inputs(bars)
        signal = _signals(build_intrinsic_repricing_signals(**inputs))[0]

        self.assertEqual(signal.signal_index, 7)
        self.assertEqual(signal.details["scenario_family"], INTRINSIC_REPRICING_FAMILY)
        self.assertEqual(signal.details["entry_path"], DIRECT_PERSISTENCE_PATH)
        self.assertEqual(signal.details["implementation_revision"], IMPLEMENTATION_REVISION)
        self.assertEqual(signal.details["event_a"]["start_position"], 2)
        self.assertEqual(signal.details["event_a"]["end_position"], 4)
        self.assertEqual(signal.details["event_b"]["start_position"], 5)
        self.assertEqual(signal.details["event_b"]["end_position"], 7)
        self.assertGreater(signal.signal_time_ns, signal.events[1].event_time_ns)
        self.assertLess(signal.structural_stop, signal.boundary_level)
        self.assertGreaterEqual(signal.net_reward_risk, 1.2)
        self.assertEqual(
            [event.event_type for event in signal.events],
            [
                "EXTERNAL_LIQUIDITY_INTERACTION_ARMED",
                "INTRINSIC_INITIATIVE_DISPLACEMENT_CONFIRMED",
                "INTRINSIC_REPRICING_CONTINUATION_CONFIRMED",
            ],
        )

    def test_counterflow_must_hold_boundary_before_a_third_resumption_event(self) -> None:
        bars = [
            _bar(open_=99.4, high=99.6, low=99.3, close=99.5, signed=0.1),
            _bar(open_=99.5, high=100.3, low=99.5, close=100.1, signed=0.2),
            _bar(open_=100.1, high=100.6, low=100.0, close=100.5, signed=1.0),
            _bar(open_=100.5, high=101.0, low=100.4, close=100.9, signed=1.0),
            _bar(open_=100.9, high=101.4, low=100.8, close=101.3, signed=1.0),
            _bar(open_=101.3, high=101.4, low=100.8, close=100.9, signed=-1.0),
            _bar(open_=100.9, high=101.0, low=100.0, close=100.4, signed=-1.0),
            _bar(open_=100.4, high=100.5, low=99.95, close=100.2, signed=-1.0),
            _bar(open_=100.2, high=100.8, low=100.1, close=100.7, signed=1.0),
            _bar(open_=100.7, high=101.3, low=100.6, close=101.1, signed=1.0),
            _bar(open_=101.1, high=101.8, low=101.0, close=101.6, signed=1.0),
        ]
        signal = _signals(build_intrinsic_repricing_signals(**_inputs(bars)))[0]

        self.assertEqual(signal.signal_index, 10)
        self.assertEqual(signal.details["entry_path"], REPRICE_RESUMPTION_PATH)
        self.assertEqual(signal.details["event_b"]["flow_direction"], -1)
        self.assertEqual(signal.details["final_event"]["flow_direction"], 1)
        self.assertEqual(signal.retest_low, 99.95)
        self.assertLess(signal.structural_stop, 99.95)

    def test_boundary_reclaim_without_hold_never_becomes_continuation(self) -> None:
        bars = [
            _bar(open_=99.4, high=99.6, low=99.3, close=99.5, signed=0.1),
            _bar(open_=99.5, high=100.3, low=99.5, close=100.1, signed=0.2),
            _bar(open_=100.1, high=100.6, low=100.0, close=100.5, signed=1.0),
            _bar(open_=100.5, high=101.0, low=100.4, close=100.9, signed=1.0),
            _bar(open_=100.9, high=101.4, low=100.8, close=101.3, signed=1.0),
            _bar(open_=101.3, high=101.4, low=100.7, close=100.8, signed=-1.0),
            _bar(open_=100.8, high=100.9, low=99.7, close=99.9, signed=-1.0),
            _bar(open_=99.9, high=100.0, low=99.2, close=99.4, signed=-1.0),
        ]
        bundle = build_intrinsic_repricing_signals(**_inputs(bars))
        self.assertEqual(_signals(bundle), [])
        self.assertEqual(
            bundle.diagnostics["INTRINSIC_PERSISTENCE_OR_REPRICE_NOT_CONFIRMED"],
            1,
        )

    def test_future_rows_do_not_change_an_already_emitted_signal(self) -> None:
        bars = [
            _bar(open_=99.4, high=99.6, low=99.3, close=99.5, signed=0.1),
            _bar(open_=99.5, high=100.3, low=99.5, close=100.1, signed=0.2),
            _bar(open_=100.1, high=100.6, low=100.0, close=100.5, signed=1.0),
            _bar(open_=100.5, high=101.0, low=100.4, close=100.9, signed=1.0),
            _bar(open_=100.9, high=101.4, low=100.8, close=101.3, signed=1.0),
            _bar(open_=101.3, high=101.7, low=101.2, close=101.6, signed=1.0),
            _bar(open_=101.6, high=102.0, low=101.5, close=101.9, signed=1.0),
            _bar(open_=101.9, high=102.3, low=101.8, close=102.2, signed=1.0),
        ]
        first = build_intrinsic_repricing_signals(**_inputs(bars))
        signal = _signals(first)[0]
        future = bars + [
            _bar(open_=102.2, high=140.0, low=70.0, close=80.0, signed=-10.0),
            _bar(open_=80.0, high=160.0, low=60.0, close=150.0, signed=10.0),
        ]
        second = build_intrinsic_repricing_signals(**_inputs(future))
        self.assertEqual(signal, second.signals_by_time_ns[signal.signal_time_ns][0])

    def test_source_contains_no_outcome_model_or_execution_engine(self) -> None:
        source = (Path(__file__).resolve().parent / "aggtrade_intrinsic_repricing_signals.py").read_text(
            encoding="utf-8"
        )
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

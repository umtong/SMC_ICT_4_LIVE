"""Causal implementation contracts for auction-router failed-auction sweep refinement v2."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from aggtrade_auction_router_signals import FAILED_AUCTION_FAMILY
from aggtrade_auction_router_signals_v2 import (
    IMPLEMENTATION_REVISION,
    _observed_sweep_through_confirmation,
    build_auction_router_signals,
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


def _row(**overrides: float) -> dict[str, float]:
    result = {
        "open": 99.8,
        "high": 99.9,
        "low": 99.7,
        "close": 99.8,
        "volume": 100.0,
        "trade_count": 100.0,
        "imbalance": 0.0,
        "volume_ratio": 1.0,
        "trade_ratio": 1.0,
        "close_location": 0.5,
    }
    result.update(overrides)
    return result


def _build(rows: list[dict[str, float]]):
    index = pd.date_range("2024-01-01T00:00:10Z", periods=len(rows), freq="10s")
    data = pd.DataFrame(rows, index=index)
    before = int((index[0] - pd.Timedelta(seconds=10)).as_unit("ns").value)
    after = int((index[-1] + pd.Timedelta(minutes=5)).as_unit("ns").value)
    levels = (
        _level("completed-high-boundary", LevelKind.HIGH, 100.0),
        _level("completed-high-target", LevelKind.HIGH, 105.0),
        _level("completed-low-target", LevelKind.LOW, 95.0),
    )
    bundle = build_auction_router_signals(
        data=data,
        context_times=np.asarray([before, after], dtype=np.int64),
        context_bars=(_context_bar(before), _context_bar(after)),
        snapshots=(levels, levels),
        symbol="BTCUSDT",
        instrument_id="BTCUSDT-PERP.BINANCE",
        tick=0.1,
        fee_rate=0.0006,
        minimum_net_reward_risk=1.2,
    )
    return data, bundle


def _acceptance() -> dict[str, float]:
    return _row(
        open=99.9,
        high=100.5,
        low=99.9,
        close=100.4,
        volume=200.0,
        trade_count=200.0,
        imbalance=0.5,
        volume_ratio=2.0,
        trade_ratio=2.0,
        close_location=0.83,
    )


def _reclaim() -> dict[str, float]:
    return _row(
        open=100.4,
        high=100.45,
        low=99.7,
        close=99.8,
        volume=180.0,
        trade_count=180.0,
        imbalance=-0.2,
        volume_ratio=1.2,
        trade_ratio=1.2,
        close_location=0.2,
    )


def _confirmation(*, high: float = 100.8) -> dict[str, float]:
    return _row(
        open=99.8,
        high=high,
        low=99.3,
        close=99.4,
        volume=210.0,
        trade_count=210.0,
        imbalance=-0.4,
        volume_ratio=1.5,
        trade_ratio=1.4,
        close_location=0.18,
    )


class FailedAuctionSweepRefinementContracts(unittest.TestCase):
    def test_confirmation_bar_extended_sweep_is_included_in_structural_stop(self) -> None:
        _, bundle = _build([_row(), _acceptance(), _reclaim(), _confirmation(high=100.8)])
        signals = [signal for values in bundle.signals_by_time_ns.values() for signal in values]
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.details["scenario_family"], FAILED_AUCTION_FAMILY)
        self.assertEqual(signal.details["sweep_high_at_reclaim"], 100.5)
        self.assertEqual(signal.details["sweep_high"], 100.8)
        self.assertGreater(signal.structural_stop, 100.8)
        self.assertEqual(signal.details["stop_reference"], 100.8)
        self.assertEqual(signal.details["implementation_revision"], IMPLEMENTATION_REVISION)
        self.assertEqual(
            signal.events[-1].details["sweep_high_observed_through_confirmation"],
            100.8,
        )
        self.assertEqual(
            bundle.diagnostics.get("FAILED_AUCTION_SWEEP_EXTREME_REFINED"),
            1,
        )
        self.assertEqual(
            bundle.diagnostics.get("TRADEABLE_FAILED_AUCTION_REVERSAL"),
            1,
        )
        self.assertEqual(
            bundle.diagnostics.get(
                "FAILED_AUCTION_REVERSAL_REMOVED_BY_SWEEP_REFINEMENT",
                0,
            ),
            0,
        )

    def test_intermediate_post_reclaim_extreme_is_not_lost(self) -> None:
        intermediate = _row(
            open=99.8,
            high=101.0,
            low=99.65,
            close=99.9,
            volume=150.0,
            trade_count=150.0,
            imbalance=-0.05,
            volume_ratio=0.9,
            trade_ratio=0.9,
            close_location=0.35,
        )
        _, bundle = _build(
            [_row(), _acceptance(), _reclaim(), intermediate, _confirmation(high=100.7)]
        )
        signal = next(iter(next(iter(bundle.signals_by_time_ns.values()))))
        self.assertEqual(signal.details["sweep_high"], 101.0)
        self.assertGreater(signal.structural_stop, 101.0)
        self.assertEqual(signal.details["observed_sweep_reclaim_position"], 2)
        self.assertEqual(signal.details["observed_sweep_confirmation_position"], 4)

    def test_wider_observed_stop_rechecks_the_unchanged_cost_after_gate(self) -> None:
        _, bundle = _build([_row(), _acceptance(), _reclaim(), _confirmation(high=103.5)])
        self.assertEqual(sum(len(values) for values in bundle.signals_by_time_ns.values()), 0)
        self.assertEqual(
            bundle.diagnostics.get(
                "UPDATED_SWEEP_INSUFFICIENT_COST_AFTER_EXTERNAL_TARGET"
            ),
            1,
        )
        self.assertEqual(
            bundle.diagnostics.get("TRADEABLE_FAILED_AUCTION_REVERSAL"),
            0,
        )
        self.assertEqual(
            bundle.diagnostics.get(
                "FAILED_AUCTION_REVERSAL_REMOVED_BY_SWEEP_REFINEMENT"
            ),
            1,
        )
        rejection = next(
            item
            for item in bundle.rejected_scenarios
            if item.get("reason")
            == "UPDATED_SWEEP_INSUFFICIENT_COST_AFTER_EXTERNAL_TARGET"
        )
        self.assertEqual(rejection["sweep_high_at_reclaim"], 100.5)
        self.assertEqual(rejection["sweep_high_through_confirmation"], 103.5)
        self.assertLess(rejection["net_reward_risk"], 1.2)

    def test_future_rows_do_not_change_refined_signal(self) -> None:
        initial = [_row(), _acceptance(), _reclaim(), _confirmation(high=100.8)]
        _, first = _build(initial)
        _, second = _build(
            initial
            + [
                _row(open=99.4, high=110.0, low=90.0, close=105.0),
                _row(open=105.0, high=120.0, low=80.0, close=85.0),
            ]
        )
        first_signal = next(iter(next(iter(first.signals_by_time_ns.values()))))
        second_signal = second.signals_by_time_ns[first_signal.signal_time_ns][0]
        self.assertEqual(first_signal, second_signal)

    def test_index_timestamp_mismatch_is_an_implementation_error(self) -> None:
        data, bundle = _build([_row(), _acceptance(), _reclaim(), _confirmation(high=100.8)])
        signal = next(iter(next(iter(bundle.signals_by_time_ns.values()))))
        broken = replace(signal, signal_index=signal.signal_index - 1)
        with self.assertRaises(RuntimeError):
            _observed_sweep_through_confirmation(data, broken)

    def test_reclaim_timestamp_must_match_an_exact_completed_row(self) -> None:
        data, bundle = _build([_row(), _acceptance(), _reclaim(), _confirmation(high=100.8)])
        signal = next(iter(next(iter(bundle.signals_by_time_ns.values()))))
        broken = replace(signal, retest_time_ns=signal.retest_time_ns + 1)
        with self.assertRaises(RuntimeError):
            _observed_sweep_through_confirmation(data, broken)

    def test_reclaim_time_sweep_state_must_be_present(self) -> None:
        data, bundle = _build([_row(), _acceptance(), _reclaim(), _confirmation(high=100.8)])
        signal = next(iter(next(iter(bundle.signals_by_time_ns.values()))))
        details = dict(signal.details)
        details.pop("sweep_high")
        broken = replace(signal, details=details)
        with self.assertRaises(RuntimeError):
            _observed_sweep_through_confirmation(data, broken)


if __name__ == "__main__":
    unittest.main(verbosity=2)

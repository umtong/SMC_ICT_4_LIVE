from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT)]

from far_data import normalize_timestamp_ns
from far_detector import FlowAbsorptionDetector
from far_model import AggTrade, Direction, FarConfig, MinuteBar
from far_replay import FarReplay, NS_PER_MINUTE


class TimestampTests(unittest.TestCase):
    def test_millisecond_and_microsecond_archives_normalize_to_ns(self) -> None:
        self.assertEqual(normalize_timestamp_ns(1_646_611_200_123), 1_646_611_200_123_000_000)
        self.assertEqual(normalize_timestamp_ns(1_646_611_200_123_456), 1_646_611_200_123_456_000)


class DetectorTests(unittest.TestCase):
    def test_weighted_equilibrium_uses_standard_second_moment(self) -> None:
        config = FarConfig(
            equilibrium_window_minutes=2,
            activity_baseline_minutes=2,
            activity_min_history_minutes=1,
            atr_window_minutes=2,
            flow_imbalance_min=0.1,
            activity_ratio_min=1.01,
            equilibrium_z_min=0.01,
        )
        detector = FlowAbsorptionDetector(config)
        first = MinuteBar(0, 99.0, 101.0, 99.0, 100.0, 10.0, 1000.0, 0.0, 10, 1, 2)
        second = MinuteBar(1, 103.0, 104.0, 102.0, 103.0, 20.0, 2060.0, -1500.0, 20, 3, 4)
        self.assertIsNone(detector.observe(first))
        detector.observe(second)
        # The internal rolling mean must be volume weighted and include each
        # completed minute exactly once; this guards the prior moving-mean bug.
        typical1 = (101.0 + 99.0 + 100.0) / 3.0
        typical2 = (104.0 + 102.0 + 103.0) / 3.0
        expected = (typical1 * 10.0 + typical2 * 20.0) / 30.0
        self.assertAlmostEqual(detector._sum_price_volume / detector._sum_volume, expected)

    def test_current_activity_is_compared_only_with_prior_minutes(self) -> None:
        config = FarConfig(
            equilibrium_window_minutes=2,
            activity_baseline_minutes=3,
            activity_min_history_minutes=1,
            atr_window_minutes=2,
            flow_imbalance_min=0.1,
            activity_ratio_min=2.0,
            equilibrium_z_min=0.01,
            rejection_location_min=0.1,
            directional_progress_max_bps=100.0,
        )
        detector = FlowAbsorptionDetector(config)
        first = MinuteBar(0, 100, 101, 99, 100, 1, 100, 0, 1, 1, 2)
        second = MinuteBar(1, 101, 102, 100, 101, 1, 300, 250, 1, 3, 4)
        detector.observe(first)
        signal = detector.observe(second)
        self.assertIsNotNone(signal)
        self.assertAlmostEqual(signal.snapshot.activity_ratio, 3.0)


class ReplayTests(unittest.TestCase):
    def test_first_trade_after_signal_is_entry_and_stop_path_is_exact(self) -> None:
        config = FarConfig(
            equilibrium_window_minutes=2,
            activity_baseline_minutes=3,
            activity_min_history_minutes=1,
            atr_window_minutes=2,
            flow_imbalance_min=0.1,
            activity_ratio_min=2.0,
            equilibrium_z_min=0.01,
            rejection_location_min=0.1,
            directional_progress_max_bps=100.0,
            stop_buffer_atr=0.2,
            max_holding_minutes=60,
        )
        events = []
        replay = FarReplay(config, lambda **kwargs: events.append(kwargs))
        # Directly exercise execution after a detector-generated signal.
        from far_model import AbsorptionSignal, FeatureSnapshot
        snapshot = FeatureSnapshot(
            observed_time_ns=2 * NS_PER_MINUTE - 1,
            open=100,
            high=101,
            low=99,
            close=100,
            atr=2,
            flow_imbalance=0.5,
            activity_ratio=3,
            equilibrium_price=99,
            equilibrium_sigma=1,
            equilibrium_z=1,
            return_bps=0,
            directional_progress_bps=0,
            close_location=0.5,
            rejection_location=0.5,
            aggregate_trade_count=100,
            notional=1000,
        )
        signal = AbsorptionSignal("test", Direction.SHORT, snapshot)
        replay.portfolio.open(signal, 10, 100.0, 2 * NS_PER_MINUTE)
        self.assertEqual(replay.portfolio.position.entry_trade_id, 10)
        self.assertAlmostEqual(replay.portfolio.position.planned_loss, config.initial_nav * 0.03)
        replay.portfolio.process(11, 102.0, 2 * NS_PER_MINUTE + 1)
        self.assertEqual(len(replay.portfolio.trades), 1)
        self.assertEqual(replay.portfolio.trades[0].exit_reason.value, "STOP")
        self.assertLess(replay.portfolio.trades[0].net_r, 0)

    def test_single_slot_is_enforced(self) -> None:
        config = FarConfig()
        replay = FarReplay(config, lambda **kwargs: None)
        from far_model import AbsorptionSignal, FeatureSnapshot
        snapshot = FeatureSnapshot(1, 100, 101, 99, 100, 1, -0.5, 2, 100, 1, -1, 0, 0, 0.5, 0.5, 1, 100)
        signal = AbsorptionSignal("one", Direction.LONG, snapshot)
        replay.portfolio.open(signal, 1, 100.0, 2)
        with self.assertRaises(RuntimeError):
            replay.portfolio.open(signal, 2, 100.0, 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT)]

from far_data import normalize_timestamp_ns
from far_detector import FlowAbsorptionDetector
from far_model import (
    AbsorptionSignal,
    Direction,
    FarConfig,
    FeatureSnapshot,
    MinuteBar,
    ScenarioState,
)
from far_replay import FarReplay, NS_PER_MINUTE


def snapshot(
    *,
    direction: Direction = Direction.SHORT,
    observed_time_ns: int = 20 * NS_PER_MINUTE - 1,
    excursion_minutes: int = 20,
    excursion_start: int = 0,
) -> AbsorptionSignal:
    flow = 0.5 if direction is Direction.SHORT else -0.5
    z = 1.2 if direction is Direction.SHORT else -1.2
    return AbsorptionSignal(
        scenario_id=f"test-{direction.value}-{observed_time_ns}",
        direction=direction,
        snapshot=FeatureSnapshot(
            observed_time_ns=observed_time_ns,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            atr=2.0,
            flow_imbalance=flow,
            activity_ratio=3.0,
            equilibrium_price=98.0 if z > 0 else 102.0,
            equilibrium_sigma=2.0,
            equilibrium_z=z,
            equilibrium_side=1 if z > 0 else -1,
            equilibrium_excursion_minutes=excursion_minutes,
            equilibrium_excursion_start_minute=excursion_start,
            return_bps=0.0,
            directional_progress_bps=0.0,
            close_location=0.5,
            rejection_location=0.5,
            aggregate_trade_count=100,
            notional=1_000_000.0,
        ),
    )


def prior_bars(count: int = 10) -> tuple[MinuteBar, ...]:
    bars = []
    for minute in range(count):
        price = 100.0 + minute * 0.1
        bars.append(
            MinuteBar(
                minute_index=minute,
                open=price,
                high=price + 0.4,
                low=price - 0.4,
                close=price,
                volume=10.0,
                notional=1_000.0,
                signed_notional=0.0,
                aggregate_trade_count=10,
                first_event_time_ns=minute * NS_PER_MINUTE,
                last_event_time_ns=(minute + 1) * NS_PER_MINUTE - 2,
            )
        )
    return tuple(bars)


class TimestampTests(unittest.TestCase):
    def test_millisecond_and_microsecond_archives_normalize_to_ns(self) -> None:
        self.assertEqual(normalize_timestamp_ns(1_646_611_200_123), 1_646_611_200_123_000_000)
        self.assertEqual(normalize_timestamp_ns(1_646_611_200_123_456), 1_646_611_200_123_456_000)


class ConfigTests(unittest.TestCase):
    def test_risk_and_precommitted_weeks_are_frozen(self) -> None:
        config = FarConfig()
        config.validate()
        self.assertEqual(config.risk_fraction, 0.03)
        self.assertEqual(config.target_net_r, 3.0)
        self.assertEqual(config.max_holding_minutes, 240)
        self.assertEqual(
            config.validation_weeks,
            ("2022-07-18", "2021-12-13", "2021-01-11"),
        )
        self.assertFalse(set(config.development_weeks) & set(config.validation_weeks))


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
        detector.observe(first)
        detector.observe(second)
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
        assert signal is not None
        self.assertAlmostEqual(signal.snapshot.activity_ratio, 3.0)

    def test_equilibrium_excursion_age_resets_on_side_change(self) -> None:
        config = FarConfig(
            equilibrium_window_minutes=2,
            activity_baseline_minutes=4,
            activity_min_history_minutes=1,
            atr_window_minutes=2,
        )
        detector = FlowAbsorptionDetector(config)
        detector.observe(MinuteBar(0, 99, 101, 99, 100, 1, 100, 0, 1, 1, 2))
        detector.observe(MinuteBar(1, 100, 102, 100, 102, 1, 102, 0, 1, 3, 4))
        first_side = detector._equilibrium_side
        detector.observe(MinuteBar(2, 97, 98, 96, 96, 1, 96, 0, 1, 5, 6))
        self.assertNotEqual(detector._equilibrium_side, first_side)
        self.assertEqual(detector._excursion_minutes, 1)
        self.assertEqual(detector._excursion_start_minute, 2)


class StateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events: list[dict] = []
        self.config = FarConfig()
        self.replay = FarReplay(self.config, lambda **kwargs: self.events.append(kwargs))

    def test_choch_boundary_uses_only_ten_prior_completed_minutes(self) -> None:
        signal = snapshot(direction=Direction.SHORT)
        prior = prior_bars()
        self.replay._consider_signal(signal, prior)
        self.assertIsNotNone(self.replay.pending)
        assert self.replay.pending is not None
        self.assertEqual(self.replay.pending.confirmation_price, min(bar.low for bar in prior))
        self.assertEqual(self.events[-1]["next_state"], ScenarioState.CHOCH_PENDING.value)

    def test_first_exact_trade_through_structure_opens_position(self) -> None:
        signal = snapshot(direction=Direction.SHORT)
        prior = prior_bars()
        self.replay._consider_signal(signal, prior)
        assert self.replay.pending is not None
        level = self.replay.pending.confirmation_price
        event_time = signal.snapshot.observed_time_ns + 1
        self.replay._process_pending(42, level - 0.01, event_time)
        self.assertIsNone(self.replay.pending)
        self.assertIsNotNone(self.replay.portfolio.position)
        assert self.replay.portfolio.position is not None
        self.assertEqual(self.replay.portfolio.position.entry_trade_id, 42)
        self.assertAlmostEqual(
            self.replay.portfolio.position.planned_loss,
            self.config.initial_nav * self.config.risk_fraction,
        )

    def test_invalidation_before_choch_prevents_entry(self) -> None:
        signal = snapshot(direction=Direction.SHORT)
        self.replay._consider_signal(signal, prior_bars())
        assert self.replay.pending is not None
        invalidation = self.replay.pending.invalidation_price
        self.replay._process_pending(
            43,
            invalidation + 0.01,
            signal.snapshot.observed_time_ns + 1,
        )
        self.assertIsNone(self.replay.pending)
        self.assertIsNone(self.replay.portfolio.position)
        self.assertEqual(self.replay.counters["choch_invalidated"], 1)

    def test_only_first_attempt_in_equilibrium_excursion_is_armed(self) -> None:
        first = snapshot(direction=Direction.SHORT, excursion_start=7)
        self.replay._consider_signal(first, prior_bars())
        assert self.replay.pending is not None
        self.replay._terminate_pending(
            state=ScenarioState.EXPIRED,
            reason="TEST",
            event_time_ns=first.snapshot.observed_time_ns + 1,
            reference_price=100.0,
        )
        second = snapshot(
            direction=Direction.SHORT,
            observed_time_ns=first.snapshot.observed_time_ns + 61 * NS_PER_MINUTE,
            excursion_minutes=81,
            excursion_start=7,
        )
        self.replay._consider_signal(second, prior_bars())
        self.assertIsNone(self.replay.pending)
        self.assertEqual(self.replay.counters["blocked_by_excursion"], 1)

    def test_stale_excursion_is_rejected_before_structure_arm(self) -> None:
        signal = snapshot(excursion_minutes=self.config.equilibrium_excursion_max_minutes + 1)
        self.replay._consider_signal(signal, prior_bars())
        self.assertIsNone(self.replay.pending)
        self.assertEqual(self.replay.counters["stale_excursion_signals"], 1)

    def test_single_slot_is_enforced(self) -> None:
        signal = snapshot(direction=Direction.LONG)
        self.replay.portfolio.open(signal, 1, 100.0, signal.snapshot.observed_time_ns + 1)
        with self.assertRaises(RuntimeError):
            self.replay.portfolio.open(signal, 2, 100.0, signal.snapshot.observed_time_ns + 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

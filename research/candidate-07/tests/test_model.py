from __future__ import annotations

from pathlib import Path
import sys
import unittest

CANDIDATE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_DIR))

from model import CausalLiquidityRouter, Direction, LogicConfig, ScenarioKind, SignalBar  # noqa: E402


NS = 300_000_000_000


def config(**overrides) -> LogicConfig:
    values = {
        "signal_minutes": 1,
        "atr_period": 6,
        "volume_period": 6,
        "external_lookback": 10,
        "internal_lookback": 4,
        "trend_period": 6,
        "min_history": 10,
        "sweep_min_atr": 0.02,
        "sweep_max_atr": 1.0,
        "sweep_wick_fraction": 0.25,
        "sweep_volume_z": 0.1,
        "reclaim_buffer_atr": 0.01,
        "break_min_atr": 0.05,
        "displacement_body_atr": 0.2,
        "displacement_close_location": 0.65,
        "break_volume_z": 0.1,
        "confirmation_bars": 3,
        "reverse_confirm_body_atr": 0.12,
        "continuation_hold_atr": 0.08,
        "stop_buffer_atr": 0.02,
        "minimum_stop_atr": 0.1,
        "maximum_stop_atr": 2.0,
        "minimum_rr": 0.9,
        "continuation_target_rr": 1.5,
        "maximum_target_rr": 4.0,
        "continuation_efficiency_min": 0.1,
        "episode_cooldown_bars": 1,
    }
    values.update(overrides)
    return LogicConfig.from_mapping(values)


def bar(index: int, open_: float, high: float, low: float, close: float, volume: float) -> SignalBar:
    return SignalBar(index * NS, open_, high, low, close, volume)


class CausalLiquidityRouterTests(unittest.TestCase):
    def test_upper_sweep_routes_to_short_absorption_reclaim(self) -> None:
        router = CausalLiquidityRouter(config())
        history = [
            bar(i + 1, 98.0, 100.8 + (0.2 if i == 4 else 0.0), 94.0, 98.4, 90.0 + i * 3.0)
            for i in range(10)
        ]
        for index, item in enumerate(history):
            result = router.observe(item, index)
            self.assertIsNone(result.plan)

        sweep = bar(11, 100.4, 101.5, 99.5, 100.0, 180.0)
        contact = router.observe(sweep, 10)
        self.assertIsNone(contact.plan)
        self.assertEqual(len(contact.transitions), 1)
        self.assertEqual(contact.transitions[0].reason_code, "UPPER_POOL_SWEEP_RECLAIM")
        self.assertLess(float(contact.transitions[0].reference_price), sweep.high)

        confirm = bar(12, 100.0, 100.1, 97.9, 98.2, 150.0)
        result = router.observe(confirm, 11)
        self.assertIsNotNone(result.plan)
        assert result.plan is not None
        self.assertEqual(result.plan.kind, ScenarioKind.ABSORPTION_RECLAIM)
        self.assertEqual(result.plan.direction, Direction.SHORT)
        self.assertGreater(result.plan.stop_price, result.plan.entry_reference)
        self.assertLess(result.plan.target_price, result.plan.entry_reference)
        self.assertGreaterEqual(result.plan.expected_rr, 0.9)
        self.assertEqual(
            [(item.previous_state, item.next_state) for item in result.transitions],
            [("CONTACTED", "CONFIRMED"), ("CONFIRMED", "ENTRY_READY")],
        )

    def test_accepted_break_routes_to_long_continuation(self) -> None:
        router = CausalLiquidityRouter(config())
        history = []
        for i in range(10):
            open_ = 100.0 + i * 0.3
            close = open_ + 0.22
            history.append(bar(i + 1, open_, close + 0.18, open_ - 0.18, close, 90.0 + i * 3.0))
        for index, item in enumerate(history):
            router.observe(item, index)

        prior_upper = max(item.high for item in history)
        breakout = bar(11, prior_upper - 0.1, prior_upper + 0.75, prior_upper - 0.15, prior_upper + 0.60, 180.0)
        contact = router.observe(breakout, 10)
        self.assertEqual(contact.transitions[0].reason_code, "UPPER_POOL_ACCEPTED_DISPLACEMENT")

        hold = bar(12, prior_upper + 0.42, prior_upper + 0.9, prior_upper + 0.05, prior_upper + 0.7, 140.0)
        result = router.observe(hold, 11)
        self.assertIsNotNone(result.plan)
        assert result.plan is not None
        self.assertEqual(result.plan.kind, ScenarioKind.ACCEPTANCE_CONTINUATION)
        self.assertEqual(result.plan.direction, Direction.LONG)
        self.assertAlmostEqual(result.plan.expected_rr, 1.5)

    def test_ineligible_observation_cancels_active_episode_causally(self) -> None:
        router = CausalLiquidityRouter(config())
        for i in range(10):
            router.observe(bar(i + 1, 98.0, 101.0, 94.0, 98.5, 90.0 + i * 3.0), i)
        router.observe(bar(11, 100.3, 101.5, 99.5, 100.0, 180.0), 10)
        result = router.observe(bar(12, 100.0, 100.2, 98.0, 98.4, 150.0), 11, eligible=False)
        self.assertIsNone(result.plan)
        self.assertEqual(result.transitions[-1].reason_code, "ELIGIBILITY_LOST")
        self.assertIsNone(router.active_scenario_id)

    def test_config_rejects_unknown_parameters(self) -> None:
        with self.assertRaises(ValueError):
            LogicConfig.from_mapping({"not_a_parameter": 1})


if __name__ == "__main__":
    unittest.main()

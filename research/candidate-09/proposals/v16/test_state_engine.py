from __future__ import annotations

import unittest

from state_engine_v16_direct import (
    AuctionLevel,
    EngineConfig,
    FlowBar,
    LiquidityStateEngine,
    PendingResolution,
)


def bar(ts: int, o: float, h: float, l: float, c: float, imbalance: float, volume: float = 100.0) -> FlowBar:
    buy = volume * (imbalance + 1.0) / 2.0
    return FlowBar(ts, o, h, l, c, volume, buy, 100)


def pending(direction: str) -> PendingResolution:
    if direction == "UP":
        level = AuctionLevel("level", "HIGH", 100.0, 60, 0, 60, 120.0, 80.0, 90.0, 40.0, 0)
        extreme = 101.0
        signed = 80.0
    else:
        level = AuctionLevel("level", "LOW", 100.0, 60, 0, 60, 120.0, 80.0, 110.0, 40.0, 0)
        extreme = 99.0
        signed = -80.0
    return PendingResolution(
        "scenario", level, direction, "BREACHED", 0, 0.3,
        0.2 if direction == "UP" else -0.2, 1, extreme,
        outside_closes=1, displacement_seen=True, directional_flow_seen=True,
        max_volume_ratio=1.5, post_signed_flow=signed, post_volume=100.0,
    )


def engine(**kwargs) -> LiquidityStateEngine:
    config = EngineConfig(
        minimum_net_reward_to_risk=1.2,
        composite_cost_per_fill=0.00075,
        boundary_stop_all_reversals=True,
        **kwargs,
    )
    value = LiquidityStateEngine(config)
    value._atr = 2.0
    value._volume_median = 100.0
    value._index = 1
    return value


class UnacceptedAbsorptionTest(unittest.TestCase):
    def test_up_sweep_absorbed_against_residual_buy_flow_enters_sell(self):
        value = engine()
        value._pending = pending("UP")
        events = []
        signal = value._advance_pending(bar(120, 100.5, 100.7, 97.8, 98.0, -0.2), events)
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.side, "SELL")
        self.assertEqual(signal.stop_price, 101.24)
        self.assertEqual(signal.target_price, 90.0)
        self.assertGreaterEqual(signal.net_reward_to_risk, 1.2)
        self.assertIn("PASSIVELY_ABSORBED", signal.reason_code)
        self.assertTrue(any(e.event_type == "UNACCEPTED_SWEEP_ABSORPTION_CONFIRMED" for e in events))

    def test_down_sweep_is_symmetric(self):
        value = engine()
        value._pending = pending("DOWN")
        events = []
        signal = value._advance_pending(bar(120, 99.5, 102.2, 99.3, 102.0, 0.2), events)
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.side, "BUY")
        self.assertEqual(signal.stop_price, 98.76)
        self.assertEqual(signal.target_price, 110.0)

    def test_reversed_cumulative_flow_is_not_passive_absorption(self):
        value = engine()
        state = pending("UP")
        state.post_signed_flow = 10.0
        value._pending = state
        events = []
        signal = value._advance_pending(bar(120, 100.5, 100.7, 97.8, 98.0, -0.8), events)
        self.assertIsNone(signal)
        rejected = [e for e in events if e.event_type == "UNACCEPTED_SWEEP_ABSORPTION_REJECTED"]
        self.assertEqual(rejected[0].reason_code, "PRICE_REENTRY_NOT_AGAINST_RESIDUAL_BREAKOUT_FLOW")

    def test_no_residual_flow_ablation_is_a_single_component_control(self):
        value = engine(require_residual_aligned_flow=False)
        state = pending("UP")
        state.post_signed_flow = 10.0
        value._pending = state
        signal = value._advance_pending(bar(120, 100.5, 100.7, 97.8, 98.0, -0.8), [])
        self.assertIsNotNone(signal)

    def test_volume_participation_and_its_ablation(self):
        state = pending("UP")
        state.max_volume_ratio = 0.5
        strict = engine()
        strict._volume_median = 400.0
        strict._pending = state
        self.assertIsNone(strict._advance_pending(bar(120, 100.5, 100.7, 97.8, 98.0, -0.2), []))

        control_state = pending("UP")
        control_state.max_volume_ratio = 0.5
        control = engine(require_sweep_volume_participation=False)
        control._volume_median = 400.0
        control._pending = control_state
        self.assertIsNotNone(control._advance_pending(bar(120, 100.5, 100.7, 97.8, 98.0, -0.2), []))

    def test_exact_v14_control_keeps_original_expiry(self):
        value = engine(enable_unaccepted_absorption=False)
        value._pending = pending("UP")
        events = []
        self.assertIsNone(value._advance_pending(bar(120, 100.5, 100.7, 97.8, 98.0, -0.2), events))
        self.assertFalse(any(e.event_type.startswith("UNACCEPTED_SWEEP") for e in events))
        expiry = [e for e in events if e.event_type == "SCENARIO_EXPIRED"]
        self.assertEqual(expiry[0].reason_code, "BREACH_REENTERED_RANGE_BEFORE_ACCEPTANCE")

    def test_later_reentry_remains_v14_not_a_one_bar_sweep(self):
        value = engine()
        value._index = 2
        value._pending = pending("UP")
        events = []
        self.assertIsNone(value._advance_pending(bar(180, 100.5, 100.7, 97.8, 98.0, -0.2), events))
        self.assertFalse(any(e.event_type.startswith("UNACCEPTED_SWEEP") for e in events))
        self.assertTrue(any(e.reason_code == "BREACH_REENTERED_RANGE_BEFORE_ACCEPTANCE" for e in events))

    def test_full_cost_gate_rejects_short_target_without_new_threshold(self):
        value = engine()
        state = pending("UP")
        state.level.range_midpoint = 97.0
        state.level.range_low = 96.0
        value._pending = state
        events = []
        signal = value._advance_pending(bar(120, 100.5, 100.7, 97.8, 98.0, -0.2), events)
        self.assertIsNone(signal)
        confirmed = [e for e in events if e.event_type == "UNACCEPTED_SWEEP_ABSORPTION_CONFIRMED"]
        self.assertEqual(confirmed[0].reason_code, "UNACCEPTED_SWEEP_NET_REWARD_TO_RISK_BELOW_GATE")


if __name__ == "__main__":
    unittest.main()

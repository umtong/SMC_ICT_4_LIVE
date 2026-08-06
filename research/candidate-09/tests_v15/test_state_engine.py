from __future__ import annotations

import unittest

from state_engine_v15_direct import (
    AuctionLevel,
    EngineConfig,
    FlowBar,
    LiquidityStateEngine,
    PendingResolution,
)


def flow_bar(ts: int, o: float, h: float, l: float, c: float, imbalance: float) -> FlowBar:
    volume = 100.0
    buy = volume * (imbalance + 1.0) / 2.0
    return FlowBar(ts, o, h, l, c, volume, buy, 100)


def pending_up() -> PendingResolution:
    level = AuctionLevel(
        level_id="level", kind="HIGH", price=100.0, horizon_minutes=60,
        range_start_ns=0, range_end_ns=60, range_high=110.0, range_low=90.0,
        range_midpoint=95.0, range_width=20.0, observed_index=0,
    )
    return PendingResolution(
        scenario_id="scenario", level=level, direction="UP", state="ACCEPTED",
        start_index=0, approach_efficiency=0.2, approach_flow=0.1,
        confluence_count=1, extreme=101.5, outside_closes=2,
        displacement_seen=True, directional_flow_seen=True, max_volume_ratio=2.0,
        post_signed_flow=20.0, post_volume=100.0, acceptance_index=1,
    )


def engine(mode: str = "causal") -> LiquidityStateEngine:
    config = EngineConfig(
        minimum_net_reward_to_risk=0.5,
        composite_cost_per_fill=0.0,
        boundary_stop_all_reversals=True,
        impact_resolution_mode=mode,
    )
    value = LiquidityStateEngine(config)
    value._atr = 2.0
    return value


def accept(value: LiquidityStateEngine, pending: PendingResolution) -> None:
    value._event(
        pending,
        flow_bar(60, 100.2, 101.2, 100.1, 101.0, 0.2),
        "OUTSIDE_ACCEPTANCE",
        "BREACHED",
        "ACCEPTED",
        "ORDERFLOW_DISPLACEMENT_ACCEPTED_OUTSIDE_AUCTION",
    )


class ImpactClassificationTest(unittest.TestCase):
    def test_passive_absorption_is_price_reentry_against_residual_breakout_flow(self):
        value = engine("causal")
        state = pending_up()
        accept(value, state)
        state.post_signed_flow = 10.0
        state.post_volume = 200.0
        failure = flow_bar(120, 100.5, 100.6, 98.8, 99.0, -0.2)
        self.assertTrue(value._failure_confirmed(state, failure))
        self.assertEqual(value._impact_diagnostic["resolution_class"], "PASSIVE_ABSORPTION")
        signal = value._build_signal(state, failure, branch="REVERSAL")
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertIn("PASSIVE_ABSORPTION", signal.reason_code)

    def test_active_liquidity_flip_requires_relative_impact_dominance(self):
        value = engine("causal")
        state = pending_up()
        accept(value, state)
        state.post_signed_flow = -10.0
        state.post_volume = 200.0
        failure = flow_bar(120, 100.5, 100.6, 98.3, 98.5, -0.2)
        self.assertTrue(value._failure_confirmed(state, failure))
        self.assertGreaterEqual(value._impact_diagnostic["impact_efficiency_ratio"], 1.0)
        self.assertEqual(value._impact_diagnostic["resolution_class"], "ACTIVE_LIQUIDITY_FLIP")
        self.assertIsNotNone(value._build_signal(state, failure, branch="REVERSAL"))

    def test_weak_active_flow_reversal_is_rejected_without_parameter_fitting(self):
        value = engine("causal")
        state = pending_up()
        accept(value, state)
        state.post_signed_flow = -10.0
        state.post_volume = 200.0
        failure = flow_bar(120, 100.1, 100.2, 99.3, 99.5, -0.2)
        self.assertTrue(value._failure_confirmed(state, failure))
        self.assertLess(value._impact_diagnostic["impact_efficiency_ratio"], 1.0)
        self.assertEqual(value._impact_diagnostic["resolution_class"], "UNCONFIRMED_FLOW_REVERSAL")
        self.assertIsNone(value._build_signal(state, failure, branch="REVERSAL"))
        self.assertEqual(value._boundary_diagnostic["rejection_reason"], "FAILED_AUCTION_IMPACT_CLASS_NOT_CONFIRMED")

    def test_exact_v14_control_keeps_unclassified_failure(self):
        value = engine("all")
        state = pending_up()
        accept(value, state)
        state.post_signed_flow = -10.0
        state.post_volume = 200.0
        failure = flow_bar(120, 100.1, 100.2, 99.3, 99.5, -0.2)
        self.assertTrue(value._failure_confirmed(state, failure))
        signal = value._build_signal(state, failure, branch="REVERSAL")
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.reason_code, "ACCEPTED_BREAKOUT_FAILURE_WITH_BOUNDARY_REACCEPTANCE_INVALIDATION")

    def test_down_direction_is_symmetric(self):
        level = AuctionLevel(
            level_id="low", kind="LOW", price=100.0, horizon_minutes=15,
            range_start_ns=0, range_end_ns=60, range_high=110.0, range_low=90.0,
            range_midpoint=105.0, range_width=20.0, observed_index=0,
        )
        state = PendingResolution(
            scenario_id="down", level=level, direction="DOWN", state="ACCEPTED",
            start_index=0, approach_efficiency=0.2, approach_flow=-0.1,
            confluence_count=1, extreme=98.0, outside_closes=2,
            displacement_seen=True, directional_flow_seen=True, max_volume_ratio=2.0,
            post_signed_flow=-20.0, post_volume=100.0, acceptance_index=1,
        )
        value = engine("causal")
        value._event(
            state,
            flow_bar(60, 99.8, 99.9, 98.8, 99.0, -0.2),
            "OUTSIDE_ACCEPTANCE", "BREACHED", "ACCEPTED",
            "ORDERFLOW_DISPLACEMENT_ACCEPTED_OUTSIDE_AUCTION",
        )
        state.post_signed_flow = -10.0
        state.post_volume = 200.0
        failure = flow_bar(120, 99.5, 101.2, 99.4, 101.0, 0.2)
        self.assertTrue(value._failure_confirmed(state, failure))
        self.assertEqual(value._impact_diagnostic["resolution_class"], "PASSIVE_ABSORPTION")
        signal = value._build_signal(state, failure, branch="REVERSAL")
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.side, "BUY")

    def test_mechanism_only_controls_are_disjoint(self):
        passive = engine("passive-only")
        pstate = pending_up()
        accept(passive, pstate)
        pstate.post_signed_flow = 10.0
        pstate.post_volume = 200.0
        pbar = flow_bar(120, 100.5, 100.6, 98.8, 99.0, -0.2)
        self.assertTrue(passive._failure_confirmed(pstate, pbar))
        self.assertIsNotNone(passive._build_signal(pstate, pbar, branch="REVERSAL"))

        active = engine("active-only")
        astate = pending_up()
        accept(active, astate)
        astate.post_signed_flow = 10.0
        astate.post_volume = 200.0
        abar = flow_bar(120, 100.5, 100.6, 98.8, 99.0, -0.2)
        self.assertTrue(active._failure_confirmed(astate, abar))
        self.assertIsNone(active._build_signal(astate, abar, branch="REVERSAL"))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from types import SimpleNamespace
import unittest

from candidate16_failed_far import (
    FailedFarState,
    _semantic_two_close_acceptance_plan,
)
from logic import BarObs, Direction, Scenario, Side


def semantic_state() -> FailedFarState:
    return FailedFarState(
        scenario_id="C21",
        parent_scenario_id="PARENT-FAR",
        side=Side.HIGH,
        direction=Direction.LONG,
        boundary=100.0,
        target_pool_id="TARGET-HIGH",
        target_price=110.0,
        source_pool_level=99.5,
        source_pool_source="TEST_SESSION",
        source_strength=2,
        failure_ts_ns=1,
        failure_index=1,
        expiry_index=100,
        original_entry=99.0,
        original_stop=101.0,
        original_target=95.0,
        origin_kind="SEMANTIC_REJECTION",
        state="ACCEPTANCE_CONFIRMED",
        outside_streak=2,
        acceptance_ts_ns=2,
        acceptance_impulse_extreme=102.5,
    )


class Candidate21AcceptanceEntryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events = []
        self.engine = SimpleNamespace(
            config=SimpleNamespace(
                stop_buffer_atr=0.08,
                effective_taker_rate=0.0008,
                effective_maker_rate=0.0004,
                min_stop_atr=0.18,
                min_net_r=1.25,
            ),
            _event=lambda *args: self.events.append(args),
        )
        self.bar = BarObs(2, 101.5, 102.5, 101.2, 102.0, 1000.0, 100.0)

    def test_two_close_acceptance_builds_market_aac_plan(self) -> None:
        state = semantic_state()
        plan = _semantic_two_close_acceptance_plan(self.engine, state, self.bar, 1.0)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.scenario, Scenario.AAC)
        self.assertEqual(plan.direction, Direction.LONG)
        self.assertEqual(plan.expected_entry, 102.0)
        self.assertAlmostEqual(plan.stop_price, 99.92)
        self.assertEqual(plan.target_price, 110.0)
        self.assertGreater(plan.net_r, 1.25)
        self.assertEqual(plan.entry_order_type, "MARKET")
        self.assertFalse(plan.entry_post_only)
        self.assertEqual(
            plan.reason_code,
            "SEMANTIC_REJECTED_FAR_TWO_CLOSE_ACCEPTANCE_MARKET",
        )
        self.assertEqual(plan.details["origin_kind"], "SEMANTIC_REJECTION")
        self.assertEqual(plan.details["outside_closes"], 2)
        self.assertEqual(state.state, "PLAN_CONFIRMED")
        self.assertEqual(self.events[-1][1], "TRADE_PLAN_CONFIRMED")

    def test_post_stop_origin_cannot_use_early_entry_helper(self) -> None:
        state = semantic_state()
        state.origin_kind = "POST_STOP"
        with self.assertRaisesRegex(RuntimeError, "semantic-rejection origin"):
            _semantic_two_close_acceptance_plan(self.engine, state, self.bar, 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from logic import Direction, Scenario, TradePlan
from semantic_logic_v15 import _far_structural_retrace_plan
from semantic_post_gate_v15 import amend_after_leadership


class V15ExecutionGeometryTests(unittest.TestCase):
    @staticmethod
    def plan() -> TradePlan:
        return TradePlan(
            scenario_id="S1",
            scenario=Scenario.FAR,
            direction=Direction.LONG,
            observed_ts_ns=120,
            expected_entry=100.0,
            stop_price=95.0,
            target_price=112.0,
            atr=2.0,
            loss_per_unit=5.2,
            gain_per_unit=11.9,
            net_r=2.28,
            reason_code="FAR_FIRST_EXECUTION_VOID_LIMIT",
            expire_ts_ns=132,
            entry_order_type="LIMIT",
            entry_post_only=True,
            details={"pool_level": 98.0, "sweep_extreme": 94.8},
        )

    def test_far_preserves_passive_entry_structural_stop_and_target(self) -> None:
        inherited = self.plan()
        auction = SimpleNamespace(scenario=Scenario.FAR)
        engine = SimpleNamespace()
        confirmation = SimpleNamespace(ts_ns=120, close=104.0)
        with (
            patch("semantic_logic_v15.BASE_COSTED_LIMIT_PLAN", return_value=inherited),
            patch("semantic_logic_v15._structure_expiry", return_value=(420, 60)),
            patch(
                "semantic_logic_v15._void_repair_candidate",
                return_value={"eligible": True, "entry": 104.0, "stop": 102.0},
            ),
            patch("semantic_logic_v15._amend_last_plan_event") as amend_event,
        ):
            result = _far_structural_retrace_plan(
                engine,
                auction,
                confirmation,
                "FAR_FIRST_EXECUTION_VOID_LIMIT",
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.entry_order_type, "LIMIT")
        self.assertTrue(result.entry_post_only)
        self.assertEqual(result.expected_entry, inherited.expected_entry)
        self.assertEqual(result.stop_price, inherited.stop_price)
        self.assertEqual(result.target_price, inherited.target_price)
        self.assertEqual(result.expire_ts_ns, 420)
        self.assertEqual(
            result.details["entry_model"],
            "FAR_CAUSAL_DISPLACEMENT_RETRACE_LIMIT",
        )
        self.assertTrue(result.details["v15_market_chase_disabled"])
        self.assertTrue(result.details["v15_void_stop_disabled"])
        amend_event.assert_called_once()

    def test_leadership_cannot_rewrite_execution_geometry(self) -> None:
        original = self.plan()
        event = SimpleNamespace(
            scenario_id="S1",
            event_type="TRADE_PLAN_CONFIRMED",
            details={},
        )
        engine = SimpleNamespace(events=[event])
        decision = SimpleNamespace(
            approved=True,
            reason="SEMANTIC_FAR_EXHAUSTION_UNANIMOUS",
        )
        amended = amend_after_leadership(engine, original, decision)

        self.assertEqual(amended.entry_order_type, original.entry_order_type)
        self.assertEqual(amended.entry_post_only, original.entry_post_only)
        self.assertEqual(amended.expected_entry, original.expected_entry)
        self.assertEqual(amended.stop_price, original.stop_price)
        self.assertEqual(amended.target_price, original.target_price)
        self.assertEqual(amended.loss_per_unit, original.loss_per_unit)
        self.assertEqual(amended.net_r, original.net_r)
        self.assertFalse(amended.details["post_leadership_execution_reclassified"])
        self.assertEqual(
            event.details["post_leadership_execution_policy"],
            "PRESERVE_CAUSAL_RETRACE_AND_STRUCTURAL_STOP",
        )


if __name__ == "__main__":
    unittest.main()

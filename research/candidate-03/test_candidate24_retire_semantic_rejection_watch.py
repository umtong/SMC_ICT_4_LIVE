from __future__ import annotations

import inspect
from types import SimpleNamespace
import unittest

import candidate16_failed_far as state_module
from candidate16_failed_far import semantic_rejected_far_context
from logic import Direction, Scenario


class RetiredSemanticRejectionWatchTests(unittest.TestCase):
    def test_mark_rejected_records_retirement_without_arming_state(self) -> None:
        source = inspect.getsource(state_module.candidate16_mark_rejected)
        self.assertIn("SEMANTIC_REJECTED_FAR_WATCH_RETIRED", source)
        self.assertNotIn("arm_semantic_rejected_far(", source)
        self.assertIn("watch_armed", source)

    def test_directionally_meaningful_rejection_is_still_diagnosed(self) -> None:
        plan = SimpleNamespace(
            scenario=Scenario.FAR,
            direction=Direction.SHORT,
            scenario_id="FAR-PARENT",
            expected_entry=101.0,
            stop_price=103.0,
            target_price=96.0,
            atr=1.0,
            details={
                "sweep_extreme": 102.5,
                "pool_level": 102.0,
                "pool_source": "TEST_SESSION",
                "source_strength": 2,
                "sweep_ts_ns": 123,
                "stop_model": "SWEEP_EXTREME_INVALIDATION",
            },
        )
        context = semantic_rejected_far_context(
            plan,
            "SEMANTIC_FAR_MODERATE_COUNTERTREND_CONTESTED",
        )
        self.assertIsNotNone(context)
        self.assertEqual(context.boundary, 102.5)
        self.assertEqual(context.original_direction, Direction.SHORT)


if __name__ == "__main__":
    unittest.main(verbosity=2)

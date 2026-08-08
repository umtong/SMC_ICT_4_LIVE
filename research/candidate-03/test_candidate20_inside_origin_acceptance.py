from __future__ import annotations

import unittest

from candidate16_failed_far import FailedFarState, deep_reentry_is_terminal
from logic import BarObs, Direction, Side


def state(*, origin: str, phase: str = "WAIT_ACCEPTANCE", outside_streak: int = 0) -> FailedFarState:
    return FailedFarState(
        scenario_id="C20",
        parent_scenario_id="PARENT",
        side=Side.HIGH,
        direction=Direction.LONG,
        boundary=100.0,
        target_pool_id="TARGET",
        target_price=110.0,
        source_pool_level=99.5,
        source_pool_source="TEST",
        source_strength=2,
        failure_ts_ns=1,
        failure_index=1,
        expiry_index=100,
        original_entry=99.0,
        original_stop=101.0,
        original_target=95.0,
        origin_kind=origin,
        state=phase,
        outside_streak=outside_streak,
    )


class InsideOriginAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        # Close is materially back inside a LONG continuation boundary.
        self.inside = BarObs(1, 99.9, 100.0, 99.5, 99.6, 100.0, 40.0)

    def test_semantic_rejection_may_wait_inside_before_first_outside_close(self) -> None:
        self.assertFalse(
            deep_reentry_is_terminal(
                state(origin="SEMANTIC_REJECTION"),
                self.inside,
                1.0,
                0.18,
            )
        )

    def test_first_outside_attempt_makes_later_deep_reentry_terminal(self) -> None:
        self.assertTrue(
            deep_reentry_is_terminal(
                state(origin="SEMANTIC_REJECTION", outside_streak=1),
                self.inside,
                1.0,
                0.18,
            )
        )

    def test_post_stop_state_preserves_immediate_reentry_invalidation(self) -> None:
        self.assertTrue(
            deep_reentry_is_terminal(
                state(origin="POST_STOP"),
                self.inside,
                1.0,
                0.18,
            )
        )

    def test_semantic_state_after_acceptance_is_not_exempt(self) -> None:
        self.assertTrue(
            deep_reentry_is_terminal(
                state(origin="SEMANTIC_REJECTION", phase="WAIT_RETEST"),
                self.inside,
                1.0,
                0.18,
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

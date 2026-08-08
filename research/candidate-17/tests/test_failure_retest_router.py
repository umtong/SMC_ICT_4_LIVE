from __future__ import annotations

import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from failure_retest_router import FailureRetest
from failure_retest_router import RetestDecision
from failure_retest_router import RetestObservation
from failure_retest_router import advance_failure_retest


class FailureRetestTests(unittest.TestCase):
    def state(self, side: int = 1) -> FailureRetest:
        return FailureRetest(
            scenario_id="s",
            side=side,
            boundary=101.0 if side > 0 else 99.0,
            parent_extreme=98.0 if side > 0 else 102.0,
            atr=2.0,
            created_index=10,
            last_index=10,
            expires_index=14,
        )

    def obs(self, **changes: object) -> RetestObservation:
        values: dict[str, object] = {
            "bar_index": 11,
            "open": 100.9,
            "high": 101.8,
            "low": 100.7,
            "close": 101.6,
            "flow_15s": 0.1,
            "depth_imbalance_1": 0.2,
        }
        values.update(changes)
        return RetestObservation(**values)  # type: ignore[arg-type]

    def advance(self, state: FailureRetest, obs: RetestObservation) -> FailureRetest:
        return advance_failure_retest(
            state,
            obs,
            touch_tolerance_atr=0.15,
            max_counterflow=0.08,
            min_close_location=0.56,
        )

    def test_first_touch_hold_confirms(self) -> None:
        result = self.advance(self.state(), self.obs())
        self.assertEqual(result.decision, RetestDecision.CONFIRMED)
        self.assertTrue(result.touched)

    def test_parent_extreme_reaccess_invalidates(self) -> None:
        result = self.advance(
            self.state(),
            self.obs(open=99.0, high=101.2, low=97.9, close=100.5),
        )
        self.assertEqual(result.decision, RetestDecision.INVALIDATED)

    def test_first_touch_without_book_support_is_not_retried(self) -> None:
        result = self.advance(self.state(), self.obs(depth_imbalance_1=-0.1))
        self.assertEqual(result.decision, RetestDecision.INVALIDATED)
        self.assertTrue(result.touched)

    def test_no_touch_waits_then_expires(self) -> None:
        state = self.state()
        for index in (11, 12, 13):
            state = self.advance(
                state,
                self.obs(
                    bar_index=index,
                    open=102.0,
                    high=103.0,
                    low=101.8,
                    close=102.5,
                ),
            )
            self.assertEqual(state.decision, RetestDecision.WAITING)
        state = self.advance(
            state,
            self.obs(bar_index=14, open=102.0, high=103.0, low=101.8, close=102.5),
        )
        self.assertEqual(state.decision, RetestDecision.EXPIRED)

    def test_short_is_exact_mirror(self) -> None:
        result = self.advance(
            self.state(side=-1),
            self.obs(
                open=99.1,
                high=99.3,
                low=98.2,
                close=98.4,
                flow_15s=-0.1,
                depth_imbalance_1=-0.2,
            ),
        )
        self.assertEqual(result.decision, RetestDecision.CONFIRMED)

    def test_same_bar_cannot_confirm(self) -> None:
        with self.assertRaises(ValueError):
            self.advance(self.state(), self.obs(bar_index=10))


if __name__ == "__main__":
    unittest.main()

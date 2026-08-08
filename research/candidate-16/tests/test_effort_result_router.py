from __future__ import annotations

import unittest

from effort_result_router import AuctionDecision
from effort_result_router import AuctionObservation
from effort_result_router import ParentAuction
from effort_result_router import RouterThresholds
from effort_result_router import observe


class EffortResultRouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.thresholds = RouterThresholds()

    @staticmethod
    def parent(direction: int = 1) -> ParentAuction:
        return ParentAuction(
            scenario_id="test-1",
            direction=direction,
            pool_level=100.0,
            atr=2.0,
            started_index=10,
            last_index=10,
        )

    @staticmethod
    def bar(
        index: int,
        close: float,
        *,
        flow: float,
        burst: float = 1.4,
        efficiency: float = 0.30,
    ) -> AuctionObservation:
        return AuctionObservation(
            bar_index=index,
            ts_event=index * 60_000_000_000,
            open=100.0,
            high=max(100.8, close + 0.2),
            low=min(99.2, close - 0.2),
            close=close,
            flow_60s=flow,
            notional_burst=burst,
            efficiency_60s=efficiency,
            same_side_depth_change_1m=0.02,
        )

    def test_high_effort_low_progress_reclaim_is_failed_auction(self) -> None:
        state = observe(self.parent(), self.bar(11, 100.2, flow=0.18), self.thresholds)
        self.assertEqual(state.decision, AuctionDecision.PENDING)
        state = observe(state, self.bar(12, 99.9, flow=0.16, efficiency=0.25), self.thresholds)
        self.assertEqual(state.decision, AuctionDecision.FAILED_AUCTION)

    def test_acceptance_requires_two_outside_closes(self) -> None:
        state = observe(
            self.parent(),
            self.bar(11, 100.6, flow=0.18, efficiency=0.55),
            self.thresholds,
        )
        self.assertEqual(state.decision, AuctionDecision.PENDING)
        state = observe(
            state,
            self.bar(12, 100.7, flow=0.17, efficiency=0.52),
            self.thresholds,
        )
        self.assertEqual(state.decision, AuctionDecision.ACCEPTANCE_CONTINUATION)

    def test_weak_reentry_is_unresolved_not_reversal(self) -> None:
        state = observe(self.parent(), self.bar(11, 100.1, flow=0.03), self.thresholds)
        state = observe(state, self.bar(12, 99.8, flow=0.02), self.thresholds)
        self.assertEqual(state.decision, AuctionDecision.UNRESOLVED)

    def test_mirror_symmetry(self) -> None:
        high = observe(self.parent(1), self.bar(11, 100.2, flow=0.18), self.thresholds)
        high = observe(high, self.bar(12, 99.9, flow=0.16), self.thresholds)

        low = observe(self.parent(-1), self.bar(11, 99.8, flow=-0.18), self.thresholds)
        low = observe(low, self.bar(12, 100.1, flow=-0.16), self.thresholds)
        self.assertEqual(high.decision, low.decision)

    def test_terminal_state_cannot_be_reused(self) -> None:
        state = observe(self.parent(), self.bar(11, 100.2, flow=0.18), self.thresholds)
        state = observe(state, self.bar(12, 99.9, flow=0.16), self.thresholds)
        with self.assertRaises(ValueError):
            observe(state, self.bar(13, 99.7, flow=0.2), self.thresholds)

    def test_non_monotonic_observation_rejected(self) -> None:
        state = observe(self.parent(), self.bar(11, 100.2, flow=0.18), self.thresholds)
        with self.assertRaises(ValueError):
            observe(state, self.bar(11, 100.3, flow=0.18), self.thresholds)


if __name__ == "__main__":
    unittest.main()

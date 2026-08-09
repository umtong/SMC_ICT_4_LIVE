from __future__ import annotations

import unittest

from directional_sequential_flow_logic import DirectionalSequentialFlowState
from directional_sequential_flow_logic import update_directional_sequential_flow
from strategy_v26 import ScenarioValidEntryStrategy
from strategy_v35b_directional_sequential_flow import DirectionalSequentialFlowRegimeStrategy


class DirectionalSequentialFlowLogicTest(unittest.TestCase):
    def update(self, state, flow, index, high=None, low=None):
        return update_directional_sequential_flow(
            state=state,
            flow_60s=flow,
            high=float(100 + index if high is None else high),
            low=float(99 + index if low is None else low),
            bar_index=index,
            minimum_absolute_flow=0.1,
        )

    def test_opposing_evidence_resets_range_only_when_likelihood_reaches_zero(self) -> None:
        state = DirectionalSequentialFlowState()
        for index in range(3):
            state = self.update(state, 0.2, index).state
        self.assertEqual(state.upward.first_index, 0)
        original_high = state.upward.range_high

        # One contrary observation weakens, but correctly does not erase, the
        # fixed likelihood ratio. Continue until the upward LLR reaches zero.
        restarted = False
        for index in range(3, 6):
            opposed = self.update(state, -0.2, index)
            state = opposed.state
            restarted = restarted or opposed.upward_restarted
        self.assertTrue(restarted)
        self.assertEqual(state.upward.observations, 0)
        self.assertEqual(state.downward.first_index, 3)
        self.assertNotEqual(state.downward.range_high, original_high)

    def test_new_directional_episode_cannot_reuse_old_price_extreme(self) -> None:
        state = DirectionalSequentialFlowState()
        for index in range(3):
            state = self.update(state, 0.2, index, high=200.0, low=100.0).state
        for index in range(3, 6):
            state = self.update(state, -0.2, index, high=101.0, low=99.0).state
        state = self.update(state, 0.2, 6, high=105.0, low=104.0).state
        self.assertEqual(state.upward.first_index, 6)
        self.assertEqual(state.upward.range_high, 105.0)
        self.assertEqual(state.upward.range_low, 104.0)

    def test_directional_decision_reference_excludes_breakout_bar(self) -> None:
        state = DirectionalSequentialFlowState()
        decision = 0
        prior = state
        for index in range(30):
            prior = state
            update = self.update(state, 0.2, index, high=101.0, low=99.0)
            state = update.state
            decision = update.decision
            if decision:
                break
        self.assertEqual(decision, 1)
        self.assertTrue(prior.upward.available)
        self.assertEqual(prior.upward.last_index, index - 1)
        self.assertEqual(state.upward.last_index, index)

    def test_v35b_preserves_v26_execution_inheritance(self) -> None:
        self.assertTrue(
            issubclass(DirectionalSequentialFlowRegimeStrategy, ScenarioValidEntryStrategy),
        )


if __name__ == "__main__":
    unittest.main()

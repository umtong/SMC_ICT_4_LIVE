from __future__ import annotations

import math
import unittest

from sequential_flow_regime_logic import ALTERNATIVE_DIRECTION_PROBABILITY
from sequential_flow_regime_logic import NULL_DIRECTION_PROBABILITY
from sequential_flow_regime_logic import SequentialFlowState
from sequential_flow_regime_logic import UPPER_LOG_LIKELIHOOD_BOUNDARY
from sequential_flow_regime_logic import first_sequential_boundary_retest
from sequential_flow_regime_logic import sequential_release_breakout
from sequential_flow_regime_logic import sequential_structural_stop
from sequential_flow_regime_logic import update_sequential_flow


class SequentialFlowRegimeLogicTest(unittest.TestCase):
    def test_probability_contract_is_exact_two_to_one_against_one_to_one(self) -> None:
        self.assertEqual(NULL_DIRECTION_PROBABILITY, 0.5)
        self.assertEqual(ALTERNATIVE_DIRECTION_PROBABILITY, 2.0 / 3.0)
        self.assertAlmostEqual(UPPER_LOG_LIKELIHOOD_BOUNDARY, math.log(19.0))

    def test_repeated_direction_reaches_boundary_and_mirror_is_symmetric(self) -> None:
        upward = SequentialFlowState()
        downward = SequentialFlowState()
        up_decision = down_decision = 0
        for index in range(20):
            up = update_sequential_flow(
                state=upward,
                flow_60s=0.2,
                high=101.0 + index,
                low=99.0 + index,
                bar_index=index,
                minimum_absolute_flow=0.1,
            )
            down = update_sequential_flow(
                state=downward,
                flow_60s=-0.2,
                high=201.0 - index,
                low=199.0 - index,
                bar_index=index,
                minimum_absolute_flow=0.1,
            )
            upward, downward = up.state, down.state
            up_decision, down_decision = up.decision, down.decision
            if up_decision or down_decision:
                break
        self.assertEqual(up_decision, 1)
        self.assertEqual(down_decision, -1)
        self.assertEqual(upward.informative_observations, downward.informative_observations)

    def test_small_flow_is_not_evidence_and_opposing_flow_restarts_side(self) -> None:
        state = SequentialFlowState(upward_log_likelihood=0.2)
        unchanged = update_sequential_flow(
            state=state,
            flow_60s=0.05,
            high=101.0,
            low=99.0,
            bar_index=1,
            minimum_absolute_flow=0.1,
        )
        self.assertFalse(unchanged.informative)
        self.assertIs(unchanged.state, state)

        opposed = update_sequential_flow(
            state=state,
            flow_60s=-0.2,
            high=101.0,
            low=99.0,
            bar_index=1,
            minimum_absolute_flow=0.1,
        )
        self.assertEqual(opposed.state.upward_log_likelihood, 0.0)
        self.assertGreater(opposed.state.downward_log_likelihood, 0.0)

    def test_release_requires_structure_flow_efficiency_activity_and_withdrawal(self) -> None:
        base = dict(
            side=1,
            prior_high=100.0,
            prior_low=95.0,
            open_price=99.8,
            high=101.5,
            low=99.5,
            close=101.2,
            atr=2.0,
            flow_60s=0.4,
            efficiency_60s=0.60,
            notional_burst=1.20,
            bid_depth_change_1m=0.02,
            ask_depth_change_1m=-0.05,
            minimum_break_distance_atr=0.05,
            minimum_flow=0.10,
            minimum_efficiency=0.45,
            minimum_notional_burst=1.05,
            minimum_depth_withdrawal=0.01,
            minimum_close_location=0.62,
        )
        self.assertTrue(sequential_release_breakout(**base))
        self.assertFalse(sequential_release_breakout(**{**base, "efficiency_60s": 0.2}))
        self.assertFalse(sequential_release_breakout(**{**base, "ask_depth_change_1m": 0.02}))

        mirrored = {
            **base,
            "side": -1,
            "prior_high": 105.0,
            "prior_low": 100.0,
            "open_price": 100.2,
            "high": 100.5,
            "low": 98.5,
            "close": 98.8,
            "flow_60s": -0.4,
            "bid_depth_change_1m": -0.05,
            "ask_depth_change_1m": 0.02,
        }
        self.assertTrue(sequential_release_breakout(**mirrored))

    def test_first_retest_and_stop_are_mirror_symmetric(self) -> None:
        self.assertTrue(
            first_sequential_boundary_retest(
                side=1,
                boundary=100.0,
                high=102.0,
                low=99.9,
                close=101.0,
                flow_15s=0.0,
                depth_imbalance=0.2,
                maximum_counterflow=0.08,
                minimum_directional_depth=0.1,
            ),
        )
        self.assertTrue(
            first_sequential_boundary_retest(
                side=-1,
                boundary=100.0,
                high=100.1,
                low=98.0,
                close=99.0,
                flow_15s=0.0,
                depth_imbalance=-0.2,
                maximum_counterflow=0.08,
                minimum_directional_depth=0.1,
            ),
        )
        self.assertEqual(
            sequential_structural_stop(
                side=1,
                evidence_high=105.0,
                evidence_low=95.0,
                atr=2.0,
                stop_buffer_atr=0.1,
            ),
            94.8,
        )
        self.assertEqual(
            sequential_structural_stop(
                side=-1,
                evidence_high=105.0,
                evidence_low=95.0,
                atr=2.0,
                stop_buffer_atr=0.1,
            ),
            105.2,
        )


if __name__ == "__main__":
    unittest.main()

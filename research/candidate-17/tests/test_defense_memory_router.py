from __future__ import annotations

import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from defense_memory_router import DefenseMemory
from defense_memory_router import DepletionDecision
from defense_memory_router import ReattackObservation
from defense_memory_router import advance_defense_memory
from defense_memory_router import clean_first_defense


class CleanFirstDefenseTests(unittest.TestCase):
    def test_only_first_observation_without_outside_close_is_clean(self) -> None:
        self.assertTrue(clean_first_defense(observations=1, outside_closes=0))
        self.assertFalse(clean_first_defense(observations=2, outside_closes=0))
        self.assertFalse(clean_first_defense(observations=1, outside_closes=1))

    def test_negative_counts_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            clean_first_defense(observations=-1, outside_closes=0)


class DefenseMemoryTests(unittest.TestCase):
    def memory(self) -> DefenseMemory:
        return DefenseMemory(
            scenario_id="s-1",
            direction=1,
            defended_level=100.0,
            parent_extreme=101.0,
            atr=2.0,
            created_index=10,
            last_index=10,
            expires_index=13,
            baseline_efficiency=0.20,
            first_defense_change=0.04,
        )

    def observation(self, **changes: object) -> ReattackObservation:
        values: dict[str, object] = {
            "bar_index": 11,
            "open": 100.8,
            "high": 102.0,
            "low": 100.7,
            "close": 101.6,
            "flow_60s": 0.25,
            "ret_60s_bps": 3.0,
            "efficiency_60s": 0.35,
            "depth_imbalance_1": 0.20,
            "defending_depth_change_1m": -0.01,
            "liquidity_ahead_change_1m": -0.01,
            "oi_change_5m": 0.001,
            "positioning_ready": True,
            "positioning_age_seconds": 120.0,
        }
        values.update(changes)
        return ReattackObservation(**values)  # type: ignore[arg-type]

    def test_complete_relative_depletion_confirms(self) -> None:
        state = advance_defense_memory(self.memory(), self.observation())
        self.assertEqual(state.decision, DepletionDecision.CONFIRMED)
        self.assertGreater(state.latest_accepted_distance_atr, 0.0)

    def test_open_interest_contraction_does_not_confirm(self) -> None:
        state = advance_defense_memory(
            self.memory(),
            self.observation(oi_change_5m=-0.001),
        )
        self.assertEqual(state.decision, DepletionDecision.WAITING)

    def test_reattack_must_be_more_efficient_than_first_attack(self) -> None:
        state = advance_defense_memory(
            self.memory(),
            self.observation(efficiency_60s=0.20),
        )
        self.assertEqual(state.decision, DepletionDecision.WAITING)

    def test_replenished_defense_does_not_confirm(self) -> None:
        state = advance_defense_memory(
            self.memory(),
            self.observation(
                defending_depth_change_1m=0.06,
                liquidity_ahead_change_1m=0.06,
            ),
        )
        self.assertEqual(state.decision, DepletionDecision.WAITING)

    def test_stale_positioning_does_not_confirm(self) -> None:
        state = advance_defense_memory(
            self.memory(),
            self.observation(positioning_age_seconds=301.0),
        )
        self.assertEqual(state.decision, DepletionDecision.WAITING)

    def test_close_back_inside_invalidates_without_reversal_permission(self) -> None:
        state = advance_defense_memory(
            self.memory(),
            self.observation(
                open=100.2,
                high=100.5,
                low=99.0,
                close=99.8,
                flow_60s=-0.1,
                ret_60s_bps=-1.0,
                depth_imbalance_1=-0.1,
                efficiency_60s=0.1,
                oi_change_5m=-0.001,
            ),
        )
        self.assertEqual(state.decision, DepletionDecision.INVALIDATED)

    def test_window_expires_causally(self) -> None:
        state = self.memory()
        state = advance_defense_memory(
            state,
            self.observation(
                bar_index=11,
                close=100.9,
                flow_60s=0.0,
                ret_60s_bps=0.0,
                efficiency_60s=0.1,
                depth_imbalance_1=0.0,
                oi_change_5m=0.0,
            ),
        )
        state = advance_defense_memory(
            state,
            self.observation(
                bar_index=12,
                close=100.9,
                flow_60s=0.0,
                ret_60s_bps=0.0,
                efficiency_60s=0.1,
                depth_imbalance_1=0.0,
                oi_change_5m=0.0,
            ),
        )
        state = advance_defense_memory(
            state,
            self.observation(
                bar_index=13,
                close=100.9,
                flow_60s=0.0,
                ret_60s_bps=0.0,
                efficiency_60s=0.1,
                depth_imbalance_1=0.0,
                oi_change_5m=0.0,
            ),
        )
        self.assertEqual(state.decision, DepletionDecision.EXPIRED)

    def test_same_bar_cannot_be_reused(self) -> None:
        with self.assertRaises(ValueError):
            advance_defense_memory(self.memory(), self.observation(bar_index=10))


if __name__ == "__main__":
    unittest.main()

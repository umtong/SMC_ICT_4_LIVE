from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest

from domain_v3 import ArmedSetup, Side, TargetMode
from market_v4 import StructuralPivot
from market_v13 import pivot_key, select_first_directional_objective


def setup(side=Side.LONG, entry=100.0, stop=98.0, far=112.0):
    return ArmedSetup(
        setup_id="s",
        causal_event_id="c",
        symbol="BTCUSDT",
        family="SESSION_TRAP",
        side=side,
        observed_time_ns=100,
        entry=entry,
        stop=stop,
        target_mode=TargetMode.FIXED_STRUCTURE,
        initial_target=far,
        fixed_target_id="far",
        source_pool_id="pool",
        zone_low=entry,
        zone_high=entry,
        formation_extreme=stop + (0.1 if side is Side.LONG else -0.1),
        body_ratio=0.0,
        context_bias="ctx",
        source_timeframe_minutes=5,
    )


def pivot(side, level, event, observed=90):
    return StructuralPivot(
        center_index=0,
        observed_index=0,
        side=side,
        level=level,
        event_time_ns=event,
        observed_time_ns=observed,
    )


class TestFirstDirectionalObjective(unittest.TestCase):
    def test_long_uses_nearest_active_confirmed_high_not_far_session_cap(self):
        decision = select_first_directional_objective(
            setup=setup(),
            pivots=[pivot("HIGH", 108.0, 1), pivot("HIGH", 104.0, 2), pivot("HIGH", 110.0, 3)],
            setup_bar_high=101.0,
            setup_bar_low=99.0,
            timeframe_minutes=5,
        )
        self.assertEqual(decision.reason, "FIRST_ACTIVE_OBJECTIVE_SELECTED")
        self.assertIsNotNone(decision.setup)
        assert decision.setup is not None
        self.assertEqual(decision.setup.initial_target, 104.0)
        self.assertIn("FIRST_ACTIVE_DC_OBJECTIVE_5M", decision.setup.family)

    def test_objective_confirmed_at_same_close_is_not_available(self):
        decision = select_first_directional_objective(
            setup=setup(),
            pivots=[pivot("HIGH", 104.0, 1, observed=100)],
            setup_bar_high=101.0,
            setup_bar_low=99.0,
            timeframe_minutes=5,
        )
        self.assertEqual(decision.reason, "NO_ACTIVE_INTERNAL_OBJECTIVE_USE_FAR_CAP")
        self.assertEqual(decision.setup, setup())

    def test_setup_bar_consumed_micro_pivot_is_retired_and_next_active_is_selected(self):
        consumed = pivot("HIGH", 104.0, 1)
        active = pivot("HIGH", 108.0, 2)
        decision = select_first_directional_objective(
            setup=setup(),
            pivots=[consumed, active],
            setup_bar_high=104.0,
            setup_bar_low=99.0,
            timeframe_minutes=5,
        )
        self.assertEqual(decision.reason, "FIRST_ACTIVE_OBJECTIVE_SELECTED")
        assert decision.setup is not None
        self.assertEqual(decision.setup.initial_target, 108.0)
        self.assertEqual(decision.excluded_consumed, (consumed,))

    def test_only_consumed_internal_pivot_falls_back_to_far_cap(self):
        consumed = pivot("HIGH", 104.0, 1)
        decision = select_first_directional_objective(
            setup=setup(),
            pivots=[consumed],
            setup_bar_high=104.0,
            setup_bar_low=99.0,
            timeframe_minutes=5,
        )
        self.assertEqual(decision.reason, "NO_ACTIVE_INTERNAL_OBJECTIVE_USE_FAR_CAP")
        self.assertEqual(decision.setup, setup())

    def test_historically_consumed_pivot_is_retired(self):
        stale = pivot("HIGH", 104.0, 1)
        active = pivot("HIGH", 108.0, 2)
        decision = select_first_directional_objective(
            setup=setup(),
            pivots=[stale, active],
            setup_bar_high=101.0,
            setup_bar_low=99.0,
            timeframe_minutes=5,
            consumed_pivot_keys={pivot_key(stale)},
        )
        assert decision.setup is not None
        self.assertEqual(decision.setup.initial_target, 108.0)
        self.assertEqual(decision.excluded_consumed, (stale,))

    def test_first_active_objective_under_one_r_is_rejected_not_skipped(self):
        decision = select_first_directional_objective(
            setup=setup(entry=100.0, stop=98.0, far=112.0),
            pivots=[pivot("HIGH", 101.5, 1), pivot("HIGH", 108.0, 2)],
            setup_bar_high=100.5,
            setup_bar_low=99.0,
            timeframe_minutes=5,
        )
        self.assertEqual(decision.reason, "FIRST_ACTIVE_OBJECTIVE_RR_LT_1")
        self.assertIsNone(decision.setup)
        self.assertEqual(decision.pivot.level, 101.5)

    def test_short_side_is_symmetric(self):
        short = setup(side=Side.SHORT, entry=110.0, stop=112.0, far=98.0)
        decision = select_first_directional_objective(
            setup=short,
            pivots=[pivot("LOW", 102.0, 1), pivot("LOW", 106.0, 2)],
            setup_bar_high=111.0,
            setup_bar_low=109.0,
            timeframe_minutes=15,
        )
        self.assertEqual(decision.reason, "FIRST_ACTIVE_OBJECTIVE_SELECTED")
        assert decision.setup is not None
        self.assertEqual(decision.setup.initial_target, 106.0)
        self.assertIn("FIRST_ACTIVE_DC_OBJECTIVE_15M", decision.setup.family)


if __name__ == "__main__":
    unittest.main()

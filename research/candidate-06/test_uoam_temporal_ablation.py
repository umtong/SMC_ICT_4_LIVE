from __future__ import annotations

import unittest

from hierarchical_pool_engine import _LiquidityPool
from objective_lifecycle_engine import UnresolvedObjectiveLifecycleEngine
from test_objective_lifecycle_engine import bar, bias


class UoamTemporalAblationTests(unittest.TestCase):
    @staticmethod
    def engine(mode: str) -> UnresolvedObjectiveLifecycleEngine:
        return UnresolvedObjectiveLifecycleEngine(
            {
                "hsc_bias_period_minutes": 60,
                "hsc_liquidity_period_minutes": 5,
                "hsp_bias_expiry_mode": "STRUCTURAL_ONLY",
                "hml_pool_families": "SWING_AND_EQUAL",
                "uoam_use_objective_lifecycle": True,
                "uoam_objective_timing_mode": mode,
            },
        )

    def test_strict_mode_requires_confirmation_before_acceptance(self):
        engine = self.engine("CONFIRMED_BEFORE_ACCEPTANCE")
        engine._liquidity_pools = [
            _LiquidityPool("UPPER", 118.0, 5, 9),
        ]
        eligible = engine._eligible_objective_pools(
            bar(10, open_=101.0, high=110.0, low=100.0, close=109.0),
            bias("LONG"),
        )
        self.assertEqual(eligible, [])

    def test_ablation_allows_preexisting_source_confirmed_by_completed_acceptance(self):
        engine = self.engine("SOURCE_BEFORE_CONFIRM_BY_ACCEPTANCE_END")
        engine._liquidity_pools = [
            _LiquidityPool("UPPER", 118.0, 5, 9),
            _LiquidityPool("UPPER", 119.0, 9, 10),
        ]
        eligible = engine._eligible_objective_pools(
            bar(10, open_=101.0, high=110.0, low=100.0, close=109.0),
            bias("LONG"),
        )
        self.assertEqual([pool.level for pool in eligible], [118.0])

    def test_ablation_still_requires_objective_outside_accepting_extreme(self):
        engine = self.engine("SOURCE_BEFORE_CONFIRM_BY_ACCEPTANCE_END")
        engine._liquidity_pools = [_LiquidityPool("UPPER", 109.5, 5, 9)]
        eligible = engine._eligible_objective_pools(
            bar(10, open_=101.0, high=110.0, low=100.0, close=109.0),
            bias("LONG"),
        )
        self.assertEqual(eligible, [])


if __name__ == "__main__":
    unittest.main()

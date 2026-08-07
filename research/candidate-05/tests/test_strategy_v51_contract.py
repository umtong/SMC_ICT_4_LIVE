from __future__ import annotations

import inspect
import unittest

import strategy_v51_rolling_value as v51


class V51ContractTest(unittest.TestCase):
    def test_natural_value_and_regime_boundaries(self)->None:
        self.assertEqual(v51.WINDOW,30)
        self.assertEqual(v51.EXCURSION_Z,2.0)
        self.assertEqual(v51.REENTRY_Z,1.5)
        self.assertAlmostEqual(v51.BALANCED_MAX_EFFICIENCY,1.0/3.0)
        self.assertAlmostEqual(v51.DIRECTIONAL_MIN_EFFICIENCY,1.0/2.0)

    def test_both_causal_branches_use_inherited_paths(self)->None:
        source=inspect.getsource(v51.RollingValueAuctionStrategy)
        self.assertIn('PendingSetup',source)
        self.assertIn('PositionBuildingSetup',source)
        self.assertIn("oi_change_15m']<=0.0",source)
        self.assertIn("oi_change_15m']>0.0",source)

    def test_no_risk_or_engine_override(self)->None:
        source=inspect.getsource(v51)
        for token in ('risk_fraction =','max_notional','leverage_cap','match_order','calculate_pnl'):
            self.assertNotIn(token,source)


if __name__=='__main__':unittest.main()

from __future__ import annotations

import inspect
import unittest

import strategy_v48_session_value as v48
import strategy_v48b_session_value as v48b


class V48BContractTest(unittest.TestCase):
    def test_natural_value_and_regime_boundaries(self)->None:
        self.assertEqual(v48.FADE_Z,2.5)
        self.assertAlmostEqual(v48.BALANCED_MAX_EFFICIENCY,1.0/3.0)
        self.assertAlmostEqual(v48.DIRECTIONAL_MIN_EFFICIENCY,1.0/2.0)

    def test_prior_excursion_is_preserved(self)->None:
        source=inspect.getsource(v48b.CorrectedSessionValueAuctionStrategy)
        self.assertIn('v48b_prior_z_for_decision',source)
        self.assertIn('side*previous<=1.0',source)
        self.assertIn('abs(state.z)>=abs(previous)',source)

    def test_inherits_v26_execution_and_no_risk_override(self)->None:
        self.assertTrue(issubclass(v48b.CorrectedSessionValueAuctionStrategy,v48.SessionValueAuctionStrategy))
        source=inspect.getsource(v48)+inspect.getsource(v48b)
        for token in ('risk_fraction =','max_notional','leverage_cap','match_order','calculate_pnl'):
            self.assertNotIn(token,source)


if __name__=='__main__':unittest.main()

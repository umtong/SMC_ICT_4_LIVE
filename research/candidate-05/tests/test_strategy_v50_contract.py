from __future__ import annotations

import inspect
import unittest

import train_v50_analog_model as trainer
import v50_candidate_common as common
import strategy_v50_candidate_recorder as recorder


class V50ContractTest(unittest.TestCase):
    def test_fixed_high_precision_contract(self)->None:
        self.assertEqual(trainer.K,41)
        self.assertEqual(trainer.MIN_NEIGHBOR_WIN_RATE,0.85)
        self.assertEqual(trainer.MIN_NEIGHBOR_EXPECTANCY_R,0.50)
        self.assertEqual(trainer.MAX_HOLD_BARS,180)
        source=inspect.getsource(trainer.main).lower()
        self.assertNotIn('for threshold',source)
        self.assertNotIn('gridsearch',source)

    def test_exact_side_never_treats_price_as_direction(self)->None:
        self.assertIsNone(common.exact_side(30000.0))
        self.assertEqual(common.exact_side(1),1)
        self.assertEqual(common.exact_side(-1),-1)

    def test_recorder_observes_without_calling_original_order_helper(self)->None:
        source=inspect.getsource(recorder._wrap)
        self.assertIn('clear_rejected_state',source)
        self.assertNotIn('return original(',source)

    def test_feature_contract_and_no_risk_override(self)->None:
        self.assertGreaterEqual(len(common.FEATURE_NAMES),15)
        source=inspect.getsource(recorder)+inspect.getsource(common)
        for token in ('risk_fraction =','max_notional','leverage_cap','match_order','calculate_pnl'):
            self.assertNotIn(token,source)


if __name__=='__main__':unittest.main()

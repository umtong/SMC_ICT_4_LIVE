from __future__ import annotations

import inspect
import unittest

from directional_change_logic import DirectionalChangeState,aligned_change,trend_pullback_realignment
import strategy_v49_directional_change as v49


class V49ContractTest(unittest.TestCase):
    def test_directional_change_symmetry(self)->None:
        up=DirectionalChangeState(0.5,mode=-1,extreme=100.0)
        self.assertEqual(up.update(high=101.0,low=100.0,close=101.0,atr=2.0,index=1),1)
        down=DirectionalChangeState(0.5,mode=1,extreme=100.0)
        self.assertEqual(down.update(high=100.0,low=99.0,close=99.0,atr=2.0,index=1),-1)

    def test_multiscale_and_realign_contracts(self)->None:
        a=DirectionalChangeState(0.5,last_change_index=10,last_change_side=1)
        b=DirectionalChangeState(1.0,last_change_index=12,last_change_side=1)
        self.assertTrue(aligned_change(a,b,side=1,max_delay=2))
        self.assertEqual(trend_pullback_realignment(large_mode=1,small_previous_change=-1,small_current_change=1),1)

    def test_natural_atr_scales_and_inherited_paths(self)->None:
        source=inspect.getsource(v49.MultiScaleDirectionalChangeStrategy)
        self.assertIn('DirectionalChangeState(0.5)',source)
        self.assertIn('DirectionalChangeState(1.0)',source)
        self.assertIn('PendingSetup',source)
        self.assertIn('PositionBuildingSetup',source)

    def test_no_risk_or_engine_override(self)->None:
        source=inspect.getsource(v49)
        for token in ('risk_fraction =','max_notional','leverage_cap','match_order','calculate_pnl'):
            self.assertNotIn(token,source)


if __name__=='__main__':unittest.main()

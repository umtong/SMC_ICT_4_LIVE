from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
from domain import Candle, CostAssumptions, LiquidityPool, Side, TradePlan, detect_easychart_order_block, select_structural_target, size_for_fixed_risk

class TestDomain(unittest.TestCase):
    def candle(self,o,h,l,c,t): return Candle(t,t+1,o,h,l,c)
    def test_bullish_body_engulf_zone_is_previous_body_and_observed_at_close(self):
        prev=self.candle(10,10.2,8.8,9,0); cur=self.candle(8.9,11,8.7,10.5,2)
        ob=detect_easychart_order_block(prev,cur); self.assertIsNotNone(ob)
        self.assertEqual(ob.side,Side.LONG); self.assertEqual((ob.zone_low,ob.zone_high),(9,10)); self.assertEqual(ob.observed_time_ns,3)
        self.assertEqual(ob.proximal,10)
    def test_unclosed_or_non_engulfing_information_not_inferred(self):
        prev=self.candle(10,10.2,8.8,9,0); cur=self.candle(9.1,10.1,9,9.9,2)
        self.assertIsNone(detect_easychart_order_block(prev,cur))
    def test_fixed_risk_never_exceeds_three_percent(self):
        plan=TradePlan("p","c","BTCUSDT","x",Side.LONG,1,100,98,104,2,"s","t",99,100,98,2)
        q,per,planned=size_for_fixed_risk(nav=100000,risk_fraction=.03,plan=plan,costs=CostAssumptions(),size_increment="0.001")
        self.assertLessEqual(planned,3000); self.assertGreater(q,0); self.assertGreater(per,2)
    def test_rr_below_one_rejected(self):
        with self.assertRaises(ValueError): TradePlan("p","c","BTCUSDT","x",Side.LONG,1,100,98,101,0.5,"s","t",99,100,98,2)
    def test_first_structural_objective_below_one_r_rejects_trade(self):
        pools = [
            LiquidityPool("near", "HIGH", 101.5, 0, 1, 5),
            LiquidityPool("far", "HIGH", 110.0, 0, 1, 15),
        ]
        target = select_structural_target(
            side=Side.LONG, entry=100.0, stop=98.0, pools=pools, min_gross_rr=1.0,
        )
        self.assertIsNone(target)

    def test_risk_fraction_cannot_be_softened_or_increased(self):
        plan=TradePlan("p","c","BTCUSDT","x",Side.LONG,1,100,98,104,2,"s","t",99,100,98,2)
        with self.assertRaises(ValueError): size_for_fixed_risk(nav=100000,risk_fraction=.02,plan=plan,costs=CostAssumptions(),size_increment="0.001")
if __name__=="__main__": unittest.main()

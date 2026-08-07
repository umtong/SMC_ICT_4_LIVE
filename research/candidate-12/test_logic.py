from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
import unittest
from logic import BarObs, BoundarySide, CausalLiquidityAuctionEngine, Direction, FiveBar, LogicConfig, RiskSizer, ScenarioKind

NS_MINUTE=60_000_000_000

def ts(y,m,d,h,minute):
    return int(datetime(y,m,d,h,minute,tzinfo=timezone.utc).timestamp()*1_000_000_000)

def bar(t,o,h,l,c): return FiveBar(t,o,h,l,c,10,5)

class RiskTests(unittest.TestCase):
    def test_budget(self):
        d=RiskSizer(.03).size(nav=Decimal('100000'),loss_per_unit=Decimal('100'),entry_price=Decimal('26000'),quantity_increment=Decimal('.001'),min_quantity=Decimal('.001'),min_notional=Decimal('10'),margin_init=Decimal('.05'),free_balance=Decimal('100000'))
        self.assertTrue(d.feasible); self.assertLessEqual(d.expected_total_loss,Decimal('3000'))

class LogicTests(unittest.TestCase):
    def seed(self, engine, day, close=104):
        y,m,d=day
        # ATR warm-up before London
        for minute in range(180,365,5):
            engine._on_five(bar(ts(y,m,d,minute//60,minute%60),100,101,99,100),True)
        for minute in range(365,721,5):
            high=105 if minute==720 else 104
            low=95 if minute==715 else 96
            c=close if minute==720 else 100
            engine._on_five(bar(ts(y,m,d,minute//60,minute%60),100,high,low,c),True)
    def test_bar_validation(self):
        with self.assertRaises(ValueError): BarObs(1,100,99,98,100,1,.5)
    def test_high_forceful_rejection(self):
        e=CausalLiquidityAuctionEngine(LogicConfig(atr_period=2,min_net_r=0), 'X')
        self.seed(e,(2023,1,2),close=99) # below midpoint, forceful reclaim must prove rejection
        e._on_five(bar(ts(2023,1,2,12,5),104,108,103,107),True)
        e._on_five(bar(ts(2023,1,2,12,10),110,111,96,98),True)
        p=e._on_five(bar(ts(2023,1,2,12,15),100,103,100,102),True)
        self.assertIsNotNone(p); self.assertEqual(p.scenario,ScenarioKind.LONDON_HIGH_REJECTION); self.assertEqual(p.direction,Direction.SHORT)
    def test_weak_high_reclaim_becomes_acceptance(self):
        e=CausalLiquidityAuctionEngine(LogicConfig(atr_period=2,min_net_r=0), 'X')
        self.seed(e,(2023,1,2),close=96) # discount close
        e._on_five(bar(ts(2023,1,2,12,5),104,108,103,106),True)
        e._on_five(bar(ts(2023,1,2,12,10),106,107,103,104),True) # weak reclaim
        self.assertIsNone(e._on_five(bar(ts(2023,1,2,12,15),104,106,103,104),True))
        p=e._on_five(bar(ts(2023,1,2,12,20),104,112,104,111),True)
        self.assertIsNotNone(p); self.assertEqual(p.scenario,ScenarioKind.LONDON_HIGH_ACCEPTANCE); self.assertEqual(p.direction,Direction.LONG)
    def test_discount_low_rejection_targets_opposite_boundary(self):
        e=CausalLiquidityAuctionEngine(LogicConfig(atr_period=2,min_net_r=0), 'X')
        self.seed(e,(2023,1,2),close=96)
        e._on_five(bar(ts(2023,1,2,12,5),93,98,92,96),True) # low raid and bullish reclaim
        p=e._on_five(bar(ts(2023,1,2,12,10),96,99,95,98),True)
        self.assertIsNotNone(p); self.assertEqual(p.scenario,ScenarioKind.LONDON_LOW_REJECTION); self.assertEqual(p.target_price,105)
    def test_deep_discount_low_acceptance(self):
        e=CausalLiquidityAuctionEngine(LogicConfig(atr_period=2,min_net_r=0), 'X')
        self.seed(e,(2023,1,2),close=95.5)
        e._on_five(bar(ts(2023,1,2,12,5),96,98,92,96),True)
        self.assertIsNone(e._on_five(bar(ts(2023,1,2,12,10),96,98,94,94.5),True))
        p=e._on_five(bar(ts(2023,1,2,12,15),94.5,95,88,89),True)
        self.assertIsNotNone(p); self.assertEqual(p.scenario,ScenarioKind.LONDON_LOW_ACCEPTANCE); self.assertEqual(p.direction,Direction.SHORT)
    def test_consumed_target_cannot_be_reused(self):
        e=CausalLiquidityAuctionEngine(LogicConfig(atr_period=2,min_net_r=0), 'X')
        self.seed(e,(2023,1,2),close=96)
        e._on_five(bar(ts(2023,1,2,12,5),93,98,92,96),True)
        # Confirmation bar trades through the opposite London high before the
        # close-time decision.  That high is no longer a live objective.
        p=e._on_five(bar(ts(2023,1,2,12,10),96,106,95,98),True)
        self.assertIsNone(p)
        self.assertEqual(e.skips["STRUCTURAL_TARGET_REACHED_BEFORE_DECISION"],1)

    def test_weekend_no_episode(self):
        e=CausalLiquidityAuctionEngine(LogicConfig(atr_period=2,min_net_r=0),'X'); self.seed(e,(2023,1,7),close=104)
        e._on_five(bar(ts(2023,1,7,12,5),104,108,103,104),True)
        self.assertFalse(e.scenario_counts)

if __name__=='__main__': unittest.main()

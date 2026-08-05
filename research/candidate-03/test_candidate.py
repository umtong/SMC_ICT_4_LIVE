from __future__ import annotations
import sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT/'test_support'),str(ROOT)]
from model import Bar,Direction,EntryPlan,LiquidityPool,PoolKind,PoolSide,ScenarioKind,StrategyConfig,SweepObservation
from strategy import AuctionScenarioEngine,LiquidityDetector,PortfolioSimulator,NS_PER_MINUTE
from smc_ict_4.contracts import ResearchEvent
from smc_ict_4.event_log import validate_events
from run_research import deterministic_weeks

BASE=1_640_995_200_000_000_000

def bar(i,o,h,l,c,volume=100.0,taker=50.0):
    start=BASE+i*NS_PER_MINUTE
    return Bar(start,start+NS_PER_MINUTE-1_000_000,o,h,l,c,volume,volume*c,100,taker)

def collector():
    events=[]
    def emit(**kwargs):
        events.append(ResearchEvent(
            scenario_id=kwargs['scenario_id'],instrument_id='BTCUSDT-PERP.BINANCE',event_type=kwargs['event_type'],
            event_time_ns=kwargs['event_time_ns'],observed_time_ns=kwargs['observed_time_ns'],
            previous_state=kwargs['previous_state'],next_state=kwargs['next_state'],reason_code=kwargs['reason_code'],
            reference_price=None if kwargs['reference_price'] is None else str(kwargs['reference_price']),details=kwargs['details']))
    return events,emit

class DetectorTests(unittest.TestCase):
    def test_confirmed_swing_is_observed_after_pivot(self):
        events,emit=collector()
        cfg=StrategyConfig(atr_window=3,five_minute_atr_window=3,swing_left=1,swing_right=1,
                           swing_prominence_atr=0.05,range_history_count=100,range_window_minutes=60)
        detector=LiquidityDetector(cfg,emit)
        group_highs=[100,103,101,100,100]
        group_lows=[98,99,98.5,98,97.5]
        price=99
        index=0
        for gh,gl in zip(group_highs,group_lows):
            for minute in range(5):
                open_price=99.0;close=99.02
                detector.on_bar(bar(index,open_price,gh if minute==2 else 99.12,
                                    gl if minute==3 else 98.90,close),index)
                index+=1
        swings=[e for e in events if e.reason_code==PoolKind.CONFIRMED_SWING.value and e.details['pool_side']=='HIGH']
        self.assertTrue(swings)
        event=swings[0]
        self.assertGreater(event.observed_time_ns,event.event_time_ns)
        self.assertEqual(event.details['confirmation_delay_bars'],1)
        validate_events(events)

class ScenarioTests(unittest.TestCase):
    def test_rejection_requires_reclaim_structure_flow_and_displacement(self):
        events,emit=collector();cfg=StrategyConfig(flow_imbalance_threshold=0.05,min_net_reward_risk=1.2)
        class StubDetector:
            bars=[bar(0,99.4,99.8,99.0,99.5),bar(1,99.5,99.9,99.1,99.6),bar(2,99.6,100,99.2,99.7),bar(3,99.7,100,99.0,99.8)]
            def targets(self,direction,entry,observed):return [97.0] if direction is Direction.SHORT else [103.0]
        engine=AuctionScenarioEngine(cfg,StubDetector(),emit)
        pool=LiquidityPool('p',PoolSide.HIGH,PoolKind.DEALING_RANGE,100.0,BASE,BASE,BASE+10_000*NS_PER_MINUTE,
                           counterpart_price=97.0)
        sweep=SweepObservation(pool,4,BASE+5*NS_PER_MINUTE,100.5,0.5,0.2,99.0,1.0,ScenarioKind.REJECTION)
        engine.consider_sweeps([sweep],4)
        displacement=bar(5,100.0,100.1,98.7,98.8,100,30)
        self.assertIsNone(engine.on_bar(displacement,5))
        self.assertIsNotNone(engine.entry_plan)
        self.assertEqual(engine.entry_plan.kind,ScenarioKind.REJECTION)
        retest=bar(6,98.8,99.6,98.6,99.2,100,55)
        plan=engine.on_bar(retest,6)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.direction,Direction.SHORT)

class PortfolioTests(unittest.TestCase):
    def test_stop_wins_ambiguous_bar_and_planned_loss_caps_loss(self):
        events,emit=collector();cfg=StrategyConfig(max_holding_bars=180)
        portfolio=PortfolioSimulator(cfg,emit)
        plan=EntryPlan('s',ScenarioKind.REJECTION,Direction.LONG,100.0,99.0,103.0,0,8,1.0,'p',{})
        portfolio.open(plan,bar(0,100,100.2,99.9,100),0)
        trade=portfolio.on_bar(bar(1,100,104,98.5,101),1)
        self.assertIsNotNone(trade)
        self.assertEqual(trade.exit_reason.value,'STOP')
        self.assertGreaterEqual(trade.net_r,-1.000001)
        self.assertLess(trade.net_pnl,0)
        validate_events(events)
    def test_single_slot_is_enforced(self):
        _,emit=collector();portfolio=PortfolioSimulator(StrategyConfig(),emit)
        plan=EntryPlan('s',ScenarioKind.ACCEPTANCE,Direction.LONG,100,99,103,0,8,1,'p',{})
        portfolio.open(plan,bar(0,100,100.2,99.9,100),0)
        with self.assertRaises(RuntimeError):portfolio.open(plan,bar(1,100,100.2,99.9,100),1)

class SelectionTests(unittest.TestCase):
    def test_precommitted_random_weeks(self):
        weeks=[item.isoformat() for item in deterministic_weeks()]
        self.assertEqual(weeks,['2022-03-07','2025-03-17','2023-08-28'])
        self.assertGreaterEqual(abs((deterministic_weeks()[0]-deterministic_weeks()[1]).days),180)

if __name__=='__main__':unittest.main(verbosity=2)

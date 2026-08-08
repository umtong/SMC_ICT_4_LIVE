from __future__ import annotations
import unittest
from pathlib import Path
from types import SimpleNamespace
from candidate15_v11_completed_auction_materializer import completed_source_auction_family, materialize_v11_completed_auction_router_source, FAILED_AUCTION_FAMILY, ACCEPTED_AUCTION_FAMILY

class FamilyTests(unittest.TestCase):
    def plan(self,scenario,**extra):
        d=dict(pool_source='ASIA',range_id='R1',sweep_ts_ns=1,zone_low=99,zone_high=101); d.update(extra)
        return SimpleNamespace(scenario=SimpleNamespace(value=scenario),details=d)
    def test_far(self): self.assertEqual(completed_source_auction_family(self.plan('FAR',structural_stop=98)),FAILED_AUCTION_FAMILY)
    def test_aac(self): self.assertEqual(completed_source_auction_family(self.plan('AAC',defended_pullback=100,source_boundary=99)),ACCEPTED_AUCTION_FAMILY)
    def test_incomplete_fails_closed(self): self.assertIsNone(completed_source_auction_family(SimpleNamespace(scenario=SimpleNamespace(value='FAR'),details={})))
    def test_v10_source_materializes_once(self):
        import run_leadership_scdam
        out=materialize_v11_completed_auction_router_source(run_leadership_scdam._SOURCE)
        self.assertEqual(out.count('completed_source_auction_family(plan)'),1)
        self.assertNotIn('C15_V9_CORE_FAMILY_QUARANTINED',out)

class VendorTests(unittest.TestCase):
    @unittest.skipUnless((Path(__file__).parent/'c13_semantic_market_leadership_v16.py').is_file(),'vendor materialized in CI')
    def test_rank_one_rotation_source_rejected(self):
        from market_leadership import LeadershipDecision
        from c13_semantic_market_leadership_v16 import refine_v15_decision,FAR_ROTATION_SOURCE_NOT_TRANSFER
        d=LeadershipDecision(True,'SEMANTIC_FAR_ROTATION_TRANSFER_EVENT_DISPLACEMENT','BTCUSDT','ETHUSDT','FAR','LONG',1,2,{}, {},{},1,1,1,2,1,.2,.8)
        out=refine_v15_decision(d)
        self.assertFalse(out.approved); self.assertEqual(out.reason,FAR_ROTATION_SOURCE_NOT_TRANSFER)
    @unittest.skipUnless((Path(__file__).parent/'c13_semantic_logic_v15.py').is_file(),'vendor materialized in CI')
    def test_v15_forbids_market_chase_and_rearm(self):
        text=(Path(__file__).parent/'c13_semantic_logic_v15.py').read_text()
        self.assertIn('FAR_CAUSAL_DISPLACEMENT_RETRACE_LIMIT',text)
        self.assertIn('v15_market_chase_disabled',text)
        self.assertNotIn('REARM_AFTER_MISSED_RETRACE',text)
if __name__=='__main__': unittest.main()

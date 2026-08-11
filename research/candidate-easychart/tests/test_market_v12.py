from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest

from domain_v3 import Candle
from market_v7 import SessionLiquidityRange, SessionTrapConfig
from market_v12 import EasyChartWMTrapEngine

NS = 60_000_000_000

def bar(i,o,h,l,c):
    return Candle(i*5*NS,(i+1)*5*NS-1,o,h,l,c,1.0)

def rng():
    return SessionLiquidityRange("r","ASIA_RANGE","LONDON_KZ",0,5*NS,100*NS,110.0,100.0)

class TestWMTrap(unittest.TestCase):
    def engine(self):
        return EasyChartWMTrapEngine("BTCUSDT",[rng()],SessionTrapConfig(enable_immediate_fakeout=False,enable_delayed_trap=True,tick_size=0.1))

    def test_simple_v_reclaim_is_not_called_trap(self):
        e=self.engine()
        self.assertEqual(e.on_close(bar(1,101,102,99,99.5),1),[])
        self.assertEqual(e.on_close(bar(2,99.5,101,99,100.5),2),[])
        self.assertEqual(e.diagnostics.get("delayed_reclaim_without_wm_shape"),1)

    def test_w_shape_then_reclaim_is_trap(self):
        e=self.engine()
        e.on_close(bar(1,101,102,99,99.5),1)       # first outside low
        e.on_close(bar(2,99.5,100,99.1,99.8),2)    # rebound
        e.on_close(bar(3,99.8,99.9,98.5,99.2),3)   # second leg
        setups=e.on_close(bar(4,99.2,101,99,100.5),4)
        self.assertEqual(len(setups),1)
        self.assertIn("WM_TRAP_RETEST",setups[0].family)
        self.assertEqual(setups[0].stop,98.4)

    def test_m_shape_is_symmetric(self):
        e=self.engine()
        e.on_close(bar(1,109,111,108,110.5),1)
        e.on_close(bar(2,110.5,110.9,109.8,110.2),2)
        e.on_close(bar(3,110.2,111.5,110.1,110.8),3)
        setups=e.on_close(bar(4,110.8,111,109,109.5),4)
        self.assertEqual(len(setups),1)
        self.assertIn("WM_TRAP_RETEST",setups[0].family)
        self.assertEqual(setups[0].stop,111.6)

if __name__=="__main__": unittest.main()

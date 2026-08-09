from __future__ import annotations

import inspect
import unittest

import strategy_v52_cross_sectional_residual as v52


class V52ContractTest(unittest.TestCase):
    def test_all_assets_are_symmetric(self)->None:
        self.assertEqual(set(v52.ALL_SYMBOLS),{'BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT'})
        self.assertEqual(v52.ROBUST_Z,2.5)
        self.assertEqual(v52.MIN_OBSERVATIONS,90)

    def test_same_timestamp_peer_use_is_forbidden(self)->None:
        source=inspect.getsource(v52.CrossSectionalResidualStrategy._peer_state)
        self.assertIn('before_ts=ts',source)
        self.assertIn('history[-1].ts>=ts',source)

    def test_reversal_requires_oi_flow_and_depth(self)->None:
        source=inspect.getsource(v52.CrossSectionalResidualStrategy._maybe_arm_cross_sectional)
        self.assertIn('oi>0.0',source)
        self.assertIn("flow_15s']>0.0",source)
        self.assertIn("depth_imbalance_1']>0.0",source)
        self.assertIn('PendingSetup',source)

    def test_no_risk_or_engine_override(self)->None:
        source=inspect.getsource(v52)
        for token in ('risk_fraction =','max_notional','leverage_cap','match_order','calculate_pnl'):
            self.assertNotIn(token,source)


if __name__=='__main__':unittest.main()

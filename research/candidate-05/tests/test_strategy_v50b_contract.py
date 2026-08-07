from __future__ import annotations

import inspect
import unittest

import strategy_v50b_candidate_recorder as recorder
import strategy_v50b_analog_selector as selector
import v50_order_capture as capture


class V50BContractTest(unittest.TestCase):
    def test_recorder_and_selector_capture_actual_orders(self)->None:
        self.assertTrue(issubclass(recorder.ActualOrderCandidateRecorderStrategy,recorder._BASE))
        self.assertTrue(issubclass(selector.ActualOrderAnalogStrategy,selector._BASE))
        self.assertIn('submit_order_list',inspect.getsource(recorder.ActualOrderCandidateRecorderStrategy))
        self.assertIn('submit_order_list',inspect.getsource(selector.ActualOrderAnalogStrategy))

    def test_capture_requires_one_entry_and_real_exit_geometry(self)->None:
        source=inspect.getsource(capture.bracket_geometry)
        self.assertIn('len(entries)!=1',source)
        self.assertIn('side*(entry-stop)<=0.0',source)
        self.assertIn('side*(target-entry)<=0.0',source)

    def test_rejected_candidate_never_reaches_broker(self)->None:
        source=inspect.getsource(selector.ActualOrderAnalogStrategy.submit_order_list)
        self.assertIn('if decision',source)
        self.assertIn('return None',source)

    def test_no_risk_or_accounting_override(self)->None:
        source=inspect.getsource(recorder)+inspect.getsource(selector)+inspect.getsource(capture)
        for token in ('risk_fraction =','max_notional','leverage_cap','match_order','calculate_pnl'):
            self.assertNotIn(token,source)


if __name__=='__main__':unittest.main()

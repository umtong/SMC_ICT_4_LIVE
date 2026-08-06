"""Zero-trade evidence must be a clean economic failure, not an implementation failure."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from aggtrade_flow_response_auction_signals_v3 import (
    ABSORPTION_FAMILY,
    IMPLEMENTATION_REVISION,
    INITIATIVE_FAMILY,
)
from flow_response_trade_path_diagnostics_v2 import DIAGNOSTIC_REVISION
import run_aggtrade_flow_response_auction_nautilus as runner
from run_flow_response_staged_validation_v2 import validate_base_summary


class ZeroTradeEvidenceContracts(unittest.TestCase):
    def test_wrapper_records_explicit_zero_revision_count(self) -> None:
        original = runner._original_suite_summary
        try:
            runner._original_suite_summary = lambda *_args: {
                "suite_gate_passed": False,
                "promotable": True,
                "closed_trades": 0,
                "suite_gate_checks": {},
            }
            with patch.dict(
                os.environ,
                {"FLOW_RESPONSE_AUCTION_FAMILY_MODE": "both"},
                clear=False,
            ):
                summary = runner._flow_response_suite_summary({}, "first", [])
        finally:
            runner._original_suite_summary = original

        self.assertFalse(summary["suite_gate_passed"])
        self.assertTrue(
            summary["suite_gate_checks"]["complete_post_run_trade_path_diagnostics"]
        )
        self.assertEqual(
            summary["trade_path_diagnostic_summary"]["diagnostic_revision_counts"],
            {DIAGNOSTIC_REVISION: 0},
        )
        self.assertEqual(summary["trade_path_diagnostic_summary"]["records"], 0)
        self.assertEqual(summary["trade_path_diagnostic_summary"]["complete_records"], 0)

    def test_staged_evidence_validator_accepts_complete_zero_trade_evidence(self) -> None:
        summary = {
            "suite": "first",
            "implementation_revision": IMPLEMENTATION_REVISION,
            "ten_second_cadence_contract": "EXACT_CONSECUTIVE_10_SECONDS",
            "trade_path_diagnostic_revision": DIAGNOSTIC_REVISION,
            "flow_response_family_mode": "both",
            "diagnostic_family_ablation": False,
            "promotable": True,
            "scenario_attribution_passed": True,
            "scenario_attribution_checks": {
                "all_signals_attributed": True,
                "all_closed_trades_attributed": True,
                "no_unclassified_signals": True,
                "no_unclassified_closed_trades": True,
            },
            "suite_gate_passed": False,
            "suite_gate_checks": {
                "complete_auction_scenario_attribution": True,
                "complete_post_run_trade_path_diagnostics": True,
                "base_contract_includes_both_auction_families": True,
                "base_contract_includes_both_flow_response_families": True,
            },
            "closed_trades": 0,
            "trade_path_diagnostic_summary": {
                "records": 0,
                "complete_records": 0,
                "diagnostic_revision_counts": {DIAGNOSTIC_REVISION: 0},
                "expected_diagnostic_revision": DIAGNOSTIC_REVISION,
            },
            "scenario_family_results": {
                INITIATIVE_FAMILY: {
                    "signals": 0,
                    "closed_trades": 0,
                    "wins": 0,
                    "realized_pnl_usdt": 0.0,
                },
                ABSORPTION_FAMILY: {
                    "signals": 0,
                    "closed_trades": 0,
                    "wins": 0,
                    "realized_pnl_usdt": 0.0,
                },
            },
        }
        self.assertEqual(validate_base_summary(summary, expected_suite="first"), ())


if __name__ == "__main__":
    unittest.main(verbosity=2)

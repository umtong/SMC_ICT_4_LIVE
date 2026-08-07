"""Evidence-only contracts for the V3/V4 flow-response Nautilus entrypoint."""

from __future__ import annotations

import unittest

from flow_response_trade_path_diagnostics_v2 import DIAGNOSTIC_REVISION
import run_aggtrade_flow_response_auction_nautilus_v4 as runner


class FlowResponseV4EvidenceContracts(unittest.TestCase):
    def test_zero_trade_run_records_an_explicit_zero_revision_count(self) -> None:
        original = runner._ORIGINAL_SUITE_SUMMARY
        try:
            runner._ORIGINAL_SUITE_SUMMARY = lambda *_args: {
                "suite_gate_passed": False,
                "closed_trades": 0,
                "suite_gate_checks": {
                    "complete_post_run_trade_path_diagnostics": True,
                },
                "trade_path_diagnostic_summary": {
                    "records": 0,
                    "complete_records": 0,
                    "diagnostic_revision_counts": {},
                    "expected_diagnostic_revision": DIAGNOSTIC_REVISION,
                },
            }
            summary = runner._flow_response_suite_summary_v4({}, "first", [])
        finally:
            runner._ORIGINAL_SUITE_SUMMARY = original

        self.assertFalse(summary["suite_gate_passed"])
        self.assertTrue(
            summary["suite_gate_checks"]["complete_post_run_trade_path_diagnostics"]
        )
        self.assertEqual(
            summary["trade_path_diagnostic_summary"]["diagnostic_revision_counts"],
            {DIAGNOSTIC_REVISION: 0},
        )
        self.assertEqual(
            summary["evidence_wrapper_revision"],
            runner.EVIDENCE_WRAPPER_REVISION,
        )

    def test_nonzero_revision_counts_are_not_rewritten(self) -> None:
        original = runner._ORIGINAL_SUITE_SUMMARY
        try:
            runner._ORIGINAL_SUITE_SUMMARY = lambda *_args: {
                "suite_gate_passed": True,
                "closed_trades": 2,
                "suite_gate_checks": {
                    "complete_post_run_trade_path_diagnostics": True,
                },
                "trade_path_diagnostic_summary": {
                    "records": 2,
                    "complete_records": 2,
                    "diagnostic_revision_counts": {DIAGNOSTIC_REVISION: 2},
                    "expected_diagnostic_revision": DIAGNOSTIC_REVISION,
                },
            }
            summary = runner._flow_response_suite_summary_v4({}, "first", [])
        finally:
            runner._ORIGINAL_SUITE_SUMMARY = original

        self.assertTrue(summary["suite_gate_passed"])
        self.assertEqual(
            summary["trade_path_diagnostic_summary"]["diagnostic_revision_counts"],
            {DIAGNOSTIC_REVISION: 2},
        )

    def test_native_execution_entrypoint_is_still_the_verified_base_runner(self) -> None:
        # The V3 wrapper deliberately rebinds ``base_runner.run_window`` only to capture the exact
        # official replay frame for post-run diagnostics.  The function it delegates to remains the
        # verified native Nautilus implementation, and the wrapper itself owns no engine or order
        # construction.
        self.assertEqual(
            runner.base._original_run_window.__module__,
            "run_aggtrade_acceptance_nautilus",
        )
        self.assertIs(
            runner.base.runner.base_runner.run_window,
            runner.base._flow_response_run_window,
        )
        self.assertIs(
            runner.base.runner.base_runner._suite_summary,
            runner._flow_response_suite_summary_v4,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""V3 production-wrapper contracts for flow-response Nautilus execution."""

from __future__ import annotations

from pathlib import Path
import os
import unittest
from unittest.mock import patch

from aggtrade_flow_response_auction_signals_v3 import (
    ABSORPTION_FAMILY,
    IMPLEMENTATION_REVISION,
    INITIATIVE_FAMILY,
    build_flow_response_auction_signals,
)
from flow_response_trade_path_diagnostics_v2 import DIAGNOSTIC_REVISION
import run_aggtrade_flow_response_auction_nautilus as runner


class FlowResponseV3RunnerContracts(unittest.TestCase):
    def test_wrapper_binds_exact_v3_detector_and_complete_horizon_diagnostics(self) -> None:
        self.assertIs(
            runner.runner.build_auction_router_signals,
            build_flow_response_auction_signals,
        )
        self.assertIs(
            runner.runner.base_runner.build_acceptance_signals,
            runner._build_flow_response_signals,
        )
        self.assertEqual(runner.IMPLEMENTATION_REVISION, IMPLEMENTATION_REVISION)
        self.assertEqual(runner.DIAGNOSTIC_REVISION, DIAGNOSTIC_REVISION)
        self.assertEqual(runner.runner.INITIATIVE_FAMILY, INITIATIVE_FAMILY)
        self.assertEqual(runner.runner.FAILED_AUCTION_FAMILY, ABSORPTION_FAMILY)

    def test_native_execution_engine_is_not_reimplemented(self) -> None:
        self.assertEqual(
            runner._original_run_window.__module__,
            "run_aggtrade_acceptance_nautilus",
        )
        source = Path(runner.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "BacktestEngine(",
            "add_venue(",
            "risk_sized_quantity(",
            "order_factory.bracket(",
            "submit_order_list(",
            "default_leverage=",
        ):
            self.assertNotIn(forbidden, source)

    def test_config_loader_requires_exact_v3_revision(self) -> None:
        config = runner._load_auction_config()
        self.assertEqual(config.response.response_window_bars, 3)
        self.assertEqual(config.interaction_expiry_bars, 9)
        self.assertEqual(config.reversal_expiry_bars, 6)

    def test_single_family_mode_is_diagnostic_and_base_mode_is_identity(self) -> None:
        self.assertEqual(
            runner.FAMILY_MODES,
            {
                "both": frozenset((INITIATIVE_FAMILY, ABSORPTION_FAMILY)),
                "initiative_only": frozenset((INITIATIVE_FAMILY,)),
                "absorption_only": frozenset((ABSORPTION_FAMILY,)),
            },
        )
        with patch.dict(
            os.environ,
            {"FLOW_RESPONSE_AUCTION_FAMILY_MODE": "both"},
            clear=False,
        ):
            self.assertEqual(runner._active_family_mode(), "both")
        with patch.dict(
            os.environ,
            {"FLOW_RESPONSE_AUCTION_FAMILY_MODE": "fit_best"},
            clear=False,
        ):
            with self.assertRaises(RuntimeError):
                runner._active_family_mode()

    def test_suite_summary_requires_exact_path_revision_for_every_trade(self) -> None:
        original = runner._original_suite_summary
        try:
            runner._original_suite_summary = lambda *_args: {
                "suite_gate_passed": True,
                "promotable": True,
                "closed_trades": 1,
                "suite_gate_checks": {},
            }
            complete_trade = {
                "path_diagnostic": {
                    "path_diagnostic_status": "COMPLETE",
                    "diagnostic_revision": DIAGNOSTIC_REVISION,
                    "structural_first_touch": "TARGET",
                    "actual_holding_first_touch": "TARGET",
                    "target_reached_after_actual_close": False,
                    "target_reached_after_invalidation": False,
                    "actual_holding_favorable_target_distance_fraction": 1.0,
                    "actual_holding_adverse_stop_distance_fraction": 0.2,
                }
            }
            with patch.dict(
                os.environ,
                {"FLOW_RESPONSE_AUCTION_FAMILY_MODE": "both"},
                clear=False,
            ):
                summary = runner._flow_response_suite_summary(
                    {},
                    "first",
                    [{"closed_trade_records": [complete_trade]}],
                )
        finally:
            runner._original_suite_summary = original

        self.assertTrue(summary["suite_gate_passed"])
        self.assertEqual(summary["implementation_revision"], IMPLEMENTATION_REVISION)
        self.assertEqual(
            summary["ten_second_cadence_contract"],
            "EXACT_CONSECUTIVE_10_SECONDS",
        )
        self.assertEqual(summary["trade_path_diagnostic_revision"], DIAGNOSTIC_REVISION)
        self.assertEqual(
            summary["trade_path_diagnostic_summary"]["diagnostic_revision_counts"],
            {DIAGNOSTIC_REVISION: 1},
        )
        self.assertTrue(
            summary["suite_gate_checks"]["complete_post_run_trade_path_diagnostics"]
        )

    def test_wrong_or_missing_path_revision_closes_the_gate(self) -> None:
        original = runner._original_suite_summary
        try:
            runner._original_suite_summary = lambda *_args: {
                "suite_gate_passed": True,
                "promotable": True,
                "closed_trades": 1,
                "suite_gate_checks": {},
            }
            incomplete_trade = {
                "path_diagnostic": {
                    "path_diagnostic_status": "COMPLETE",
                    "diagnostic_revision": "old",
                    "structural_first_touch": "TARGET",
                    "actual_holding_first_touch": "TARGET",
                    "target_reached_after_actual_close": False,
                    "target_reached_after_invalidation": False,
                    "actual_holding_favorable_target_distance_fraction": 1.0,
                    "actual_holding_adverse_stop_distance_fraction": 0.2,
                }
            }
            with patch.dict(
                os.environ,
                {"FLOW_RESPONSE_AUCTION_FAMILY_MODE": "both"},
                clear=False,
            ):
                summary = runner._flow_response_suite_summary(
                    {},
                    "first",
                    [{"closed_trade_records": [incomplete_trade]}],
                )
        finally:
            runner._original_suite_summary = original

        self.assertFalse(summary["suite_gate_passed"])
        self.assertFalse(
            summary["suite_gate_checks"]["complete_post_run_trade_path_diagnostics"]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

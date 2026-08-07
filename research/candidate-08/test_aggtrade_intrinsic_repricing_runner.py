"""Native-runner and evidence contracts for intrinsic repricing."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import os
import unittest
from unittest.mock import patch

from aggtrade_acceptance_signals import AcceptanceSignalBundle
from aggtrade_intrinsic_repricing_signals import (
    DIRECT_PERSISTENCE_PATH,
    IMPLEMENTATION_REVISION,
    INTRINSIC_REPRICING_FAMILY,
    REPRICE_RESUMPTION_PATH,
    build_intrinsic_repricing_signals,
)
from flow_response_trade_path_diagnostics_v2 import DIAGNOSTIC_REVISION
import run_aggtrade_intrinsic_repricing_nautilus as runner


def _signal(*, scenario_id: str, path: str, timestamp: int):
    return SimpleNamespace(
        scenario_id=scenario_id,
        symbol="BTCUSDT",
        boundary_id="boundary",
        signal_time_ns=timestamp,
        details={
            "scenario_family": INTRINSIC_REPRICING_FAMILY,
            "entry_path": path,
        },
    )


def _complete_trade(*, path: str, pnl: float = 100.0) -> dict:
    return {
        "scenario_id": f"scenario-{path}",
        "scenario_family": INTRINSIC_REPRICING_FAMILY,
        "entry_path": path,
        "realized_pnl": pnl,
        "path_diagnostic": {
            "path_diagnostic_status": "COMPLETE",
            "diagnostic_revision": DIAGNOSTIC_REVISION,
            "structural_first_touch": "TARGET" if pnl > 0 else "STOP",
            "actual_holding_first_touch": "TARGET" if pnl > 0 else "STOP",
            "target_reached_after_actual_close": False,
            "target_reached_after_invalidation": False,
            "actual_holding_favorable_target_distance_fraction": 1.0 if pnl > 0 else 0.1,
            "actual_holding_adverse_stop_distance_fraction": 0.2 if pnl > 0 else 1.0,
        },
    }


class IntrinsicRepricingRunnerContracts(unittest.TestCase):
    def test_detector_is_rebound_without_reimplementing_native_engine(self) -> None:
        self.assertIs(
            runner.execution.runner.build_auction_router_signals,
            build_intrinsic_repricing_signals,
        )
        self.assertIs(
            runner.execution.runner.base_runner.build_acceptance_signals,
            runner._build_signals,
        )
        self.assertEqual(
            runner.execution._original_run_window.__module__,
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

    def test_config_loader_requires_exact_frozen_revision(self) -> None:
        config = runner._load_repricing_config()
        self.assertEqual(config.maximum_event_bars, 9)
        self.assertEqual(config.response.response_window_bars, 3)
        self.assertEqual(IMPLEMENTATION_REVISION, "CAUSAL_INTRINSIC_REPRICING_CONTINUATION_V1")

    def test_path_mode_defaults_to_both_and_rejects_search_modes(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("INTRINSIC_REPRICING_PATH_MODE", None)
            self.assertEqual(runner._active_path_mode(), "both_paths")
        with patch.dict(
            os.environ,
            {"INTRINSIC_REPRICING_PATH_MODE": "direct_only"},
            clear=False,
        ):
            self.assertEqual(runner._active_path_mode(), "direct_only")
        with patch.dict(
            os.environ,
            {"INTRINSIC_REPRICING_PATH_MODE": "fit_best_path"},
            clear=False,
        ):
            with self.assertRaises(RuntimeError):
                runner._active_path_mode()

    def test_single_path_mode_removes_only_the_other_path(self) -> None:
        direct = _signal(
            scenario_id="direct-1",
            path=DIRECT_PERSISTENCE_PATH,
            timestamp=10,
        )
        reprice = _signal(
            scenario_id="reprice-1",
            path=REPRICE_RESUMPTION_PATH,
            timestamp=20,
        )
        bundle = AcceptanceSignalBundle(
            signals_by_time_ns={10: (direct,), 20: (reprice,)},
            diagnostics={"BASE": 1},
            rejected_scenarios=(),
        )
        with patch.dict(
            os.environ,
            {"INTRINSIC_REPRICING_PATH_MODE": "reprice_only"},
            clear=False,
        ):
            filtered = runner._filter_bundle(bundle)

        self.assertNotIn(10, filtered.signals_by_time_ns)
        self.assertEqual(filtered.signals_by_time_ns[20], (reprice,))
        self.assertEqual(filtered.diagnostics["DIAGNOSTIC_PATH_REMOVED_SIGNALS"], 1)
        self.assertEqual(
            filtered.rejected_scenarios[-1]["reason"],
            "DIAGNOSTIC_ENTRY_PATH_REMOVED",
        )
        self.assertEqual(filtered.rejected_scenarios[-1]["path_mode"], "reprice_only")

    def test_both_path_mode_is_identity(self) -> None:
        direct = _signal(
            scenario_id="direct-1",
            path=DIRECT_PERSISTENCE_PATH,
            timestamp=10,
        )
        bundle = AcceptanceSignalBundle(
            signals_by_time_ns={10: (direct,)},
            diagnostics={"BASE": 1},
            rejected_scenarios=(),
        )
        with patch.dict(
            os.environ,
            {"INTRINSIC_REPRICING_PATH_MODE": "both_paths"},
            clear=False,
        ):
            self.assertIs(runner._filter_bundle(bundle), bundle)

    def test_suite_summary_attributes_family_path_and_complete_post_run_evidence(self) -> None:
        original = runner._ORIGINAL_SUITE_SUMMARY
        try:
            runner._ORIGINAL_SUITE_SUMMARY = lambda *_args: {
                "suite_gate_passed": True,
                "promotable": True,
                "closed_trades": 1,
                "suite_gate_checks": {},
            }
            result = {
                "detector": {
                    "signals": 1,
                    "by_scenario_family": {INTRINSIC_REPRICING_FAMILY: 1},
                    "by_entry_path": {DIRECT_PERSISTENCE_PATH: 1},
                },
                "closed_trade_records": [
                    _complete_trade(path=DIRECT_PERSISTENCE_PATH)
                ],
            }
            with patch.dict(
                os.environ,
                {"INTRINSIC_REPRICING_PATH_MODE": "both_paths"},
                clear=False,
            ):
                summary = runner._suite_summary({}, "first", [result])
        finally:
            runner._ORIGINAL_SUITE_SUMMARY = original

        self.assertTrue(summary["suite_gate_passed"])
        self.assertEqual(summary["implementation_revision"], IMPLEMENTATION_REVISION)
        self.assertEqual(summary["single_scenario_family"], INTRINSIC_REPRICING_FAMILY)
        self.assertTrue(summary["single_family_attribution_passed"])
        self.assertTrue(summary["entry_path_attribution_passed"])
        self.assertEqual(
            summary["entry_path_results"][DIRECT_PERSISTENCE_PATH]["closed_trades"],
            1,
        )
        self.assertEqual(
            summary["entry_path_results"][REPRICE_RESUMPTION_PATH]["closed_trades"],
            0,
        )
        self.assertEqual(
            summary["trade_path_diagnostic_summary"]["diagnostic_revision_counts"],
            {DIAGNOSTIC_REVISION: 1},
        )
        self.assertTrue(
            summary["suite_gate_checks"]["complete_post_run_trade_path_diagnostics"]
        )

    def test_diagnostic_path_mode_cannot_open_promotion_gate(self) -> None:
        original = runner._ORIGINAL_SUITE_SUMMARY
        try:
            runner._ORIGINAL_SUITE_SUMMARY = lambda *_args: {
                "suite_gate_passed": True,
                "promotable": True,
                "closed_trades": 1,
                "suite_gate_checks": {},
            }
            result = {
                "detector": {
                    "signals": 1,
                    "by_scenario_family": {INTRINSIC_REPRICING_FAMILY: 1},
                    "by_entry_path": {DIRECT_PERSISTENCE_PATH: 1},
                },
                "closed_trade_records": [
                    _complete_trade(path=DIRECT_PERSISTENCE_PATH)
                ],
            }
            with patch.dict(
                os.environ,
                {"INTRINSIC_REPRICING_PATH_MODE": "direct_only"},
                clear=False,
            ):
                summary = runner._suite_summary({}, "first", [result])
        finally:
            runner._ORIGINAL_SUITE_SUMMARY = original

        self.assertTrue(summary["diagnostic_path_ablation"])
        self.assertFalse(summary["promotable"])
        self.assertFalse(summary["suite_gate_passed"])
        self.assertFalse(
            summary["suite_gate_checks"]["base_contract_includes_both_entry_paths"]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Source-stable contracts for the flow-response auction V2 Nautilus wrapper."""

from __future__ import annotations

from inspect import signature
from pathlib import Path
from types import SimpleNamespace
import os
import unittest
from unittest.mock import patch

import pandas as pd

from aggtrade_acceptance_signals import AcceptanceSignalBundle
from aggtrade_flow_response_auction_signals_v2 import (
    ABSORPTION_FAMILY,
    IMPLEMENTATION_REVISION,
    INITIATIVE_FAMILY,
    build_flow_response_auction_signals,
)
import run_aggtrade_flow_response_auction_nautilus as flow_runner


def _signal(*, scenario_id: str, family: str, timestamp: int):
    return SimpleNamespace(
        scenario_id=scenario_id,
        symbol="BTCUSDT",
        boundary_id="boundary",
        signal_time_ns=timestamp,
        details={"scenario_family": family},
    )


def _path_frame() -> pd.DataFrame:
    index = pd.date_range("2024-01-01T00:00:10Z", periods=4, freq="10s")
    return pd.DataFrame(
        {
            "high": [100.1, 100.5, 104.2, 105.0],
            "low": [99.9, 97.7, 99.0, 103.0],
            "close": [100.0, 98.2, 104.0, 104.5],
        },
        index=index,
    )


class FlowResponseRunnerWiringContracts(unittest.TestCase):
    def test_detector_and_family_vocabulary_are_rebound_exactly(self) -> None:
        self.assertIs(
            flow_runner.runner.build_auction_router_signals,
            build_flow_response_auction_signals,
        )
        self.assertIs(
            flow_runner.runner.base_runner.build_acceptance_signals,
            flow_runner._build_flow_response_signals,
        )
        self.assertEqual(flow_runner.runner.INITIATIVE_FAMILY, INITIATIVE_FAMILY)
        self.assertEqual(flow_runner.runner.FAILED_AUCTION_FAMILY, ABSORPTION_FAMILY)
        self.assertEqual(
            flow_runner.FAMILY_MODES,
            {
                "both": frozenset((INITIATIVE_FAMILY, ABSORPTION_FAMILY)),
                "initiative_only": frozenset((INITIATIVE_FAMILY,)),
                "absorption_only": frozenset((ABSORPTION_FAMILY,)),
            },
        )
        self.assertIn(
            "require_retest_contraction",
            signature(build_flow_response_auction_signals).parameters,
        )

    def test_execution_engine_remains_the_verified_native_base(self) -> None:
        self.assertEqual(
            flow_runner._original_run_window.__module__,
            "run_aggtrade_acceptance_nautilus",
        )
        self.assertEqual(
            flow_runner.runner.base_runner.run_suite.__module__,
            "run_aggtrade_acceptance_nautilus",
        )
        source = Path(flow_runner.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "BacktestEngine(",
            "add_venue(",
            "risk_sized_quantity(",
            "default_leverage=",
            "liquidation_enabled=",
            "order_factory.bracket(",
            "submit_order_list(",
        ):
            self.assertNotIn(forbidden, source)

    def test_family_mode_uses_dedicated_environment_and_rejects_unknown_values(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FLOW_RESPONSE_AUCTION_FAMILY_MODE", None)
            self.assertEqual(flow_runner._active_family_mode(), "both")
        with patch.dict(
            os.environ,
            {"FLOW_RESPONSE_AUCTION_FAMILY_MODE": "initiative_only"},
            clear=False,
        ):
            self.assertEqual(flow_runner._active_family_mode(), "initiative_only")
        with patch.dict(
            os.environ,
            {"FLOW_RESPONSE_AUCTION_FAMILY_MODE": "fit_best_family"},
            clear=False,
        ):
            with self.assertRaises(RuntimeError):
                flow_runner._active_family_mode()

    def test_single_family_filter_removes_only_the_other_economic_family(self) -> None:
        initiative = _signal(
            scenario_id="initiative-1",
            family=INITIATIVE_FAMILY,
            timestamp=10,
        )
        absorption = _signal(
            scenario_id="absorption-1",
            family=ABSORPTION_FAMILY,
            timestamp=20,
        )
        bundle = AcceptanceSignalBundle(
            signals_by_time_ns={10: (initiative,), 20: (absorption,)},
            diagnostics={"BASE": 1},
            rejected_scenarios=(),
        )
        with patch.dict(
            os.environ,
            {"FLOW_RESPONSE_AUCTION_FAMILY_MODE": "absorption_only"},
            clear=False,
        ):
            filtered = flow_runner._filter_bundle(bundle)

        self.assertNotIn(10, filtered.signals_by_time_ns)
        self.assertEqual(filtered.signals_by_time_ns[20], (absorption,))
        self.assertEqual(filtered.diagnostics["FAMILY_MODE_REMOVED_SIGNALS"], 1)
        self.assertEqual(
            filtered.diagnostics[f"FAMILY_MODE_REMOVED_{INITIATIVE_FAMILY}"],
            1,
        )
        self.assertEqual(
            filtered.rejected_scenarios[-1]["reason"],
            "DIAGNOSTIC_FAMILY_MODE_REMOVED",
        )
        self.assertEqual(
            filtered.rejected_scenarios[-1]["auction_family_mode"],
            "absorption_only",
        )

    def test_both_mode_is_identity_and_does_not_rewrite_evidence(self) -> None:
        initiative = _signal(
            scenario_id="initiative-1",
            family=INITIATIVE_FAMILY,
            timestamp=10,
        )
        bundle = AcceptanceSignalBundle(
            signals_by_time_ns={10: (initiative,)},
            diagnostics={"BASE": 1},
            rejected_scenarios=(),
        )
        with patch.dict(
            os.environ,
            {"FLOW_RESPONSE_AUCTION_FAMILY_MODE": "both"},
            clear=False,
        ):
            self.assertIs(flow_runner._filter_bundle(bundle), bundle)

    def test_suite_summary_stamps_exact_detector_revision_and_blocks_diagnostic_promotion(self) -> None:
        original = flow_runner._original_suite_summary
        try:
            flow_runner._original_suite_summary = lambda *_args: {
                "suite_gate_passed": True,
                "promotable": True,
                "closed_trades": 0,
                "suite_gate_checks": {},
            }
            with patch.dict(
                os.environ,
                {"FLOW_RESPONSE_AUCTION_FAMILY_MODE": "initiative_only"},
                clear=False,
            ):
                summary = flow_runner._flow_response_suite_summary({}, "first", [])
        finally:
            flow_runner._original_suite_summary = original

        self.assertEqual(summary["implementation_revision"], IMPLEMENTATION_REVISION)
        self.assertEqual(summary["flow_response_family_mode"], "initiative_only")
        self.assertEqual(
            summary["scenario_contract"],
            "CAUSAL_AGGRESSIVE_FLOW_PRICE_RESPONSE_AT_COMPLETED_EXTERNAL_LIQUIDITY",
        )
        self.assertTrue(summary["diagnostic_family_ablation"])
        self.assertFalse(summary["suite_gate_passed"])
        self.assertFalse(summary["promotable"])
        self.assertFalse(
            summary["suite_gate_checks"][
                "base_contract_includes_both_flow_response_families"
            ]
        )
        self.assertTrue(
            summary["suite_gate_checks"]["complete_post_run_trade_path_diagnostics"]
        )

    def test_missing_path_diagnostic_blocks_base_promotion_evidence(self) -> None:
        original = flow_runner._original_suite_summary
        try:
            flow_runner._original_suite_summary = lambda *_args: {
                "suite_gate_passed": True,
                "promotable": True,
                "closed_trades": 1,
                "suite_gate_checks": {},
            }
            with patch.dict(
                os.environ,
                {"FLOW_RESPONSE_AUCTION_FAMILY_MODE": "both"},
                clear=False,
            ):
                summary = flow_runner._flow_response_suite_summary(
                    {},
                    "first",
                    [{"closed_trade_records": [{"scenario_id": "s1"}]}],
                )
        finally:
            flow_runner._original_suite_summary = original

        self.assertFalse(summary["suite_gate_passed"])
        self.assertFalse(
            summary["suite_gate_checks"]["complete_post_run_trade_path_diagnostics"]
        )
        self.assertEqual(
            summary["trade_path_diagnostic_summary"]["complete_records"],
            0,
        )

    def test_base_runner_uses_revision_stamped_reporting_and_post_run_hooks(self) -> None:
        self.assertIs(
            flow_runner.runner.base_runner._suite_summary,
            flow_runner._flow_response_suite_summary,
        )
        self.assertIs(
            flow_runner.runner.base_runner._global_signal_summary,
            flow_runner._flow_response_global_signal_summary,
        )
        self.assertIs(
            flow_runner.runner.base_runner.load_ten_second_aggtrades,
            flow_runner._capturing_load_ten_second_aggtrades,
        )
        self.assertIs(
            flow_runner.runner.base_runner._closed_trade_records,
            flow_runner._flow_response_closed_trade_records,
        )
        self.assertIs(
            flow_runner.runner.base_runner.run_window,
            flow_runner._flow_response_run_window,
        )


class FlowResponsePostRunPathIntegrationContracts(unittest.TestCase):
    def test_captured_frame_enriches_closed_record_after_execution_only(self) -> None:
        frame = _path_frame()
        first_time = int(frame.index[0].as_unit("ns").value)
        second_time = int(frame.index[1].as_unit("ns").value)
        intent = {
            "scenario_id": "s1",
            "scenario_family": INITIATIVE_FAMILY,
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entry_fill_time_ns": first_time,
            "entry_fill_price": 100.0,
            "structural_stop": 98.0,
            "external_target": 104.0,
            "logic_details": {"scenario_family": INITIATIVE_FAMILY},
        }
        original = flow_runner._original_closed_trade_records
        old_hold = flow_runner._CURRENT_MAXIMUM_HOLD_MINUTES
        old_frames = dict(flow_runner._CAPTURED_TEN_SECOND_FRAMES)
        try:
            flow_runner._original_closed_trade_records = lambda *_args: [
                {
                    "scenario_id": "s1",
                    "scenario_family": INITIATIVE_FAMILY,
                    "symbol": "BTCUSDT",
                    "position_close_time_ns": second_time,
                    "realized_pnl": -100.0,
                    "close_reason": "STRUCTURAL_STOP",
                }
            ]
            flow_runner._CAPTURED_TEN_SECOND_FRAMES.clear()
            flow_runner._CAPTURED_TEN_SECOND_FRAMES["BTCUSDT"] = frame
            flow_runner._CURRENT_MAXIMUM_HOLD_MINUTES = 5
            records = flow_runner._flow_response_closed_trade_records([], [intent], [])
        finally:
            flow_runner._original_closed_trade_records = original
            flow_runner._CURRENT_MAXIMUM_HOLD_MINUTES = old_hold
            flow_runner._CAPTURED_TEN_SECOND_FRAMES.clear()
            flow_runner._CAPTURED_TEN_SECOND_FRAMES.update(old_frames)

        diagnostic = records[0]["path_diagnostic"]
        self.assertEqual(diagnostic["path_diagnostic_status"], "COMPLETE")
        self.assertEqual(diagnostic["structural_first_touch"], "STOP")
        self.assertTrue(diagnostic["target_reached_after_invalidation"])

    def test_run_window_scopes_capture_and_hold_contract_to_one_native_call(self) -> None:
        original = flow_runner._original_run_window
        seen: dict[str, object] = {}
        try:
            def fake_run_window(*_args, **_kwargs):
                seen["hold"] = flow_runner._CURRENT_MAXIMUM_HOLD_MINUTES
                flow_runner._CAPTURED_TEN_SECOND_FRAMES["BTCUSDT"] = _path_frame()
                seen["captured_during"] = bool(flow_runner._CAPTURED_TEN_SECOND_FRAMES)
                return {"ok": True}

            flow_runner._original_run_window = fake_run_window
            result = flow_runner._flow_response_run_window(
                config={"maximum_hold_minutes": 240}
            )
        finally:
            flow_runner._original_run_window = original

        self.assertEqual(result, {"ok": True})
        self.assertEqual(seen["hold"], 240)
        self.assertTrue(seen["captured_during"])
        self.assertIsNone(flow_runner._CURRENT_MAXIMUM_HOLD_MINUTES)
        self.assertEqual(flow_runner._CAPTURED_TEN_SECOND_FRAMES, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)

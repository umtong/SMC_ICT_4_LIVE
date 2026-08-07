"""Native-runner, configuration and evidence contracts for delayed reacceptance."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import os
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aggtrade_acceptance_signals import AcceptanceLogicEvent
from aggtrade_delayed_reacceptance_signals_v3 import (
    ABLATION_INITIAL_MODE,
    BASE_INITIAL_MODE,
    DelayedReacceptanceConfig,
    IMPLEMENTATION_REVISION,
    REACCEPTANCE_FAMILY,
    build_delayed_reacceptance_signals,
)
from aggtrade_flow_response import FlowResponseConfig
from flow_response_trade_path_diagnostics_v2 import DIAGNOSTIC_REVISION
import run_aggtrade_delayed_reacceptance_nautilus as runner
from smc_ict_4.event_log import validate_event_file


HERE = Path(__file__).resolve().parent


def _complete_trade(*, pnl: float = 100.0) -> dict:
    return {
        "scenario_id": "scenario-1",
        "scenario_family": REACCEPTANCE_FAMILY,
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


def _logic_event(
    *,
    event_type: str,
    timestamp_ns: int,
    previous_state: str,
    next_state: str,
) -> AcceptanceLogicEvent:
    return AcceptanceLogicEvent(
        scenario_id="scenario-1",
        symbol="BTCUSDT",
        instrument_id="BTCUSDT-PERP.BINANCE",
        event_type=event_type,
        event_time_ns=timestamp_ns,
        observed_time_ns=timestamp_ns,
        previous_state=previous_state,
        next_state=next_state,
        reason_code=event_type,
        reference_price=100.0,
        details={"implementation_revision": IMPLEMENTATION_REVISION},
    )


class DelayedReacceptanceRunnerContracts(unittest.TestCase):
    def test_frozen_config_matches_detector_and_project_contract(self) -> None:
        payload = json.loads(
            (HERE / "config_delayed_reacceptance_btc_v1.json").read_text(
                encoding="utf-8"
            )
        )
        default = DelayedReacceptanceConfig()
        self.assertEqual(payload["implementation_revision"], IMPLEMENTATION_REVISION)
        self.assertEqual(payload["flow_response_config"], asdict(FlowResponseConfig()))
        self.assertEqual(
            payload["delayed_reacceptance_config"],
            {"setup_expiry_minutes": default.setup_expiry_minutes},
        )
        self.assertEqual(set(payload["assets"]), {"BTCUSDT"})
        self.assertEqual(payload["risk_fraction"], 0.03)
        self.assertEqual(payload["venue"]["default_leverage"], 125)
        self.assertNotIn("maximum_notional", payload)
        self.assertNotIn("risk_multiplier", payload)
        self.assertEqual(
            payload["screen_gate"]["combined_daily_geometric_growth"],
            0.01,
        )

    def test_detector_is_rebound_without_reimplementing_native_engine(self) -> None:
        self.assertIs(
            runner.execution.runner.build_auction_router_signals,
            build_delayed_reacceptance_signals,
        )
        self.assertIs(
            runner.execution.runner.base_runner.build_acceptance_signals,
            runner._build_signals,
        )
        self.assertIs(
            runner.execution.runner.base_runner._write_merged_events,
            runner._write_merged_events,
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

    def test_four_state_logic_chain_serializes_without_gap(self) -> None:
        events = (
            _logic_event(
                event_type="EXTERNAL_LIQUIDITY_INTERACTION_ARMED",
                timestamp_ns=10,
                previous_state="IDLE",
                next_state="INTERACTION_ARMED",
            ),
            _logic_event(
                event_type="INITIAL_OUTWARD_RESPONSE_CONFIRMED_NO_ENTRY",
                timestamp_ns=20,
                previous_state="INTERACTION_ARMED",
                next_state="INITIAL_OUTWARD_RESPONSE",
            ),
            _logic_event(
                event_type="INITIAL_RESPONSE_RECLAIMED",
                timestamp_ns=30,
                previous_state="INITIAL_OUTWARD_RESPONSE",
                next_state="BOUNDARY_RECLAIMED",
            ),
            _logic_event(
                event_type="DELAYED_OUTWARD_REACCEPTANCE_CONFIRMED",
                timestamp_ns=40,
                previous_state="BOUNDARY_RECLAIMED",
                next_state="CONFIRMED",
            ),
        )
        signal = SimpleNamespace(scenario_id="scenario-1", events=events)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "scenario_events.jsonl"
            count = runner._write_merged_events(
                path,
                signals_by_time_ns={40: (signal,)},
                execution_events=[],
            )
            written = validate_event_file(path)

        self.assertEqual(count, 4)
        self.assertEqual(len(written), 4)
        self.assertEqual(written[-1].next_state, "CONFIRMED")

    def test_initial_mode_defaults_to_base_and_rejects_search(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DELAYED_REACCEPTANCE_INITIAL_MODE", None)
            self.assertEqual(runner._active_initial_mode(), BASE_INITIAL_MODE)
        with patch.dict(
            os.environ,
            {"DELAYED_REACCEPTANCE_INITIAL_MODE": ABLATION_INITIAL_MODE},
            clear=False,
        ):
            self.assertEqual(runner._active_initial_mode(), ABLATION_INITIAL_MODE)
        with patch.dict(
            os.environ,
            {"DELAYED_REACCEPTANCE_INITIAL_MODE": "fit_best"},
            clear=False,
        ):
            with self.assertRaises(RuntimeError):
                runner._active_initial_mode()

    def test_suite_summary_attributes_single_family_and_complete_paths(self) -> None:
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
                    "by_scenario_family": {REACCEPTANCE_FAMILY: 1},
                },
                "closed_trade_records": [_complete_trade()],
            }
            with patch.dict(
                os.environ,
                {"DELAYED_REACCEPTANCE_INITIAL_MODE": BASE_INITIAL_MODE},
                clear=False,
            ):
                summary = runner._suite_summary({}, "first", [result])
        finally:
            runner._ORIGINAL_SUITE_SUMMARY = original

        self.assertTrue(summary["suite_gate_passed"])
        self.assertEqual(summary["implementation_revision"], IMPLEMENTATION_REVISION)
        self.assertEqual(summary["single_scenario_family"], REACCEPTANCE_FAMILY)
        self.assertTrue(summary["single_family_attribution_passed"])
        self.assertEqual(
            summary["trade_path_diagnostic_summary"]["diagnostic_revision_counts"],
            {DIAGNOSTIC_REVISION: 1},
        )
        self.assertTrue(
            summary["suite_gate_checks"]["base_initial_initiative_required"]
        )
        self.assertEqual(
            summary["event_chain_contract"],
            "IDLE->INTERACTION_ARMED->INITIAL_OUTWARD_RESPONSE"
            "->BOUNDARY_RECLAIMED->CONFIRMED",
        )

    def test_diagnostic_initial_mode_is_never_promotable(self) -> None:
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
                    "by_scenario_family": {REACCEPTANCE_FAMILY: 1},
                },
                "closed_trade_records": [_complete_trade()],
            }
            with patch.dict(
                os.environ,
                {"DELAYED_REACCEPTANCE_INITIAL_MODE": ABLATION_INITIAL_MODE},
                clear=False,
            ):
                summary = runner._suite_summary({}, "first", [result])
        finally:
            runner._ORIGINAL_SUITE_SUMMARY = original

        self.assertTrue(summary["diagnostic_initial_ablation"])
        self.assertFalse(summary["promotable"])
        self.assertFalse(summary["suite_gate_passed"])
        self.assertFalse(
            summary["suite_gate_checks"]["base_initial_initiative_required"]
        )

    def test_zero_trade_evidence_is_explicit_and_complete(self) -> None:
        original = runner._ORIGINAL_SUITE_SUMMARY
        try:
            runner._ORIGINAL_SUITE_SUMMARY = lambda *_args: {
                "suite_gate_passed": False,
                "promotable": True,
                "closed_trades": 0,
                "suite_gate_checks": {},
            }
            result = {
                "detector": {"signals": 0, "by_scenario_family": {}},
                "closed_trade_records": [],
            }
            with patch.dict(
                os.environ,
                {"DELAYED_REACCEPTANCE_INITIAL_MODE": BASE_INITIAL_MODE},
                clear=False,
            ):
                summary = runner._suite_summary({}, "first", [result])
        finally:
            runner._ORIGINAL_SUITE_SUMMARY = original

        self.assertTrue(summary["single_family_attribution_passed"])
        self.assertEqual(
            summary["trade_path_diagnostic_summary"]["diagnostic_revision_counts"],
            {DIAGNOSTIC_REVISION: 0},
        )
        self.assertTrue(
            summary["suite_gate_checks"]["complete_post_run_trade_path_diagnostics"]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

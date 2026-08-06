"""Source-stable contracts for the flow-response auction Nautilus wrapper."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import os
import unittest
from unittest.mock import patch

from aggtrade_acceptance_signals import AcceptanceSignalBundle
from aggtrade_flow_response_auction_signals import (
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


class FlowResponseRunnerWiringContracts(unittest.TestCase):
    def test_detector_and_family_vocabulary_are_rebound_exactly(self) -> None:
        self.assertIs(
            flow_runner.runner.build_auction_router_signals,
            build_flow_response_auction_signals,
        )
        self.assertIs(
            flow_runner.runner.base_runner.build_acceptance_signals,
            flow_runner.runner._build_router_signals,
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

    def test_execution_window_remains_the_verified_native_base_function(self) -> None:
        self.assertEqual(
            flow_runner.runner.base_runner.run_window.__module__,
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
            filtered = flow_runner.runner._filter_bundle(bundle)

        self.assertNotIn(10, filtered.signals_by_time_ns)
        self.assertEqual(filtered.signals_by_time_ns[20], (absorption,))
        self.assertEqual(filtered.diagnostics["FAMILY_MODE_REMOVED_SIGNALS"], 1)
        self.assertEqual(
            filtered.diagnostics[
                f"FAMILY_MODE_REMOVED_{INITIATIVE_FAMILY}"
            ],
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

    def test_suite_summary_stamps_exact_detector_revision(self) -> None:
        original = flow_runner._original_suite_summary
        try:
            flow_runner._original_suite_summary = lambda *_args: {
                "suite_gate_passed": False,
                "promotable": False,
                "diagnostic_family_ablation": True,
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
        self.assertFalse(summary["suite_gate_passed"])
        self.assertFalse(summary["promotable"])

    def test_base_runner_uses_the_revision_stamped_summary(self) -> None:
        self.assertIs(
            flow_runner.runner.base_runner._suite_summary,
            flow_runner._flow_response_suite_summary,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

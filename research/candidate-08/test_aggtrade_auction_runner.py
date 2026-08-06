"""Pure reporting contracts for the auction-router Nautilus wrapper."""

from __future__ import annotations

from types import SimpleNamespace
import unittest

import run_aggtrade_auction_router_nautilus as runner


class AuctionRunnerAttributionContracts(unittest.TestCase):
    def test_logic_details_override_legacy_acceptance_label(self) -> None:
        intent = {
            "scenario_family": "BREAKOUT_ACCEPTANCE_CONTINUATION",
            "logic_details": {"scenario_family": "FAILED_AUCTION_REVERSAL"},
        }
        self.assertEqual(
            runner._scenario_family_from_intent(intent),
            "FAILED_AUCTION_REVERSAL",
        )
        runner._normalize_intent_scenario_families([intent])
        self.assertEqual(intent["scenario_family"], "FAILED_AUCTION_REVERSAL")

    def test_missing_detector_family_is_explicitly_unclassified(self) -> None:
        self.assertEqual(
            runner._scenario_family_from_intent({}),
            "UNCLASSIFIED_AUCTION_SCENARIO",
        )

    def test_global_signal_summary_attributes_both_economic_families(self) -> None:
        initiative = SimpleNamespace(
            net_reward_risk=2.0,
            boundary_source="FOUR_HOUR",
            target_source="DAY",
            symbol="BTCUSDT",
            direction_name="LONG",
            details={"scenario_family": "INITIATIVE_ACCEPTANCE_CONTINUATION"},
        )
        failed = SimpleNamespace(
            net_reward_risk=1.5,
            boundary_source="DAY",
            target_source="FOUR_HOUR",
            symbol="ETHUSDT",
            direction_name="SHORT",
            details={"scenario_family": "FAILED_AUCTION_REVERSAL"},
        )
        summary = runner._auction_global_signal_summary(
            {1: (initiative,), 2: (failed,)}
        )
        self.assertEqual(summary["signals"], 2)
        self.assertEqual(
            summary["by_scenario_family"],
            {
                "FAILED_AUCTION_REVERSAL": 1,
                "INITIATIVE_ACCEPTANCE_CONTINUATION": 1,
            },
        )
        self.assertEqual(summary["auction_family_mode"], "both")

    def test_family_filter_removes_only_the_selected_economic_family(self) -> None:
        initiative = SimpleNamespace(
            scenario_id="initiative-1",
            symbol="BTCUSDT",
            signal_time_ns=10,
            details={"scenario_family": "INITIATIVE_ACCEPTANCE_CONTINUATION"},
        )
        failed = SimpleNamespace(
            scenario_id="failed-1",
            symbol="ETHUSDT",
            signal_time_ns=10,
            details={"scenario_family": "FAILED_AUCTION_REVERSAL"},
        )
        bundle = runner.AcceptanceSignalBundle(
            signals_by_time_ns={10: (initiative, failed)},
            diagnostics={"RAW": 2},
            rejected_scenarios=(),
        )

        unchanged = runner._filter_bundle_for_family_mode(bundle, mode="both")
        initiative_only = runner._filter_bundle_for_family_mode(
            bundle,
            mode="initiative_only",
        )
        failed_only = runner._filter_bundle_for_family_mode(
            bundle,
            mode="failed_auction_only",
        )

        self.assertIs(unchanged, bundle)
        self.assertEqual(initiative_only.signals_by_time_ns[10], (initiative,))
        self.assertEqual(failed_only.signals_by_time_ns[10], (failed,))
        self.assertEqual(
            initiative_only.diagnostics[
                "DIAGNOSTIC_FAMILY_ABLATION_REMOVED_SIGNALS"
            ],
            1,
        )
        self.assertEqual(
            initiative_only.rejected_scenarios[-1]["removed_family"],
            "FAILED_AUCTION_REVERSAL",
        )
        self.assertEqual(
            failed_only.rejected_scenarios[-1]["removed_family"],
            "INITIATIVE_ACCEPTANCE_CONTINUATION",
        )

    def test_unknown_family_filter_mode_is_rejected(self) -> None:
        bundle = runner.AcceptanceSignalBundle({}, {}, ())
        with self.assertRaises(ValueError):
            runner._filter_bundle_for_family_mode(bundle, mode="fit_the_week")

    def test_closed_records_receive_family_without_changing_execution_fields(self) -> None:
        original = runner._original_closed_trade_records
        try:
            runner._original_closed_trade_records = lambda *_args: [
                {
                    "scenario_id": "auction-1",
                    "realized_pnl": 125.0,
                    "close_reason": "EXTERNAL_TARGET",
                }
            ]
            intents = [
                {
                    "scenario_id": "auction-1",
                    "scenario_family": "BREAKOUT_ACCEPTANCE_CONTINUATION",
                    "logic_details": {
                        "scenario_family": "FAILED_AUCTION_REVERSAL"
                    },
                }
            ]
            records = runner._auction_closed_trade_records([], intents, [])
        finally:
            runner._original_closed_trade_records = original

        self.assertEqual(intents[0]["scenario_family"], "FAILED_AUCTION_REVERSAL")
        self.assertEqual(records[0]["scenario_family"], "FAILED_AUCTION_REVERSAL")
        self.assertEqual(records[0]["realized_pnl"], 125.0)
        self.assertEqual(records[0]["close_reason"], "EXTERNAL_TARGET")

    def test_family_execution_summary_is_complete_and_keeps_zero_trade_family(self) -> None:
        results = [
            {
                "detector": {
                    "signals": 5,
                    "by_scenario_family": {
                        "INITIATIVE_ACCEPTANCE_CONTINUATION": 3,
                        "FAILED_AUCTION_REVERSAL": 2,
                    },
                },
                "closed_trade_records": [
                    {
                        "scenario_family": "INITIATIVE_ACCEPTANCE_CONTINUATION",
                        "realized_pnl": 200.0,
                        "close_reason": "EXTERNAL_TARGET",
                        "symbol": "ETHUSDT",
                    },
                    {
                        "scenario_family": "INITIATIVE_ACCEPTANCE_CONTINUATION",
                        "realized_pnl": -75.0,
                        "close_reason": "STRUCTURAL_STOP",
                        "symbol": "BTCUSDT",
                    },
                ],
            }
        ]
        summary = runner._family_execution_summary(results)
        initiative = summary["by_family"]["INITIATIVE_ACCEPTANCE_CONTINUATION"]
        failed = summary["by_family"]["FAILED_AUCTION_REVERSAL"]

        self.assertEqual(initiative["signals"], 3)
        self.assertEqual(initiative["closed_trades"], 2)
        self.assertEqual(initiative["wins"], 1)
        self.assertEqual(initiative["losses"], 1)
        self.assertEqual(initiative["realized_pnl_usdt"], 125.0)
        self.assertEqual(
            initiative["close_reasons"],
            {"EXTERNAL_TARGET": 1, "STRUCTURAL_STOP": 1},
        )
        self.assertEqual(failed["signals"], 2)
        self.assertEqual(failed["closed_trades"], 0)
        self.assertEqual(summary["signals_attributed"], 5)
        self.assertTrue(summary["attribution_complete"])

    def test_suite_summary_adds_and_enforces_attribution_contract(self) -> None:
        original = runner._original_suite_summary
        try:
            runner._original_suite_summary = lambda *_args: {
                "closed_trades": 1,
                "promotable": True,
                "suite_gate_checks": {},
                "suite_gate_passed": True,
            }
            result = runner._auction_suite_summary(
                {},
                "first",
                [
                    {
                        "detector": {
                            "signals": 1,
                            "by_scenario_family": {
                                "FAILED_AUCTION_REVERSAL": 1
                            },
                        },
                        "closed_trade_records": [
                            {
                                "scenario_family": "FAILED_AUCTION_REVERSAL",
                                "realized_pnl": 50.0,
                                "close_reason": "EXTERNAL_TARGET",
                                "symbol": "BTCUSDT",
                            }
                        ],
                    }
                ],
            )
        finally:
            runner._original_suite_summary = original

        checks = result["scenario_attribution_checks"]
        self.assertEqual(checks["signals_attributed"], 1)
        self.assertEqual(checks["reported_signals"], 1)
        self.assertTrue(checks["all_signals_attributed"])
        self.assertEqual(checks["closed_trades_attributed"], 1)
        self.assertEqual(checks["reported_closed_trades"], 1)
        self.assertTrue(checks["all_closed_trades_attributed"])
        self.assertTrue(checks["no_unclassified_signals"])
        self.assertTrue(checks["no_unclassified_closed_trades"])
        self.assertTrue(result["scenario_attribution_passed"])
        self.assertTrue(
            result["suite_gate_checks"]["complete_auction_scenario_attribution"]
        )
        self.assertTrue(
            result["suite_gate_checks"][
                "base_contract_includes_both_auction_families"
            ]
        )
        self.assertTrue(result["promotable"])
        self.assertTrue(result["suite_gate_passed"])

    def test_unclassified_signal_blocks_an_otherwise_passing_suite(self) -> None:
        original = runner._original_suite_summary
        try:
            runner._original_suite_summary = lambda *_args: {
                "closed_trades": 0,
                "promotable": True,
                "suite_gate_checks": {},
                "suite_gate_passed": True,
            }
            result = runner._auction_suite_summary(
                {},
                "first",
                [
                    {
                        "detector": {
                            "signals": 1,
                            "by_scenario_family": {
                                "UNCLASSIFIED_AUCTION_SCENARIO": 1
                            },
                        },
                        "closed_trade_records": [],
                    }
                ],
            )
        finally:
            runner._original_suite_summary = original

        self.assertFalse(result["scenario_attribution_passed"])
        self.assertFalse(result["suite_gate_passed"])

    def test_single_family_mode_is_diagnostic_and_never_promotable(self) -> None:
        original_summary = runner._original_suite_summary
        original_mode = runner.FAMILY_MODE
        try:
            runner._original_suite_summary = lambda *_args: {
                "closed_trades": 0,
                "promotable": True,
                "suite_gate_checks": {},
                "suite_gate_passed": True,
            }
            runner.FAMILY_MODE = "initiative_only"
            result = runner._auction_suite_summary(
                {},
                "first",
                [
                    {
                        "detector": {
                            "signals": 0,
                            "by_scenario_family": {},
                        },
                        "closed_trade_records": [],
                    }
                ],
            )
        finally:
            runner._original_suite_summary = original_summary
            runner.FAMILY_MODE = original_mode

        self.assertEqual(result["auction_family_mode"], "initiative_only")
        self.assertTrue(result["diagnostic_family_ablation"])
        self.assertFalse(result["promotable"])
        self.assertFalse(
            result["suite_gate_checks"][
                "base_contract_includes_both_auction_families"
            ]
        )
        self.assertFalse(result["suite_gate_passed"])

    def test_wrapper_patches_reporting_but_reuses_base_execution_entrypoint(self) -> None:
        self.assertIs(runner.base_runner.build_acceptance_signals, runner._build_router_signals)
        self.assertIs(runner.base_runner._position_metrics, runner._auction_position_metrics)
        self.assertIs(runner.base_runner._closed_trade_records, runner._auction_closed_trade_records)
        self.assertIs(runner.base_runner._global_signal_summary, runner._auction_global_signal_summary)
        self.assertIs(runner.base_runner._suite_summary, runner._auction_suite_summary)


if __name__ == "__main__":
    unittest.main(verbosity=2)

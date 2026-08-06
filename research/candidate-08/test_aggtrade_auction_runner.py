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
                    "by_scenario_family": {
                        "INITIATIVE_ACCEPTANCE_CONTINUATION": 3,
                        "FAILED_AUCTION_REVERSAL": 2,
                    }
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
        self.assertTrue(summary["attribution_complete"])

    def test_suite_summary_adds_auditable_attribution_contract(self) -> None:
        original = runner._original_suite_summary
        try:
            runner._original_suite_summary = lambda *_args: {"closed_trades": 1}
            result = runner._auction_suite_summary(
                {},
                "first",
                [
                    {
                        "detector": {
                            "by_scenario_family": {
                                "FAILED_AUCTION_REVERSAL": 1
                            }
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
        self.assertEqual(checks["closed_trades_attributed"], 1)
        self.assertEqual(checks["reported_closed_trades"], 1)
        self.assertTrue(checks["all_closed_trades_attributed"])
        self.assertTrue(checks["no_unclassified_closed_trades"])

    def test_wrapper_patches_reporting_but_reuses_base_execution_entrypoint(self) -> None:
        self.assertIs(runner.base_runner.build_acceptance_signals, runner.build_auction_router_signals)
        self.assertIs(runner.base_runner._position_metrics, runner._auction_position_metrics)
        self.assertIs(runner.base_runner._closed_trade_records, runner._auction_closed_trade_records)
        self.assertIs(runner.base_runner._global_signal_summary, runner._auction_global_signal_summary)
        self.assertIs(runner.base_runner._suite_summary, runner._auction_suite_summary)


if __name__ == "__main__":
    unittest.main(verbosity=2)

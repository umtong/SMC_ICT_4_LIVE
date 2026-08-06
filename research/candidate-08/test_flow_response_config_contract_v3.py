"""Machine-readable V3 configuration contracts for the BTC flow-response candidate."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import unittest

from aggtrade_flow_response import FlowResponseConfig
from aggtrade_flow_response_auction_signals_v3 import (
    IMPLEMENTATION_REVISION,
    FlowResponseAuctionConfig,
)
from flow_response_trade_path_diagnostics_v2 import DIAGNOSTIC_REVISION


HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config_flow_response_auction_btc_v1.json"
RUNNER_PATH = HERE / "run_aggtrade_flow_response_auction_nautilus.py"


class FlowResponseV3ConfigContracts(unittest.TestCase):
    def test_json_matches_exact_frozen_detector_and_evidence_revisions(self) -> None:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(payload["implementation_revision"], IMPLEMENTATION_REVISION)
        self.assertEqual(
            payload["ten_second_cadence_contract"],
            "EXACT_CONSECUTIVE_10_SECONDS",
        )
        self.assertEqual(
            payload["trade_path_diagnostic_revision"],
            DIAGNOSTIC_REVISION,
        )
        self.assertEqual(payload["flow_response_config"], asdict(FlowResponseConfig()))
        auction = FlowResponseAuctionConfig()
        self.assertEqual(
            payload["flow_response_auction_config"],
            {
                "interaction_response_windows": auction.interaction_response_windows,
                "reversal_confirmation_windows": auction.reversal_confirmation_windows,
            },
        )

    def test_json_round_trip_constructs_the_frozen_config(self) -> None:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        response = FlowResponseConfig(**payload["flow_response_config"])
        auction = FlowResponseAuctionConfig(
            response=response,
            **payload["flow_response_auction_config"],
        )
        auction.validate()
        self.assertEqual(auction.response.response_window_bars, 3)
        self.assertEqual(auction.interaction_expiry_bars, 9)
        self.assertEqual(auction.reversal_expiry_bars, 6)

    def test_native_wrapper_injects_config_and_complete_horizon_diagnostics(self) -> None:
        source = RUNNER_PATH.read_text(encoding="utf-8")
        self.assertIn('kwargs["auction_config"] = _load_auction_config()', source)
        self.assertIn("aggtrade_flow_response_auction_signals_v3", source)
        self.assertIn("flow_response_trade_path_diagnostics_v2", source)
        self.assertIn("complete_post_run_trade_path_diagnostics", source)
        self.assertIn("trade_path_diagnostic_revision", source)

    def test_project_risk_and_execution_contracts_remain_exact(self) -> None:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(payload["risk_fraction"], 0.03)
        self.assertEqual(payload["effective_fee_rate_per_fill"], 0.0006)
        self.assertEqual(payload["minimum_net_reward_risk"], 1.2)
        self.assertEqual(payload["venue"]["default_leverage"], 125)
        self.assertTrue(payload["venue"]["liquidation_enabled"])
        self.assertEqual(list(payload["assets"]), ["BTCUSDT"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

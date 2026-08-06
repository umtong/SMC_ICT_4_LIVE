"""Machine-readable configuration contracts for the BTC flow-response candidate."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import unittest

from aggtrade_flow_response import FlowResponseConfig
from aggtrade_flow_response_auction_signals_v2 import (
    IMPLEMENTATION_REVISION,
    FlowResponseAuctionConfig,
)


HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config_flow_response_auction_btc_v1.json"
RUNNER_PATH = HERE / "run_aggtrade_flow_response_auction_nautilus.py"


class FlowResponseConfigContracts(unittest.TestCase):
    def test_json_matches_the_exact_frozen_detector_defaults(self) -> None:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(payload["implementation_revision"], IMPLEMENTATION_REVISION)
        self.assertEqual(payload["flow_response_config"], asdict(FlowResponseConfig()))
        auction = FlowResponseAuctionConfig()
        self.assertEqual(
            payload["flow_response_auction_config"],
            {
                "interaction_response_windows": auction.interaction_response_windows,
                "reversal_confirmation_windows": auction.reversal_confirmation_windows,
            },
        )

    def test_json_round_trip_constructs_a_valid_frozen_config(self) -> None:
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

    def test_native_wrapper_injects_the_frozen_config_into_every_detector_call(self) -> None:
        source = RUNNER_PATH.read_text(encoding="utf-8")
        self.assertIn('kwargs["auction_config"] = _load_auction_config()', source)
        self.assertIn('FLOW_RESPONSE_AUCTION_CONFIG_PATH', source)
        self.assertIn('flow-response implementation/config revision mismatch', source)

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

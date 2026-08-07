"""Frozen configuration contracts for the intrinsic repricing successor."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import unittest

from aggtrade_flow_response import FlowResponseConfig
from aggtrade_intrinsic_repricing_signals import (
    IMPLEMENTATION_REVISION,
    IntrinsicRepricingConfig,
)
from flow_response_trade_path_diagnostics_v2 import DIAGNOSTIC_REVISION


HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config_intrinsic_repricing_btc_v1.json"


class IntrinsicRepricingConfigContracts(unittest.TestCase):
    def test_json_matches_exact_default_detector_contract(self) -> None:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        detector = IntrinsicRepricingConfig()
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
        self.assertEqual(
            payload["intrinsic_repricing_config"],
            {"maximum_event_bars": detector.maximum_event_bars},
        )
        detector.validate()

    def test_project_risk_and_btc_first_contract_are_unchanged(self) -> None:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(set(payload["assets"]), {"BTCUSDT"})
        self.assertEqual(payload["risk_fraction"], 0.03)
        self.assertEqual(payload["effective_fee_rate_per_fill"], 0.0006)
        self.assertEqual(payload["minimum_net_reward_risk"], 1.2)
        self.assertEqual(payload["venue"]["default_leverage"], 125)
        self.assertTrue(payload["venue"]["liquidation_enabled"])
        self.assertNotIn("maximum_notional", payload)
        self.assertNotIn("leverage_cap", payload)
        self.assertNotIn("risk_multiplier", payload)

    def test_fixed_windows_and_growth_gate_are_not_changed(self) -> None:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["suites"]["screen"],
            [
                {
                    "name": "screen-01",
                    "start": "2024-04-08T00:00:00Z",
                    "end": "2024-04-15T00:00:00Z",
                },
                {
                    "name": "screen-02",
                    "start": "2025-06-09T00:00:00Z",
                    "end": "2025-06-16T00:00:00Z",
                },
                {
                    "name": "screen-03",
                    "start": "2025-09-29T00:00:00Z",
                    "end": "2025-10-06T00:00:00Z",
                },
            ],
        )
        self.assertEqual(
            payload["screen_gate"]["combined_daily_geometric_growth"],
            0.01,
        )
        self.assertEqual(payload["screen_gate"]["minimum_closed_trades_per_week"], 3)
        self.assertEqual(payload["screen_gate"]["minimum_positive_trade_share"], 0.45)
        self.assertEqual(payload["screen_gate"]["maximum_single_positive_pnl_share"], 0.5)

    def test_source_has_no_parameter_search_or_asset_specific_override(self) -> None:
        source = (HERE / "aggtrade_intrinsic_repricing_signals.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "grid_search",
            "optuna",
            "bayes_opt",
            "best_parameter",
            "BTCUSDT" + ":",
            "ETHUSDT" + ":",
            "SOLUSDT" + ":",
            "XRPUSDT" + ":",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

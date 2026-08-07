from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import cross_market_information_transfer_compiler_v2 as candidate


class AllowedSymbolConfigTests(unittest.TestCase):
    def base_values(self) -> dict:
        return json.loads(
            Path("research/candidate-04/inventory_transfer_config.json").read_text()
        )

    def write(self, values: dict) -> Path:
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            delete=False,
            encoding="utf-8",
        )
        json.dump(values, handle)
        handle.close()
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)
        return Path(handle.name)

    def test_allowed_follower_preserves_all_other_config_fields(self) -> None:
        values = self.base_values()
        values["symbol"] = "ETHUSDT"
        config = candidate.load_allowed_symbol_config(self.write(values))
        self.assertEqual(config.symbol, "ETHUSDT")
        self.assertEqual(config.risk_fraction, 0.03)
        self.assertEqual(config.fee_bps, values["fee_bps"])
        self.assertEqual(config.stop_slippage_bps, values["stop_slippage_bps"])

    def test_unknown_symbol_remains_rejected(self) -> None:
        values = self.base_values()
        values["symbol"] = "DOGEUSDT"
        with self.assertRaises(candidate.base.v22.CandidateError):
            candidate.load_allowed_symbol_config(self.write(values))

    def test_non_symbol_validation_is_not_bypassed(self) -> None:
        values = self.base_values()
        values["symbol"] = "SOLUSDT"
        values["risk_fraction"] = 0.031
        with self.assertRaises(candidate.base.v22.CandidateError):
            candidate.load_allowed_symbol_config(self.write(values))

    def test_unknown_config_key_remains_rejected(self) -> None:
        values = self.base_values()
        values["symbol"] = "XRPUSDT"
        values["unapproved_parameter"] = 1
        with self.assertRaises(candidate.base.v22.CandidateError):
            candidate.load_allowed_symbol_config(self.write(values))


if __name__ == "__main__":
    unittest.main()

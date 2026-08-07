from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

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
        self.assertEqual(
            config.stress_inventory_quantile,
            values["stress_inventory_quantile"],
        )

    def test_saved_original_loader_survives_runtime_adapter_patch(self) -> None:
        descriptor = candidate.base.v22.Config.__dict__["load"]
        candidate.base.v22.Config.load = classmethod(
            lambda cls, path: (_ for _ in ()).throw(
                AssertionError("runtime adapter called recursively")
            )
        )
        try:
            values = self.base_values()
            values["symbol"] = "XRPUSDT"
            config = candidate.load_allowed_symbol_config(self.write(values))
        finally:
            candidate.base.v22.Config.load = descriptor
        self.assertEqual(config.symbol, "XRPUSDT")

    def test_unknown_symbol_remains_rejected(self) -> None:
        values = self.base_values()
        values["symbol"] = "DOGEUSDT"
        with self.assertRaises(candidate.CandidateError):
            candidate.load_allowed_symbol_config(self.write(values))

    def test_non_symbol_validation_is_not_bypassed(self) -> None:
        values = self.base_values()
        values["symbol"] = "SOLUSDT"
        values["risk_fraction"] = 0.031
        with self.assertRaises(candidate.CandidateError):
            candidate.load_allowed_symbol_config(self.write(values))

    def test_unknown_config_key_remains_rejected(self) -> None:
        values = self.base_values()
        values["symbol"] = "XRPUSDT"
        values["unapproved_parameter"] = 1
        with self.assertRaises(candidate.CandidateError):
            candidate.load_allowed_symbol_config(self.write(values))


class SymbolAwareRichLoaderTests(unittest.TestCase):
    def directory(self, name: str) -> Path:
        root = Path(tempfile.mkdtemp()) / name
        root.mkdir(parents=True)
        self.addCleanup(
            lambda: __import__("shutil").rmtree(root.parent, ignore_errors=True)
        )
        return root

    def test_allowed_follower_file_is_loaded_with_close_observed_contract(self) -> None:
        root = self.directory("ETHUSDT")
        frame = pd.DataFrame(
            {
                "open_time": ["2025-07-21T00:00:00Z"],
                "observed_time": ["2025-07-21T00:01:00Z"],
                "value": [1.0],
            }
        )
        frame.to_csv(
            root / "ETHUSDT-rich-2025-07-21.csv.gz",
            index=False,
            compression="gzip",
        )
        loaded = candidate.load_allowed_symbol_rich(root)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(float(loaded["value"].iloc[0]), 1.0)

    def test_wrong_symbol_filename_does_not_silently_load(self) -> None:
        root = self.directory("SOLUSDT")
        frame = pd.DataFrame(
            {
                "open_time": ["2025-07-21T00:00:00Z"],
                "observed_time": ["2025-07-21T00:01:00Z"],
            }
        )
        frame.to_csv(
            root / "BTCUSDT-rich-2025-07-21.csv.gz",
            index=False,
            compression="gzip",
        )
        with self.assertRaises(candidate.CandidateError):
            candidate.load_allowed_symbol_rich(root)

    def test_future_observed_timestamp_remains_rejected(self) -> None:
        root = self.directory("XRPUSDT")
        frame = pd.DataFrame(
            {
                "open_time": ["2025-07-21T00:00:00Z"],
                "observed_time": ["2025-07-21T00:02:00Z"],
            }
        )
        frame.to_csv(
            root / "XRPUSDT-rich-2025-07-21.csv.gz",
            index=False,
            compression="gzip",
        )
        with self.assertRaises(candidate.CandidateError):
            candidate.load_allowed_symbol_rich(root)


if __name__ == "__main__":
    unittest.main()

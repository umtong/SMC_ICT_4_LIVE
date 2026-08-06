from __future__ import annotations

from datetime import date
import ast
import json
from pathlib import Path
import tempfile
import unittest

from nautilus_trader.backtest.config import BacktestVenueConfig

import nt_multi_asset_rich_backtest as base
import nt_multi_asset_rich_backtest_v2 as candidate
import nt_trusted_execution_factory as factory


class TrustedVenueFactoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = json.loads(
            Path(
                factory.__file__
            ).with_name("nt_liquidity_config.json").read_text()
        )

    def test_exact_trusted_venue_constructor_is_extracted(self) -> None:
        call_source, dependencies = factory.trusted_source_contract()
        expression = ast.parse(call_source, mode="eval")
        call = expression.body
        self.assertIsInstance(call, ast.Call)
        assert isinstance(call, ast.Call)
        self.assertEqual(factory.call_name(call), "BacktestVenueConfig")
        self.assertTrue(all(isinstance(item, str) for item in dependencies))
        self.assertFalse(
            any(
                forbidden in source
                for forbidden in factory.FORBIDDEN_CALLS
                for source in dependencies
            )
        )

    def test_factory_returns_real_nautilus_venue_config(self) -> None:
        venue = factory.make_trusted_venue_config(self.config)
        self.assertIsInstance(venue, BacktestVenueConfig)

    def test_contract_evidence_declares_no_execution_model_change(self) -> None:
        evidence = factory.execution_contract_evidence()
        self.assertFalse(evidence["execution_model_modified"])
        self.assertFalse(evidence["market_or_performance_data_accessed"])
        self.assertIn("BacktestVenueConfig", evidence["venue_constructor_ast"])


class FourInstrumentRunConfigTests(unittest.TestCase):
    def test_v2_uses_trusted_venue_and_four_data_streams(self) -> None:
        config = json.loads(
            Path(candidate.__file__).with_name("nt_liquidity_config.json").read_text()
        )
        candidate._TRUSTED_CONFIG = config
        with tempfile.TemporaryDirectory() as temp:
            run_config = candidate.build_run_config(
                Path(temp) / "catalog",
                [],
                date(2025, 1, 1),
                date(2025, 1, 2),
                float(config["starting_nav"]),
            )
        self.assertEqual(len(run_config.venues), 1)
        self.assertEqual(len(run_config.data), len(base.SYMBOLS))
        expected = {str(base.instrument_id(symbol)) for symbol in base.SYMBOLS}
        observed = {
            str(getattr(item, "instrument_id", ""))
            for item in run_config.data
        }
        self.assertEqual(observed, expected)


if __name__ == "__main__":
    unittest.main()

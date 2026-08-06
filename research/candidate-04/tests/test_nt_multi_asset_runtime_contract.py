from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

import nt_multi_asset_rich_backtest as candidate
from nt_rich_signal_strategy import RichSignalStrategy


class MultiAssetNautilusContractTests(unittest.TestCase):
    def setUp(self) -> None:
        config_path = Path(candidate.__file__).with_name("nt_liquidity_config.json")
        self.config = json.loads(config_path.read_text(encoding="utf-8"))

    def test_all_instruments_construct_under_pinned_nautilus(self) -> None:
        instruments = [
            candidate.make_instrument(
                symbol,
                float(self.config["all_in_cost_bps_each_side"]),
            )
            for symbol in candidate.SYMBOLS
        ]
        self.assertEqual(len(instruments), 4)
        self.assertEqual(
            {str(item.id) for item in instruments},
            {str(candidate.instrument_id(symbol)) for symbol in candidate.SYMBOLS},
        )

    def test_four_strategy_configs_and_run_config_construct(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            signals = root / "signals"
            strategy_root = root / "strategy"
            for symbol in candidate.SYMBOLS:
                path = signals / symbol
                path.mkdir(parents=True, exist_ok=True)
                (path / "signals.json").write_text("[]\n", encoding="utf-8")
            strategies = [
                candidate.strategy_config(
                    symbol,
                    self.config,
                    signals,
                    strategy_root,
                    "runtime-contract-test",
                )
                for symbol in candidate.SYMBOLS
            ]
            self.assertEqual(len(strategies), 4)
            run_config = candidate.build_run_config(
                root / "catalog",
                strategies,
                date(2025, 1, 1),
                date(2025, 1, 2),
                float(self.config["starting_nav"]),
            )
            self.assertIsNotNone(run_config)

    def test_parent_strategy_has_cancel_callback_used_by_global_release(self) -> None:
        self.assertTrue(hasattr(RichSignalStrategy, "on_order_canceled"))

    def test_global_config_declares_portfolio_coordinator_fields(self) -> None:
        fields = candidate.struct_fields(candidate.GlobalRichSignalConfig)
        self.assertIn("global_instrument_ids", fields)
        self.assertIn("coordinator_key", fields)
        self.assertIn("signals_path", fields)


if __name__ == "__main__":
    unittest.main()

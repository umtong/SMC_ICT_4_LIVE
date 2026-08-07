from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from nautilus_trader.trading.config import StrategyFactory

import nt_multi_asset_rich_backtest_v44 as candidate


class EvaluationBoundsTests(unittest.TestCase):
    def test_declared_week_becomes_inclusive_utc_nanosecond_bounds(self) -> None:
        start, end = candidate.evaluation_bounds_ns(
            [
                "runner",
                "--evaluation-start",
                "2025-07-21",
                "--evaluation-end",
                "2025-07-27",
            ]
        )
        self.assertEqual(start, int(pd.Timestamp("2025-07-21", tz="UTC").value))
        self.assertEqual(
            end,
            int(
                (
                    pd.Timestamp("2025-07-28", tz="UTC")
                    - pd.Timedelta(nanoseconds=1)
                ).value
            ),
        )

    def test_reversed_bounds_are_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            candidate.evaluation_bounds_ns(
                [
                    "runner",
                    "--evaluation-start=2025-07-28",
                    "--evaluation-end=2025-07-27",
                ]
            )


class StrategyConstructionTests(unittest.TestCase):
    def test_explicit_strategy_id_is_removed_before_factory_creation(self) -> None:
        config = json.loads(
            Path(candidate.__file__).with_name("nt_liquidity_config.json").read_text()
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            signal_root = root / "signals"
            strategy_root = root / "strategies"
            (signal_root / "ETHUSDT").mkdir(parents=True)
            (signal_root / "ETHUSDT" / "signals.json").write_text("[]\n")
            with patch.object(
                candidate.sys,
                "argv",
                [
                    "runner",
                    "--evaluation-start",
                    "2025-07-21",
                    "--evaluation-end",
                    "2025-07-27",
                ],
            ):
                imported = candidate._strategy_config_with_evaluation_bounds(
                    "ETHUSDT",
                    config,
                    signal_root,
                    strategy_root,
                    "test-coordinator",
                )
            self.assertNotIn("strategy_id", imported.config)
            strategy = StrategyFactory.create(imported)
            self.assertEqual(str(strategy.config.instrument_id), "ETHUSDT-PERP.BINANCE")
            self.assertEqual(
                strategy.config.evaluation_start_ns,
                int(pd.Timestamp("2025-07-21", tz="UTC").value),
            )


if __name__ == "__main__":
    unittest.main()

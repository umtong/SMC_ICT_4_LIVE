from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from shared_account_backtest import PROJECT_SYMBOLS
from shared_account_backtest import SharedAccountError
from shared_account_backtest import aggregate_scenarios
from shared_account_backtest import load_validated_winner
from shared_account_backtest import normalize_equity_files
from shared_account_backtest import position_pnls


class SharedAccountBacktestContractTest(unittest.TestCase):
    def test_only_exact_control_validated_winner_is_accepted(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "winner.json"
            path.write_text(
                json.dumps(
                    {
                        "classification": "VALIDATED_BTC_WINNER_RESOLVED",
                        "winner": "strategy_v26:ScenarioValidEntryStrategy",
                    },
                ),
                encoding="utf-8",
            )
            payload, winner = load_validated_winner(path)
            self.assertEqual(winner, "strategy_v26:ScenarioValidEntryStrategy")
            self.assertEqual(payload["classification"], "VALIDATED_BTC_WINNER_RESOLVED")

            path.write_text(
                json.dumps(
                    {
                        "classification": "BTC_91D_ALPHA_GATE_PASSED",
                        "winner": "strategy_v26:ScenarioValidEntryStrategy",
                    },
                ),
                encoding="utf-8",
            )
            with self.assertRaises(SharedAccountError):
                load_validated_winner(path)

    def test_four_equity_streams_form_one_daily_nav_sequence(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for symbol_index, symbol in enumerate(PROJECT_SYMBOLS):
                output = root / "symbols" / symbol
                output.mkdir(parents=True)
                frame = pd.DataFrame(
                    {
                        "ts_event": [
                            pd.Timestamp("2024-03-01T00:00:00Z").value,
                            pd.Timestamp("2024-03-02T00:00:00Z").value,
                            pd.Timestamp("2024-03-03T00:00:00Z").value,
                        ],
                        "equity": [
                            100_000.0,
                            101_000.0 + symbol_index,
                            102_000.0 + symbol_index,
                        ],
                    },
                )
                frame.to_csv(output / "equity.csv", index=False)

            selected, daily, drawdown, minimum = normalize_equity_files(
                output=root,
                evaluation_start=date(2024, 3, 1),
                evaluation_end=date(2024, 3, 2),
                starting_nav=100_000.0,
                ending_nav=102_003.0,
            )
            self.assertFalse(selected.empty)
            self.assertAlmostEqual(daily["2024-03-01"], 0.01)
            self.assertAlmostEqual(
                daily["2024-03-02"],
                102_003.0 / 101_000.0 - 1.0,
            )
            self.assertGreaterEqual(drawdown, 0.0)
            self.assertEqual(minimum, 100_000.0)

    def test_position_pnl_and_scenario_aggregation(self) -> None:
        positions = pd.DataFrame(
            {
                "instrument_id": [
                    "BTCUSDT-PERP.BINANCE",
                    "ETHUSDT-PERP.BINANCE",
                ],
                "realized_pnl": ["1200.00 USDT", "-500.00 USDT"],
            },
        )
        self.assertEqual(position_pnls(positions), [1200.0, -500.0])

        records = [
            {
                "symbol": "BTCUSDT",
                "branch": "A",
                "realized_pnl_number": 1200.0,
            },
            {
                "symbol": "ETHUSDT",
                "branch": "A",
                "realized_pnl_number": -500.0,
            },
            {
                "symbol": "SOLUSDT",
                "branch": "B",
                "realized_pnl_number": 300.0,
            },
        ]
        branches, symbols = aggregate_scenarios(records)
        self.assertEqual(branches["A"]["trades"], 2)
        self.assertEqual(branches["A"]["wins"], 1)
        self.assertEqual(branches["A"]["net_pnl"], 700.0)
        self.assertEqual(symbols["SOLUSDT"]["net_pnl"], 300.0)


if __name__ == "__main__":
    unittest.main()

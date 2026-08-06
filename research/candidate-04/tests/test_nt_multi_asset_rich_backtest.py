from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

import nt_multi_asset_rich_backtest as candidate


class InstrumentContractTests(unittest.TestCase):
    def test_all_four_instruments_have_distinct_ids_and_bar_types(self) -> None:
        ids = {str(candidate.instrument_id(symbol)) for symbol in candidate.SYMBOLS}
        bars = {str(candidate.bar_type(symbol)) for symbol in candidate.SYMBOLS}
        self.assertEqual(len(ids), 4)
        self.assertEqual(len(bars), 4)
        self.assertTrue(all(value.endswith(".BINANCE") for value in ids))


class EventMergeTests(unittest.TestCase):
    def test_json_rows_are_sorted_by_event_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "first.json"
            second = root / "second.json"
            output = root / "merged.json"
            first.write_text(json.dumps([{"ts_event": 20}, {"ts_event": 40}]))
            second.write_text(json.dumps([{"ts_event": 10}, {"ts_event": 30}]))
            rows = candidate.merge_json_rows([first, second], output)
            self.assertEqual([row["ts_event"] for row in rows], [10, 20, 30, 40])
            self.assertEqual(json.loads(output.read_text()), rows)

    def test_equity_uses_last_strategy_snapshot_at_same_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "first.csv"
            second = root / "second.csv"
            output = root / "equity.csv"
            pd.DataFrame(
                {"ts_event": [1, 2], "equity": [100.0, 101.0]}
            ).to_csv(first, index=False)
            pd.DataFrame(
                {"ts_event": [1, 2], "equity": [100.5, 102.0]}
            ).to_csv(second, index=False)
            frame = candidate.merge_equity([first, second], output)
            self.assertEqual(frame["equity"].tolist(), [100.5, 102.0])


class RiskEvidenceTests(unittest.TestCase):
    def test_realized_loss_fraction_uses_entry_nav(self) -> None:
        positions = pd.DataFrame(
            {"realized_pnl": ["-3_000.0 USDT", "2_000.0 USDT"]}
        )
        events = [
            {"event_type": "ENTRY_SUBMITTED", "details": {"equity": 100_000.0}},
            {"event_type": "ENTRY_SUBMITTED", "details": {"equity": 97_000.0}},
        ]
        self.assertAlmostEqual(
            candidate.realized_loss_fraction(positions, events),
            0.03,
            places=12,
        )

    def test_mismatched_position_evidence_is_not_silently_accepted(self) -> None:
        positions = pd.DataFrame({"realized_pnl": ["-1.0 USDT"]})
        self.assertTrue(
            pd.isna(candidate.realized_loss_fraction(positions, []))
        )


class ConfigFilteringTests(unittest.TestCase):
    def test_accepted_kwargs_removes_unknown_fields(self) -> None:
        class Example:
            def __init__(self, required: int, optional: int = 0) -> None:
                self.required = required
                self.optional = optional

        result = candidate.accepted_kwargs(
            Example,
            {"required": 1, "optional": 2, "unknown": 3},
        )
        self.assertEqual(result, {"required": 1, "optional": 2})


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from pathlib import Path
import sys
import unittest

import pandas as pd

CANDIDATE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_DIR))

from diagnose_parent_initiative_continuation import (  # noqa: E402
    continuation_acceptance,
    continuation_geometry,
    first_reversal_barrier,
    path_outcome,
)
from model import Direction  # noqa: E402


class ParentInitiativeContinuationTests(unittest.TestCase):
    @staticmethod
    def _minutes(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
        records = []
        base = 1_000_000_000_000
        for index, (open_, high, low, close) in enumerate(rows):
            records.append(
                {
                    "timestamp_ns": base + index * 60_000_000_000,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": 1.0,
                }
            )
        return pd.DataFrame.from_records(records)

    def test_reversal_target_prevents_continuation(self) -> None:
        minutes = self._minutes(
            [
                (100.0, 101.0, 94.0, 95.0),
                (95.0, 96.0, 90.0, 91.0),
            ]
        )
        result = first_reversal_barrier(
            minutes,
            direction="SHORT",
            opened_ns=int(minutes.iloc[0]["timestamp_ns"]),
            stop=105.0,
            target=94.0,
            signal_minutes=5,
        )
        self.assertEqual(result.outcome, "REVERSAL_TARGET_FIRST")

    def test_extended_horizon_changes_only_stop_observation_window(self) -> None:
        rows = [(100.0, 101.0, 99.0, 100.0)] * 7
        rows.append((100.0, 106.0, 99.0, 105.0))
        minutes = self._minutes(rows)
        opened_ns = int(minutes.iloc[0]["timestamp_ns"])
        baseline = first_reversal_barrier(
            minutes,
            direction="SHORT",
            opened_ns=opened_ns,
            stop=105.0,
            target=94.0,
            signal_minutes=5,
        )
        extended = first_reversal_barrier(
            minutes,
            direction="SHORT",
            opened_ns=opened_ns,
            stop=105.0,
            target=94.0,
            signal_minutes=120,
        )
        self.assertEqual(baseline.outcome, "NO_BARRIER_IN_SIGNAL_SHOCK")
        self.assertEqual(extended.outcome, "REVERSAL_STOP_FIRST")
        self.assertEqual(extended.index, 7)

    def test_stop_then_next_bar_acceptance_is_causal(self) -> None:
        minutes = self._minutes(
            [
                (100.0, 106.0, 99.0, 104.0),
                (104.0, 107.0, 103.0, 106.0),
                (106.0, 108.0, 105.0, 107.0),
            ]
        )
        barrier = first_reversal_barrier(
            minutes,
            direction="SHORT",
            opened_ns=int(minutes.iloc[0]["timestamp_ns"]),
            stop=105.0,
            target=95.0,
            signal_minutes=5,
        )
        self.assertEqual(barrier.outcome, "REVERSAL_STOP_FIRST")
        assert barrier.index is not None
        outcome, index, _, bars_seen = continuation_acceptance(
            minutes,
            stop_index=barrier.index,
            source_scenario_id="source",
            direction=Direction.LONG,
            liquidity_level=100.0,
            acceptance_level=105.0,
            atr=2.0,
            timeout_bars=3,
        )
        self.assertEqual(outcome, "CONFIRMED")
        self.assertEqual(index, 1)
        self.assertEqual(bars_seen, 1)

    def test_continuation_geometry_reuses_existing_contract(self) -> None:
        geometry = continuation_geometry(
            direction=Direction.LONG,
            confirmation_entry=106.0,
            liquidity_level=100.0,
            atr=5.0,
            stop_buffer_atr=0.1,
            minimum_stop_atr=1.0,
            maximum_stop_atr=1.6,
            target_rr=2.2,
        )
        self.assertIsNotNone(geometry)
        assert geometry is not None
        stop, target = geometry
        self.assertAlmostEqual(stop, 99.5)
        self.assertAlmostEqual(target, 120.3)

    def test_path_stops_at_first_terminal_event(self) -> None:
        minutes = self._minutes(
            [
                (100.0, 101.0, 99.0, 100.0),
                (100.0, 103.0, 99.5, 102.0),
                (102.0, 104.0, 97.0, 98.0),
                (98.0, 120.0, 98.0, 119.0),
            ]
        )
        result = path_outcome(
            minutes,
            entry_index=0,
            direction="LONG",
            entry=100.0,
            stop=98.0,
            target=110.0,
            maximum_hold_minutes=10,
        )
        self.assertEqual(result["outcome"], "STOP")
        self.assertEqual(result["terminal_index"], 2)


if __name__ == "__main__":
    unittest.main()

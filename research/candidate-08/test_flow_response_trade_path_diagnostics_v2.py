"""Complete-horizon contracts for post-run structural path diagnostics V2."""

from __future__ import annotations

import unittest

import pandas as pd

from flow_response_trade_path_diagnostics_v2 import (
    DIAGNOSTIC_REVISION,
    diagnose_trade_path,
    enrich_closed_trade_records,
)


def _frame(*, periods: int = 8, gap_position: int | None = None) -> pd.DataFrame:
    index = list(pd.date_range("2024-01-01T00:00:10Z", periods=periods, freq="10s"))
    if gap_position is not None:
        for position in range(gap_position, len(index)):
            index[position] = index[position] + pd.Timedelta(seconds=10)
    return pd.DataFrame(
        {
            "high": [100.1, 100.5, 104.2, 105.0, 105.1, 105.2, 105.3, 105.4][:periods],
            "low": [99.9, 97.7, 99.0, 103.0, 103.5, 104.0, 104.2, 104.4][:periods],
            "close": [100.0, 98.2, 104.0, 104.5, 104.7, 104.8, 105.0, 105.1][:periods],
        },
        index=pd.DatetimeIndex(index),
    )


def _ns(frame: pd.DataFrame, position: int) -> int:
    return int(frame.index[position].as_unit("ns").value)


def _intent(frame: pd.DataFrame) -> dict:
    return {
        "scenario_id": "s1",
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "entry_fill_time_ns": _ns(frame, 0),
        "entry_fill_price": 100.0,
        "structural_stop": 98.0,
        "external_target": 104.0,
    }


def _closed(frame: pd.DataFrame) -> dict:
    return {
        "scenario_id": "s1",
        "symbol": "BTCUSDT",
        "position_close_time_ns": _ns(frame, 1),
        "realized_pnl": -100.0,
        "close_reason": "STRUCTURAL_STOP",
    }


class CompleteHorizonContracts(unittest.TestCase):
    def test_complete_contiguous_horizon_delegates_and_stamps_revision(self) -> None:
        frame = _frame()
        result = diagnose_trade_path(
            frame=frame,
            intent=_intent(frame),
            closed_trade=_closed(frame),
            maximum_hold_minutes=1,
        )
        self.assertEqual(result["path_diagnostic_status"], "COMPLETE")
        self.assertEqual(result["diagnostic_revision"], DIAGNOSTIC_REVISION)
        self.assertEqual(result["structural_first_touch"], "STOP")
        self.assertTrue(result["target_reached_after_invalidation"])

    def test_missing_tail_is_evidence_failure_even_when_trade_closed_early(self) -> None:
        frame = _frame(periods=5)
        result = diagnose_trade_path(
            frame=frame,
            intent=_intent(frame),
            closed_trade=_closed(frame),
            maximum_hold_minutes=1,
        )
        self.assertEqual(result["path_diagnostic_status"], "INCOMPLETE_MAX_HOLD_HORIZON")
        self.assertGreater(result["missing_tail_ns"], 0)
        self.assertEqual(result["diagnostic_revision"], DIAGNOSTIC_REVISION)

    def test_missing_internal_bucket_is_evidence_failure(self) -> None:
        frame = _frame(periods=8, gap_position=4)
        result = diagnose_trade_path(
            frame=frame,
            intent=_intent(frame),
            closed_trade=_closed(frame),
            maximum_hold_minutes=1,
        )
        self.assertEqual(
            result["path_diagnostic_status"],
            "NONCONTIGUOUS_TEN_SECOND_HORIZON",
        )
        self.assertEqual(result["gap_count"], 1)
        self.assertEqual(result["maximum_gap_ns"], 20_000_000_000)

    def test_missing_first_post_fill_bucket_is_evidence_failure(self) -> None:
        frame = _frame()
        index = frame.index.to_list()
        for position in range(1, len(index)):
            index[position] = index[position] + pd.Timedelta(seconds=20)
        frame.index = pd.DatetimeIndex(index)
        result = diagnose_trade_path(
            frame=frame,
            intent=_intent(frame),
            closed_trade=_closed(frame),
            maximum_hold_minutes=1,
        )
        self.assertEqual(result["path_diagnostic_status"], "MISSING_POST_ENTRY_PATH_START")
        self.assertGreater(result["gap_ns"], 11_000_000_000)

    def test_missing_execution_fields_remain_v1_explicit_with_v2_revision(self) -> None:
        frame = _frame()
        result = diagnose_trade_path(
            frame=frame,
            intent={"scenario_id": "s1"},
            closed_trade=_closed(frame),
            maximum_hold_minutes=1,
        )
        self.assertEqual(result["path_diagnostic_status"], "MISSING_EXECUTION_FIELDS")
        self.assertEqual(result["diagnostic_revision"], DIAGNOSTIC_REVISION)

    def test_enrichment_never_hides_incomplete_horizon_status(self) -> None:
        frame = _frame(periods=5)
        enriched = enrich_closed_trade_records(
            records=[_closed(frame)],
            intents=[_intent(frame)],
            frames_by_symbol={"BTCUSDT": frame},
            maximum_hold_minutes=1,
        )
        diagnostic = enriched[0]["path_diagnostic"]
        self.assertEqual(diagnostic["path_diagnostic_status"], "INCOMPLETE_MAX_HOLD_HORIZON")
        self.assertEqual(diagnostic["diagnostic_revision"], DIAGNOSTIC_REVISION)


if __name__ == "__main__":
    unittest.main(verbosity=2)

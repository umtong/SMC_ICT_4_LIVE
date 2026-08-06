"""Contracts for post-run flow-response structural path diagnostics."""

from __future__ import annotations

import unittest

import pandas as pd

from flow_response_trade_path_diagnostics import (
    diagnose_trade_path,
    enrich_closed_trade_records,
    summarize_trade_path_diagnostics,
)


def _frame(rows: list[tuple[float, float, float]]) -> pd.DataFrame:
    index = pd.date_range("2024-01-01T00:00:10Z", periods=len(rows), freq="10s")
    return pd.DataFrame(
        {
            "high": [row[0] for row in rows],
            "low": [row[1] for row in rows],
            "close": [row[2] for row in rows],
        },
        index=index,
    )


def _ns(frame: pd.DataFrame, position: int) -> int:
    return int(frame.index[position].as_unit("ns").value)


def _intent(
    frame: pd.DataFrame,
    *,
    direction: str = "LONG",
    entry: float = 100.0,
    stop: float = 98.0,
    target: float = 104.0,
) -> dict:
    return {
        "scenario_id": "s1",
        "symbol": "BTCUSDT",
        "direction": direction,
        "entry_fill_time_ns": _ns(frame, 0),
        "entry_fill_price": entry,
        "structural_stop": stop,
        "external_target": target,
    }


def _closed(frame: pd.DataFrame, *, close_position: int, pnl: float = -100.0) -> dict:
    return {
        "scenario_id": "s1",
        "symbol": "BTCUSDT",
        "position_close_time_ns": _ns(frame, close_position),
        "realized_pnl": pnl,
        "close_reason": "STRUCTURAL_STOP",
    }


class StructuralTouchContracts(unittest.TestCase):
    def test_target_then_later_stop_records_both_but_first_touch_is_target(self) -> None:
        frame = _frame(
            [
                (100.1, 99.9, 100.0),
                (104.2, 99.5, 103.8),
                (103.0, 97.8, 98.2),
                (101.0, 99.0, 100.0),
            ]
        )
        result = diagnose_trade_path(
            frame=frame,
            intent=_intent(frame),
            closed_trade=_closed(frame, close_position=2, pnl=200.0),
            maximum_hold_minutes=5,
        )
        self.assertEqual(result["structural_first_touch"], "TARGET")
        self.assertEqual(result["first_target_time_ns"], _ns(frame, 1))
        self.assertEqual(result["first_stop_time_ns"], _ns(frame, 2))
        self.assertFalse(result["target_reached_after_invalidation"])

    def test_stop_then_later_target_is_recorded_as_target_after_invalidation(self) -> None:
        frame = _frame(
            [
                (100.1, 99.9, 100.0),
                (100.5, 97.7, 98.2),
                (101.0, 97.5, 100.5),
                (104.3, 99.8, 104.0),
                (105.0, 103.0, 104.5),
            ]
        )
        result = diagnose_trade_path(
            frame=frame,
            intent=_intent(frame),
            closed_trade=_closed(frame, close_position=1),
            maximum_hold_minutes=5,
        )
        self.assertEqual(result["structural_first_touch"], "STOP")
        self.assertEqual(result["first_stop_time_ns"], _ns(frame, 1))
        self.assertEqual(result["first_target_time_ns"], _ns(frame, 3))
        self.assertTrue(result["target_reached_after_invalidation"])
        self.assertEqual(
            result["target_reached_after_invalidation_time_ns"],
            _ns(frame, 3),
        )
        self.assertTrue(result["target_reached_after_actual_close"])

    def test_repeated_stop_after_close_does_not_hide_a_later_target(self) -> None:
        frame = _frame(
            [
                (100.1, 99.9, 100.0),
                (100.5, 97.7, 98.2),
                (100.0, 97.0, 97.5),
                (101.0, 96.5, 100.5),
                (104.3, 99.0, 104.0),
            ]
        )
        result = diagnose_trade_path(
            frame=frame,
            intent=_intent(frame),
            closed_trade=_closed(frame, close_position=1),
            maximum_hold_minutes=5,
        )
        self.assertTrue(result["target_reached_after_actual_close"])
        self.assertEqual(
            result["target_reached_after_actual_close_time_ns"],
            _ns(frame, 4),
        )
        self.assertEqual(
            result["stop_reached_after_actual_close_time_ns"],
            _ns(frame, 2),
        )

    def test_same_bucket_stop_and_target_is_ambiguous(self) -> None:
        frame = _frame(
            [
                (100.1, 99.9, 100.0),
                (104.5, 97.5, 101.0),
                (102.0, 100.0, 101.0),
            ]
        )
        result = diagnose_trade_path(
            frame=frame,
            intent=_intent(frame),
            closed_trade=_closed(frame, close_position=1),
            maximum_hold_minutes=5,
        )
        self.assertEqual(result["structural_first_touch"], "AMBIGUOUS_SAME_BUCKET")
        self.assertEqual(result["first_stop_time_ns"], _ns(frame, 1))
        self.assertEqual(result["first_target_time_ns"], _ns(frame, 1))
        self.assertFalse(result["target_reached_after_invalidation"])

    def test_short_geometry_and_progress_are_symmetric(self) -> None:
        frame = _frame(
            [
                (100.1, 99.9, 100.0),
                (102.3, 99.5, 101.8),
                (101.0, 95.7, 96.0),
                (100.0, 94.0, 95.0),
            ]
        )
        result = diagnose_trade_path(
            frame=frame,
            intent=_intent(
                frame,
                direction="SHORT",
                entry=100.0,
                stop=102.0,
                target=96.0,
            ),
            closed_trade=_closed(frame, close_position=1),
            maximum_hold_minutes=5,
        )
        self.assertEqual(result["direction_value"], -1)
        self.assertEqual(result["structural_first_touch"], "STOP")
        self.assertTrue(result["target_reached_after_invalidation"])
        self.assertGreaterEqual(result["maximum_favorable_target_distance_fraction"], 1.0)
        self.assertGreaterEqual(result["maximum_adverse_stop_distance_fraction"], 1.0)


class PathEvidenceContracts(unittest.TestCase):
    def test_same_timestamp_entry_bucket_is_excluded(self) -> None:
        frame = _frame(
            [
                (110.0, 90.0, 100.0),
                (101.0, 99.0, 100.5),
                (102.0, 100.0, 101.0),
            ]
        )
        result = diagnose_trade_path(
            frame=frame,
            intent=_intent(frame),
            closed_trade=_closed(frame, close_position=2, pnl=10.0),
            maximum_hold_minutes=5,
        )
        self.assertEqual(result["structural_first_touch"], "NONE_WITHIN_HORIZON")
        self.assertEqual(result["path_bars"], 2)

    def test_missing_fields_and_invalid_geometry_are_explicit_not_silent(self) -> None:
        frame = _frame([(100.1, 99.9, 100.0), (101.0, 99.0, 100.5)])
        missing = diagnose_trade_path(
            frame=frame,
            intent={"scenario_id": "s1"},
            closed_trade=_closed(frame, close_position=1),
            maximum_hold_minutes=5,
        )
        self.assertEqual(missing["path_diagnostic_status"], "MISSING_EXECUTION_FIELDS")
        invalid = diagnose_trade_path(
            frame=frame,
            intent=_intent(frame, stop=101.0, target=104.0),
            closed_trade=_closed(frame, close_position=1),
            maximum_hold_minutes=5,
        )
        self.assertEqual(invalid["path_diagnostic_status"], "INVALID_EXECUTED_GEOMETRY")

    def test_enrichment_and_summary_preserve_records_and_count_diagnostics(self) -> None:
        frame = _frame(
            [
                (100.1, 99.9, 100.0),
                (100.5, 97.7, 98.2),
                (104.3, 99.0, 104.0),
            ]
        )
        records = [_closed(frame, close_position=1)]
        enriched = enrich_closed_trade_records(
            records=records,
            intents=[_intent(frame)],
            frames_by_symbol={"BTCUSDT": frame},
            maximum_hold_minutes=5,
        )
        self.assertEqual(enriched[0]["realized_pnl"], records[0]["realized_pnl"])
        self.assertTrue(enriched[0]["path_diagnostic"]["target_reached_after_invalidation"])
        summary = summarize_trade_path_diagnostics(enriched)
        self.assertEqual(summary["records"], 1)
        self.assertEqual(summary["complete_records"], 1)
        self.assertEqual(summary["structural_first_touch_counts"], {"STOP": 1})
        self.assertEqual(summary["target_after_invalidation_count"], 1)

    def test_diagnostics_source_is_evaluation_only(self) -> None:
        from pathlib import Path

        source = (
            Path(__file__).resolve().parent / "flow_response_trade_path_diagnostics.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "order_factory",
            "submit_order",
            "risk_sized_quantity",
            "build_flow_response_auction_signals",
            "model_score",
            "risk_multiplier",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

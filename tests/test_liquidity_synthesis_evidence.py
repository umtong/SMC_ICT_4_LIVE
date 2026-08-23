from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
import unittest

from smc_ict_4.episode_policy_live.evidence import (
    DAY_NS,
    build_episode_ledger,
    build_evidence,
    normalize_trade_records,
    write_evidence,
)


@dataclass
class NativePositionEvent:
    position_id: str
    instrument_id: str
    side: str
    ts_opened: int
    ts_closed: int
    avg_px_open: float
    avg_px_close: float
    peak_qty: float
    realized_pnl: str
    episode_id: str
    planned_rr: float
    nav_before: float
    nav_after: float


class PandasLikeReport:
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient=None):
        if orient != "records":
            raise TypeError
        return self.rows


class TradeEvidenceTests(unittest.TestCase):
    def test_native_objects_and_reports_produce_descriptive_metrics_without_gates(self):
        trades = [
            NativePositionEvent(
                "p1", "BTCUSDT-PERP.BINANCE", "LONG", DAY_NS, 2 * DAY_NS,
                100.0, 104.0, 10.0, "40 USDT", "ep1", 2.0, 1000.0, 1040.0,
            ),
            {
                "plan_id": "p2", "episode_id": "ep2", "symbol": "ETHUSDT",
                "side": "SHORT", "entry_time_ns": 2 * DAY_NS,
                "exit_time_ns": 3 * DAY_NS, "gross_pnl": -20.0, "fees": 2.0,
                "net_pnl": -22.0, "net_r": -0.733333333333,
                "planned_gross_rr": 1.5, "nav_before": 1040.0, "nav_after": 1018.0,
            },
        ]
        equity = PandasLikeReport([
            {"ts_event": 0, "equity": 1000.0},
            {"ts_event": DAY_NS, "equity": 950.0},
            {"ts_event": 2 * DAY_NS, "equity": 1040.0},
            {"ts_event": 3 * DAY_NS, "equity": 1018.0},
        ])
        evidence = build_evidence(
            trades=trades,
            equity=equity,
            start_time_ns=1,  # ns, avoid the friendly timestamp zero ambiguity
            end_time_ns=3 * DAY_NS,
            initial_nav=1000.0,
            expected_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        )
        metrics = evidence["metrics"]
        self.assertEqual(metrics["completed_trades"], 2)
        self.assertAlmostEqual(metrics["win_rate"], 0.5)
        self.assertAlmostEqual(metrics["average_planned_gross_rr"], 1.75)
        self.assertIsNotNone(metrics["average_gross_r"])
        self.assertAlmostEqual(metrics["profit_factor"], 40.0 / 22.0)
        self.assertEqual(metrics["final_nav"], 1018.0)
        self.assertAlmostEqual(metrics["maximum_continuous_drawdown"], 0.05)
        self.assertEqual(metrics["missing_symbol_coverage"], ["SOLUSDT"])
        self.assertEqual(metrics["duplicate_episode_id_count"], 0)
        self.assertEqual(metrics["overlapping_trade_pair_count"], 0)
        self.assertFalse(any("pass" in key.lower() or "threshold" in key.lower() for key in metrics))

    def test_pandas_like_nautilus_position_report_is_ingested(self):
        report = PandasLikeReport([
            {
                "position_id": "P-1", "instrument_id": "XRPUSDT.BINANCE", "side": "LONG",
                "ts_opened": DAY_NS, "ts_closed": 2 * DAY_NS,
                "avg_px_open": 1.0, "avg_px_close": 1.1, "peak_qty": 100,
                "realized_pnl": "9.5 USDT",
            }
        ])
        rows = normalize_trade_records(report)
        self.assertEqual(rows[0]["trade_id"], "P-1")
        self.assertEqual(rows[0]["symbol"], "XRPUSDT")
        self.assertEqual(rows[0]["net_pnl"], 9.5)

    def test_account_position_closed_event_details_are_ingested(self):
        event = {
            "time_ns": 2 * DAY_NS,
            "event_type": "POSITION_CLOSED",
            "details": {
                "plan_id": "P", "episode_id": "E", "symbol": "BTCUSDT",
                "entry_time_ns": DAY_NS, "exit_time_ns": 2 * DAY_NS,
                "gross_pnl": 12.0, "fees": 2.0, "net_pnl": 10.0,
                "net_r": 0.5, "planned_gross_rr": 1.4,
            },
        }
        rows = normalize_trade_records([event])
        self.assertEqual(rows[0]["trade_id"], "P")
        self.assertEqual(rows[0]["net_pnl"], 10.0)


class EpisodeEvidenceTests(unittest.TestCase):
    def test_non_trade_labels_do_not_exist_before_declared_maturity(self):
        episode = {
            "episode_id": "missed", "symbol": "BTCUSDT", "side": "LONG",
            "decision_time_ns": DAY_NS, "maturity_time_ns": 2 * DAY_NS,
            "entry": 100.0, "stop": 98.0, "target": 104.0,
            "counterfactual_outcome": "TARGET_FIRST", "reason": "NO_CONFIRMATION",
            "as_of_time_ns": 3 * DAY_NS,
        }
        before = build_episode_ledger([episode], as_of_time_ns=2 * DAY_NS - 1)
        self.assertEqual(before[0]["maturity_label"], "PENDING_MATURITY")
        self.assertIsNone(before[0]["matured_outcome"])
        after = build_episode_ledger([episode], as_of_time_ns=2 * DAY_NS)
        self.assertEqual(after[0]["maturity_label"], "STRUCTURALLY_MISSED")
        self.assertEqual(after[0]["label_available_time_ns"], 2 * DAY_NS)
        self.assertNotIn("counterfactual_outcome", after[0]["decision_evidence"])

    def test_matured_bar_path_separates_missed_from_abstained_conservatively(self):
        base = {
            "symbol": "BTCUSDT", "side": "LONG", "decision_time_ns": DAY_NS,
            "maturity_time_ns": DAY_NS + 3, "entry": 100.0, "stop": 98.0, "target": 104.0,
        }
        bars = [
            {"symbol": "BTCUSDT", "open_time_ns": DAY_NS + 1, "close_time_ns": DAY_NS + 1,
             "open": 101, "high": 101, "low": 99, "close": 100},
            {"symbol": "BTCUSDT", "open_time_ns": DAY_NS + 2, "close_time_ns": DAY_NS + 2,
             "open": 100, "high": 105, "low": 99, "close": 104},
        ]
        missed = build_episode_ledger([{**base, "episode_id": "m"}], bars=bars, as_of_time_ns=DAY_NS + 3)
        self.assertEqual(missed[0]["maturity_label"], "STRUCTURALLY_MISSED")
        ambiguous_bars = [
            {"symbol": "BTCUSDT", "open_time_ns": DAY_NS + 1, "close_time_ns": DAY_NS + 1,
             "open": 100, "high": 105, "low": 97, "close": 100},
        ]
        abstained = build_episode_ledger([{**base, "episode_id": "a"}], bars=ambiguous_bars, as_of_time_ns=DAY_NS + 3)
        self.assertEqual(abstained[0]["maturity_label"], "ABSTAINED")
        self.assertEqual(abstained[0]["matured_outcome"], "STOP_FIRST")

    def test_writes_json_and_csv_chart_window_specs(self):
        evidence = build_evidence(
            trades=[{
                "trade_id": "t", "episode_id": "e", "symbol": "SOLUSDT",
                "entry_time_ns": DAY_NS, "exit_time_ns": DAY_NS + 10,
                "entry_price": 100, "stop_price": 99, "exit_price": 102,
                "net_pnl": 20, "net_r": 2, "planned_gross_rr": 2,
                "nav_before": 1000, "nav_after": 1020,
            }],
            episodes=[{
                "episode_id": "no-trade", "symbol": "ETHUSDT", "side": "SHORT",
                "decision_time_ns": DAY_NS, "maturity_time_ns": DAY_NS + 20,
                "entry": 100, "stop": 101, "target": 98,
                "counterfactual_outcome": "STOP_FIRST",
            }],
            as_of_time_ns=DAY_NS + 20,
        )
        with tempfile.TemporaryDirectory() as directory:
            paths = write_evidence(Path(directory), evidence)
            self.assertTrue(paths["chart_windows_csv"].exists())
            payload = json.loads(paths["chart_windows_json"].read_text(encoding="utf-8"))
            self.assertEqual({row["case_kind"] for row in payload}, {"TRADE", "ABSTAINED"})
            self.assertIn("overlays", payload[0])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from funding_ledger_v13 import linear_funding_cash_flow
from funding_metrics_v13 import (
    apply_funding_adjustment,
    funding_adjusted_trade_audit,
    settlement_cash_flows,
)


class FundingLedgerTests(unittest.TestCase):
    def test_linear_perpetual_cash_flow_signs(self) -> None:
        self.assertEqual(
            linear_funding_cash_flow(
                Decimal("2"),
                Decimal("100"),
                Decimal("0.001"),
            ),
            Decimal("-0.200"),
        )
        self.assertEqual(
            linear_funding_cash_flow(
                Decimal("-2"),
                Decimal("100"),
                Decimal("0.001"),
            ),
            Decimal("0.200"),
        )
        self.assertEqual(
            linear_funding_cash_flow(
                Decimal("2"),
                Decimal("100"),
                Decimal("-0.001"),
            ),
            Decimal("0.200"),
        )
        with self.assertRaises(ValueError):
            linear_funding_cash_flow(Decimal("1"), Decimal("0"), Decimal("0.001"))

    def test_settlements_join_exactly_once_to_positions(self) -> None:
        audit = pd.DataFrame(
            [
                {
                    "position_id": "P1",
                    "realized_pnl": 10.0,
                    "risk_budget": 5.0,
                    "nav_at_submission": 100.0,
                    "actual_net_r": 2.0,
                    "ts_closed": "2024-01-01T12:00:00Z",
                },
                {
                    "position_id": "P2",
                    "realized_pnl": -5.0,
                    "risk_budget": 5.0,
                    "nav_at_submission": 110.0,
                    "actual_net_r": -1.0,
                    "ts_closed": "2024-01-02T12:00:00Z",
                },
            ],
        )
        events = [
            {
                "kind": "external_funding_settlement",
                "position_id": "P1",
                "event_time_ns": 1,
                "funding_cash_flow": "-0.2",
                "funding_rate": "0.001",
                "mark_price": "100",
                "mark_age_ns": 1_000_000,
            },
            {
                "kind": "external_funding_settlement",
                "position_id": "P1",
                "event_time_ns": 2,
                "funding_cash_flow": "0.1",
                "funding_rate": "-0.0005",
                "mark_price": "100",
                "mark_age_ns": 1_000_000,
            },
        ]
        adjusted = funding_adjusted_trade_audit(audit, events)
        p1 = adjusted.loc[adjusted["position_id"] == "P1"].iloc[0]
        p2 = adjusted.loc[adjusted["position_id"] == "P2"].iloc[0]
        self.assertAlmostEqual(p1["funding_cash_flow"], -0.1)
        self.assertEqual(p1["funding_settlement_count"], 2)
        self.assertAlmostEqual(p1["funding_adjusted_realized_pnl"], 9.9)
        self.assertAlmostEqual(p1["funding_adjusted_actual_net_r"], 1.98)
        self.assertEqual(p2["funding_cash_flow"], 0.0)
        self.assertEqual(p2["funding_settlement_count"], 0)

        duplicate = events + [dict(events[0])]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            settlement_cash_flows(duplicate)

        unknown = events + [
            {
                "kind": "external_funding_settlement",
                "position_id": "UNKNOWN",
                "event_time_ns": 3,
                "funding_cash_flow": "-1",
                "funding_rate": "0.001",
                "mark_price": "100",
                "mark_age_ns": 1,
            },
        ]
        with self.assertRaisesRegex(ValueError, "lack closed trade"):
            funding_adjusted_trade_audit(audit, unknown)

    def test_continuous_metrics_adjust_native_nav_and_robustness(self) -> None:
        audit = pd.DataFrame(
            [
                {
                    "position_id": "P1",
                    "realized_pnl": 5.0,
                    "risk_budget": 5.0,
                    "nav_at_submission": 100.0,
                    "actual_net_r": 1.0,
                    "ts_closed": "2024-01-01T12:00:00Z",
                },
                {
                    "position_id": "P2",
                    "realized_pnl": 4.2,
                    "risk_budget": 5.0,
                    "nav_at_submission": 105.0,
                    "actual_net_r": 0.84,
                    "ts_closed": "2024-01-02T12:00:00Z",
                },
                {
                    "position_id": "P3",
                    "realized_pnl": -2.184,
                    "risk_budget": 5.0,
                    "nav_at_submission": 109.2,
                    "actual_net_r": -0.4368,
                    "ts_closed": "2024-01-03T12:00:00Z",
                },
                {
                    "position_id": "P4",
                    "realized_pnl": 3.21048,
                    "risk_budget": 5.0,
                    "nav_at_submission": 107.016,
                    "actual_net_r": 0.642096,
                    "ts_closed": "2024-01-04T12:00:00Z",
                },
            ],
        )
        events = [
            {
                "kind": "external_funding_settlement",
                "position_id": "P2",
                "event_time_ns": 2,
                "funding_cash_flow": "-0.2",
                "funding_rate": "0.001",
                "mark_price": "100",
                "mark_age_ns": 1,
            },
        ]
        metrics = {
            "starting_nav": 100.0,
            "final_nav": 110.22648,
            "calendar_days": 4,
            "total_return": 0.1022648,
        }
        with tempfile.TemporaryDirectory() as directory:
            result = apply_funding_adjustment(
                metrics,
                audit,
                events,
                Path(directory),
            )
            self.assertAlmostEqual(result["external_funding_cash_flow"], -0.2)
            self.assertAlmostEqual(result["funding_adjusted_final_nav"], 110.02648)
            self.assertAlmostEqual(result["funding_adjusted_total_return"], 0.1002648)
            self.assertTrue(
                (Path(directory) / "trade_audit_funding_adjusted.csv").exists(),
            )
            self.assertTrue(
                (Path(directory) / "metrics_funding_adjusted.json").exists(),
            )


if __name__ == "__main__":
    unittest.main()

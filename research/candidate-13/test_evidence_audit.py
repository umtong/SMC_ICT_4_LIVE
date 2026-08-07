from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest

from evidence_audit import audit


class EvidenceAuditTests(unittest.TestCase):
    def make_result(self, root: Path, *, promising: bool = True, complete: bool = False) -> Path:
        metrics = {
            "starting_nav": "100000",
            "final_nav": "107213.5352107",
            "evaluation_days": 7,
            "daily_geometric_growth": 0.01,
            "net_return": 0.072135352107,
            "closed_trades": 7,
            "submitted_plans": 7,
            "engine_errors": [],
            "promising_gate_passed": promising,
            "complete_gate_passed": complete,
            "success_claim": complete,
            "liquidation_detected": False,
        }
        (root / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
        for name in ("run.json", "data_manifest.json"):
            (root / name).write_text("{}", encoding="utf-8")
        (root / "orders.csv").write_text("id,status\n1,FILLED\n", encoding="utf-8")
        (root / "account.csv").write_text("currency,balance\nUSDT,107213.5352107\n", encoding="utf-8")
        (root / "scenario_events.jsonl").write_text("{}\n", encoding="utf-8")
        (root / "submitted_plans.json").write_text(json.dumps({"plans": [{
            "nav_before": "100000", "planned_loss_budget": "3000", "expected_total_loss": "2999.99"
        }]}), encoding="utf-8")
        with (root / "positions.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=("ts_opened", "ts_closed", "realized_pnl"))
            writer.writeheader()
            writer.writerow({"ts_opened": "1", "ts_closed": "2", "realized_pnl": "100"})
            writer.writerow({"ts_opened": "3", "ts_closed": "4", "realized_pnl": "100"})
        return root

    def test_w1_can_advance_but_cannot_claim_durable_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = audit(self.make_result(Path(temp), complete=True), "W1")
        self.assertEqual(result["classification"], "W1_COMPLETE_GATE_PASSED")
        self.assertTrue(result["advance_allowed"])
        self.assertFalse(result["success_claim_allowed"])

    def test_missing_account_report_is_implementation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_result(Path(temp))
            (root / "account.csv").unlink()
            result = audit(root, "W1")
        self.assertEqual(result["classification"], "IMPLEMENTATION_OR_EVIDENCE_FAILURE")

    def test_loss_budget_must_equal_three_percent_nav(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_result(Path(temp))
            (root / "submitted_plans.json").write_text(json.dumps({"plans": [{
                "nav_before": "100000", "planned_loss_budget": "2500", "expected_total_loss": "2400"
            }]}), encoding="utf-8")
            result = audit(root, "W1")
        self.assertFalse(result["risk_budget_passed"])
        self.assertEqual(result["classification"], "IMPLEMENTATION_OR_EVIDENCE_FAILURE")

    def test_overlapping_positions_violate_global_mutex(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_result(Path(temp))
            with (root / "positions.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=("ts_opened", "ts_closed"))
                writer.writeheader()
                writer.writerow({"ts_opened": "1", "ts_closed": "5"})
                writer.writerow({"ts_opened": "4", "ts_closed": "6"})
            result = audit(root, "W1")
        self.assertFalse(result["global_slot_passed"])
        self.assertEqual(result["classification"], "IMPLEMENTATION_OR_EVIDENCE_FAILURE")

    def test_partial_entry_expiry_must_fail_close_within_one_bar(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_result(Path(temp))
            with (root / "orders.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=(
                    "instrument_id", "position_id", "status", "time_in_force",
                    "tags", "quantity", "filled_qty", "ts_last",
                ))
                writer.writeheader()
                writer.writerow({
                    "instrument_id": "XRPUSDT-PERP.BINANCE",
                    "position_id": "P-1",
                    "status": "EXPIRED",
                    "time_in_force": "GTD",
                    "tags": "['ENTRY']",
                    "quantity": "1000",
                    "filled_qty": "100",
                    "ts_last": "2024-01-01 00:10:00+00:00",
                })
            with (root / "positions.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=("instrument_id", "position_id", "ts_opened", "ts_closed", "realized_pnl"))
                writer.writeheader()
                writer.writerow({
                    "instrument_id": "XRPUSDT-PERP.BINANCE",
                    "position_id": "P-1",
                    "ts_opened": "2024-01-01 00:01:00+00:00",
                    "ts_closed": "2024-01-01 00:20:00+00:00",
                    "realized_pnl": "-10",
                })
            result = audit(root, "W1")
        self.assertFalse(result["partial_entry_protection_passed"])
        self.assertEqual(result["classification"], "IMPLEMENTATION_OR_EVIDENCE_FAILURE")

    def test_partial_entry_expiry_closed_next_bar_passes_protection_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_result(Path(temp))
            with (root / "orders.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=(
                    "instrument_id", "position_id", "status", "time_in_force",
                    "tags", "quantity", "filled_qty", "ts_last",
                ))
                writer.writeheader()
                writer.writerow({
                    "instrument_id": "XRPUSDT-PERP.BINANCE",
                    "position_id": "P-1",
                    "status": "EXPIRED",
                    "time_in_force": "GTD",
                    "tags": "['ENTRY']",
                    "quantity": "1000",
                    "filled_qty": "100",
                    "ts_last": "2024-01-01 00:10:00+00:00",
                })
            with (root / "positions.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=("instrument_id", "position_id", "ts_opened", "ts_closed", "realized_pnl"))
                writer.writeheader()
                writer.writerow({
                    "instrument_id": "XRPUSDT-PERP.BINANCE",
                    "position_id": "P-1",
                    "ts_opened": "2024-01-01 00:01:00+00:00",
                    "ts_closed": "2024-01-01 00:11:00+00:00",
                    "realized_pnl": "-10",
                })
            result = audit(root, "W1")
        self.assertTrue(result["partial_entry_protection_passed"])


if __name__ == "__main__":
    unittest.main()

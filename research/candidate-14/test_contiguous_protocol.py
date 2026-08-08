from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class ContiguousProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.protocol = json.loads((ROOT / "protocol.json").read_text(encoding="utf-8"))
        self.reservation = json.loads(
            (ROOT / "CONTIGUOUS_HOLDOUT_RESERVATION.json").read_text(encoding="utf-8")
        )

    def test_one_frozen_contiguous_interval(self) -> None:
        self.assertEqual(self.protocol["validation_mode"], "frozen_holdout")
        holdouts = self.protocol["selection"]["holdouts"]
        self.assertEqual(set(holdouts), {"L1"})
        interval = holdouts["L1"]
        days = (
            date.fromisoformat(interval["end_exclusive"])
            - date.fromisoformat(interval["start"])
        ).days
        self.assertEqual(days, 84)
        self.assertEqual(days, self.protocol["selection"]["evaluation_days"])

    def test_reservation_matches_executable_protocol(self) -> None:
        self.assertFalse(self.reservation["outcomes_inspected_before_reservation"])
        self.assertEqual(self.protocol["selection"], self.reservation["selection"])
        self.assertEqual(self.protocol["aggregate_gate"], self.reservation["aggregate_gate"])

    def test_weekly_reset_is_prohibited(self) -> None:
        self.assertFalse(self.protocol["execution_lock"]["weekly_reset_allowed"])
        self.assertTrue(
            self.protocol["aggregate_gate"]["require_continuous_account_path"]
        )

    def test_runner_inherits_protocol_day_count(self) -> None:
        source = (ROOT / "candidate14_runner.py").read_text(encoding="utf-8")
        self.assertIn(
            'config["selection"]["evaluation_days"] = protocol["selection"]["evaluation_days"]',
            source,
        )


if __name__ == "__main__":
    unittest.main()

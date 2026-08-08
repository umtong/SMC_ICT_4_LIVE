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
        locks = sorted(ROOT.glob("V*_DEVELOPMENT_LOCK.json"))
        matching = []
        for path in locks:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("candidate") == self.protocol.get("candidate"):
                matching.append(payload)
        self.development = matching[0] if len(matching) == 1 else None

    def test_one_contiguous_interval_with_explicit_evidence_role(self) -> None:
        mode = self.protocol["validation_mode"]
        self.assertIn(mode, {"frozen_holdout", "diagnostic"})
        holdouts = self.protocol["selection"]["holdouts"]
        self.assertEqual(set(holdouts), {"L1"})
        interval = holdouts["L1"]
        days = (
            date.fromisoformat(interval["end_exclusive"])
            - date.fromisoformat(interval["start"])
        ).days
        self.assertEqual(days, 84)
        self.assertEqual(days, self.protocol["selection"]["evaluation_days"])
        if mode == "diagnostic":
            self.assertEqual(
                self.protocol.get("evidence_role"),
                "post-holdout-controlled-development-diagnostic",
            )
            self.assertIsNotNone(self.development)
            assert self.development is not None
            self.assertFalse(self.development["claim_allowed"])
            self.assertTrue(
                self.development["diagnostic_interval_outcomes_previously_inspected"],
            )

    def test_evidence_role_matches_its_lock(self) -> None:
        if self.protocol["validation_mode"] == "frozen_holdout":
            self.assertFalse(self.reservation["outcomes_inspected_before_reservation"])
            self.assertEqual(self.protocol["selection"], self.reservation["selection"])
            self.assertEqual(
                self.protocol["aggregate_gate"],
                self.reservation["aggregate_gate"],
            )
            return

        self.assertIsNotNone(self.development)
        assert self.development is not None
        self.assertEqual(self.protocol["candidate"], self.development["candidate"])
        self.assertTrue(
            self.protocol["strategy_change_control"][
                "diagnostic_interval_outcomes_previously_inspected"
            ],
        )
        self.assertFalse(self.protocol["strategy_change_control"]["claim_allowed"])
        self.assertEqual(
            self.protocol["selection"]["evaluation_days"],
            self.development["selection"]["evaluation_days"],
        )
        self.assertEqual(
            self.protocol["selection"]["warmup_days"],
            self.development["selection"]["warmup_days"],
        )
        self.assertEqual(
            self.protocol["selection"]["holdouts"],
            self.development["selection"]["holdouts"],
        )
        self.assertFalse(self.reservation["outcomes_inspected_before_reservation"])
        self.assertNotEqual(self.protocol["selection"], self.reservation["selection"])

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

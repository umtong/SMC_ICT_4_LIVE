from __future__ import annotations

import unittest

from smc_ict_4.contracts import ContractError, ResearchEvent


class ResearchEventTests(unittest.TestCase):
    def make_event(self, **overrides):
        values = {
            "scenario_id": "scenario-1",
            "instrument_id": "BTCUSDT.BINANCE",
            "event_type": "SWING_CONFIRMED",
            "event_time_ns": 100,
            "observed_time_ns": 120,
            "previous_state": "SEEKING",
            "next_state": "CONFIRMED",
            "reason_code": "right_context_complete",
            "reference_price": "40000.10",
            "details": {"window": 2},
        }
        values.update(overrides)
        return ResearchEvent(**values)

    def test_rejects_future_information(self):
        with self.assertRaises(ContractError):
            self.make_event(event_time_ns=200, observed_time_ns=199)

    def test_event_id_is_deterministic(self):
        first = self.make_event()
        second = self.make_event(details={"window": 2})
        self.assertEqual(first.event_id, second.event_id)

    def test_details_are_copied(self):
        details = {"value": [1]}
        event = self.make_event(details=details)
        details["value"].append(2)
        self.assertEqual(event.details, {"value": [1]})


if __name__ == "__main__":
    unittest.main()

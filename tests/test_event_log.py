from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from smc_ict_4.contracts import ResearchEvent
from smc_ict_4.event_log import EventLogError, validate_event_file, validate_events, write_events


class EventLogTests(unittest.TestCase):
    def event(self, previous: str, next_state: str, observed: int, reason: str) -> ResearchEvent:
        return ResearchEvent(
            scenario_id="scenario-1",
            instrument_id="BTCUSDT.BINANCE",
            event_type="STATE_TRANSITION",
            event_time_ns=observed - 1,
            observed_time_ns=observed,
            previous_state=previous,
            next_state=next_state,
            reason_code=reason,
        )

    def test_round_trip(self):
        events = [
            self.event("IDLE", "ARMED", 10, "context"),
            self.event("ARMED", "OPEN", 20, "confirmation"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = write_events(Path(directory) / "events.jsonl", events)
            self.assertEqual(validate_event_file(path), events)

    def test_rejects_broken_state_chain(self):
        events = [
            self.event("IDLE", "ARMED", 10, "context"),
            self.event("IDLE", "OPEN", 20, "confirmation"),
        ]
        with self.assertRaises(EventLogError):
            validate_events(events)

    def test_rejects_global_time_reversal(self):
        events = [
            self.event("IDLE", "ARMED", 20, "context"),
            self.event("ARMED", "OPEN", 10, "confirmation"),
        ]
        with self.assertRaises(EventLogError):
            validate_events(events)


if __name__ == "__main__":
    unittest.main()

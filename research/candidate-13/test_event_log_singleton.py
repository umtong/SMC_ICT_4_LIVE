from __future__ import annotations

import unittest

from smc_ict_4.contracts import ResearchEvent
from smc_ict_4.event_log import EventLogError, validate_events


def _event(
    *,
    scenario_id: str,
    instrument_id: str,
    event_type: str,
    observed_time_ns: int,
    previous_state: str,
    next_state: str,
    reason_code: str,
    marker: int,
) -> ResearchEvent:
    return ResearchEvent(
        scenario_id=scenario_id,
        instrument_id=instrument_id,
        event_type=event_type,
        event_time_ns=observed_time_ns,
        observed_time_ns=observed_time_ns,
        previous_state=previous_state,
        next_state=next_state,
        reason_code=reason_code,
        details={"marker": marker},
    )


class AmbiguousSingletonEventTests(unittest.TestCase):
    def test_independent_ambiguous_sweeps_do_not_share_state_chain(self) -> None:
        events = [
            _event(
                scenario_id="AMBIGUOUS",
                instrument_id="BTCUSDT-PERP.BINANCE",
                event_type="AMBIGUOUS_SWEEP",
                observed_time_ns=10,
                previous_state="ARMED",
                next_state="TERMINAL",
                reason_code="BAR_PATH_UNRESOLVABLE",
                marker=1,
            ),
            _event(
                scenario_id="AMBIGUOUS",
                instrument_id="ETHUSDT-PERP.BINANCE",
                event_type="AMBIGUOUS_SWEEP",
                observed_time_ns=20,
                previous_state="ARMED",
                next_state="TERMINAL",
                reason_code="BAR_PATH_UNRESOLVABLE",
                marker=2,
            ),
        ]
        self.assertEqual(validate_events(events), events)

    def test_ordinary_scenario_reuse_still_fails_closed(self) -> None:
        events = [
            _event(
                scenario_id="scenario-1",
                instrument_id="BTCUSDT-PERP.BINANCE",
                event_type="STATE_TRANSITION",
                observed_time_ns=10,
                previous_state="ARMED",
                next_state="TERMINAL",
                reason_code="FIRST_TERMINAL",
                marker=1,
            ),
            _event(
                scenario_id="scenario-1",
                instrument_id="BTCUSDT-PERP.BINANCE",
                event_type="STATE_TRANSITION",
                observed_time_ns=20,
                previous_state="ARMED",
                next_state="TERMINAL",
                reason_code="SECOND_TERMINAL",
                marker=2,
            ),
        ]
        with self.assertRaises(EventLogError):
            validate_events(events)

    def test_near_match_does_not_receive_singleton_exception(self) -> None:
        events = [
            _event(
                scenario_id="AMBIGUOUS",
                instrument_id="BTCUSDT-PERP.BINANCE",
                event_type="AMBIGUOUS_SWEEP",
                observed_time_ns=10,
                previous_state="ARMED",
                next_state="TERMINAL",
                reason_code="DIFFERENT_REASON",
                marker=1,
            ),
            _event(
                scenario_id="AMBIGUOUS",
                instrument_id="BTCUSDT-PERP.BINANCE",
                event_type="AMBIGUOUS_SWEEP",
                observed_time_ns=20,
                previous_state="ARMED",
                next_state="TERMINAL",
                reason_code="DIFFERENT_REASON",
                marker=2,
            ),
        ]
        with self.assertRaises(EventLogError):
            validate_events(events)


if __name__ == "__main__":
    unittest.main()

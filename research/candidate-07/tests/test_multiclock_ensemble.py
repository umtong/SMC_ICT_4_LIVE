from __future__ import annotations

import json
import unittest

from nautilus_trader.model.identifiers import InstrumentId

from multiclock_ensemble_scenario import (
    build_ensemble_signals,
    select_first_retests,
)


class MulticlockFirstRetestTests(unittest.TestCase):
    @staticmethod
    def _scenario(
        scenario_id: str,
        *,
        observed_ns: int,
        source_pool_id: str = "15SH-source",
        sweep_ns: int = 14_999_999_999,
        direction: str = "SHORT",
    ) -> dict:
        return {
            "scenario_id": scenario_id,
            "outcome": "ENTRY_READY",
            "direction": direction,
            "entry": 99.0,
            "stop": 100.0,
            "target": 97.0,
            "expected_rr": 2.0,
            "source_pool_id": source_pool_id,
            "observed_time_ns": observed_ns,
            "sweep": {"timestamp_ns": sweep_ns},
            "mss": {"timestamp_ns": observed_ns - 5_000_000_000},
            "retest": {"timestamp_ns": observed_ns},
            "target_pool": {
                "pool_id": "15SL-target",
                "timeframe": "15S",
                "level": 97.0,
                "confirmed_ts_ns": 1,
            },
        }

    def test_first_completed_retest_consumes_duplicate_clock_episode(self) -> None:
        five = {"scenarios": [self._scenario("five", observed_ns=20)]}
        fifteen = {"scenarios": [self._scenario("fifteen", observed_ns=30)]}
        selected, diagnostics = select_first_retests(five, fifteen)
        self.assertEqual([item["scenario_id"] for item in selected], ["five"])
        self.assertEqual(diagnostics["duplicate_clock_episodes"], 1)
        self.assertEqual(
            diagnostics["discarded_later_confirmation_counts"],
            {"15S": 1},
        )

    def test_simultaneous_confirmation_prefers_more_complete_clock(self) -> None:
        five = {"scenarios": [self._scenario("five", observed_ns=20)]}
        fifteen = {"scenarios": [self._scenario("fifteen", observed_ns=20)]}
        selected, _ = select_first_retests(five, fifteen)
        self.assertEqual([item["scenario_id"] for item in selected], ["fifteen"])

    def test_independent_sweeps_are_both_retained(self) -> None:
        five = {"scenarios": [self._scenario("five", observed_ns=20)]}
        fifteen = {
            "scenarios": [
                self._scenario(
                    "fifteen",
                    observed_ns=30,
                    source_pool_id="15SL-other",
                    sweep_ns=29_999_999_999,
                    direction="LONG",
                )
            ]
        }
        selected, diagnostics = select_first_retests(five, fifteen)
        self.assertEqual(len(selected), 2)
        self.assertEqual(diagnostics["source_episodes"], 2)

    def test_signal_is_causal_and_contains_episode_arbitration(self) -> None:
        selected, _ = select_first_retests(
            {"scenarios": [self._scenario("five", observed_ns=44_999_999_999)]},
            {"scenarios": []},
        )
        report = {
            "summary": {"require_retest": True},
            "scenarios": selected,
        }
        signals = build_ensemble_signals(
            report=report,
            upstream_report={},
            instrument_id=InstrumentId.from_str("BTCUSDT-PERP.BINANCE"),
        )
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.ts_event, signal.observed_time_ns + 1)
        details = json.loads(signal.details_json)
        self.assertEqual(details["episode_selection"], "FIRST_COMPLETED_VALID_RETEST")
        self.assertEqual(details["execution_timeframe"], "5S")
        serialized = json.dumps(details).lower()
        for forbidden in ("path", "mfe", "mae", "terminal", "realized_r"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()

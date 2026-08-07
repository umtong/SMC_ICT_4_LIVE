from __future__ import annotations

import json
import unittest

from nautilus_trader.model.identifiers import InstrumentId

from five_second_parent_acceptance import parent_accepts
from parent_acceptance_ensemble_scenario import (
    build_parent_acceptance_ensemble_signals,
    select_parent_accepted_or_full_retest,
)


class ParentAcceptanceTests(unittest.TestCase):
    def test_parent_price_and_flow_must_accept_together(self) -> None:
        self.assertTrue(
            parent_accepts(
                {"close": 101.0, "signed_quote": 5.0},
                direction="LONG",
                boundary_level=100.0,
            )
        )
        self.assertFalse(
            parent_accepts(
                {"close": 101.0, "signed_quote": -5.0},
                direction="LONG",
                boundary_level=100.0,
            )
        )
        self.assertTrue(
            parent_accepts(
                {"close": 99.0, "signed_quote": -5.0},
                direction="SHORT",
                boundary_level=100.0,
            )
        )

    @staticmethod
    def _scenario(
        scenario_id: str,
        *,
        observed_ns: int,
        source_pool_id: str = "15SH-source",
        sweep_ns: int = 14_999_999_999,
        direction: str = "SHORT",
        parent: bool = False,
    ) -> dict:
        item = {
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
            "retest": {"timestamp_ns": observed_ns - 1_000_000_000},
            "target_pool": {
                "pool_id": "15SL-target",
                "timeframe": "15S",
                "level": 97.0,
                "confirmed_ts_ns": 1,
            },
        }
        if parent:
            item["parent_acceptance"] = {
                "timestamp_ns": observed_ns,
                "boundary_level": 100.0,
                "close": 99.0,
                "signed_quote": -5.0,
            }
        return item

    def test_first_valid_path_consumes_same_sweep(self) -> None:
        parent = {
            "scenarios": [
                self._scenario("parent", observed_ns=20, parent=True)
            ]
        }
        fifteen = {
            "scenarios": [self._scenario("fifteen", observed_ns=30)]
        }
        selected, diagnostics = select_parent_accepted_or_full_retest(
            parent,
            fifteen,
        )
        self.assertEqual([item["scenario_id"] for item in selected], ["parent"])
        self.assertEqual(diagnostics["duplicate_clock_episodes"], 1)
        self.assertEqual(
            diagnostics["discarded_later_confirmation_counts"],
            {"15S_RETEST": 1},
        )

    def test_full_15s_retest_wins_simultaneous_tie(self) -> None:
        parent = {
            "scenarios": [
                self._scenario("parent", observed_ns=20, parent=True)
            ]
        }
        fifteen = {
            "scenarios": [self._scenario("fifteen", observed_ns=20)]
        }
        selected, _ = select_parent_accepted_or_full_retest(parent, fifteen)
        self.assertEqual([item["scenario_id"] for item in selected], ["fifteen"])

    def test_signal_contains_parent_state_without_future_path(self) -> None:
        parent = {
            "scenarios": [
                self._scenario(
                    "parent",
                    observed_ns=44_999_999_999,
                    parent=True,
                )
            ]
        }
        selected, _ = select_parent_accepted_or_full_retest(
            parent,
            {"scenarios": []},
        )
        report = {
            "summary": {"require_retest": True},
            "scenarios": selected,
        }
        signals = build_parent_acceptance_ensemble_signals(
            report=report,
            upstream_report={},
            instrument_id=InstrumentId.from_str("BTCUSDT-PERP.BINANCE"),
        )
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.ts_event, signal.observed_time_ns + 1)
        details = json.loads(signal.details_json)
        self.assertIn("parent_acceptance", details)
        self.assertEqual(
            details["episode_selection"],
            "FIRST_PARENT_ACCEPTED_5S_OR_FULL_15S_RETEST",
        )
        serialized = json.dumps(details).lower()
        for forbidden in ("path", "mfe", "mae", "terminal", "realized_r"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import unittest

import pandas as pd

import diagnose_impact_resilience_1s as impact
from local_auction_state_router import (
    acceptance_direction,
    source_level_retest,
)
from local_auction_state_scenario import build_auction_state_signals
from nautilus_trader.model.identifiers import InstrumentId
import run_local_liquidity_sweep_mss_retest as local


class LocalAuctionStateRouterTests(unittest.TestCase):
    @staticmethod
    def _logic() -> local.LocalSweepMSSLogic:
        return local.LocalSweepMSSLogic(
            maximum_retest_bars=3,
        )

    @staticmethod
    def _row(**updates: float) -> pd.Series:
        values = {
            "open": 99.8,
            "high": 100.6,
            "low": 99.7,
            "close": 100.5,
            "atr": 1.0,
            "range": 0.9,
            "signed_quote": 200.0,
            "quote_volume": 1000.0,
            "imbalance": 0.2,
            "signed_flow_reference": 100.0,
            "quote_volume_reference": 500.0,
            "imbalance_reference": 0.1,
            "body": 0.7,
            "body_atr": 0.7,
            "body_reference": 0.2,
            "price_efficiency": 0.78,
            "close_location": 0.89,
        }
        values.update(updates)
        return pd.Series(values)

    def test_upper_outside_displacement_routes_long_acceptance(self) -> None:
        pool = impact.Pool("15SH-a", "15S", "UPPER", 100.0, 1, 2)
        self.assertEqual(
            acceptance_direction(self._row(), pool, self._logic()),
            "LONG",
        )

    def test_outside_close_without_directional_flow_is_not_acceptance(self) -> None:
        pool = impact.Pool("15SH-a", "15S", "UPPER", 100.0, 1, 2)
        self.assertIsNone(
            acceptance_direction(
                self._row(signed_quote=-200.0, imbalance=-0.2),
                pool,
                self._logic(),
            )
        )

    def test_low_efficiency_outside_wick_is_not_acceptance(self) -> None:
        pool = impact.Pool("15SH-a", "15S", "UPPER", 100.0, 1, 2)
        self.assertIsNone(
            acceptance_direction(
                self._row(price_efficiency=0.2),
                pool,
                self._logic(),
            )
        )

    def test_broken_source_level_must_retest_and_hold(self) -> None:
        bars = pd.DataFrame(
            [
                self._row(),
                self._row(
                    open=100.4,
                    high=100.7,
                    low=99.95,
                    close=100.6,
                    signed_quote=50.0,
                    imbalance=0.05,
                    close_location=0.87,
                ),
            ]
        )
        index, reason = source_level_retest(
            bars,
            contact_index=0,
            direction="LONG",
            source_level=100.0,
            logic=self._logic(),
        )
        self.assertEqual(index, 1)
        self.assertEqual(reason, "SOURCE_LEVEL_RETEST_ACCEPTED")

    def test_completed_close_back_inside_invalidates_acceptance(self) -> None:
        bars = pd.DataFrame(
            [
                self._row(),
                self._row(
                    open=100.4,
                    high=100.5,
                    low=99.7,
                    close=99.9,
                    signed_quote=-50.0,
                    imbalance=-0.05,
                    close_location=0.25,
                ),
            ]
        )
        index, reason = source_level_retest(
            bars,
            contact_index=0,
            direction="LONG",
            source_level=100.0,
            logic=self._logic(),
        )
        self.assertIsNone(index)
        self.assertEqual(reason, "ACCEPTED_BREAK_CLOSED_BACK_INSIDE")

    def test_acceptance_signal_contains_only_observed_state(self) -> None:
        observed = 44_999_999_999
        report = {
            "summary": {"require_retest": True},
            "scenarios": [
                {
                    "scenario_id": "accept-1",
                    "outcome": "ENTRY_READY",
                    "branch": "ACCEPTANCE_CONTINUATION",
                    "direction": "LONG",
                    "entry": 101.0,
                    "stop": 99.8,
                    "target": 103.0,
                    "expected_rr": 1.6666666667,
                    "source_pool_id": "15SH-source",
                    "observed_time_ns": observed,
                    "sweep": {"timestamp_ns": 14_999_999_999},
                    "mss": {"timestamp_ns": 14_999_999_999},
                    "retest": {"timestamp_ns": observed},
                    "target_pool": {
                        "pool_id": "15SH-target",
                        "timeframe": "15S",
                        "level": 103.0,
                        "confirmed_ts_ns": 1,
                    },
                }
            ],
        }
        signals = build_auction_state_signals(
            report=report,
            upstream_report={},
            instrument_id=InstrumentId.from_str("BTCUSDT-PERP.BINANCE"),
        )
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.ts_event, observed + 1)
        details = json.loads(signal.details_json)
        self.assertEqual(details["branch"], "ACCEPTANCE_CONTINUATION")
        serialized = json.dumps(details).lower()
        for forbidden in ("path", "mfe", "mae", "terminal", "realized_r"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()

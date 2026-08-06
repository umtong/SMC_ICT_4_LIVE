from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from derive_nt_lvcfr_v15_signals import (
    EVENT_RANGE_CHOCH_REVERSAL,
    FLOW_CONFIRMED_EVENT_ACCEPTANCE,
    derive_v15,
)
from nt_lvcfr_data import CandidateConfig, MinuteFact, NS_PER_MINUTE


def bar(
    minute: int,
    *,
    open_: float = 100.0,
    high: float = 100.5,
    low: float = 99.5,
    close: float = 100.0,
    flow: float = 0.0,
) -> MinuteFact:
    notional = 1_000_000.0
    return MinuteFact(
        minute_index=minute,
        open=open_,
        high=high,
        low=low,
        close=close,
        notional=notional,
        signed_notional=flow * notional,
    )


def base_series() -> tuple[list[MinuteFact], list[MinuteFact]]:
    futures: list[MinuteFact] = []
    spot: list[MinuteFact] = []
    for minute in range(760, 1130):
        item = bar(minute)
        futures.append(item)
        spot.append(item)
    # The frozen 10-minute event is 1000..1009 with range [99, 101].
    for index, minute in enumerate(range(1000, 1010)):
        item = bar(
            minute,
            open_=100.0,
            high=101.0 if index == 4 else 100.7,
            low=99.0 if index == 7 else 99.3,
            close=100.4,
            flow=0.1,
        )
        futures[minute - 760] = item
        spot[minute - 760] = item
    return futures, spot


def source_signal() -> dict[str, object]:
    return {
        "scenario_id": "NT-LVCFR-test",
        "direction": 1,
        "first_start_time_ns": 1000 * NS_PER_MINUTE,
        "first_end_time_ns": 1005 * NS_PER_MINUTE,
        "confirm_time_ns": 1010 * NS_PER_MINUTE,
        "eligible_time_ns": 1011 * NS_PER_MINUTE,
        "initial_stop": 98.6,
        "atr": 2.0,
        "details": {},
    }


class FlowImpactAuctionTests(unittest.TestCase):
    def run_case(
        self,
        futures: list[MinuteFact],
        spot: list[MinuteFact],
    ) -> tuple[list[dict[str, object]], dict[str, object]]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "raw"
            (raw / "futures_kline").mkdir(parents=True)
            (raw / "spot_kline").mkdir(parents=True)
            (raw / "futures_kline" / "dummy.zip").touch()
            (raw / "spot_kline" / "dummy.zip").touch()
            source = root / "signals-v1.json"
            output = root / "signals.json"
            manifest = root / "manifest.json"
            source.write_text(json.dumps([source_signal()]), encoding="utf-8")
            with patch(
                "derive_nt_lvcfr_v15_signals.load_kline_minutes",
                side_effect=[futures, spot],
            ):
                routed = derive_v15(
                    source_signals=source,
                    raw_root=raw,
                    output_signals=output,
                    output_manifest=manifest,
                    auction_expiry_minutes=6,
                )
            return routed, json.loads(manifest.read_text(encoding="utf-8"))

    def test_two_completed_minutes_and_both_markets_confirm_acceptance(self) -> None:
        futures, spot = base_series()
        futures[1010 - 760] = bar(1010, high=102.3, low=100.8, close=102.0, flow=0.3)
        futures[1011 - 760] = bar(1011, high=102.6, low=101.2, close=102.2, flow=0.2)
        spot[1010 - 760] = bar(1010, high=102.2, low=100.9, close=102.0, flow=0.25)
        spot[1011 - 760] = bar(1011, high=102.5, low=101.1, close=102.1, flow=0.15)
        routed, manifest = self.run_case(futures, spot)
        self.assertEqual(len(routed), 1)
        signal = routed[0]
        self.assertEqual(signal["scenario_kind"], FLOW_CONFIRMED_EVENT_ACCEPTANCE)
        self.assertEqual(signal["direction"], 1)
        self.assertEqual(signal["confirm_time_ns"], 1012 * NS_PER_MINUTE)
        self.assertAlmostEqual(signal["initial_stop"], 99.6)
        self.assertEqual(signal["structural_protection_trigger"], 101.0)
        self.assertEqual(manifest["state_counts"][FLOW_CONFIRMED_EVENT_ACCEPTANCE], 1)

    def test_opposite_close_overrides_pending_same_side_hold(self) -> None:
        futures, spot = base_series()
        futures[1010 - 760] = bar(1010, high=102.3, low=100.8, close=102.0, flow=0.3)
        spot[1010 - 760] = bar(1010, high=102.2, low=100.9, close=102.0, flow=0.3)
        futures[1011 - 760] = bar(1011, high=102.1, low=97.7, close=98.0, flow=-0.4)
        spot[1011 - 760] = bar(1011, high=102.0, low=97.8, close=98.1, flow=-0.4)
        routed, _ = self.run_case(futures, spot)
        self.assertEqual(len(routed), 1)
        signal = routed[0]
        self.assertEqual(signal["scenario_kind"], EVENT_RANGE_CHOCH_REVERSAL)
        self.assertEqual(signal["direction"], -1)
        self.assertEqual(signal["confirm_time_ns"], 1012 * NS_PER_MINUTE)

    def test_failed_flow_agreement_is_not_retried_as_continuation(self) -> None:
        futures, spot = base_series()
        futures[1010 - 760] = bar(1010, high=102.3, low=100.8, close=102.0, flow=0.3)
        futures[1011 - 760] = bar(1011, high=102.6, low=101.2, close=102.2, flow=0.2)
        spot[1010 - 760] = bar(1010, high=102.2, low=100.9, close=102.0, flow=-0.25)
        spot[1011 - 760] = bar(1011, high=102.5, low=101.1, close=102.1, flow=-0.15)
        # A later same-side break with positive flow must not become a second attempt.
        for minute in range(1012, 1016):
            futures[minute - 760] = bar(minute, high=103.0, low=101.5, close=102.5, flow=0.4)
            spot[minute - 760] = bar(minute, high=103.0, low=101.5, close=102.5, flow=0.4)
        routed, manifest = self.run_case(futures, spot)
        self.assertEqual(routed, [])
        self.assertEqual(manifest["counters"]["failed_same_side_acceptances"], 1)
        self.assertEqual(manifest["counters"]["expired_events"], 1)

    def test_project_risk_and_native_execution_contract_remain_fixed(self) -> None:
        config = CandidateConfig.load(Path(__file__).with_name("nt_lvcfr_v15_config.json"))
        self.assertEqual(config.risk_fraction, 0.03)
        self.assertEqual(config.validation_weeks, ("2024-01-08", "2025-06-23", "2022-05-16"))
        strategy = Path(__file__).with_name("nt_lvcfr_strategy.py").read_text(encoding="utf-8")
        self.assertIn("self.submit_order(order)", strategy)
        self.assertIn("self.portfolio.net_position", strategy)
        self.assertNotIn("simulate_fill", strategy)
        self.assertNotIn("synthetic_nav", strategy)


if __name__ == "__main__":
    unittest.main(verbosity=2)

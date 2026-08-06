from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from derive_nt_lvcfr_v2_signals import NS_PER_MINUTE
from derive_nt_lvcfr_v14_signals import (
    UNROUTED_EVENT_RANGE_CHOCH_FALLBACK,
    derive_v14,
    first_completed_event_range_break,
    nearest_waypoint_ahead,
)
from nt_lvcfr_data import CandidateConfig


def _bar(close: float, *, high: float | None = None, low: float | None = None) -> dict[str, float]:
    return {
        "open": close,
        "high": close if high is None else high,
        "low": close if low is None else low,
        "close": close,
    }


def _source(*, suffix: str, event_start: int, direction: int) -> dict[str, object]:
    event_end = event_start + 10
    return {
        "scenario_id": f"NT-LVCFR-V1-{suffix}",
        "first_start_time_ns": event_start * NS_PER_MINUTE,
        "confirm_time_ns": event_end * NS_PER_MINUTE,
        "eligible_time_ns": (event_end + 1) * NS_PER_MINUTE,
        "direction": direction,
        "initial_stop": 99.0,
        "atr": 2.0,
        "details": {},
    }


class FallbackSequenceTests(unittest.TestCase):
    def test_intrabar_touch_is_not_a_completed_fallback_break(self) -> None:
        futures = {
            10: _bar(109.0, high=112.0, low=108.0),
            11: _bar(111.0, high=112.0, low=109.0),
        }
        self.assertEqual(
            first_completed_event_range_break(
                futures,
                start_minute=10,
                event_low=90.0,
                event_high=110.0,
                expiry_minutes=2,
            ),
            (11, 1, 111.0),
        )

    def test_nearest_waypoint_is_selected_only_ahead(self) -> None:
        self.assertEqual(
            nearest_waypoint_ahead(
                direction=1,
                reference_price=100.0,
                candidates=[("EXTERNAL", 120.0), ("EQUILIBRIUM", 110.0), ("BEHIND", 90.0)],
            ),
            ("EQUILIBRIUM", 110.0),
        )
        self.assertIsNone(
            nearest_waypoint_ahead(
                direction=-1,
                reference_price=100.0,
                candidates=[("ABOVE", 110.0)],
            )
        )

    def test_v11_priority_prevents_duplicate_fallback_and_same_side_stays_no_trade(self) -> None:
        routed = _source(suffix="v11", event_start=100, direction=1)
        fallback = _source(suffix="fallback", event_start=200, direction=1)
        same_side = _source(suffix="same", event_start=300, direction=1)
        sources = [routed, fallback, same_side]

        futures: dict[int, dict[str, float]] = {}
        for start in (100, 200, 300):
            futures[start - 2] = _bar(95.0)
            futures[start - 1] = _bar(105.0)
            for minute in range(start, start + 10):
                futures[minute] = _bar(100.0, high=110.0, low=90.0)
        futures[110] = _bar(89.0)   # Opposite break, but V11 already owns it.
        futures[210] = _bar(89.0)   # Opposite break, valid fallback.
        futures[310] = _bar(111.0)  # Same-side break, no fallback.

        v11_signal = dict(routed)
        v11_signal["scenario_id"] = "NT-LVCFR-V7-OWNED-v11"
        v11_signal["scenario_kind"] = "VALUE_EDGE_CONTINUATION"
        v11_signal["entry_kind"] = "CONTINUATION"
        v11_signal["details"] = {
            "v1_confirm_time_ns": int(routed["confirm_time_ns"]),
        }

        def fake_derive_v7(**kwargs: object) -> list[dict[str, object]]:
            output_manifest = Path(kwargs["output_manifest"])
            output_signals = Path(kwargs["output_signals"])
            output_manifest.write_text(
                json.dumps({"state_counts": {"VALUE_EDGE_CONTINUATION": 1}}),
                encoding="utf-8",
            )
            output_signals.write_text(json.dumps([v11_signal]), encoding="utf-8")
            return [v11_signal]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "signals-v1.json"
            output_path = root / "signals.json"
            manifest_path = root / "manifest.json"
            source_path.write_text(json.dumps(sources), encoding="utf-8")
            with (
                patch("derive_nt_lvcfr_v14_signals.derive_v7", side_effect=fake_derive_v7),
                patch("derive_nt_lvcfr_v14_signals.load_futures_minutes", return_value=futures),
            ):
                combined = derive_v14(
                    source_signals=source_path,
                    raw_root=root,
                    output_signals=output_path,
                    output_manifest=manifest_path,
                    dealing_range_minutes=2,
                    fallback_expiry_minutes=2,
                    fallback_stop_buffer_atr=0.20,
                )

            self.assertEqual(len(combined), 2)
            states = [str(item.get("scenario_kind")) for item in combined]
            self.assertEqual(states.count("VALUE_EDGE_CONTINUATION"), 1)
            self.assertEqual(states.count(UNROUTED_EVENT_RANGE_CHOCH_FALLBACK), 1)
            fallback_signal = next(
                item
                for item in combined
                if item.get("scenario_kind") == UNROUTED_EVENT_RANGE_CHOCH_FALLBACK
            )
            self.assertEqual(fallback_signal["direction"], -1)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["v11_signal_count"], 1)
            self.assertEqual(manifest["fallback_signal_count"], 1)
            self.assertEqual(
                manifest["fallback_no_trade_reasons"]["SAME_SIDE_FIRST_BREAK_NO_FALLBACK"],
                1,
            )


class V14ContractTests(unittest.TestCase):
    def test_v14_keeps_detector_execution_risk_and_validation_order(self) -> None:
        v11 = CandidateConfig.load(Path(__file__).with_name("nt_lvcfr_v11_config.json"))
        v14 = CandidateConfig.load(Path(__file__).with_name("nt_lvcfr_v14_config.json"))
        self.assertEqual(v14.first_displacement_bp, v11.first_displacement_bp)
        self.assertEqual(v14.second_activity_min, v11.second_activity_min)
        self.assertEqual(v14.second_futures_flow_max, v11.second_futures_flow_max)
        self.assertEqual(v14.second_spot_flow_min, v11.second_spot_flow_min)
        self.assertEqual(v14.total_oi_drop_bp, v11.total_oi_drop_bp)
        self.assertEqual(v14.initial_stop_buffer_atr, 0.20)
        self.assertEqual(v14.reversal_target_net_r, 1.5)
        self.assertEqual(v14.risk_fraction, 0.03)
        self.assertEqual(v14.validation_weeks, v11.validation_weeks)

    def test_router_contains_no_fill_or_nav_simulation(self) -> None:
        source = Path(__file__).with_name("derive_nt_lvcfr_v14_signals.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("V11_VALIDATED_SCENARIO_ROUTER", source)
        self.assertIn("SAME_SIDE_FIRST_BREAK_NO_FALLBACK", source)
        self.assertNotIn("simulate_fill", source)
        self.assertNotIn("synthetic_nav", source)
        strategy = Path(__file__).with_name("nt_lvcfr_strategy.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("self.submit_order(order)", strategy)
        self.assertIn("self.portfolio.net_position", strategy)


if __name__ == "__main__":
    unittest.main(verbosity=2)

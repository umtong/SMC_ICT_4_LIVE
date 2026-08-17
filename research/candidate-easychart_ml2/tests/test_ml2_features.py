from __future__ import annotations

from types import SimpleNamespace
import unittest

from ml2_features import CausalFeatureBook, FEATURE_NAMES, build_plan_features, classify_plan_family


class Side:
    def __init__(self, name: str) -> None:
        self.name = name


class Candle:
    def __init__(self, ts: int, close: float, volume: float = 1000.0) -> None:
        self.ts_close_ns = ts
        self.open = close - 0.5
        self.high = close + 1.0
        self.low = close - 1.0
        self.close = close
        self.quote_volume = volume
        self.volume = volume


def _plan(**updates):
    values = dict(
        plan_id="p1",
        causal_event_id="e1",
        symbol="BTCUSDT",
        family="HORIZONTAL_FLIP_CONTINUATION",
        side=Side("LONG"),
        observed_time_ns=60_000_000_000,
        entry=100.0,
        stop=99.0,
        target=102.0,
        gross_rr=2.0,
        setup_id="s1",
        higher_zone_id="higher",
        higher_zone_kind="HORIZONTAL_SUPPORT",
        higher_strength_ratio=2.5,
        lower_zone_id="lower",
        lower_zone_kind="BULLISH_FVG",
        lower_strength_ratio=1.5,
        trigger_zone_id="trigger",
        trigger_strength_ratio=0.8,
        target_zone_id="target",
        target_zone_kind="SWING_HIGH",
        overlap_lower=99.8,
        overlap_upper=100.1,
        interaction_time_ns=50_000_000_000,
        trigger_time_ns=55_000_000_000,
        scenario_path="ACCEPTANCE",
        setup_observed_time_ns=40_000_000_000,
        trigger_zone_kind="FIRST_RESPONSE_ALIGNED_INITIATIVE",
        source_rule_count=5,
        rule_provenance=("UNRELATED_GLOBAL_CURRICULUM",),
        scale_name="HORIZONTAL_FLIP",
        higher_timeframe_minutes=15,
        decision_timeframe_minutes=5,
        trigger_timeframe_minutes=1,
    )
    values.update(updates)
    return SimpleNamespace(**values)


def _zone(lower: float, upper: float, strength: float, first_touch=None):
    return SimpleNamespace(
        lower=lower,
        upper=upper,
        strength_ratio=strength,
        formed_time_ns=10_000_000_000,
        first_touch_time_ns=first_touch,
    )


class FeaturesTest(unittest.TestCase):
    def test_schema_is_symbol_agnostic_and_uses_real_strength_fields(self) -> None:
        self.assertEqual(len(FEATURE_NAMES), len(set(FEATURE_NAMES)))
        self.assertFalse(any("symbol" in name.lower() for name in FEATURE_NAMES))
        self.assertEqual(len(FEATURE_NAMES), 169)
        book = CausalFeatureBook()
        ts = 60_000_000_000
        items = [
            (symbol, 1, Candle(ts, close))
            for symbol, close in (
                ("BTCUSDT", 100.0),
                ("ETHUSDT", 101.0),
                ("SOLUSDT", 99.5),
                ("XRPUSDT", 100.5),
            )
        ]
        items.extend(
            [
                ("BTCUSDT", 5, Candle(ts, 100.0)),
                ("BTCUSDT", 15, Candle(ts, 100.0)),
                ("BTCUSDT", 60, Candle(ts, 100.0)),
            ],
        )
        book.observe_bucket(items)
        zones = {
            "higher": _zone(99.0, 99.5, 2.0),
            "lower": _zone(99.7, 100.0, 1.0),
            "trigger": _zone(99.8, 100.1, 0.5, first_touch=55_000_000_000),
            "target": _zone(101.9, 102.1, 1.2),
        }
        family, features = build_plan_features(
            _plan(),
            feature_book=book,
            setup_factor_state=SimpleNamespace(side=Side("SHORT")),
            pre_response_factor_state=SimpleNamespace(side=Side("LONG")),
            zone_lookup=zones.get,
        )
        self.assertEqual(family, "ACCEPTED_BREAK")
        self.assertEqual(features["higher_strength"], 2.5)
        self.assertEqual(features["lower_strength"], 1.5)
        self.assertEqual(features["trigger_strength"], 0.8)
        self.assertEqual(features["setup_factor_opposed"], 1.0)
        self.assertEqual(features["pre_response_factor_aligned"], 1.0)
        self.assertEqual(features["zone_target_available"], 1.0)
        self.assertEqual(features["tf60_available"], 1.0)
        self.assertEqual(features["cross_available"], 1.0)
        self.assertEqual(set(features), set(FEATURE_NAMES))

    def test_family_classification(self) -> None:
        self.assertEqual(
            classify_plan_family(_plan(family="FAKEOUT_SWEEP_RECLAIM", scenario_path="REJECTION")),
            "SWEEP_RECLAIM",
        )
        self.assertEqual(
            classify_plan_family(_plan(family="MATURE_DIAGONAL_ACCEPTANCE", scenario_path="ACCEPTANCE")),
            "ACCEPTED_BREAK",
        )
        self.assertEqual(
            classify_plan_family(_plan(family="CHANNEL_FOUR_POINT", scenario_path="ROTATION")),
            "RANGE_ROTATION",
        )


if __name__ == "__main__":
    unittest.main()

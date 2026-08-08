from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from state_engine import EngineConfig, FlowBar, LiquidityStateEngine, MINUTE_NS

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
UTC = timezone.utc
NY = ZoneInfo("America/New_York")


def bar_at(dt: datetime) -> FlowBar:
    source_ns = int(dt.timestamp() * 1_000_000_000)
    return FlowBar(source_ns + MINUTE_NS, 100, 101, 99, 100.5, 10, 7, 10)


class SessionOrbContracts(unittest.TestCase):
    def test_true_settlement_anchor_and_off_session_placebo(self):
        base = LiquidityStateEngine(EngineConfig.from_mapping(CONFIG))
        placebo = LiquidityStateEngine(
            EngineConfig.from_mapping(CONFIG, ablation="off-session")
        )
        eight = bar_at(datetime(2023, 1, 2, 8, 0, tzinfo=UTC))
        ten = bar_at(datetime(2023, 1, 2, 10, 0, tzinfo=UTC))
        self.assertEqual(base._anchor(eight)[0], "SETTLEMENT")
        self.assertIsNone(placebo._anchor(eight))
        self.assertEqual(placebo._anchor(ten)[0], "SETTLEMENT")

    def test_new_york_anchor_tracks_daylight_saving(self):
        engine = LiquidityStateEngine(EngineConfig.from_mapping(CONFIG))
        winter = datetime(2023, 1, 3, 9, 30, tzinfo=NY).astimezone(UTC)
        summer = datetime(2023, 7, 3, 9, 30, tzinfo=NY).astimezone(UTC)
        self.assertEqual(engine._anchor(bar_at(winter))[0], "NYSE_OPEN")
        self.assertEqual(engine._anchor(bar_at(summer))[0], "NYSE_OPEN")
        self.assertNotEqual(winter.hour, summer.hour)

    def test_relative_volume_threshold_uses_prior_ranges_only(self):
        engine = LiquidityStateEngine(EngineConfig.from_mapping(CONFIG))
        history = engine.history["SETTLEMENT"]
        for value in range(1, 21):
            history.append(float(value))
        threshold = engine._quantile(history, 0.75)
        self.assertEqual(threshold, 15.0)

    def test_single_ablation_flags(self):
        base = EngineConfig.from_mapping(CONFIG)
        no_volume = EngineConfig.from_mapping(CONFIG, ablation="no-relative-volume")
        no_flow = EngineConfig.from_mapping(CONFIG, ablation="no-flow")
        self.assertTrue(base.use_relative_volume)
        self.assertFalse(no_volume.use_relative_volume)
        self.assertTrue(base.use_flow)
        self.assertFalse(no_flow.use_flow)


if __name__ == "__main__":
    unittest.main()

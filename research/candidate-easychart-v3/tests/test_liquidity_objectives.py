from __future__ import annotations

import unittest

from domain import Candle
from easychart_zones import ZoneSide
from liquidity import CausalLiquidityDetector, ObjectiveKind, ObjectiveZone


class LiquidityObjectiveLifecycleTest(unittest.TestCase):
    def zone(self) -> ObjectiveZone:
        return ObjectiveZone(
            zone_id="HIGH",
            kind=ObjectiveKind.SWING_HIGH,
            side=ZoneSide.RESISTANCE,
            timeframe_minutes=15,
            lower=100.0,
            upper=100.1,
            invalidation=100.2,
            impulse_extreme=100.0,
            formed_index=0,
            formed_time_ns=0,
            observed_time_ns=0,
            formation_indices=(0,),
            strength_ratio=2.0,
            pivot_span=6,
        )

    def test_equal_touch_keeps_liquidity_but_one_tick_sweep_spends_it(self) -> None:
        detector = CausalLiquidityDetector("BTCUSDT", 15, 0.1)
        zone = self.zone()
        detector.zones.append(zone)
        detector._active[zone.zone_id] = zone
        detector.observe_price(Candle(1, 99.0, 100.0, 98.5, 99.5, 1.0))
        self.assertTrue(zone.active)
        detector.observe_price(Candle(2, 99.5, 100.1, 99.0, 99.7, 1.0))
        self.assertFalse(zone.active)
        self.assertEqual(zone.consumed_time_ns, 2)
        self.assertEqual(detector.diagnostics.get("swing_high_swept"), 1)


if __name__ == "__main__":
    unittest.main()

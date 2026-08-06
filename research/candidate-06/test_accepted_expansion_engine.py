from __future__ import annotations

from datetime import datetime, timezone
import unittest

from accepted_expansion_engine import AcceptedExpansionPullbackEngine, _AuctionBar
from lrb_types import BarObservation, PrimitiveSnapshot


def ns(hour: int, minute: int) -> int:
    return int(datetime(2024, 2, 26, hour, minute, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def snap(index: int, timestamp: int, open_: float, high: float, low: float, close: float, flow: float = 0.2) -> PrimitiveSnapshot:
    width = max(high - low, 0.1)
    return PrimitiveSnapshot(
        index=index,
        observation=BarObservation(timestamp, open_, high, low, close, 100.0, 50.0 * (flow + 1.0), 10),
        ready=True,
        atr=1.0,
        rel_volume=1.5,
        flow_ratio=flow,
        body_atr=abs(close - open_),
        range_atr=width,
        upper_wick_fraction=max(high - max(open_, close), 0.0) / width,
        lower_wick_fraction=max(min(open_, close) - low, 0.0) / width,
        close_location=(close - low) / width,
        upper_fast=120.0,
        lower_fast=80.0,
        upper_slow=125.0,
        lower_slow=75.0,
        slow_mid=100.0,
        range_position=0.5,
        upper_pool_touches=2,
        lower_pool_touches=2,
    )


def auction(end: int, open_: float, high: float, low: float, close: float, volume: float = 100.0, flow: float = 0.2) -> _AuctionBar:
    return _AuctionBar(end - 1, end, open_, high, low, close, volume, volume * (flow + 1.0) / 2.0, 100)


class AcceptedExpansionTests(unittest.TestCase):
    def params(self, *, compression: bool = False, period: int = 30):
        return {
            "aepr_period_minutes": period,
            "aepr_atr_bars": 2,
            "aepr_volume_bars": 2,
            "aepr_compression_bars": 2,
            "aepr_expansion_range_atr": 0.9,
            "aepr_expansion_body_fraction": 0.55,
            "aepr_expansion_relative_volume": 1.0,
            "aepr_expansion_flow_ratio": 0.04,
            "aepr_expansion_close_location": 0.72,
            "aepr_acceptance_close_atr": 0.03,
            "aepr_require_source_compression": compression,
            "aepr_source_compression_ratio": 0.85,
            "aepr_bias_lifetime_periods": 3.0,
            "aepr_bias_invalidation_fraction": 0.5,
            "aepr_retest_band_atr": 0.12,
            "aepr_response_body_atr_1m": 0.12,
            "aepr_response_flow_ratio": 0.0,
            "aepr_response_close_location": 0.55,
            "aepr_response_mode": "BODY_FLOW",
            "aepr_stop_buffer_atr": 0.05,
            "aepr_extension_fraction": 0.75,
            "minimum_structural_rr": 1.10,
        }

    def seeded(self, *, compression: bool = False):
        engine = AcceptedExpansionPullbackEngine(self.params(compression=compression))
        first = auction(1, 95.0, 101.0, 94.0, 100.0)
        second = auction(2, 99.0, 102.0, 98.0, 101.0)
        engine._history = [first, second]
        engine._true_ranges = [7.0, 4.0]
        engine._ranges = [7.0, 4.0]
        engine._volumes = [100.0, 100.0]
        return engine

    def test_completed_30m_bar_is_not_visible_early(self):
        engine = AcceptedExpansionPullbackEngine(self.params(period=30))
        for i in range(29):
            engine.observe(snap(i, ns(0, i + 1), 100.0, 101.0, 99.0, 100.5), allow_new=True)
            self.assertEqual(len(engine._history), 0)
        engine.observe(snap(29, ns(0, 30), 100.5, 102.0, 100.0, 101.5), allow_new=True)
        self.assertEqual(len(engine._history), 1)

    def test_accepted_close_beyond_previous_high_starts_bias(self):
        engine = self.seeded()
        bar = auction(3, 101.0, 108.0, 100.5, 107.5, 140.0, 0.4)
        transition = engine._start_expansion(bar, snap(10, 3, 101.0, 108.0, 100.5, 107.5))
        self.assertIsNotNone(transition)
        assert engine._episode is not None
        self.assertEqual(engine._episode.direction, "LONG")
        self.assertEqual(engine._episode.boundary, 102.0)

    def test_compression_variant_rejects_uncompressed_source(self):
        engine = self.seeded(compression=True)
        # Source range 4 versus median 5.5 is compressed and should pass.
        bar = auction(3, 101.0, 108.0, 100.5, 107.5, 140.0, 0.4)
        self.assertIsNotNone(engine._start_expansion(bar, snap(10, 3, 101.0, 108.0, 100.5, 107.5)))
        engine = self.seeded(compression=True)
        engine._ranges = [4.0, 7.0]
        engine._history[-1] = auction(2, 98.0, 105.0, 98.0, 101.0)
        bar = auction(3, 101.0, 111.0, 100.5, 110.5, 150.0, 0.4)
        self.assertIsNone(engine._start_expansion(bar, snap(10, 3, 101.0, 111.0, 100.5, 110.5)))

    def test_touch_bar_cannot_arm_and_later_response_can(self):
        engine = self.seeded()
        bar = auction(3, 101.0, 108.0, 100.5, 107.5, 140.0, 0.4)
        engine._start_expansion(bar, snap(10, 3, 101.0, 108.0, 100.5, 107.5))
        touch = engine._advance(snap(11, 11, 103.0, 103.5, 101.8, 102.3, -0.2), allow_new=True)
        self.assertIsNone(touch.signal)
        response = engine._advance(snap(12, 12, 102.2, 104.0, 102.0, 103.8, 0.2), allow_new=True)
        self.assertIsNotNone(response.signal)
        assert response.signal is not None
        self.assertEqual(response.signal.family, "AEPR")
        self.assertEqual(response.signal.direction, "LONG")

    def test_expansion_origin_loss_resets(self):
        engine = self.seeded()
        bar = auction(3, 101.0, 108.0, 100.5, 107.5, 140.0, 0.4)
        engine._start_expansion(bar, snap(10, 3, 101.0, 108.0, 100.5, 107.5))
        result = engine._advance(snap(11, 11, 100.5, 101.5, 99.0, 100.0, -0.3), allow_new=True)
        self.assertEqual(result.transitions[-1].reason_code, "BULLISH_ACCEPTED_EXPANSION_INVALIDATED")
        self.assertIsNone(engine._episode)


if __name__ == "__main__":
    unittest.main()

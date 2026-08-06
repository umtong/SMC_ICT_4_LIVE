from __future__ import annotations

from datetime import datetime, timezone
import unittest

from displacement_rebalance_engine import FiveMinuteDisplacementRebalanceEngine, _AggregateBar
from lrb_types import BarObservation, PrimitiveSnapshot


def snapshot(index: int, ts_ns: int, open_: float, high: float, low: float, close: float, flow: float = 0.2) -> PrimitiveSnapshot:
    width = max(high - low, 0.1)
    return PrimitiveSnapshot(
        index=index,
        observation=BarObservation(
            ts_ns=ts_ns,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=100.0,
            taker_buy_volume=50.0 * (flow + 1.0),
            trades=10,
        ),
        ready=True,
        atr=1.0,
        rel_volume=1.5,
        flow_ratio=flow,
        body_atr=abs(close - open_),
        range_atr=width,
        upper_wick_fraction=0.0,
        lower_wick_fraction=0.0,
        close_location=(close - low) / width,
        upper_fast=110.0,
        lower_fast=90.0,
        upper_slow=115.0,
        lower_slow=85.0,
        slow_mid=100.0,
        range_position=0.5,
        upper_pool_touches=2,
        lower_pool_touches=2,
    )


def aggregate(end_ts_ns: int, open_: float, high: float, low: float, close: float, volume: float = 100.0, flow: float = 0.2) -> _AggregateBar:
    return _AggregateBar(
        start_ts_ns=end_ts_ns - 4 * 60_000_000_000,
        end_ts_ns=end_ts_ns,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        taker_buy_volume=volume * (flow + 1.0) / 2.0,
        trades=100,
    )


class DisplacementRebalanceEngineTests(unittest.TestCase):
    def params(self, zone_mode: str = "STRICT_FVG") -> dict[str, float | int | str]:
        return {
            "dirc_aggregate_minutes": 5,
            "dirc_atr_bars": 2,
            "dirc_volume_bars": 2,
            "dirc_displacement_body_atr": 0.8,
            "dirc_displacement_body_fraction": 0.65,
            "dirc_displacement_relative_volume": 1.1,
            "dirc_displacement_flow_ratio": 0.08,
            "dirc_displacement_close_location": 0.75,
            "dirc_zone_mode": zone_mode,
            "dirc_projection_fraction": 1.0,
            "dirc_rebalance_bars": 20,
            "dirc_response_body_atr_1m": 0.12,
            "dirc_response_flow_ratio": 0.0,
            "dirc_response_close_location": 0.55,
            "dirc_stop_buffer_atr5": 0.05,
            "minimum_structural_rr": 1.25,
        }

    def seeded(self, zone_mode: str = "STRICT_FVG") -> FiveMinuteDisplacementRebalanceEngine:
        engine = FiveMinuteDisplacementRebalanceEngine(self.params(zone_mode))
        first = aggregate(1, 100.0, 101.0, 99.0, 100.0)
        second = aggregate(2, 100.0, 102.0, 99.5, 101.0)
        engine._history = [first, second]
        engine._true_ranges = [2.0, 2.5]
        engine._volumes = [100.0, 100.0]
        return engine

    def test_completed_five_minute_bar_is_known_only_at_last_source_minute(self) -> None:
        engine = FiveMinuteDisplacementRebalanceEngine(self.params())
        base = datetime(2024, 2, 26, 0, 1, tzinfo=timezone.utc)
        for index in range(4):
            ts_ns = int((base.timestamp() + index * 60) * 1_000_000_000)
            engine.observe(snapshot(index, ts_ns, 100.0, 101.0, 99.0, 100.5), allow_new=True)
            self.assertEqual(len(engine._history), 0)
        final_ts_ns = int((base.timestamp() + 4 * 60) * 1_000_000_000)
        engine.observe(snapshot(4, final_ts_ns, 100.5, 101.5, 100.0, 101.0), allow_new=True)
        self.assertEqual(len(engine._history), 1)

    def test_strict_fvg_requires_a_true_three_bar_gap(self) -> None:
        engine = self.seeded("STRICT_FVG")
        no_gap = aggregate(3, 102.0, 106.0, 100.8, 105.5, 150.0, 0.4)
        transition = engine._start_episode(no_gap, snapshot(10, 3, 102.0, 106.0, 100.8, 105.5))
        self.assertIsNone(transition)
        self.assertIsNone(engine._episode)

        strict_gap = aggregate(4, 102.0, 106.0, 103.0, 105.5, 150.0, 0.4)
        transition = engine._start_episode(strict_gap, snapshot(10, 4, 102.0, 106.0, 103.0, 105.5))
        self.assertIsNotNone(transition)
        assert engine._episode is not None
        self.assertEqual(engine._episode.zone_low, 101.0)
        self.assertEqual(engine._episode.zone_high, 103.0)

    def test_displacement_origin_is_a_distinct_zone_definition(self) -> None:
        engine = self.seeded("DISPLACEMENT_BODY_ORIGIN")
        bar = aggregate(3, 102.0, 106.0, 100.8, 105.5, 150.0, 0.4)
        transition = engine._start_episode(bar, snapshot(10, 3, 102.0, 106.0, 100.8, 105.5))
        self.assertIsNotNone(transition)
        assert engine._episode is not None
        self.assertEqual(engine._episode.zone_low, 102.0)
        self.assertEqual(engine._episode.zone_high, 103.75)

    def test_touch_bar_cannot_emit_and_later_response_can(self) -> None:
        engine = self.seeded("STRICT_FVG")
        bar = aggregate(3, 102.0, 106.0, 103.0, 105.5, 150.0, 0.4)
        engine._start_episode(bar, snapshot(10, 3, 102.0, 106.0, 103.0, 105.5))
        touch = engine._advance_episode(
            snapshot(11, 11, 103.8, 104.4, 102.5, 103.5, -0.1),
            allow_new=True,
        )
        self.assertIsNone(touch.signal)
        self.assertEqual(touch.transitions[-1].next_state, "REBALANCE_TOUCHED")
        response = engine._advance_episode(
            snapshot(12, 12, 103.4, 104.4, 103.2, 104.1, 0.2),
            allow_new=True,
        )
        self.assertIsNotNone(response.signal)
        assert response.signal is not None
        self.assertEqual(response.signal.family, "DIRC")
        self.assertEqual(response.signal.direction, "LONG")

    def test_impulse_origin_invalidation_resets(self) -> None:
        engine = self.seeded("STRICT_FVG")
        bar = aggregate(3, 102.0, 106.0, 103.0, 105.5, 150.0, 0.4)
        engine._start_episode(bar, snapshot(10, 3, 102.0, 106.0, 103.0, 105.5))
        invalid = engine._advance_episode(
            snapshot(11, 11, 100.0, 100.5, 98.0, 98.5, -0.4),
            allow_new=True,
        )
        self.assertIsNone(invalid.signal)
        self.assertEqual(invalid.transitions[-1].reason_code, "BULLISH_IMPULSE_ORIGIN_INVALIDATED")
        self.assertIsNone(engine._episode)


if __name__ == "__main__":
    unittest.main()

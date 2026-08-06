from __future__ import annotations

from datetime import datetime, timezone
import unittest

from inventory_absorption_engine import InventoryAbsorptionPullbackEngine, _TrendBar
from lrb_types import BarObservation, PrimitiveSnapshot


def ns(hour: int, minute: int) -> int:
    return int(datetime(2024, 2, 26, hour, minute, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def snap(
    index: int,
    timestamp: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    flow: float = 0.0,
    volume: float = 100.0,
) -> PrimitiveSnapshot:
    width = max(high - low, 0.1)
    return PrimitiveSnapshot(
        index=index,
        observation=BarObservation(
            timestamp,
            open_,
            high,
            low,
            close,
            volume,
            volume * (flow + 1.0) / 2.0,
            10,
        ),
        ready=True,
        atr=1.0,
        rel_volume=1.5,
        flow_ratio=flow,
        body_atr=abs(close - open_),
        range_atr=width,
        upper_wick_fraction=max(high - max(open_, close), 0.0) / width,
        lower_wick_fraction=max(min(open_, close) - low, 0.0) / width,
        close_location=(close - low) / width,
        upper_fast=112.0,
        lower_fast=88.0,
        upper_slow=118.0,
        lower_slow=82.0,
        slow_mid=100.0,
        range_position=0.5,
        upper_pool_touches=2,
        lower_pool_touches=2,
    )


def trend_bar(
    end: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100.0,
    flow: float = 0.0,
) -> _TrendBar:
    return _TrendBar(
        end - 1,
        end,
        open_,
        high,
        low,
        close,
        volume,
        volume * (flow + 1.0) / 2.0,
        100,
    )


class InventoryAbsorptionTests(unittest.TestCase):
    def params(self, **overrides):
        params = {
            "iapc_period_minutes": 15,
            "iapc_atr_bars": 2,
            "iapc_volume_bars": 2,
            "iapc_breakout_lookback": 2,
            "iapc_acceptance_close_atr": 0.02,
            "iapc_regime_range_atr": 0.70,
            "iapc_regime_body_fraction": 0.50,
            "iapc_regime_relative_volume": 0.95,
            "iapc_regime_flow_ratio": 0.04,
            "iapc_regime_close_location": 0.68,
            "iapc_regime_lifetime_periods": 4.0,
            "iapc_boundary_loss_atr": 0.08,
            "iapc_pullback_min_atr": 0.08,
            "iapc_pullback_max_atr": 0.80,
            "iapc_pullback_start_flow": 0.02,
            "iapc_pullback_min_bars": 2,
            "iapc_pullback_max_bars": 8,
            "iapc_pullback_mode": "FLOW_ABSORPTION",
            "iapc_absorption_opposing_flow": 0.03,
            "iapc_response_body_atr_1m": 0.12,
            "iapc_response_flow_ratio": 0.03,
            "iapc_response_close_location": 0.58,
            "iapc_response_mode": "BREAK_LAST_BAR",
            "iapc_stop_buffer_atr": 0.04,
            "iapc_extension_atr": 0.75,
            "minimum_structural_rr": 1.05,
        }
        params.update(overrides)
        return params

    def seeded(self, **overrides):
        engine = InventoryAbsorptionPullbackEngine(self.params(**overrides))
        first = trend_bar(1, 95.0, 101.0, 94.0, 100.0)
        second = trend_bar(2, 99.0, 102.0, 98.0, 101.0)
        engine._history = [first, second]
        engine._true_ranges = [7.0, 4.0]
        engine._volumes = [100.0, 100.0]
        return engine

    def start_long(self, engine: InventoryAbsorptionPullbackEngine) -> None:
        accepted = trend_bar(3, 101.0, 108.0, 100.5, 107.5, 140.0, 0.40)
        transition = engine._start_regime(
            accepted,
            snap(10, 3, 101.0, 108.0, 100.5, 107.5, 0.40),
        )
        self.assertIsNotNone(transition)
        assert engine._regime is not None
        self.assertEqual(engine._regime.direction, "LONG")

    def start_short(self, engine: InventoryAbsorptionPullbackEngine) -> None:
        accepted = trend_bar(3, 99.0, 99.5, 92.0, 92.5, 140.0, -0.40)
        transition = engine._start_regime(
            accepted,
            snap(10, 3, 99.0, 99.5, 92.0, 92.5, -0.40),
        )
        self.assertIsNotNone(transition)
        assert engine._regime is not None
        self.assertEqual(engine._regime.direction, "SHORT")

    def test_completed_15m_bar_is_not_visible_early(self):
        engine = InventoryAbsorptionPullbackEngine(self.params())
        for i in range(14):
            engine.observe(
                snap(i, ns(0, i + 1), 100.0, 101.0, 99.0, 100.5),
                allow_new=True,
            )
            self.assertEqual(len(engine._history), 0)
        engine.observe(
            snap(14, ns(0, 15), 100.5, 102.0, 100.0, 101.5),
            allow_new=True,
        )
        self.assertEqual(len(engine._history), 1)

    def test_accepted_breakout_starts_long_regime(self):
        engine = self.seeded()
        self.start_long(engine)
        assert engine._regime is not None
        self.assertEqual(engine._regime.boundary, 102.0)
        self.assertEqual(engine._regime.state, "ACCEPTED_TREND_PULLBACK_WAIT")

    def test_pullback_start_bar_cannot_emit_and_later_response_can(self):
        engine = self.seeded()
        self.start_long(engine)
        first = engine._advance(
            snap(11, 11, 107.0, 107.2, 105.5, 106.0, -0.25),
            allow_new=True,
        )
        self.assertIsNone(first.signal)
        self.assertEqual(first.transitions[-1].next_state, "OPPOSING_FLOW_PULLBACK_BUILD")
        second = engine._advance(
            snap(12, 12, 106.0, 106.2, 105.0, 105.5, -0.30),
            allow_new=True,
        )
        self.assertIsNone(second.signal)
        response = engine._advance(
            snap(13, 13, 105.6, 107.5, 105.4, 107.2, 0.30),
            allow_new=True,
        )
        self.assertIsNotNone(response.signal)
        assert response.signal is not None
        self.assertEqual(response.signal.family, "IAPC")
        self.assertEqual(response.signal.direction, "LONG")
        self.assertLess(response.signal.stop_price, 105.0)
        self.assertGreater(response.signal.target_price, response.signal.reference_entry)

    def test_absorption_mode_requires_aggregate_opposing_flow(self):
        engine = self.seeded(iapc_pullback_start_flow=0.0, iapc_absorption_opposing_flow=0.10)
        self.start_long(engine)
        engine._advance(snap(11, 11, 107.0, 107.2, 105.5, 106.0, -0.02), allow_new=True)
        engine._advance(snap(12, 12, 106.0, 106.2, 105.0, 105.5, 0.01), allow_new=True)
        response = engine._advance(snap(13, 13, 105.6, 107.5, 105.4, 107.2, 0.30), allow_new=True)
        self.assertIsNone(response.signal)
        self.assertIsNotNone(engine._regime)

    def test_structural_pullback_ablation_does_not_require_flow(self):
        engine = self.seeded(
            iapc_pullback_mode="STRUCTURAL_PULLBACK",
            iapc_pullback_start_flow=0.0,
        )
        self.start_long(engine)
        engine._advance(snap(11, 11, 107.0, 107.2, 105.5, 106.0, 0.0), allow_new=True)
        engine._advance(snap(12, 12, 106.0, 106.2, 105.0, 105.5, 0.05), allow_new=True)
        response = engine._advance(snap(13, 13, 105.6, 107.5, 105.4, 107.2, 0.30), allow_new=True)
        self.assertIsNotNone(response.signal)

    def test_short_path_is_symmetric(self):
        engine = self.seeded()
        self.start_short(engine)
        first = engine._advance(
            snap(11, 11, 93.0, 94.5, 92.8, 94.0, 0.25),
            allow_new=True,
        )
        self.assertIsNone(first.signal)
        engine._advance(
            snap(12, 12, 94.0, 95.0, 93.8, 94.3, 0.30),
            allow_new=True,
        )
        response = engine._advance(
            snap(13, 13, 94.4, 94.6, 92.5, 92.8, -0.30),
            allow_new=True,
        )
        self.assertIsNotNone(response.signal)
        assert response.signal is not None
        self.assertEqual(response.signal.direction, "SHORT")
        self.assertGreater(response.signal.stop_price, 95.0)
        self.assertLess(response.signal.target_price, response.signal.reference_entry)

    def test_boundary_loss_resets_regime(self):
        engine = self.seeded()
        self.start_long(engine)
        result = engine._advance(
            snap(11, 11, 102.0, 102.2, 100.0, 101.0, -0.4),
            allow_new=True,
        )
        self.assertEqual(result.transitions[-1].reason_code, "BULLISH_ACCEPTED_BOUNDARY_LOST")
        self.assertIsNone(engine._regime)

    def test_entry_slot_unavailable_resets_at_response(self):
        engine = self.seeded()
        self.start_long(engine)
        engine._advance(snap(11, 11, 107.0, 107.2, 105.5, 106.0, -0.25), allow_new=True)
        engine._advance(snap(12, 12, 106.0, 106.2, 105.0, 105.5, -0.30), allow_new=True)
        response = engine._advance(
            snap(13, 13, 105.6, 107.5, 105.4, 107.2, 0.30),
            allow_new=False,
        )
        self.assertIsNone(response.signal)
        self.assertEqual(response.transitions[-1].reason_code, "ENTRY_SLOT_UNAVAILABLE_AT_PULLBACK_RESPONSE")
        self.assertIsNone(engine._regime)

    def test_break_pullback_structure_is_stricter_than_last_bar(self):
        engine = self.seeded(iapc_response_mode="BREAK_PULLBACK_STRUCTURE")
        self.start_long(engine)
        engine._advance(snap(11, 11, 107.0, 107.2, 105.5, 106.0, -0.25), allow_new=True)
        engine._advance(snap(12, 12, 106.0, 106.2, 105.0, 105.5, -0.30), allow_new=True)
        response = engine._advance(snap(13, 13, 105.6, 107.0, 105.4, 106.8, 0.30), allow_new=True)
        self.assertIsNone(response.signal)


if __name__ == "__main__":
    unittest.main()

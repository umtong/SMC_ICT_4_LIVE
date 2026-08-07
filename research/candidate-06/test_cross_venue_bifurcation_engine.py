from __future__ import annotations

import unittest

from cross_venue_bifurcation_engine import (
    CrossVenuePriceDiscoveryBifurcationEngine,
    _JointAuction,
)
from lrb_types import BarObservation, PrimitiveSnapshot

MINUTE_NS = 60_000_000_000


def observation(ts_ns, open_, high, low, close, flow=0.0, volume=100.0):
    return BarObservation(
        ts_ns=ts_ns,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        taker_buy_volume=volume * (flow + 1.0) / 2.0,
        trades=10,
    )


def snapshot(index, value, atr=1.0):
    width = max(value.high - value.low, 0.1)
    return PrimitiveSnapshot(
        index=index,
        observation=value,
        ready=True,
        atr=atr,
        rel_volume=1.2,
        flow_ratio=value.flow_ratio,
        body_atr=abs(value.close - value.open) / atr,
        range_atr=width / atr,
        upper_wick_fraction=0.0,
        lower_wick_fraction=0.0,
        close_location=(value.close - value.low) / width,
        upper_fast=110.0,
        lower_fast=90.0,
        upper_slow=120.0,
        lower_slow=80.0,
        slow_mid=100.0,
        range_position=0.5,
        upper_pool_touches=2,
        lower_pool_touches=2,
    )


def parameters(**overrides):
    result = {
        "cvpd_period_minutes": 15,
        "cvpd_entry_window_minutes": 13,
        "cvpd_basis_lookback": 120,
        "cvpd_basis_min_history": 5,
        "cvpd_basis_z_threshold": 1.0,
        "cvpd_lag_basis_z_ceiling": 0.5,
        "cvpd_spot_atr_bars": 5,
        "cvpd_spot_volume_bars": 5,
        "cvpd_min_sweep_atr": 0.10,
        "cvpd_confirm_tolerance_atr": 0.03,
        "cvpd_spot_accept_close_atr": 0.05,
        "cvpd_spot_body_atr": 0.20,
        "cvpd_spot_flow_ratio": 0.03,
        "cvpd_spot_relative_volume": 0.80,
        "cvpd_perp_shock_flow_ratio": 0.03,
        "cvpd_response_bars": 3,
        "cvpd_response_body_atr": 0.10,
        "cvpd_response_flow_ratio": 0.01,
        "cvpd_response_close_location": 0.55,
        "cvpd_perp_accept_close_atr": 0.02,
        "cvpd_stop_buffer_atr": 0.05,
        "cvpd_projection_fraction": 0.50,
        "cvpd_cooldown_bars": 1,
        "minimum_structural_rr": 0.30,
        "cvpd_enable_perp_reversion": True,
        "cvpd_enable_spot_relay": True,
        "cvpd_use_basis_filter": True,
    }
    result.update(overrides)
    return result


def seed(engine):
    engine._previous = _JointAuction(
        bucket=1,
        start_ts_ns=1,
        end_ts_ns=2,
        perp_open=100.0,
        perp_high=102.0,
        perp_low=98.0,
        perp_close=100.0,
        spot_open=100.0,
        spot_high=102.0,
        spot_low=98.0,
        spot_close=100.0,
    )
    engine._prior_perp_close = 100.0
    engine._prior_spot_close = 100.0
    engine._basis_history = [0.0, -0.0001, 0.0001, 0.0, 0.0002]
    engine._spot_true_ranges = [1.0] * 5
    engine._spot_volumes = [100.0] * 5
    engine._current_bucket = 2


class CrossVenueEngineTests(unittest.TestCase):
    def test_missing_same_timestamp_spot_is_implementation_failure(self):
        engine = CrossVenuePriceDiscoveryBifurcationEngine(
            parameters(),
            spot_observations={},
        )
        seed(engine)
        with self.assertRaises(RuntimeError):
            engine.observe(
                snapshot(10, observation(16 * MINUTE_NS, 100, 103, 99.8, 102.7, 0.4)),
                allow_new=True,
            )

    def test_perpetual_only_sweep_needs_later_reclaim_response(self):
        event_ts = 16 * MINUTE_NS
        spots = {
            event_ts: observation(event_ts, 100, 101.9, 99.5, 101.0, 0.0),
            event_ts + MINUTE_NS: observation(
                event_ts + MINUTE_NS,
                101.0,
                101.8,
                99.8,
                100.8,
                -0.1,
            ),
        }
        engine = CrossVenuePriceDiscoveryBifurcationEngine(
            parameters(),
            spot_observations=spots,
        )
        seed(engine)
        first = engine.observe(
            snapshot(10, observation(event_ts, 100, 103, 99.8, 102.7, 0.4)),
            allow_new=True,
        )
        self.assertIsNone(first.signal)
        self.assertIsNotNone(engine._episode)
        second = engine.observe(
            snapshot(
                11,
                observation(event_ts + MINUTE_NS, 102.7, 102.8, 100.5, 100.7, -0.4),
            ),
            allow_new=True,
        )
        self.assertIsNotNone(second.signal)
        assert second.signal is not None
        self.assertEqual(second.signal.family, "CVPD_R")
        self.assertEqual(second.signal.direction, "SHORT")

    def test_spot_led_acceptance_needs_later_perpetual_relay(self):
        event_ts = 16 * MINUTE_NS
        spots = {
            event_ts: observation(event_ts, 100, 103, 99.8, 102.8, 0.5, 150),
            event_ts + MINUTE_NS: observation(
                event_ts + MINUTE_NS,
                102.8,
                103.5,
                102.5,
                103.2,
                0.3,
                120,
            ),
        }
        engine = CrossVenuePriceDiscoveryBifurcationEngine(
            parameters(),
            spot_observations=spots,
        )
        seed(engine)
        first = engine.observe(
            snapshot(10, observation(event_ts, 100, 101.9, 99.7, 101.0, 0.0)),
            allow_new=True,
        )
        self.assertIsNone(first.signal)
        self.assertIsNotNone(engine._episode)
        assert engine._episode is not None
        self.assertEqual(engine._episode.family, "SPOT_LED_RELAY")
        second = engine.observe(
            snapshot(11, observation(event_ts + MINUTE_NS, 101, 103, 100.8, 102.7, 0.4)),
            allow_new=True,
        )
        self.assertIsNotNone(second.signal)
        assert second.signal is not None
        self.assertEqual(second.signal.family, "CVPD_C")
        self.assertEqual(second.signal.direction, "LONG")

    def test_same_side_break_on_both_venues_is_ambiguous_not_trade(self):
        event_ts = 16 * MINUTE_NS
        spots = {event_ts: observation(event_ts, 100, 103, 99.8, 102.7, 0.4, 140)}
        engine = CrossVenuePriceDiscoveryBifurcationEngine(
            parameters(),
            spot_observations=spots,
        )
        seed(engine)
        step = engine.observe(
            snapshot(10, observation(event_ts, 100, 103, 99.8, 102.6, 0.4)),
            allow_new=True,
        )
        self.assertIsNone(step.signal)
        self.assertIsNone(engine._episode)
        self.assertTrue(
            any(
                transition.reason_code == "SPOT_AND_PERPETUAL_CONFIRMED_SAME_LIQUIDITY_EVENT"
                for transition in step.transitions
            ),
        )


if __name__ == "__main__":
    unittest.main()

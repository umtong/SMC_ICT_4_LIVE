from __future__ import annotations

import unittest

from agg_trade_profile_data import AggMinuteStat, AuctionProfile
from auction_imbalance_migration_engine import AuctionImbalanceMigrationDiscoveryEngine
from lrb_types import BarObservation, PrimitiveSnapshot

MINUTE = 60_000_000_000


def profile(end: int, *, open_: float, high: float, low: float, close: float, poc: float, val: float, vah: float, delta: float) -> AuctionProfile:
    return AuctionProfile(
        start_ts_ns=end - 15 * MINUTE,
        end_ts_ns=end,
        open=open_, high=high, low=low, close=close,
        total_volume=1000.0,
        signed_aggressive_volume=1000.0 * delta,
        trades=100,
        poc=poc, val=val, vah=vah,
        value_volume_fraction=0.70,
        poc_concentration=0.08,
        lower_tail_share=0.10,
        upper_tail_share=0.10,
    )


def snap(index: int, ts: int, open_: float, high: float, low: float, close: float, flow: float) -> PrimitiveSnapshot:
    width = max(high - low, 0.1)
    return PrimitiveSnapshot(
        index=index,
        observation=BarObservation(ts, open_, high, low, close, 100.0, 50.0 * (flow + 1.0), 10),
        ready=True,
        atr=10.0,
        rel_volume=1.2,
        flow_ratio=flow,
        body_atr=abs(close - open_) / 10.0,
        range_atr=width / 10.0,
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


def minute(ts: int, flow: float, close: float) -> AggMinuteStat:
    return AggMinuteStat(ts, 100.0, 100.0 * flow, 10, close + 1.0, close - 1.0, close)


class AIMDTests(unittest.TestCase):
    def params(self, **overrides):
        values = {
            "aimd_profile_period_minutes": 15,
            "aimd_poc_shift_atr": 0.15,
            "aimd_value_shift_atr": 0.05,
            "aimd_close_acceptance_atr": 0.03,
            "aimd_efficiency_floor": 0.50,
            "aimd_delta_floor": 0.08,
            "aimd_value_overlap_ceiling": 0.65,
            "aimd_use_profile_delta": True,
            "aimd_require_poc_migration": True,
            "aimd_use_agg_trade_flow": True,
            "aimd_retest_band_atr": 0.12,
            "aimd_old_value_tolerance_atr": 0.03,
            "aimd_retest_opposing_flow": 0.02,
            "aimd_response_bars": 4,
            "aimd_response_body_atr": 0.15,
            "aimd_response_flow_ratio": 0.03,
            "aimd_response_close_location": 0.62,
            "aimd_stop_buffer_atr": 0.08,
            "aimd_projection_fraction": 0.50,
            "aimd_cooldown_bars": 2,
            "minimum_structural_rr": 0.75,
        }
        values.update(overrides)
        return values

    def profiles(self):
        first_end = 15 * MINUTE
        second_end = 30 * MINUTE
        first = profile(first_end, open_=95, high=105, low=92, close=101, poc=99, val=96, vah=102, delta=0.02)
        second = profile(second_end, open_=101, high=116, low=101, close=114, poc=108, val=103, vah=112, delta=0.30)
        return first, second

    def engine(self, **overrides):
        first, second = self.profiles()
        stats = {
            first.end_ts_ns: minute(first.end_ts_ns, 0.0, first.close),
            second.end_ts_ns: minute(second.end_ts_ns, 0.3, second.close),
            second.end_ts_ns + MINUTE: minute(second.end_ts_ns + MINUTE, -0.2, 103.5),
            second.end_ts_ns + 2 * MINUTE: minute(second.end_ts_ns + 2 * MINUTE, 0.3, 107.0),
            second.end_ts_ns + 3 * MINUTE: minute(second.end_ts_ns + 3 * MINUTE, 0.0, 107.0),
        }
        return AuctionImbalanceMigrationDiscoveryEngine(
            self.params(**overrides),
            profiles={first.end_ts_ns: first, second.end_ts_ns: second},
            minute_stats=stats,
        ), first, second

    def test_profile_completion_cannot_retest_itself(self):
        engine, first, second = self.engine()
        engine.observe(snap(1, first.end_ts_ns, 99, 102, 98, 101, 0.0), allow_new=True)
        step = engine.observe(snap(2, second.end_ts_ns, 101, 116, 101, 114, 0.3), allow_new=True)
        self.assertIsNotNone(engine._context)
        self.assertIsNone(engine._episode)
        self.assertIsNone(step.signal)

    def test_delta_ablation_changes_only_migration_gate(self):
        first, second = self.profiles()
        weak = profile(second.end_ts_ns, open_=101, high=116, low=101, close=114, poc=108, val=103, vah=112, delta=0.0)
        full = AuctionImbalanceMigrationDiscoveryEngine(self.params(), profiles={}, minute_stats={})
        ablated = AuctionImbalanceMigrationDiscoveryEngine(self.params(aimd_use_profile_delta=False), profiles={}, minute_stats={})
        self.assertIsNone(full._migration_direction(first, weak, 10.0)[0])
        self.assertEqual(ablated._migration_direction(first, weak, 10.0)[0], "LONG")

    def test_poc_migration_ablation_allows_edge_shift_without_poc_shift(self):
        first, second = self.profiles()
        weak_poc = profile(second.end_ts_ns, open_=101, high=116, low=101, close=114, poc=100, val=103, vah=112, delta=0.3)
        full = AuctionImbalanceMigrationDiscoveryEngine(self.params(), profiles={}, minute_stats={})
        ablated = AuctionImbalanceMigrationDiscoveryEngine(self.params(aimd_require_poc_migration=False), profiles={}, minute_stats={})
        self.assertIsNone(full._migration_direction(first, weak_poc, 10.0)[0])
        self.assertEqual(ablated._migration_direction(first, weak_poc, 10.0)[0], "LONG")

    def test_long_retest_and_separate_response_emit(self):
        engine, first, second = self.engine()
        engine.observe(snap(1, first.end_ts_ns, 99, 102, 98, 101, 0.0), allow_new=True)
        engine.observe(snap(2, second.end_ts_ns, 101, 116, 101, 114, 0.3), allow_new=True)
        retest_ts = second.end_ts_ns + MINUTE
        step = engine.observe(snap(3, retest_ts, 105, 106, 102.8, 103.5, -0.2), allow_new=True)
        self.assertIsNotNone(engine._episode)
        self.assertIsNone(step.signal)
        response_ts = second.end_ts_ns + 2 * MINUTE
        step = engine.observe(snap(4, response_ts, 103.5, 108, 103.2, 107.5, 0.3), allow_new=True)
        self.assertIsNotNone(step.signal)
        assert step.signal is not None
        self.assertEqual(step.signal.family, "AIMD")
        self.assertEqual(step.signal.direction, "LONG")
        self.assertLess(step.signal.stop_price, step.signal.reference_entry)
        self.assertGreater(step.signal.target_price, step.signal.reference_entry)

    def test_old_value_reacceptance_invalidates_context(self):
        engine, first, second = self.engine()
        engine.observe(snap(1, first.end_ts_ns, 99, 102, 98, 101, 0.0), allow_new=True)
        engine.observe(snap(2, second.end_ts_ns, 101, 116, 101, 114, 0.3), allow_new=True)
        ts = second.end_ts_ns + MINUTE
        step = engine.observe(snap(3, ts, 103, 103.2, 100, 101.0, -0.2), allow_new=True)
        self.assertIsNone(engine._context)
        self.assertTrue(any(t.reason_code == "OLD_VALUE_REACCEPTED_BEFORE_RETEST" for t in step.transitions))


if __name__ == "__main__":
    unittest.main()

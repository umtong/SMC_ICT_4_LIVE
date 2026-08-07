from __future__ import annotations

import unittest

from agg_trade_profile_data import AggMinuteStat, AuctionProfile
from balanced_auction_value_engine import BalancedAuctionValueReversionEngine
from lrb_types import BarObservation, PrimitiveSnapshot

MINUTE = 60_000_000_000


def profile(end_minute: int, *, poc: float = 100.0, val: float = 99.0, vah: float = 101.0) -> AuctionProfile:
    return AuctionProfile(
        start_ts_ns=(end_minute - 15) * MINUTE,
        end_ts_ns=end_minute * MINUTE,
        open=99.8,
        high=101.2,
        low=98.8,
        close=100.1,
        total_volume=1000.0,
        signed_aggressive_volume=50.0,
        trades=500,
        poc=poc,
        val=val,
        vah=vah,
        value_volume_fraction=0.72,
        poc_concentration=0.08,
        lower_tail_share=0.14,
        upper_tail_share=0.14,
    )


def minute(end_minute: int, flow: float, *, high: float = 100.0, low: float = 100.0, close: float = 100.0) -> AggMinuteStat:
    return AggMinuteStat(
        end_ts_ns=end_minute * MINUTE,
        total_volume=100.0,
        signed_aggressive_volume=flow * 100.0,
        trades=30,
        high=high,
        low=low,
        close=close,
    )


def snap(index: int, end_minute: int, open_: float, high: float, low: float, close: float, flow: float) -> PrimitiveSnapshot:
    width = max(high - low, 0.1)
    return PrimitiveSnapshot(
        index=index,
        observation=BarObservation(
            ts_ns=end_minute * MINUTE,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=100.0,
            taker_buy_volume=50.0 * (flow + 1.0),
            trades=30,
        ),
        ready=True,
        atr=1.0,
        rel_volume=1.0,
        flow_ratio=flow,
        body_atr=abs(close - open_),
        range_atr=width,
        upper_wick_fraction=max(high - max(open_, close), 0.0) / width,
        lower_wick_fraction=max(min(open_, close) - low, 0.0) / width,
        close_location=(close - low) / width,
        upper_fast=103.0,
        lower_fast=97.0,
        upper_slow=105.0,
        lower_slow=95.0,
        slow_mid=100.0,
        range_position=0.5,
        upper_pool_touches=2,
        lower_pool_touches=2,
    )


class BalancedAuctionValueEngineTests(unittest.TestCase):
    def params(self, **overrides):
        params = {
            "bavr_profile_period_minutes": 15,
            "bavr_use_trade_distribution": True,
            "bavr_require_balance": True,
            "bavr_use_agg_trade_flow": True,
            "bavr_value_overlap_floor": 0.50,
            "bavr_balance_efficiency_ceiling": 0.55,
            "bavr_balance_delta_ceiling": 0.30,
            "bavr_two_sided_tail_floor": 0.05,
            "bavr_poc_concentration_ceiling": 0.12,
            "bavr_excursion_min_atr": 0.08,
            "bavr_excursion_flow_ratio": 0.05,
            "bavr_outside_acceptance_atr": 0.05,
            "bavr_acceptance_flow_ratio": 0.05,
            "bavr_acceptance_closes": 2,
            "bavr_reclaim_tolerance_atr": 0.02,
            "bavr_reclaim_flow_ratio": 0.03,
            "bavr_response_bars": 4,
            "bavr_response_body_atr": 0.15,
            "bavr_response_flow_ratio": 0.03,
            "bavr_response_close_location": 0.62,
            "bavr_stop_buffer_atr": 0.08,
            "bavr_cooldown_bars": 2,
            "minimum_structural_rr": 0.75,
        }
        params.update(overrides)
        return params

    def make_engine(self, **overrides):
        profiles = {15 * MINUTE: profile(15), 30 * MINUTE: profile(30)}
        stats = {
            minute_index * MINUTE: minute(minute_index, 0.0)
            for minute_index in range(1, 40)
        }
        return BalancedAuctionValueReversionEngine(
            self.params(**overrides), profiles=profiles, minute_stats=stats,
        ), stats

    def seed_balance(self, engine, stats):
        engine.observe(snap(14, 15, 100.0, 100.2, 99.8, 100.1, 0.0), allow_new=True)
        engine.observe(snap(29, 30, 100.0, 100.2, 99.8, 100.1, 0.0), allow_new=True)
        self.assertIsNotNone(engine._context)

    def test_profile_completing_now_cannot_seed_same_bar_excursion(self):
        engine, stats = self.make_engine()
        engine.observe(snap(14, 15, 100.0, 100.2, 99.8, 100.1, 0.0), allow_new=True)
        stats[30 * MINUTE] = minute(30, 0.50, high=102.0, low=99.8, close=101.5)
        step = engine.observe(snap(29, 30, 100.0, 102.0, 99.8, 101.5, 0.50), allow_new=True)
        self.assertIsNone(engine._excursion)
        self.assertTrue(any(t.reason_code == "ADJACENT_COMPLETED_AUCTIONS_ACCEPTED_SHARED_VALUE" for t in step.transitions))

    def test_upper_failed_discovery_emits_short_to_poc_after_separate_response(self):
        engine, stats = self.make_engine()
        self.seed_balance(engine, stats)
        engine._minute_stats[31 * MINUTE] = minute(31, 0.25, high=101.4, low=100.9, close=101.2)
        start = engine.observe(snap(30, 31, 101.0, 101.4, 100.9, 101.2, 0.25), allow_new=True)
        self.assertTrue(any(t.reason_code == "BALANCED_VALUE_EDGE_SWEPT_BY_AGGRESSIVE_FLOW" for t in start.transitions))
        engine._minute_stats[32 * MINUTE] = minute(32, -0.25, high=101.25, low=100.9, close=100.95)
        reclaim = engine.observe(snap(31, 32, 101.2, 101.25, 100.9, 100.95, -0.25), allow_new=True)
        self.assertTrue(any(t.reason_code == "EXCURSION_REJECTED_BACK_INTO_COMPLETED_VALUE" for t in reclaim.transitions))
        engine._minute_stats[33 * MINUTE] = minute(33, -0.30, high=101.0, low=100.65, close=100.75)
        response = engine.observe(snap(32, 33, 100.98, 101.0, 100.65, 100.75, -0.30), allow_new=True)
        self.assertIsNotNone(response.signal)
        assert response.signal is not None
        self.assertEqual(response.signal.direction, "SHORT")
        self.assertEqual(response.signal.target_reason, "BALANCED_AUCTION_POINT_OF_CONTROL")
        self.assertEqual(response.signal.target_price, 100.0)

    def test_two_outside_acceptance_closes_cancel_reversion(self):
        engine, stats = self.make_engine()
        self.seed_balance(engine, stats)
        engine._minute_stats[31 * MINUTE] = minute(31, 0.25)
        engine.observe(snap(30, 31, 101.0, 101.4, 100.9, 101.2, 0.25), allow_new=True)
        engine._minute_stats[32 * MINUTE] = minute(32, 0.20)
        engine.observe(snap(31, 32, 101.2, 101.5, 101.1, 101.3, 0.20), allow_new=True)
        engine._minute_stats[33 * MINUTE] = minute(33, 0.20)
        step = engine.observe(snap(32, 33, 101.3, 101.6, 101.2, 101.4, 0.20), allow_new=True)
        self.assertTrue(any(t.reason_code == "OUTSIDE_VALUE_ACCEPTED_PRICE_DISCOVERY" for t in step.transitions))
        self.assertIsNone(engine._excursion)

    def test_distribution_ablation_changes_only_balance_gate(self):
        prior = profile(15)
        current = AuctionProfile(
            start_ts_ns=15 * MINUTE, end_ts_ns=30 * MINUTE,
            open=99.8, high=101.2, low=98.8, close=100.1,
            total_volume=1000.0, signed_aggressive_volume=500.0, trades=500,
            poc=100.0, val=99.0, vah=101.0, value_volume_fraction=0.72,
            poc_concentration=0.08, lower_tail_share=0.01, upper_tail_share=0.20,
        )
        strict, _ = BalancedAuctionValueReversionEngine(
            self.params(), profiles={}, minute_stats={},
        )._profile_is_balanced(prior, current)
        ablated, _ = BalancedAuctionValueReversionEngine(
            self.params(bavr_use_trade_distribution=False), profiles={}, minute_stats={},
        )._profile_is_balanced(prior, current)
        self.assertFalse(strict)
        self.assertTrue(ablated)


if __name__ == "__main__":
    unittest.main()

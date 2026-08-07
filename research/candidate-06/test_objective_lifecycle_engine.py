from __future__ import annotations

import unittest

from hierarchical_pool_engine import _LiquidityPool
from hierarchical_sweep_engine import _AuctionBar, _Bias
from lrb_types import BarObservation, PrimitiveSnapshot
from objective_lifecycle_engine import UnresolvedObjectiveLifecycleEngine


def snap(index: int, ts: int, *, open_: float, high: float, low: float, close: float) -> PrimitiveSnapshot:
    width = max(high - low, 0.1)
    return PrimitiveSnapshot(
        index=index,
        observation=BarObservation(ts, open_, high, low, close, 100.0, 50.0, 10),
        ready=True,
        atr=1.0,
        rel_volume=1.0,
        flow_ratio=0.0,
        body_atr=abs(close - open_),
        range_atr=width,
        upper_wick_fraction=0.0,
        lower_wick_fraction=0.0,
        close_location=(close - low) / width,
        upper_fast=120.0,
        lower_fast=80.0,
        upper_slow=130.0,
        lower_slow=70.0,
        slow_mid=100.0,
        range_position=0.5,
        upper_pool_touches=2,
        lower_pool_touches=2,
    )


def bar(end: int, *, open_: float, high: float, low: float, close: float) -> _AuctionBar:
    return _AuctionBar(end - 1, end, open_, high, low, close, 100.0, 50.0, 100)


def bias(direction: str = "LONG") -> _Bias:
    return _Bias(
        context_id="BIAS-1",
        direction=direction,
        boundary=100.0,
        origin=101.0 if direction == "LONG" else 99.0,
        high=110.0,
        low=100.0,
        close=109.0 if direction == "LONG" else 91.0,
        extreme=110.0 if direction == "LONG" else 90.0,
        atr_htf=5.0,
        created_index=10,
        expires_index=1000,
        range_atr=2.0,
        body_fraction=0.8,
        flow_ratio=0.2 if direction == "LONG" else -0.2,
        relative_volume=1.5,
    )


class ObjectiveLifecycleTests(unittest.TestCase):
    def engine(self, **overrides) -> UnresolvedObjectiveLifecycleEngine:
        params = {
            "hsc_bias_period_minutes": 60,
            "hsc_liquidity_period_minutes": 5,
            "hsp_bias_expiry_mode": "STRUCTURAL_ONLY",
            "hml_pool_families": "SWING_AND_EQUAL",
            "hsc_use_flow_proxy": True,
            "hff_use_bias_flow": True,
            "hff_use_sweep_flow": False,
            "hff_use_response_flow": True,
            "hsc_response_mode": "BREAK_SWEEP_BAR",
            "minimum_structural_rr": 0.75,
            "uoam_use_objective_lifecycle": True,
            "uoam_use_origin_invalidation": True,
            "uoam_exit_open_position_on_invalidation": True,
        }
        params.update(overrides)
        return UnresolvedObjectiveLifecycleEngine(params)

    def test_only_prior_confirmed_untouched_pools_are_bound(self):
        engine = self.engine()
        engine._bias = bias("LONG")
        engine._liquidity_pools = [
            _LiquidityPool("UPPER", 112.0, 1, 8),
            _LiquidityPool("UPPER", 115.0, 2, 10),  # same-time confirmation: forbidden
            _LiquidityPool("UPPER", 109.5, 3, 8),  # touched by accepting impulse
            _LiquidityPool("UPPER", 118.0, 4, 7),
            _LiquidityPool("LOWER", 90.0, 5, 7),
        ]
        engine._pool_kinds = {("UPPER", 1): "CONFIRMED_SWING", ("UPPER", 4): "EQUAL_HIGH"}
        transitions = engine._bind_objective_ladder(
            bar(10, open_=101.0, high=110.0, low=100.0, close=109.0),
            engine._bias,
        )
        self.assertEqual([value.level for value in engine._objective_ladder], [112.0, 118.0])
        self.assertEqual(engine._objective_ladder[1].kind, "EQUAL_HIGH")
        self.assertEqual(transitions[0].reason_code, "PREEXISTING_UNRESOLVED_OBJECTIVE_BOUND")

    def test_consumption_advances_ladder_then_ends_context(self):
        engine = self.engine(uoam_use_origin_invalidation=False)
        engine._bias = bias("LONG")
        engine._objective_context_id = "BIAS-1"
        engine._objective_ladder = []
        pools = [
            _LiquidityPool("UPPER", 112.0, 1, 8),
            _LiquidityPool("UPPER", 118.0, 2, 8),
        ]
        engine._liquidity_pools = pools
        engine._bind_objective_ladder(
            bar(10, open_=101.0, high=110.0, low=100.0, close=109.0),
            engine._bias,
        )
        first = engine._advance_consumed_objectives(
            snap(11, 11, open_=110.0, high=113.0, low=109.5, close=112.0),
        )
        self.assertIsNotNone(engine._bias)
        self.assertEqual(engine._current_objective().level, 118.0)
        self.assertTrue(any(t.reason_code == "NEXT_PREEXISTING_OBJECTIVE_ACTIVATED" for t in first.transitions))
        second = engine._advance_consumed_objectives(
            snap(12, 12, open_=116.0, high=119.0, low=115.0, close=118.5),
        )
        self.assertIsNone(engine._bias)
        self.assertTrue(any(t.reason_code == "ALL_BOUND_OBJECTIVES_CONSUMED" for t in second.transitions))

    def test_full_origin_rebalance_invalidates_remaining_objective(self):
        engine = self.engine()
        engine._bias = bias("LONG")
        engine._objective_context_id = "BIAS-1"
        engine._objective_ladder = []
        engine._liquidity_pools = [_LiquidityPool("UPPER", 118.0, 1, 8)]
        engine._bind_objective_ladder(
            bar(10, open_=101.0, high=110.0, low=100.0, close=109.0),
            engine._bias,
        )
        step = engine._advance_bias(
            snap(11, 11, open_=105.0, high=105.5, low=100.5, close=100.8),
        )
        self.assertIsNone(engine._bias)
        self.assertTrue(any(t.reason_code == "UOAM_BOUND_IMPULSE_ORIGIN_REBALANCED" for t in step.transitions))

    def test_bound_objective_is_the_only_target(self):
        engine = self.engine()
        engine._bias = bias("LONG")
        engine._objective_context_id = "BIAS-1"
        engine._liquidity_pools = [_LiquidityPool("UPPER", 118.0, 1, 8)]
        engine._bind_objective_ladder(
            bar(10, open_=101.0, high=110.0, low=100.0, close=109.0),
            engine._bias,
        )
        selected = engine._select_target(
            "LONG",
            110.0,
            108.0,
            [(111.6, "DYNAMIC_NEAR_POOL"), (130.0, "EXTENSION")],
        )
        self.assertEqual(selected, (118.0, "BOUND_PREEXISTING_SWING_BUYSIDE_LIQUIDITY"))

    def test_entry_armed_objective_cannot_be_reused(self):
        engine = self.engine()
        engine._bias = bias("LONG")
        engine._objective_context_id = "BIAS-1"
        engine._liquidity_pools = [_LiquidityPool("UPPER", 118.0, 1, 8)]
        engine._bind_objective_ladder(
            bar(10, open_=101.0, high=110.0, low=100.0, close=109.0),
            engine._bias,
        )
        engine._current_objective().entry_armed = True
        self.assertIsNone(
            engine._maybe_start_sweep(
                snap(11, 11, open_=105.0, high=105.5, low=104.5, close=105.0),
            ),
        )

    def test_signal_declares_objective_consumption_and_context_exit_codes(self):
        engine = self.engine()
        engine._bias = bias("LONG")
        engine._objective_context_id = "BIAS-1"
        engine._liquidity_pools = [_LiquidityPool("UPPER", 118.0, 1, 8)]
        engine._bind_objective_ladder(
            bar(10, open_=101.0, high=110.0, low=100.0, close=109.0),
            engine._bias,
        )
        # The contract is attached in _emit; assert the declared list itself is
        # complete without constructing a synthetic parent sweep here.
        declared = {
            "UOAM_BOUND_IMPULSE_ORIGIN_REBALANCED",
            "BULLISH_ACCEPTED_BOUNDARY_LOST",
            "BEARISH_ACCEPTED_BOUNDARY_LOST",
            "HIGHER_TIMEFRAME_BIAS_REPLACED",
            "BOUND_OBJECTIVE_CONSUMED",
            "ALL_BOUND_OBJECTIVES_CONSUMED",
        }
        source = __import__("inspect").getsource(engine._emit)
        for code in declared:
            self.assertIn(code, source)


if __name__ == "__main__":
    unittest.main()

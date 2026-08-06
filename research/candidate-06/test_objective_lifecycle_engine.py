from __future__ import annotations

import unittest

from hierarchical_pool_engine import _LiquidityPool
from hierarchical_sweep_engine import _AuctionBar, _SweepEpisode
from lrb_types import BarObservation, PrimitiveSnapshot, ScenarioSignal
from objective_lifecycle_core import ControlAuction
from objective_lifecycle_engine import ObjectiveLifecycleAcceptanceRelayEngine


def snap(index, timestamp, open_, high, low, close, flow=0.0, volume=100.0):
    width = max(high - low, 0.1)
    return PrimitiveSnapshot(
        index,
        BarObservation(
            timestamp,
            open_,
            high,
            low,
            close,
            volume,
            volume * (flow + 1.0) / 2.0,
            10,
        ),
        True,
        1.0,
        1.5,
        flow,
        abs(close - open_),
        width,
        max(high - max(open_, close), 0.0) / width,
        max(min(open_, close) - low, 0.0) / width,
        (close - low) / width,
        105.0,
        88.0,
        118.0,
        82.0,
        100.0,
        0.5,
        2,
        2,
    )


def auction(end, open_, high, low, close, volume=100.0, flow=0.0):
    return _AuctionBar(
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


class ObjectiveLifecycleEngineTests(unittest.TestCase):
    def params(self, **overrides):
        params = {
            "hsc_bias_period_minutes": 60,
            "hsc_liquidity_period_minutes": 5,
            "hsc_bias_atr_bars": 2,
            "hsc_bias_volume_bars": 2,
            "hsc_bias_breakout_lookback": 2,
            "hsc_bias_acceptance_close_atr": 0.02,
            "hsc_bias_range_atr": 0.75,
            "hsc_bias_body_fraction": 0.50,
            "hsc_bias_relative_volume": 0.95,
            "hsc_bias_flow_ratio": 0.04,
            "hsc_bias_close_location": 0.68,
            "hsc_bias_lifetime_periods": 3.0,
            "hsp_bias_expiry_mode": "STRUCTURAL_ONLY",
            "hsc_bias_boundary_loss_atr": 0.08,
            "hsc_sweep_min_atr_1m": 0.10,
            "hsc_sweep_opposing_flow_ratio": 0.03,
            "hsc_sweep_reclaim_tolerance_atr_1m": 0.02,
            "hsc_max_impulse_position": 0.70,
            "hsc_response_bars": 3,
            "hsc_response_body_atr_1m": 0.20,
            "hsc_response_flow_ratio": 0.05,
            "hsc_response_close_location": 0.62,
            "hsc_response_mode": "BREAK_SWEEP_BAR",
            "hsc_stop_buffer_atr_htf": 0.025,
            "hsc_cooldown_bars": 2,
            "minimum_structural_rr": 0.40,
            "hsc_use_flow_proxy": True,
            "hsp_liquidity_pool_mode": "CONFIRMED_SWING",
            "hml_pool_families": "SWING_AND_EQUAL",
            "hff_use_bias_flow": True,
            "hff_use_sweep_flow": False,
            "hff_use_response_flow": True,
            "olar_control_period_minutes": 15,
            "olar_control_atr_bars": 2,
            "olar_control_volume_bars": 2,
            "olar_control_breakout_lookback": 2,
            "olar_use_objective_one_use": True,
        }
        params.update(overrides)
        return params

    def seeded(self, **overrides):
        engine = ObjectiveLifecycleAcceptanceRelayEngine(self.params(**overrides))
        first = auction(1, 95.0, 101.0, 94.0, 100.0)
        second = auction(2, 99.0, 102.0, 98.0, 101.0)
        engine._bias_history = [first, second]
        engine._bias_true_ranges = [7.0, 4.0]
        engine._bias_volumes = [100.0, 100.0]
        return engine

    def start_long(self, engine):
        engine._evaluate_completed_bias(
            auction(3, 101.0, 108.0, 100.5, 107.5, 140.0, 0.40),
            snap(10, 3, 101.0, 108.0, 100.5, 107.5, 0.40, 140.0),
        )
        self.assertIsNotNone(engine._bias)
        self.assertIsNotNone(engine._active_leg)

    @staticmethod
    def seed_control(engine):
        history = [
            ControlAuction(99.0, 102.0, 98.0, 101.0, 100.0, 55.0, 1),
            ControlAuction(101.0, 104.0, 100.0, 103.0, 100.0, 55.0, 2),
        ]
        engine._control_history = history
        engine._control_true_ranges = [4.0, 4.0]
        engine._control_volumes = [100.0, 100.0]

    def add_upper_pool(self, engine, *, level=106.0, source=20, confirmed=25):
        pool = _LiquidityPool("UPPER", level, source, confirmed)
        engine._liquidity_pools = [pool]
        engine._pool_kinds[("UPPER", source)] = "CONFIRMED_SWING"
        engine._pool_touches[("UPPER", source)] = 1
        engine._sync_pool_objectives(snap(11, 25, 104.0, 104.2, 103.8, 104.0))
        return pool

    def test_initial_htf_acceptance_creates_leg_and_objective(self):
        engine = self.seeded()
        self.start_long(engine)
        leg = engine._active_leg
        assert leg is not None
        state = engine._objective_ledger.get(leg.objective_key)
        self.assertIsNotNone(state)
        self.assertEqual((leg.origin, leg.extreme), (101.0, 108.0))
        self.assertTrue(state.available)  # type: ignore[union-attr]

    def test_same_direction_control_auction_renews_leg(self):
        engine = self.seeded()
        self.start_long(engine)
        old_leg = engine._active_leg
        self.seed_control(engine)
        engine._process_completed_control(
            ControlAuction(103.0, 107.0, 102.5, 106.5, 120.0, 90.0, 30),
            snap(30, 30, 103.0, 107.0, 102.5, 106.5, 0.50, 120.0),
        )
        self.assertIsNotNone(engine._bias)
        self.assertIsNotNone(engine._active_leg)
        self.assertNotEqual(engine._active_leg.leg_id, old_leg.leg_id)  # type: ignore[union-attr]
        self.assertEqual(engine._active_leg.origin, 103.0)  # type: ignore[union-attr]

    def test_opposing_control_acceptance_invalidates_bias_and_requests_exit(self):
        engine = self.seeded()
        self.start_long(engine)
        context = engine._bias.context_id  # type: ignore[union-attr]
        self.seed_control(engine)
        engine._process_completed_control(
            ControlAuction(100.0, 100.5, 93.0, 93.5, 120.0, 20.0, 45),
            snap(45, 45, 100.0, 100.5, 93.0, 93.5, -0.66, 120.0),
        )
        self.assertIsNone(engine._bias)
        request = engine.pop_position_exit_for(context_id=context, direction="LONG")
        self.assertIsNotNone(request)
        self.assertEqual(request["reason"], "OLAR_OPPOSING_CONTROL_AUCTION_ACCEPTED")

    def test_origin_loss_suspends_leg_but_keeps_htf_bias(self):
        engine = self.seeded()
        self.start_long(engine)
        context = engine._bias.context_id  # type: ignore[union-attr]
        engine._control_history = [
            ControlAuction(96.0, 104.0, 90.0, 101.0, 100.0, 55.0, 1),
            ControlAuction(101.0, 108.0, 91.0, 103.0, 100.0, 55.0, 2),
        ]
        engine._control_true_ranges = [4.0, 4.0]
        engine._control_volumes = [100.0, 100.0]
        engine._process_completed_control(
            ControlAuction(103.0, 103.2, 99.0, 100.0, 120.0, 20.0, 60),
            snap(60, 60, 103.0, 103.2, 99.0, 100.0, -0.66, 120.0),
        )
        self.assertIsNotNone(engine._bias)
        self.assertIsNone(engine._active_leg)
        self.assertIsNotNone(
            engine.pop_position_exit_for(context_id=context, direction="LONG"),
        )

    def test_reserved_pool_objective_cannot_repeat_across_new_leg(self):
        engine = self.seeded()
        self.start_long(engine)
        pool = self.add_upper_pool(engine)
        key = engine._pool_objective_key(pool)
        self.assertTrue(engine._objective_ledger.reserve(key, index=12))
        engine._create_directional_leg(
            bias=engine._bias,  # type: ignore[arg-type]
            origin=104.0,
            extreme=109.0,
            snapshot=snap(13, 13, 104.0, 109.0, 103.5, 108.0),
            reason="TEST_RENEWAL",
            source="TEST",
        )
        candidates = engine._candidate_objectives("LONG", 104.5)
        self.assertNotIn(106.0, [value.level for value in candidates])

    def test_reuse_ablation_keeps_old_pool_candidate(self):
        engine = self.seeded(olar_use_objective_one_use=False)
        self.start_long(engine)
        pool = self.add_upper_pool(engine)
        key = engine._pool_objective_key(pool)
        engine._objective_ledger.reserve(key, index=12)
        candidates = engine._candidate_objectives("LONG", 104.5)
        self.assertIn(106.0, [value.level for value in candidates])

    def test_emit_excludes_parent_fast_and_slow_rolling_extrema(self):
        engine = self.seeded()
        self.start_long(engine)
        self.add_upper_pool(engine, level=106.0)
        engine._sweep = _SweepEpisode(
            scenario_id="OLAR-TEST",
            direction="LONG",
            state="COUNTER_DIRECTION_LIQUIDITY_SWEEP",
            level=103.0,
            level_ts_ns=20,
            started_index=11,
            started_ts_ns=11,
            sweep_low=102.7,
            sweep_high=103.8,
            previous_high=103.8,
            previous_low=102.7,
            impulse_position=0.30,
            sweep_flow_ratio=-0.20,
        )
        response = snap(12, 12, 103.0, 104.7, 102.9, 104.5, 0.30)
        step = engine._emit(response, engine._bias, engine._sweep)  # type: ignore[arg-type]
        self.assertIsNotNone(step.signal)
        self.assertEqual(step.signal.family, "OLAR")  # type: ignore[union-attr]
        # snapshot.upper_fast=105.0 is nearer, but it is not an objective ledger entry.
        self.assertEqual(step.signal.target_price, 106.0)  # type: ignore[union-attr]

    def test_pending_signal_is_invalid_after_leg_renewal(self):
        engine = self.seeded()
        self.start_long(engine)
        leg = engine._active_leg
        assert leg is not None
        leg.reserve_entry(scenario_id="s1", index=11)
        signal = ScenarioSignal(
            scenario_id="s1",
            family="OLAR",
            direction="LONG",
            observed_ts_ns=11,
            reference_entry=104.0,
            stop_price=102.0,
            target_price=108.0,
            target_reason="ACTIVE_DIRECTIONAL_LEG_EXTREME",
            atr=1.0,
            liquidity_level=103.0,
            details={
                "bias_context_id": leg.context_id,
                "olar_leg_id": leg.leg_id,
                "olar_signal_index": 11,
            },
        )
        engine._create_directional_leg(
            bias=engine._bias,  # type: ignore[arg-type]
            origin=105.0,
            extreme=110.0,
            snapshot=snap(12, 12, 105.0, 110.0, 104.5, 109.0),
            reason="TEST_RENEWAL",
            source="TEST",
        )
        reason = engine.validate_pending_signal(
            signal,
            snap(13, 13, 109.0, 109.5, 108.0, 108.5),
        )
        self.assertEqual(reason, "OLAR_DIRECTIONAL_LEG_SUPERSEDED_BEFORE_ENTRY")

    def test_leg_cannot_sweep_on_creation_bar(self):
        engine = self.seeded()
        self.start_long(engine)
        self.assertIsNone(
            engine._maybe_start_sweep(
                snap(10, 10, 104.0, 104.5, 103.0, 104.2, -0.30),
            ),
        )


if __name__ == "__main__":
    unittest.main()

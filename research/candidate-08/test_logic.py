from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
import unittest
import pandas as pd


DATA_MODULE_PATH = Path(__file__).with_name("data.py")
DATA_SPEC = importlib.util.spec_from_file_location("candidate08_data", DATA_MODULE_PATH)
assert DATA_SPEC and DATA_SPEC.loader
candidate_data = importlib.util.module_from_spec(DATA_SPEC)
sys.modules[DATA_SPEC.name] = candidate_data
DATA_SPEC.loader.exec_module(candidate_data)


MODULE_PATH = Path(__file__).with_name("logic.py")
SPEC = importlib.util.spec_from_file_location("candidate08_logic", MODULE_PATH)
assert SPEC and SPEC.loader
logic = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = logic
SPEC.loader.exec_module(logic)


class LogicContractTests(unittest.TestCase):
    def test_millisecond_index_is_converted_to_epoch_nanoseconds(self) -> None:
        index = pd.to_datetime([1_712_534_459_999], unit="ms", utc=True)
        converted = candidate_data._index_to_nanoseconds(index)
        self.assertEqual(int(converted[0]), 1_712_534_459_999_000_000)

    def test_risk_quantity_never_exceeds_three_percent_budget(self) -> None:
        qty, planned = logic.risk_sized_quantity(
            nav=100_000.0,
            risk_fraction=0.03,
            expected_loss_per_unit=125.75,
            size_increment=0.001,
        )
        self.assertGreater(qty, 0)
        self.assertLessEqual(planned, 3_000.0)
        self.assertLess(3_000.0 - planned, 125.75 * 0.001 + 1e-9)

    def test_net_reward_risk_includes_both_fill_costs(self) -> None:
        loss, gain, ratio = logic.net_reward_risk(
            direction=logic.Direction.LONG,
            entry=100.0,
            stop=99.0,
            target=102.0,
            fee_rate=0.001,
            tick_size=0.1,
        )
        self.assertAlmostEqual(loss, 1.399, places=9)
        self.assertAlmostEqual(gain, 1.598, places=9)
        self.assertAlmostEqual(ratio, gain / loss, places=9)

    def test_confirmed_swing_is_observed_only_after_right_bars(self) -> None:
        cfg = logic.LogicConfig(
            atr_period=2,
            volume_period=2,
            swing_left=1,
            swing_right=1,
            minimum_atr_fraction=0.0,
        )
        engine = logic.LiquidityBifurcationLogic(cfg)
        bars = [
            logic.BarPoint(0, 1_000, 10.0, 11.0, 9.0, 10.0, 1.0),
            logic.BarPoint(1, 2_000, 10.0, 13.0, 9.5, 12.0, 1.0),
            logic.BarPoint(2, 3_000, 12.0, 12.5, 10.0, 11.0, 1.0),
        ]
        for bar in bars:
            engine.on_bar(bar)
        confirmations = [event for event in engine.events if event.event_type == "LIQUIDITY_POOL_CONFIRMED"]
        high = next(event for event in confirmations if event.reason_code == "CAUSAL_HIGH_SWING")
        self.assertEqual(high.event_time_ns, 2_000)
        self.assertEqual(high.observed_time_ns, 3_000)
        self.assertGreater(high.observed_time_ns, high.event_time_ns)

    def test_funding_distance_uses_utc_boundaries(self) -> None:
        # 07:30 UTC -> 30 minutes to 08:00 UTC.
        self.assertEqual(logic.minutes_to_next_funding((7 * 60 + 30) * 60 * 1_000_000_000), 30)

    def test_high_pool_interaction_splits_rejection_and_acceptance(self) -> None:
        cfg = logic.LogicConfig(
            minimum_rejection_wick_body=0.5,
            minimum_interaction_range_atr=0.5,
            minimum_volume_ratio=0.5,
            acceptance_close_atr=0.1,
            acceptance_body_atr=0.5,
            acceptance_close_location=0.65,
            acceptance_volume_ratio=0.8,
        )

        rejection = logic.LiquidityBifurcationLogic(cfg)
        rejected_pool = logic.LiquidityPool(
            pool_id="pool-high-r",
            kind=logic.PoolKind.HIGH,
            level=100.0,
            pivot_index=1,
            event_time_ns=1_000,
            observed_time_ns=2_000,
        )
        rejection.pools.append(rejected_pool)
        rejection._classify_interaction(
            rejected_pool,
            logic.BarPoint(10, 10_000, 99.8, 100.6, 99.0, 99.2, 100.0),
            atr=1.0,
            volume_median=100.0,
        )
        self.assertIsNotNone(rejection.pending)
        self.assertEqual(rejection.pending.family, logic.ScenarioFamily.REJECTION)
        self.assertEqual(rejection.pending.direction, logic.Direction.SHORT)

        acceptance = logic.LiquidityBifurcationLogic(cfg)
        accepted_pool = logic.LiquidityPool(
            pool_id="pool-high-a",
            kind=logic.PoolKind.HIGH,
            level=100.0,
            pivot_index=1,
            event_time_ns=1_000,
            observed_time_ns=2_000,
        )
        acceptance.pools.append(accepted_pool)
        acceptance._classify_interaction(
            accepted_pool,
            logic.BarPoint(10, 10_000, 99.8, 101.0, 99.7, 100.8, 100.0),
            atr=1.0,
            volume_median=100.0,
        )
        self.assertIsNotNone(acceptance.pending)
        self.assertEqual(acceptance.pending.family, logic.ScenarioFamily.ACCEPTANCE)
        self.assertEqual(acceptance.pending.direction, logic.Direction.LONG)

    def test_rejection_requires_subsequent_displacement_before_trade(self) -> None:
        cfg = logic.LogicConfig(rejection_confirmation_atr=0.08, stop_buffer_atr=0.1)
        engine = logic.LiquidityBifurcationLogic(cfg)
        engine.pending = logic.PendingScenario(
            scenario_id="lsb-test-r",
            family=logic.ScenarioFamily.REJECTION,
            direction=logic.Direction.LONG,
            pool_id="pool-low",
            pool_level=100.0,
            armed_index=5,
            expiry_index=8,
            atr=1.0,
            extreme=98.5,
            confirmation_level=100.0,
            reference_range=4.0,
            interaction_time_ns=5_000,
        )
        engine.pools.append(
            logic.LiquidityPool(
                pool_id="pool-target",
                kind=logic.PoolKind.HIGH,
                level=104.0,
                pivot_index=2,
                event_time_ns=2_000,
                observed_time_ns=3_000,
            )
        )
        not_confirmed = engine._advance_pending(
            logic.BarPoint(6, 6_000, 99.8, 100.1, 99.4, 99.9, 100.0),
            atr=1.0,
            volume_median=100.0,
            trading_available=True,
        )
        self.assertIsNone(not_confirmed)
        setup = engine._advance_pending(
            logic.BarPoint(7, 7_000, 99.9, 101.0, 99.8, 100.8, 100.0),
            atr=1.0,
            volume_median=100.0,
            trading_available=True,
        )
        self.assertIsNotNone(setup)
        self.assertEqual(setup.family, logic.ScenarioFamily.REJECTION)
        self.assertLess(setup.structural_stop, setup.estimated_entry)
        self.assertGreater(setup.liquidity_target, setup.estimated_entry)

    def test_acceptance_requires_contracted_retest_then_independent_follow_through(self) -> None:
        cfg = logic.LogicConfig(
            retest_outer_atr=0.22,
            retest_inner_atr=0.38,
            retest_close_atr=0.01,
            acceptance_retest_volume_fraction=0.75,
            acceptance_follow_through_bars=3,
            acceptance_follow_through_atr=0.05,
            acceptance_follow_through_body_atr=0.25,
        )
        engine = logic.LiquidityBifurcationLogic(cfg)
        engine.pending = logic.PendingScenario(
            scenario_id="lsb-test-a",
            family=logic.ScenarioFamily.ACCEPTANCE,
            direction=logic.Direction.LONG,
            pool_id="pool-break",
            pool_level=100.0,
            armed_index=5,
            expiry_index=15,
            atr=1.0,
            extreme=99.5,
            confirmation_level=100.0,
            reference_range=4.0,
            interaction_time_ns=5_000,
            interaction_volume_ratio=2.0,
            pool_age_bars=60,
        )
        engine.pools.append(
            logic.LiquidityPool(
                pool_id="pool-target",
                kind=logic.PoolKind.HIGH,
                level=104.0,
                pivot_index=2,
                event_time_ns=2_000,
                observed_time_ns=3_000,
            )
        )
        no_retest = engine._advance_pending(
            logic.BarPoint(6, 6_000, 101.0, 101.4, 100.5, 101.2, 100.0),
            atr=1.0,
            volume_median=100.0,
            trading_available=True,
        )
        self.assertIsNone(no_retest)
        held_only = engine._advance_pending(
            logic.BarPoint(7, 7_000, 100.5, 100.8, 100.1, 100.6, 100.0),
            atr=1.0,
            volume_median=100.0,
            trading_available=True,
        )
        self.assertIsNone(held_only)
        self.assertEqual(engine.pending.retest_index, 7)
        setup = engine._advance_pending(
            logic.BarPoint(8, 8_000, 100.6, 101.5, 100.5, 101.3, 120.0),
            atr=1.0,
            volume_median=100.0,
            trading_available=True,
        )
        self.assertIsNotNone(setup)
        self.assertEqual(setup.family, logic.ScenarioFamily.ACCEPTANCE)
        self.assertEqual(setup.direction, logic.Direction.LONG)

    def test_acceptance_cancels_noncontracted_retest(self) -> None:
        engine = logic.LiquidityBifurcationLogic(
            logic.LogicConfig(acceptance_retest_volume_fraction=0.75)
        )
        engine.pending = logic.PendingScenario(
            scenario_id="lsb-test-hot-retest",
            family=logic.ScenarioFamily.ACCEPTANCE,
            direction=logic.Direction.LONG,
            pool_id="pool-break",
            pool_level=100.0,
            armed_index=5,
            expiry_index=15,
            atr=1.0,
            extreme=99.5,
            confirmation_level=100.0,
            reference_range=4.0,
            interaction_time_ns=5_000,
            interaction_volume_ratio=1.0,
            pool_age_bars=60,
        )
        result = engine._advance_pending(
            logic.BarPoint(6, 6_000, 100.5, 100.8, 100.1, 100.6, 100.0),
            atr=1.0,
            volume_median=100.0,
            trading_available=True,
        )
        self.assertIsNone(result)
        self.assertIsNone(engine.pending)
        self.assertEqual(engine.events[-1].reason_code, "ACCEPTANCE_RETEST_NOT_CONTRACTED")

    def test_fresh_single_touch_pool_is_not_external_liquidity(self) -> None:
        cfg = logic.LogicConfig(
            minimum_pool_visibility_bars=30,
            acceptance_close_atr=0.1,
            acceptance_body_atr=0.5,
            acceptance_close_location=0.65,
            acceptance_volume_ratio=0.8,
        )
        engine = logic.LiquidityBifurcationLogic(cfg)
        pool = logic.LiquidityPool(
            pool_id="pool-fresh",
            kind=logic.PoolKind.HIGH,
            level=100.0,
            pivot_index=9,
            event_time_ns=9_000,
            observed_time_ns=10_000,
        )
        engine.pools.append(pool)
        bar = logic.BarPoint(10, 10_000, 99.8, 101.0, 99.7, 100.8, 100.0)
        engine._detect_new_interaction(bar, 99.8, 1.0, 100.0)
        self.assertIsNone(engine.pending)
        pool.touches = 2
        engine._detect_new_interaction(
            logic.BarPoint(11, 11_000, 99.8, 101.0, 99.7, 100.8, 100.0),
            99.8,
            1.0,
            100.0,
        )
        self.assertIsNotNone(engine.pending)


if __name__ == "__main__":
    unittest.main()

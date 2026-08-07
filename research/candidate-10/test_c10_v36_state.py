from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from logic import Auction, BarObs, Direction, LogicConfig, Pool, Scenario, Side

from c10_v36_overlay import consequent_encroachment
from c10_v36_overlay import rejection_displacement
from c10_v36_overlay import source_equilibrium
from c10_v36_state import ConsequentEncroachmentRejectionEngine


def bar(
    ts: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    taker_buy: float,
) -> BarObs:
    return BarObs(
        ts_ns=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        taker_buy_volume=taker_buy,
    )


def config() -> LogicConfig:
    return LogicConfig(
        atr_period=2,
        volume_period=2,
        internal_tf_bars=2,
        external_tf_bars=4,
        min_net_r=1.25,
        displacement_body_atr=0.20,
        displacement_flow_min=0.03,
        acceptance_close_location=0.60,
        retrace_expiry_bars=12,
        stop_buffer_atr=0.08,
    )


def auction() -> tuple[ConsequentEncroachmentRejectionEngine, Auction, BarObs]:
    engine = ConsequentEncroachmentRejectionEngine(config(), "TEST-PERP.BINANCE")
    source = Pool(
        scenario_id="HIGH-RAID",
        side=Side.HIGH,
        level=100.0,
        source="NYAM_0700_1000_NY",
        candidate_ts_ns=10,
        confirmed_ts_ns=20,
        confirmed_index=0,
        expiry_index=100,
        opposite_level=80.0,
        range_id="RANGE-1",
        strength=2,
    )
    sweep = bar(100, 100.0, 104.0, 99.0, 101.0, 70.0)
    confirmation = bar(200, 99.0, 100.0, 95.8, 96.0, 20.0)
    a = Auction(
        pool=source,
        sweep=sweep,
        sweep_index=0,
        atr=4.0,
        internal_level=98.0,
        sweep_extreme=104.0,
        rejection_seed=True,
        acceptance_seed=False,
        state="FAR_CONFIRMED",
        scenario=Scenario.FAR,
        direction=Direction.SHORT,
        stop_price=105.0,
        target_price=80.0,
        displacement_index=1,
        zone_low=98.0,
        zone_high=100.0,
        initial_sweep_ts_ns=100,
    )
    engine.active = a
    engine.bars = [sweep, confirmation]
    engine._index = 1
    return engine, a, confirmation


class V36PureContractTest(unittest.TestCase):
    def test_consequent_encroachment_is_exact_midpoint(self) -> None:
        self.assertEqual(consequent_encroachment(98.0, 100.0), 99.0)

    def test_source_equilibrium_is_exact_source_range_midpoint(self) -> None:
        engine, a, _ = auction()
        self.assertEqual(source_equilibrium(a.pool), 90.0)
        self.assertIsNotNone(engine)

    def test_rejection_uses_all_frozen_displacement_conditions(self) -> None:
        cfg = config()
        candidate = bar(300, 95.4, 95.5, 93.8, 94.0, 20.0)
        signal = rejection_displacement(
            direction="SHORT",
            bar=candidate,
            touch_bar_threshold=95.6,
            atr=4.0,
            config=cfg,
        )
        self.assertTrue(signal.confirmed)
        weak_flow = bar(300, 95.4, 95.5, 93.8, 94.0, 49.0)
        rejected = rejection_displacement(
            direction="SHORT",
            bar=weak_flow,
            touch_bar_threshold=95.6,
            atr=4.0,
            config=cfg,
        )
        self.assertFalse(rejected.confirmed)
        self.assertFalse(rejected.directional_flow)


class V36StateMachineTest(unittest.TestCase):
    def test_first_displacement_arms_state_but_emits_no_plan(self) -> None:
        engine, a, confirmation = auction()
        with patch.dict(
            os.environ,
            {
                "C10_V36_CE_REJECTION": "1",
                "C10_V36_EQUILIBRIUM_TARGET": "1",
            },
        ):
            plan = engine._costed_limit_plan(a, confirmation, "FIRST")
        self.assertIsNone(plan)
        self.assertEqual(a.state, "FAR_CE_RETEST_ARMED")
        self.assertEqual(a.target_price, 90.0)
        self.assertEqual(engine._ce_states[a.pool.scenario_id]["ce"], 99.0)
        self.assertEqual(engine.events[-1].event_type, "CE_RETEST_ARMED")

    def test_touch_requires_a_later_completed_rejection_displacement(self) -> None:
        engine, a, confirmation = auction()
        touch = bar(300, 97.0, 99.1, 95.6, 98.0, 55.0)
        rejection = bar(400, 95.4, 95.5, 93.8, 94.0, 20.0)
        with patch.dict(
            os.environ,
            {
                "C10_V36_CE_REJECTION": "1",
                "C10_V36_EQUILIBRIUM_TARGET": "1",
            },
        ):
            self.assertIsNone(engine._costed_limit_plan(a, confirmation, "FIRST"))
            engine.bars.append(touch)
            engine._index = 2
            self.assertIsNone(engine._confirm_far(a, touch))
            self.assertEqual(a.state, "FAR_CE_RETEST_TOUCHED")
            engine.bars.append(rejection)
            engine._index = 3
            plan = engine._confirm_far(a, rejection)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.target_price, 90.0)
        self.assertAlmostEqual(plan.stop_price, 99.42)
        self.assertEqual(plan.expected_entry, 95.5)
        self.assertGreater(plan.net_r, 1.25)
        self.assertEqual(plan.reason_code, "FAR_CE_REJECTION_TO_SOURCE_EQUILIBRIUM")
        contract = plan.details["ce_rejection_primary"]
        self.assertEqual(contract["ce_touch_ts_ns"], 300)
        self.assertEqual(contract["rejection_confirmation_ts_ns"], 400)
        self.assertEqual(contract["target_contract"], "SOURCE_DEALING_RANGE_EQUILIBRIUM")

    def test_primary_target_before_touch_terminates_without_entry(self) -> None:
        engine, a, confirmation = auction()
        target_bar = bar(300, 97.0, 99.1, 89.0, 91.0, 40.0)
        with patch.dict(
            os.environ,
            {
                "C10_V36_CE_REJECTION": "1",
                "C10_V36_EQUILIBRIUM_TARGET": "1",
            },
        ):
            self.assertIsNone(engine._costed_limit_plan(a, confirmation, "FIRST"))
            engine.bars.append(target_bar)
            engine._index = 2
            self.assertIsNone(engine._confirm_far(a, target_bar))
        self.assertIsNone(engine.active)
        self.assertEqual(
            engine.events[-1].reason_code,
            "V36_PRIMARY_TARGET_REACHED_BEFORE_CE_ENTRY",
        )

    def test_raid_invalidation_before_touch_terminates_without_entry(self) -> None:
        engine, a, confirmation = auction()
        invalidation = bar(300, 102.0, 105.1, 98.5, 104.0, 70.0)
        with patch.dict(
            os.environ,
            {
                "C10_V36_CE_REJECTION": "1",
                "C10_V36_EQUILIBRIUM_TARGET": "1",
            },
        ):
            self.assertIsNone(engine._costed_limit_plan(a, confirmation, "FIRST"))
            engine.bars.append(invalidation)
            engine._index = 2
            self.assertIsNone(engine._confirm_far(a, invalidation))
        self.assertIsNone(engine.active)
        self.assertEqual(
            engine.events[-1].reason_code,
            "V36_RAID_INVALIDATED_BEFORE_CE_ENTRY",
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from contracts_v5 import (
    Channel,
    ObjectKind,
    Pivot,
    ScenarioPath,
    ScenarioSetup,
    SetupState,
    TrendLine,
)
from domain import Candle, Side
from easychart_zones import ZoneSide
from scenario_bundle_v5 import ResearchScenarioBundleV5
from scenario_engine_v5 import StructureScenarioEngine

NS = 60_000_000_000


def candle(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(index * NS, open_, high, low, close, 1.0)


def pivot(
    pivot_id: str,
    side: str,
    price: float,
    event_index: int,
    observed_index: int,
    span: int = 2,
) -> Pivot:
    return Pivot(
        pivot_id,
        side,
        price,
        event_index,
        event_index * NS,
        observed_index,
        observed_index * NS,
        span,
        2.0,
    )


def make_engine() -> StructureScenarioEngine:
    return StructureScenarioEngine(
        "TEST",
        0.1,
        scale_name="MACRO",
        higher_minutes=60,
        decision_minutes=15,
        trigger_minutes=5,
        minimum_gross_rr=1.0,
    )


def add_pivots(engine: StructureScenarioEngine, *items: Pivot) -> None:
    engine.structure.pivots.extend(items)
    engine.structure._pivot_ids.update(item.pivot_id for item in items)
    engine.structure._active_pivots.update({item.pivot_id: item for item in items})


class StructureScenarioTests(unittest.TestCase):
    def test_sweep_reclaim_uses_event_local_footprint_and_first_retest(self) -> None:
        engine = make_engine()
        source = pivot("LOW_SOURCE", "LOW", 100.0, 1, 3)
        target = pivot("HIGH_TARGET", "HIGH", 120.0, 0, 2)
        add_pivots(engine, target, source)

        engine.on_bar(15, candle(10, 105, 106, 104, 105))
        engine.on_bar(15, candle(11, 101, 102, 99.5, 101))
        active = list(engine._active.values())
        self.assertEqual(len(active), 1)
        self.assertIs(active[0].path, ScenarioPath.REJECTION)
        self.assertIs(active[0].state, SetupState.WAITING_DISPLACEMENT)

        engine.on_bar(5, candle(12, 101.0, 101.2, 99.8, 100.5))
        self.assertIs(active[0].state, SetupState.WAITING_DISPLACEMENT)
        engine.on_bar(5, candle(13, 100.4, 102.2, 99.7, 102.0))
        self.assertIs(active[0].state, SetupState.WAITING_FOOTPRINT_RETEST)
        plans = engine.on_bar(5, candle(14, 101.2, 102.0, 100.6, 101.5))
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertIs(plan.side, Side.LONG)
        self.assertEqual(plan.scenario_path, "REJECTION")
        self.assertEqual(plan.entry, 101.5)
        self.assertLess(plan.stop, plan.entry)
        self.assertLess(plan.entry, plan.target)
        self.assertGreaterEqual(plan.gross_rr, 1.0)
        self.assertIs(active[0].state, SetupState.PLANNED)

    def test_acceptance_requires_next_decision_hold_then_first_retest(self) -> None:
        engine = make_engine()
        target = pivot("HIGH_TARGET", "HIGH", 120.0, 0, 2)
        source = pivot("HIGH_SOURCE", "HIGH", 110.0, 3, 5)
        origin = pivot("LOW_ORIGIN", "LOW", 107.0, 5, 7)
        add_pivots(engine, target, source, origin)

        engine.on_bar(15, candle(10, 108, 109, 107.5, 108.5))
        engine.on_bar(15, candle(11, 108.5, 112, 108, 111.0))
        setup = list(engine._active.values())[0]
        self.assertIs(setup.path, ScenarioPath.ACCEPTANCE)
        self.assertIs(setup.state, SetupState.WAITING_ACCEPTANCE_HOLD)
        engine.on_bar(15, candle(12, 111.2, 112.0, 110.8, 111.5))
        self.assertIs(setup.state, SetupState.WAITING_ACCEPTANCE_RETEST)
        plans = engine.on_bar(5, candle(13, 111.0, 112.0, 110.0, 111.4))
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertIs(plan.side, Side.LONG)
        self.assertEqual(plan.scenario_path, "ACCEPTANCE")
        self.assertEqual(plan.stop, 106.9)
        self.assertEqual(plan.target, 120.0)
        self.assertGreaterEqual(plan.gross_rr, 1.0)

    def test_failed_first_footprint_retest_is_not_retried(self) -> None:
        engine = make_engine()
        source = pivot("LOW_SOURCE", "LOW", 100.0, 1, 3)
        target = pivot("HIGH_TARGET", "HIGH", 120.0, 0, 2)
        add_pivots(engine, target, source)
        engine.on_bar(15, candle(10, 105, 106, 104, 105))
        engine.on_bar(15, candle(11, 101, 102, 99.5, 101))
        setup = list(engine._active.values())[0]
        engine.on_bar(5, candle(12, 101.0, 101.2, 99.8, 100.5))
        engine.on_bar(5, candle(13, 100.4, 102.2, 99.7, 102.0))
        self.assertIs(setup.state, SetupState.WAITING_FOOTPRINT_RETEST)
        self.assertFalse(engine.on_bar(5, candle(14, 101.2, 101.4, 100.6, 100.7)))
        self.assertIs(setup.state, SetupState.UNRESOLVED)
        self.assertFalse(engine.on_bar(5, candle(15, 100.7, 102.0, 100.6, 101.8)))
        self.assertFalse(engine.plans)

    def test_no_preexisting_opposite_target_produces_no_trade(self) -> None:
        engine = make_engine()
        source = pivot("LOW_SOURCE", "LOW", 100.0, 1, 3)
        add_pivots(engine, source)
        engine.on_bar(15, candle(10, 105, 106, 104, 105))
        engine.on_bar(15, candle(11, 101, 102, 99.5, 101))
        self.assertFalse(engine._active)
        self.assertIs(engine.setups[-1].state, SetupState.NO_TARGET)
        self.assertFalse(engine.plans)

    def test_trigger_bar_cannot_consume_context_before_decision_close(self) -> None:
        engine = make_engine()
        source = pivot("LOW_SOURCE", "LOW", 100.0, 1, 3)
        target = pivot("HIGH_TARGET", "HIGH", 120.0, 0, 2)
        add_pivots(engine, target, source)
        engine.on_bar(5, candle(9, 101.0, 101.2, 99.5, 100.5))
        self.assertIn(source.pivot_id, engine.structure._active_pivots)
        engine.on_bar(15, candle(10, 105, 106, 104, 105))
        engine.on_bar(15, candle(11, 101, 102, 99.5, 101))
        self.assertTrue(
            any(setup.context.source_structure_id == source.pivot_id for setup in engine.setups),
        )

    def test_meaningful_structure_allows_sub_two_x_ob_footprint(self) -> None:
        engine = make_engine()
        source = pivot("LOW_SOURCE", "LOW", 100.0, 1, 3)
        target = pivot("HIGH_TARGET", "HIGH", 120.0, 0, 2)
        add_pivots(engine, target, source)
        engine.on_bar(15, candle(10, 105, 106, 104, 105))
        engine.on_bar(15, candle(11, 101, 102, 99.5, 101))
        setup = list(engine._active.values())[0]
        engine.on_bar(5, candle(12, 101.2, 101.3, 99.8, 100.2))
        engine.on_bar(5, candle(13, 100.1, 101.4, 99.7, 101.3))
        self.assertIsNotNone(setup.trigger_zone)
        self.assertLess(setup.trigger_zone.strength_ratio, 2.0)
        self.assertIs(setup.state, SetupState.WAITING_FOOTPRINT_RETEST)

    def test_acceptance_without_origin_is_terminal_and_claims_interaction(self) -> None:
        engine = make_engine()
        target = pivot("HIGH_TARGET", "HIGH", 130.0, 0, 2)
        source = pivot("HIGH_SOURCE", "HIGH", 110.0, 3, 5)
        add_pivots(engine, target, source)
        engine.on_bar(15, candle(10, 108, 109, 107.5, 108.5))
        engine.on_bar(15, candle(11, 108.5, 112, 108, 111.0))
        self.assertFalse(engine._active)
        self.assertIs(engine.setups[-1].state, SetupState.UNRESOLVED)
        count = len(engine.setups)
        engine.on_bar(15, candle(12, 111.0, 113, 109.9, 112.0))
        self.assertEqual(len(engine.setups), count)

    def test_channel_target_is_frozen_at_entry_not_interaction(self) -> None:
        engine = make_engine()
        channel = Channel(
            channel_id="ASC_CH",
            timeframe_minutes=60,
            direction="ASCENDING",
            main_first_pivot_id="L1",
            main_second_pivot_id="L2",
            opposite_pivot_id="H1",
            first_time_ns=0,
            second_time_ns=10 * NS,
            first_price=100.0,
            second_price=110.0,
            offset=10.0,
            observed_time_ns=10 * NS,
            pivot_span=2,
            strength_ratio=2.0,
        )
        engine.structure.channels.append(channel)
        context = engine.structure.channel_edge_snapshot(channel, "LOWER", 11 * NS)
        setup = engine._create_setup(
            path=ScenarioPath.ROTATION,
            context=context,
            members=(context,),
            bar=candle(11, 111, 112, 110.8, 111.5),
            decision_index=0,
            state=SetupState.WAITING_DISPLACEMENT,
        )
        self.assertIsNotNone(setup)
        assert setup is not None
        self.assertAlmostEqual(setup.target_price, 121.0)
        plan = engine._make_plan(
            setup,
            candle(20, 117.0, 119.0, 116.5, 118.0),
            entry=118.0,
            stop=109.5,
            trigger_zone=context,
            trigger_kind=context.kind,
            trigger_strength=context.strength_ratio,
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertAlmostEqual(plan.target, 130.0)

    def test_acceptance_retest_uses_current_diagonal_line_value(self) -> None:
        engine = make_engine()
        line = TrendLine(
            structure_id="DOWN_LINE",
            kind=ObjectKind.DOWNTREND_LINE,
            side=ZoneSide.RESISTANCE,
            timeframe_minutes=60,
            first_pivot_id="H1",
            second_pivot_id="H2",
            first_time_ns=0,
            second_time_ns=10 * NS,
            first_price=120.0,
            second_price=110.0,
            observed_time_ns=10 * NS,
            pivot_span=2,
            strength_ratio=2.0,
        )
        engine.structure.trend_lines.append(line)
        context = engine.structure._line_snapshot(line, 11 * NS)
        target_pivot = pivot("HIGH_TARGET", "HIGH", 130.0, 0, 2)
        origin = pivot("LOW_ORIGIN", "LOW", 100.0, 2, 4)
        engine.structure.pivots.extend([target_pivot, origin])
        target_zone = engine.structure._horizontal_snapshot(target_pivot, 11 * NS)
        setup = ScenarioSetup(
            setup_id="ACCEPT_DYNAMIC",
            scale_name="MACRO",
            path=ScenarioPath.ACCEPTANCE,
            side=Side.LONG,
            state=SetupState.WAITING_ACCEPTANCE_RETEST,
            context=context,
            context_members=(context,),
            observed_time_ns=10 * NS,
            interaction_time_ns=11 * NS,
            interaction_index=0,
            interaction_extreme=108.0,
            target_zone=target_zone,
            target_price=130.0,
            confirmation_time_ns=12 * NS,
            acceptance_break_index=0,
            acceptance_origin=origin,
        )
        engine.setups.append(setup)
        engine._active[setup.setup_id] = setup
        plans = engine._advance_acceptance_retests(candle(13, 107.2, 108.0, 106.9, 107.8), 0)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].entry, 107.8)

    def test_cross_scale_overlapping_decision_intervals_are_one_episode(self) -> None:
        from types import SimpleNamespace

        bundle = ResearchScenarioBundleV5("TEST", 0.1)
        macro = SimpleNamespace(
            side=Side.LONG,
            decision_timeframe_minutes=15,
            interaction_time_ns=30 * NS,
            overlap_lower=99.9,
            overlap_upper=100.1,
        )
        micro_same_event = SimpleNamespace(
            side=Side.LONG,
            decision_timeframe_minutes=5,
            interaction_time_ns=25 * NS,
            overlap_lower=100.0,
            overlap_upper=100.2,
        )
        micro_other_price = SimpleNamespace(
            side=Side.LONG,
            decision_timeframe_minutes=5,
            interaction_time_ns=25 * NS,
            overlap_lower=102.0,
            overlap_upper=102.2,
        )
        bundle._claim_episode(macro)
        self.assertTrue(bundle._duplicate_episode(micro_same_event))
        self.assertFalse(bundle._duplicate_episode(micro_other_price))


if __name__ == "__main__":
    unittest.main()

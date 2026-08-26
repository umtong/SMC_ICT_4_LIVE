from __future__ import annotations

import unittest

from contracts_v5 import (
    Channel,
    Pivot,
    ScenarioPath,
    ScenarioSetup,
    SetupState,
)
from domain import Candle, Side
from scenario_engine_v5 import StructureScenarioEngine

NS = 60_000_000_000


def candle(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(index * NS, open_, high, low, close, 1.0)


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


class EntryBarStopGeometryTests(unittest.TestCase):
    def test_channel_acceptance_stop_extends_beyond_completed_retest_bar(self) -> None:
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
            first_price=90.0,
            second_price=100.0,
            offset=10.0,
            observed_time_ns=10 * NS,
            pivot_span=2,
            strength_ratio=2.0,
        )
        engine.structure.channels.append(channel)
        context = engine.structure.channel_edge_snapshot(channel, "UPPER", 20 * NS)
        setup = ScenarioSetup(
            setup_id="CHANNEL_ACCEPTANCE",
            scale_name="MACRO",
            path=ScenarioPath.ACCEPTANCE,
            side=Side.LONG,
            state=SetupState.WAITING_ACCEPTANCE_RETEST,
            context=context,
            context_members=(context,),
            observed_time_ns=10 * NS,
            interaction_time_ns=20 * NS,
            interaction_index=0,
            interaction_extreme=118.0,
            target_zone=None,
            target_price=None,
            confirmation_time_ns=20 * NS,
        )
        retest = candle(21, 120.5, 121.0, 119.5, 120.4)
        engine._current_trigger_bar = retest
        try:
            stop = engine._acceptance_stop(setup, retest.ts_close_ns)
        finally:
            engine._current_trigger_bar = None
        self.assertEqual(stop, 119.4)
        self.assertLess(stop, retest.low)
        self.assertEqual(
            engine.diagnostics.get("acceptance_stop_extended_beyond_entry_bar"),
            1,
        )

    def test_plan_guard_rejects_stop_already_traded_inside_entry_bar(self) -> None:
        engine = make_engine()
        source = Pivot("LOW_SOURCE", "LOW", 100.0, 1, NS, 3, 3 * NS, 2, 2.0)
        target = Pivot("HIGH_TARGET", "HIGH", 110.0, 0, 0, 2, 2 * NS, 2, 2.0)
        context = engine.structure._horizontal_snapshot(source, 10 * NS)
        target_zone = engine.structure._horizontal_snapshot(target, 10 * NS)
        setup = ScenarioSetup(
            setup_id="BAD_STOP",
            scale_name="MACRO",
            path=ScenarioPath.ACCEPTANCE,
            side=Side.LONG,
            state=SetupState.WAITING_ACCEPTANCE_RETEST,
            context=context,
            context_members=(context,),
            observed_time_ns=3 * NS,
            interaction_time_ns=10 * NS,
            interaction_index=0,
            interaction_extreme=99.0,
            target_zone=target_zone,
            target_price=110.0,
            confirmation_time_ns=10 * NS,
        )
        bar = candle(11, 100.8, 101.5, 100.0, 101.0)
        plan = engine._make_plan(
            setup,
            bar,
            entry=101.0,
            stop=100.5,
            trigger_zone=context,
            trigger_kind=context.kind,
            trigger_strength=context.strength_ratio,
        )
        self.assertIsNone(plan)
        self.assertIs(setup.state, SetupState.NO_TRADE_GEOMETRY)
        self.assertEqual(setup.terminal_reason, "stop_inside_observed_entry_bar")


if __name__ == "__main__":
    unittest.main()

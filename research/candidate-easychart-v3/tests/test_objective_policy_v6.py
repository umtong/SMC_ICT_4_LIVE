from __future__ import annotations

import unittest

from contracts_v5 import Channel, Pivot, ScenarioPath, SetupState
from domain import Candle
from objective_policy_v6 import FirstObstacleScenarioContextMixin
from scenario_engine_v5 import StructureScenarioEngine

NS = 60_000_000_000


def candle(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(index * NS, open_, high, low, close, 1.0)


def pivot(pivot_id: str, side: str, price: float, event_index: int, observed_index: int) -> Pivot:
    return Pivot(
        pivot_id=pivot_id,
        side=side,
        price=price,
        index=event_index,
        event_time_ns=event_index * NS,
        observed_index=observed_index,
        observed_time_ns=observed_index * NS,
        span=2,
        strength_ratio=2.0,
    )


def engine() -> StructureScenarioEngine:
    return StructureScenarioEngine(
        "TEST",
        0.1,
        scale_name="MICRO",
        higher_minutes=60,
        decision_minutes=15,
        trigger_minutes=5,
        minimum_gross_rr=1.0,
    )


def ascending_channel() -> Channel:
    return Channel(
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


class FirstObstacleObjectivePolicyTests(unittest.TestCase):
    def test_engine_uses_first_obstacle_context_mixin(self) -> None:
        self.assertTrue(issubclass(StructureScenarioEngine, FirstObstacleScenarioContextMixin))

    def test_nearer_horizontal_objective_blocks_farther_channel_edge(self) -> None:
        item = engine()
        channel = ascending_channel()
        item.structure.channels.append(channel)
        near = pivot("HIGH_NEAR", "HIGH", 115.0, 1, 2)
        item.structure.pivots.append(near)
        item.structure._pivot_ids.add(near.pivot_id)
        item.structure._active_pivots[near.pivot_id] = near
        context = item.structure.channel_edge_snapshot(channel, "LOWER", 11 * NS)

        setup = item._create_setup(
            path=ScenarioPath.ROTATION,
            context=context,
            members=(context,),
            bar=candle(11, 111.0, 112.0, 110.8, 111.5),
            decision_index=0,
            state=SetupState.WAITING_DISPLACEMENT,
        )

        self.assertIsNotNone(setup)
        assert setup is not None
        self.assertAlmostEqual(setup.target_price or 0.0, 115.0)
        self.assertEqual(setup.target_zone.source_structure_id, "HIGH_NEAR")
        self.assertIsNone(setup.channel_id)

    def test_channel_edge_remains_target_when_it_is_first_obstacle(self) -> None:
        item = engine()
        channel = ascending_channel()
        item.structure.channels.append(channel)
        far = pivot("HIGH_FAR", "HIGH", 130.0, 1, 2)
        item.structure.pivots.append(far)
        item.structure._pivot_ids.add(far.pivot_id)
        item.structure._active_pivots[far.pivot_id] = far
        context = item.structure.channel_edge_snapshot(channel, "LOWER", 11 * NS)

        setup = item._create_setup(
            path=ScenarioPath.ROTATION,
            context=context,
            members=(context,),
            bar=candle(11, 111.0, 112.0, 110.8, 111.5),
            decision_index=0,
            state=SetupState.WAITING_DISPLACEMENT,
        )

        self.assertIsNotNone(setup)
        assert setup is not None
        self.assertAlmostEqual(setup.target_price or 0.0, 121.0)
        self.assertEqual(setup.channel_id, "ASC_CH")

    def test_already_traded_near_level_is_not_reintroduced_as_future_space(self) -> None:
        item = engine()
        channel = ascending_channel()
        item.structure.channels.append(channel)
        spent_inside_bar = pivot("HIGH_INSIDE", "HIGH", 111.8, 1, 2)
        item.structure.pivots.append(spent_inside_bar)
        item.structure._pivot_ids.add(spent_inside_bar.pivot_id)
        item.structure._active_pivots[spent_inside_bar.pivot_id] = spent_inside_bar
        context = item.structure.channel_edge_snapshot(channel, "LOWER", 11 * NS)

        setup = item._create_setup(
            path=ScenarioPath.ROTATION,
            context=context,
            members=(context,),
            bar=candle(11, 111.0, 112.0, 110.8, 111.5),
            decision_index=0,
            state=SetupState.WAITING_DISPLACEMENT,
        )

        self.assertIsNotNone(setup)
        assert setup is not None
        self.assertAlmostEqual(setup.target_price or 0.0, 121.0)
        self.assertEqual(setup.channel_id, "ASC_CH")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from causal_lifecycle_v5 import LifecycleAwareStructureBook
from contracts_v5 import Pivot, ScenarioPath
from domain import Side
from objective_ladder_v5 import CausalObjectiveLadder
from scenario_engine_v5 import StructureScenarioEngine

NS = 60_000_000_000


def pivot(
    pivot_id: str,
    side: str,
    price: float,
    *,
    span: int,
    event_index: int,
    observed_index: int,
    consumed_time_ns: int | None = None,
) -> Pivot:
    return Pivot(
        pivot_id=pivot_id,
        side=side,
        price=price,
        index=event_index,
        event_time_ns=event_index * NS,
        observed_index=observed_index,
        observed_time_ns=observed_index * NS,
        span=span,
        strength_ratio=2.0,
        consumed=consumed_time_ns is not None,
        consumed_time_ns=consumed_time_ns,
    )


class ObjectiveLadderTests(unittest.TestCase):
    def make_ladder(self) -> CausalObjectiveLadder:
        primary = LifecycleAwareStructureBook("TEST", 60, 0.1)
        return CausalObjectiveLadder(
            primary,
            symbol="TEST",
            decision_minutes=15,
            trigger_minutes=5,
            tick_size=0.1,
        )

    def test_trigger_objective_caps_decision_and_context_targets(self) -> None:
        ladder = self.make_ladder()
        primary = pivot(
            "60M_HIGH",
            "HIGH",
            120.0,
            span=6,
            event_index=1,
            observed_index=7,
        )
        decision = pivot(
            "15M_HIGH",
            "HIGH",
            108.0,
            span=2,
            event_index=2,
            observed_index=4,
        )
        trigger = pivot(
            "5M_HIGH",
            "HIGH",
            104.0,
            span=2,
            event_index=3,
            observed_index=5,
        )
        ladder.primary.pivots.append(primary)
        ladder.decision.pivots.append(decision)
        ladder.trigger.pivots.append(trigger)

        target = ladder.target_for(
            Side.LONG,
            interaction_time_ns=10 * NS,
            source_span=6,
            current_high=101.0,
            current_low=99.0,
        )
        self.assertIsNotNone(target)
        assert target is not None
        zone, price = target
        self.assertEqual(price, 104.0)
        self.assertEqual(zone.source_structure_id, trigger.pivot_id)
        self.assertEqual(zone.timeframe_minutes, 5)
        self.assertEqual(
            ladder.diagnostics.get("lower_timeframe_objective_preceded_context_objective"),
            1,
        )

    def test_consumed_trigger_objective_falls_back_to_decision_objective(self) -> None:
        ladder = self.make_ladder()
        ladder.primary.pivots.append(
            pivot(
                "60M_HIGH",
                "HIGH",
                120.0,
                span=6,
                event_index=1,
                observed_index=7,
            ),
        )
        decision = pivot(
            "15M_HIGH",
            "HIGH",
            108.0,
            span=2,
            event_index=2,
            observed_index=4,
        )
        ladder.decision.pivots.append(decision)
        ladder.trigger.pivots.append(
            pivot(
                "5M_HIGH_CONSUMED",
                "HIGH",
                104.0,
                span=2,
                event_index=3,
                observed_index=5,
                consumed_time_ns=8 * NS,
            ),
        )
        target = ladder.target_for(
            Side.LONG,
            interaction_time_ns=10 * NS,
            source_span=6,
            current_high=101.0,
            current_low=99.0,
        )
        self.assertIsNotNone(target)
        assert target is not None
        zone, price = target
        self.assertEqual(price, 108.0)
        self.assertEqual(zone.source_structure_id, decision.pivot_id)
        self.assertEqual(zone.timeframe_minutes, 15)

    def test_same_price_prefers_higher_timeframe_representation(self) -> None:
        ladder = self.make_ladder()
        primary = pivot(
            "60M_HIGH",
            "HIGH",
            110.0,
            span=2,
            event_index=1,
            observed_index=4,
        )
        decision = pivot(
            "15M_HIGH",
            "HIGH",
            110.0,
            span=6,
            event_index=2,
            observed_index=8,
        )
        ladder.primary.pivots.append(primary)
        ladder.decision.pivots.append(decision)
        target = ladder.target_for(
            Side.LONG,
            interaction_time_ns=10 * NS,
            source_span=2,
            current_high=101.0,
            current_low=99.0,
        )
        self.assertIsNotNone(target)
        assert target is not None
        zone, price = target
        self.assertEqual(price, 110.0)
        self.assertEqual(zone.timeframe_minutes, 60)
        self.assertEqual(zone.source_structure_id, primary.pivot_id)

    def test_target_spent_lookup_uses_owning_timeframe_book(self) -> None:
        ladder = self.make_ladder()
        decision = pivot(
            "15M_HIGH",
            "HIGH",
            108.0,
            span=2,
            event_index=2,
            observed_index=4,
            consumed_time_ns=12 * NS,
        )
        ladder.decision.pivots.append(decision)
        zone = ladder.decision._horizontal_snapshot(decision, 10 * NS)
        self.assertTrue(ladder.target_spent_after(zone, 10 * NS))
        self.assertFalse(ladder.target_spent_after(zone, 13 * NS))

    def test_engine_target_selection_uses_trigger_book_before_context_book(self) -> None:
        engine = StructureScenarioEngine(
            "TEST",
            0.1,
            scale_name="MACRO",
            higher_minutes=60,
            decision_minutes=15,
            trigger_minutes=5,
            minimum_gross_rr=1.0,
        )
        context_low = pivot(
            "60M_LOW",
            "LOW",
            100.0,
            span=6,
            event_index=1,
            observed_index=7,
        )
        far_high = pivot(
            "60M_HIGH",
            "HIGH",
            120.0,
            span=6,
            event_index=0,
            observed_index=6,
        )
        near_high = pivot(
            "5M_HIGH",
            "HIGH",
            104.0,
            span=2,
            event_index=2,
            observed_index=5,
        )
        engine.structure.pivots.extend([far_high, context_low])
        engine.structure._active_pivots.update(
            {far_high.pivot_id: far_high, context_low.pivot_id: context_low},
        )
        engine.objectives.trigger.pivots.append(near_high)
        context = engine.structure._horizontal_snapshot(context_low, 10 * NS)
        target = engine._select_target(
            context,
            Side.LONG,
            ScenarioPath.REJECTION,
            type(
                "Bar",
                (),
                {
                    "ts_close_ns": 10 * NS,
                    "high": 101.0,
                    "low": 99.0,
                },
            )(),
        )
        self.assertIsNotNone(target)
        assert target is not None
        zone, price, channel_id, midline = target
        self.assertEqual(price, 104.0)
        self.assertEqual(zone.timeframe_minutes, 5)
        self.assertIsNone(channel_id)
        self.assertIsNone(midline)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from contracts_v5 import Pivot, SetupState
from domain import Candle
from scenario_engine_v5 import StructureScenarioEngine

NS = 60_000_000_000


def candle(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(index * NS, open_, high, low, close, 1.0)


def pivot(pivot_id: str, side: str, price: float, event: int, observed: int) -> Pivot:
    return Pivot(
        pivot_id=pivot_id,
        side=side,
        price=price,
        index=event,
        event_time_ns=event * NS,
        observed_index=observed,
        observed_time_ns=observed * NS,
        span=6,
        strength_ratio=2.0,
    )


def engine() -> StructureScenarioEngine:
    return StructureScenarioEngine(
        "TEST",
        0.1,
        scale_name="MICRO",
        higher_minutes=15,
        decision_minutes=5,
        trigger_minutes=1,
        minimum_gross_rr=1.0,
    )


def register(item: StructureScenarioEngine, *pivots: Pivot) -> None:
    item.structure.pivots.extend(pivots)
    item.structure._pivot_ids.update(pivot.pivot_id for pivot in pivots)
    item.structure._active_pivots.update({pivot.pivot_id: pivot for pivot in pivots})


class DirectionalRejectionConfirmationTests(unittest.TestCase):
    def test_long_first_retest_on_valid_side_but_red_body_is_consumed(self) -> None:
        item = engine()
        register(
            item,
            pivot("TARGET_HIGH", "HIGH", 120.0, 0, 2),
            pivot("SOURCE_LOW", "LOW", 100.0, 1, 3),
        )
        item.on_bar(5, candle(10, 105.0, 106.0, 104.0, 105.0))
        item.on_bar(5, candle(11, 101.0, 102.0, 99.5, 101.1))
        setup = list(item._active.values())[0]
        self.assertIs(setup.state, SetupState.WAITING_REJECTION_RETEST)

        plans = item.on_bar(1, candle(12, 101.1, 101.2, 99.9, 100.8))
        self.assertFalse(plans)
        self.assertTrue(setup.first_retest_consumed)
        self.assertIs(setup.state, SetupState.UNRESOLVED)
        self.assertEqual(
            setup.terminal_reason,
            "rejection_first_structure_retest_lacked_directional_close",
        )
        self.assertFalse(item.on_bar(1, candle(13, 100.5, 101.2, 99.9, 101.0)))

    def test_short_first_retest_on_valid_side_but_green_body_is_consumed(self) -> None:
        item = engine()
        register(
            item,
            pivot("TARGET_LOW", "LOW", 80.0, 0, 2),
            pivot("SOURCE_HIGH", "HIGH", 100.0, 1, 3),
        )
        item.on_bar(5, candle(10, 95.0, 96.0, 94.0, 95.0))
        item.on_bar(5, candle(11, 99.9, 100.6, 98.8, 99.8))
        setup = list(item._active.values())[0]
        self.assertIs(setup.state, SetupState.WAITING_REJECTION_RETEST)

        plans = item.on_bar(1, candle(12, 98.9, 100.1, 98.8, 99.0))
        self.assertFalse(plans)
        self.assertTrue(setup.first_retest_consumed)
        self.assertIs(setup.state, SetupState.UNRESOLVED)
        self.assertEqual(
            setup.terminal_reason,
            "rejection_first_structure_retest_lacked_directional_close",
        )

    def test_directional_first_retest_still_creates_one_immutable_plan(self) -> None:
        item = engine()
        register(
            item,
            pivot("TARGET_HIGH", "HIGH", 120.0, 0, 2),
            pivot("SOURCE_LOW", "LOW", 100.0, 1, 3),
        )
        item.on_bar(5, candle(10, 105.0, 106.0, 104.0, 105.0))
        item.on_bar(5, candle(11, 101.0, 102.0, 99.5, 101.1))
        plans = item.on_bar(1, candle(12, 100.5, 101.2, 99.9, 100.8))
        self.assertEqual(len(plans), 1)
        self.assertEqual(len(item.plans), 1)
        self.assertGreaterEqual(plans[0].gross_rr, 1.0)


if __name__ == "__main__":
    unittest.main()

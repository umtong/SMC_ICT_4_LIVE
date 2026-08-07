from __future__ import annotations

import unittest

from c10_v25_model import LiquidityShelf
from test_v25_state import LiquidityResponseStateTests, bar


class V25EventIdentityTests(unittest.TestCase):
    def test_nontradable_true_cross_consumes_source_shelf(self) -> None:
        helper = LiquidityResponseStateTests()
        machine = helper.machine(quote=False)
        helper.add_shelves(machine)
        helper.seed_approach(machine)
        machine.sequence = 9

        events, plan = machine.on_bar(
            bar(
                10,
                mid=100.1,
                high=100.3,
                low=99.9,
                total_quote=1.0,
                buy_quote=0.5,
                total_base=0.01,
                buy_base=0.005,
            ),
        )

        self.assertIsNone(plan)
        supply = next(
            shelf for shelf in machine.shelves if shelf.shelf_id == "SUPPLY"
        )
        demand = next(
            shelf for shelf in machine.shelves if shelf.shelf_id == "DEMAND"
        )
        self.assertFalse(supply.active)
        self.assertTrue(demand.active)
        self.assertTrue(
            any(
                event.reason_code
                == "TRUE_CROSS_WITHOUT_ALIGNED_AGGRESSIVE_FLOW"
                for event in events
            ),
        )
        self.assertEqual(
            machine.counters["NONTRADABLE_TRUE_CROSS_CONSUMED"],
            1,
        )

    def test_two_sided_true_cross_consumes_both_sides(self) -> None:
        helper = LiquidityResponseStateTests()
        machine = helper.machine(quote=False)
        machine.shelves.extend(
            [
                LiquidityShelf(
                    shelf_id="SUPPLY",
                    side=1,
                    price=100.0,
                    zone=0.1,
                    created_ns=1,
                    formation_start_ns=1,
                    formation_end_ns=1,
                    flow_dominance=0.8,
                    impact_efficiency=0.0,
                ),
                LiquidityShelf(
                    shelf_id="DEMAND",
                    side=-1,
                    price=99.8,
                    zone=0.1,
                    created_ns=1,
                    formation_start_ns=1,
                    formation_end_ns=1,
                    flow_dominance=0.8,
                    impact_efficiency=0.0,
                ),
            ],
        )
        machine.recent_bars.append(bar(9, mid=99.9, high=100.0, low=99.8))
        machine.sequence = 9

        events, plan = machine.on_bar(
            bar(
                10,
                mid=99.9,
                high=100.2,
                low=99.6,
                total_quote=100.0,
                buy_quote=100.0,
                total_base=1.0,
                buy_base=1.0,
            ),
        )

        self.assertIsNone(plan)
        self.assertTrue(all(not shelf.active for shelf in machine.shelves))
        self.assertTrue(
            any(
                event.reason_code == "BOTH_SIDES_TRUE_CROSS_PATH_ORDER_UNKNOWN"
                for event in events
            ),
        )


if __name__ == "__main__":
    unittest.main()

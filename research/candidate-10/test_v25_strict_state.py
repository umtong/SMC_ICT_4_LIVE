from __future__ import annotations

import unittest

from c10_v25_model import LiquidityShelf
from c10_v25_strict_state import StrictLiquidityResponseStateMachine
from test_v25_state import LiquidityResponseStateTests, bar


class V25StrictLifecycleTests(unittest.TestCase):
    def machine(self, *, quote: bool) -> StrictLiquidityResponseStateMachine:
        helper = LiquidityResponseStateTests()
        machine = StrictLiquidityResponseStateMachine(
            helper.params(quote=quote),
            tick_size=0.1,
            instrument_id="BTCUSDT-PERP.BINANCE",
        )
        for value in (10.0, 10.0, 10.0, 10.0):
            machine.abs_trade_quote_history.append(value)
            machine.abs_ofi_history.append(1.0)
            machine.range_history.append(0.1)
            machine.spread_history.append(0.1)
            machine.depth_history.append(2.0)
            machine.notional_history.append(10_000.0)
        machine.formation_abs_flow.append(10.0)
        machine.formation_efficiency.append(0.001)
        machine.formation_dominance.append(0.5)
        return machine

    @staticmethod
    def add_base_shelves(machine: StrictLiquidityResponseStateMachine) -> None:
        helper = LiquidityResponseStateTests()
        helper.add_shelves(machine)
        helper.seed_approach(machine)
        machine.sequence = 9

    @staticmethod
    def start_probe(machine: StrictLiquidityResponseStateMachine) -> None:
        events, plan = machine.on_bar(
            bar(
                10,
                mid=100.1,
                high=100.3,
                low=99.9,
                total_quote=100.0,
                buy_quote=100.0,
                total_base=1.0,
                buy_base=1.0,
                ofi=10.0,
                ask_remove=5.0,
            ),
        )
        assert plan is None
        assert any(event.event_type == "LIQUIDITY_SHELF_SWEPT" for event in events)

    def test_acceptance_is_quote_independent_in_full_and_ablation(self) -> None:
        machines = [self.machine(quote=True), self.machine(quote=False)]
        for machine in machines:
            self.add_base_shelves(machine)
            self.start_probe(machine)
            machine.on_bar(
                bar(
                    11,
                    mid=99.95,
                    high=100.05,
                    low=99.9,
                    total_quote=20.0,
                    buy_quote=10.0,
                    ofi=-10.0,
                ),
            )
            machine.on_bar(
                bar(
                    12,
                    mid=100.0,
                    high=100.05,
                    low=99.95,
                    total_quote=20.0,
                    buy_quote=10.0,
                    ofi=-10.0,
                ),
            )
            events, plan = machine.on_bar(
                bar(
                    13,
                    mid=100.2,
                    high=100.25,
                    low=100.15,
                    total_quote=100.0,
                    buy_quote=100.0,
                    ofi=-10.0,
                ),
            )
            self.assertIsNone(plan)
            self.assertIsNone(machine.active_probe)
            self.assertTrue(
                any(
                    event.reason_code
                    == "SHELF_ACCEPTED_WITH_PERSISTENT_SAME_SIDE_FLOW"
                    for event in events
                ),
            )

    def test_other_shelf_crossed_during_probe_is_consumed(self) -> None:
        machine = self.machine(quote=True)
        self.add_base_shelves(machine)
        machine.shelves.append(
            LiquidityShelf(
                shelf_id="SECOND_SUPPLY",
                side=1,
                price=101.0,
                zone=0.1,
                created_ns=1,
                formation_start_ns=1,
                formation_end_ns=1,
                flow_dominance=0.8,
                impact_efficiency=0.0,
            ),
        )
        self.start_probe(machine)
        events, plan = machine.on_bar(
            bar(
                11,
                mid=101.1,
                high=101.2,
                low=100.9,
                total_quote=100.0,
                buy_quote=100.0,
                ofi=10.0,
            ),
        )
        self.assertIsNone(plan)
        second = next(
            shelf for shelf in machine.shelves
            if shelf.shelf_id == "SECOND_SUPPLY"
        )
        self.assertFalse(second.active)
        self.assertTrue(
            any(
                event.reason_code
                == "TRUE_CROSS_CONSUMED_WHILE_SOURCE_EVENT_ACTIVE"
                for event in events
            ),
        )

    def test_shelf_finalized_inside_parent_bar_is_also_consumed(self) -> None:
        class InjectingMachine(StrictLiquidityResponseStateMachine):
            roll_calls = 0

            def _maybe_roll_formation(self, current_bar):  # type: ignore[override]
                self.roll_calls += 1
                if self.roll_calls == 2:
                    self.shelves.append(
                        LiquidityShelf(
                            shelf_id="ROLLED_SUPPLY",
                            side=1,
                            price=101.0,
                            zone=0.1,
                            created_ns=current_bar.ts_ns - 1,
                            formation_start_ns=1,
                            formation_end_ns=current_bar.ts_ns - 1,
                            flow_dominance=0.8,
                            impact_efficiency=0.0,
                        ),
                    )

        helper = LiquidityResponseStateTests()
        machine = InjectingMachine(
            helper.params(quote=True),
            tick_size=0.1,
            instrument_id="BTCUSDT-PERP.BINANCE",
        )
        for value in (10.0, 10.0, 10.0, 10.0):
            machine.abs_trade_quote_history.append(value)
            machine.abs_ofi_history.append(1.0)
            machine.range_history.append(0.1)
            machine.spread_history.append(0.1)
            machine.depth_history.append(2.0)
            machine.notional_history.append(10_000.0)
        machine.formation_abs_flow.append(10.0)
        machine.formation_efficiency.append(0.001)
        machine.formation_dominance.append(0.5)
        helper.add_shelves(machine)
        helper.seed_approach(machine)
        machine.sequence = 9
        self.start_probe(machine)

        events, plan = machine.on_bar(
            bar(
                11,
                mid=101.1,
                high=101.2,
                low=100.9,
                total_quote=100.0,
                buy_quote=100.0,
                ofi=10.0,
            ),
        )
        self.assertIsNone(plan)
        rolled = next(
            shelf for shelf in machine.shelves
            if shelf.shelf_id == "ROLLED_SUPPLY"
        )
        self.assertFalse(rolled.active)
        self.assertTrue(
            any(
                bool(
                    event.details.get(
                        "newly_finalized_before_current_bar_decision",
                    ),
                )
                for event in events
            ),
        )

    def test_target_touched_before_confirmation_is_consumed(self) -> None:
        machine = self.machine(quote=True)
        self.add_base_shelves(machine)
        self.start_probe(machine)
        events, plan = machine.on_bar(
            bar(
                11,
                mid=97.0,
                high=99.0,
                low=96.9,
                total_quote=100.0,
                buy_quote=0.0,
                ofi=-10.0,
                ask_add=10.0,
            ),
        )
        self.assertIsNone(plan)
        target = next(
            shelf for shelf in machine.shelves if shelf.shelf_id == "DEMAND"
        )
        self.assertFalse(target.active)
        self.assertTrue(
            any(
                event.reason_code
                == "PREEXISTING_TARGET_REACHED_BEFORE_CONFIRMATION"
                for event in events
            ),
        )
        self.assertEqual(
            machine.counters["TARGET_SHELF_CONSUMED_BEFORE_ENTRY"],
            1,
        )


if __name__ == "__main__":
    unittest.main()

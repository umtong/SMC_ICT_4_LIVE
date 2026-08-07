from __future__ import annotations

from dataclasses import replace
import unittest

from c10_v25_model import (
    LiquidityResponseBar,
    LiquidityResponseParams,
    LiquidityShelf,
)
from c10_v25_state import LiquidityResponseStateMachine


def bar(
    ts: int,
    *,
    mid: float,
    high: float | None = None,
    low: float | None = None,
    total_quote: float = 10.0,
    buy_quote: float = 5.0,
    total_base: float = 1.0,
    buy_base: float = 0.5,
    ofi: float = 0.0,
    bid_add: float = 0.0,
    bid_remove: float = 0.0,
    ask_add: float = 0.0,
    ask_remove: float = 0.0,
) -> LiquidityResponseBar:
    high = mid if high is None else high
    low = mid if low is None else low
    return LiquidityResponseBar(
        ts_ns=ts * 1_000_000_000,
        mid_open=mid,
        mid_high=high,
        mid_low=low,
        mid_close=mid,
        bid_price=mid - 0.05,
        ask_price=mid + 0.05,
        bid_size=1.0,
        ask_size=1.0,
        mean_spread=0.1,
        max_spread=0.1,
        quote_updates=10,
        ofi_qty=ofi,
        bid_add_qty=bid_add,
        bid_remove_qty=bid_remove,
        ask_add_qty=ask_add,
        ask_remove_qty=ask_remove,
        trade_quote_volume=total_quote,
        taker_buy_quote=buy_quote,
        trade_base_volume=total_base,
        taker_buy_base=buy_base,
        trade_count=1 if total_quote > 0 else 0,
    )


class LiquidityResponseStateTests(unittest.TestCase):
    def params(self, *, quote: bool = True) -> LiquidityResponseParams:
        return LiquidityResponseParams(
            bar_seconds=1,
            formation_seconds=2,
            feature_lookback_windows=20,
            minimum_feature_windows=1,
            formation_flow_quantile=0.5,
            formation_efficiency_quantile=0.5,
            formation_dominance_quantile=0.5,
            interaction_flow_quantile=0.5,
            confirmation_flow_quantile=0.5,
            quote_ofi_quantile=0.5,
            replenishment_ratio=1.0,
            probe_max_bars=5,
            approach_bars=2,
            min_net_rr=1.0,
            max_shelves=20,
            use_quote_response=quote,
        )

    def machine(self, *, quote: bool = True) -> LiquidityResponseStateMachine:
        machine = LiquidityResponseStateMachine(
            self.params(quote=quote),
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
    def add_shelves(
        machine: LiquidityResponseStateMachine,
        *,
        target_created: int = 1,
    ) -> None:
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
                    price=97.0,
                    zone=0.1,
                    created_ns=target_created,
                    formation_start_ns=1,
                    formation_end_ns=1,
                    flow_dominance=0.8,
                    impact_efficiency=0.0,
                ),
            ],
        )

    def seed_approach(self, machine: LiquidityResponseStateMachine) -> None:
        machine.recent_bars.append(bar(8, mid=99.9, low=99.8, high=100.0))
        machine.recent_bars.append(bar(9, mid=99.9, low=99.8, high=100.0))

    def test_full_quote_response_creates_reversal_plan(self) -> None:
        machine = self.machine(quote=True)
        self.add_shelves(machine)
        self.seed_approach(machine)
        machine.sequence = 9
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
        self.assertIsNone(plan)
        self.assertTrue(
            any(event.event_type == "LIQUIDITY_SHELF_SWEPT" for event in events),
        )
        events, plan = machine.on_bar(
            bar(
                11,
                mid=99.7,
                high=100.0,
                low=99.6,
                total_quote=100.0,
                buy_quote=0.0,
                total_base=1.0,
                buy_base=0.0,
                ofi=-10.0,
                ask_add=10.0,
            ),
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.direction, -1)
        self.assertEqual(plan.target_pool_id, "DEMAND")
        self.assertTrue(
            any(
                event.event_type == "LIQUIDITY_RESPONSE_REVERSAL_CONFIRMED"
                for event in events
            ),
        )

    def test_full_rejects_price_only_reclaim_but_ablation_accepts(self) -> None:
        full = self.machine(quote=True)
        ablation = self.machine(quote=False)
        for machine in (full, ablation):
            self.add_shelves(machine)
            self.seed_approach(machine)
            machine.sequence = 9
            machine.on_bar(
                bar(
                    10,
                    mid=100.1,
                    high=100.3,
                    low=99.9,
                    total_quote=100.0,
                    buy_quote=100.0,
                    total_base=1.0,
                    buy_base=1.0,
                    ask_remove=5.0,
                ),
            )
        confirmation = bar(
            11,
            mid=99.7,
            high=100.0,
            low=99.6,
            total_quote=100.0,
            buy_quote=0.0,
            total_base=1.0,
            buy_base=0.0,
            ofi=0.0,
            ask_add=0.0,
        )
        _, full_plan = full.on_bar(confirmation)
        _, ablation_plan = ablation.on_bar(confirmation)
        self.assertIsNone(full_plan)
        self.assertIsNotNone(ablation_plan)

    def test_target_must_preexist_interaction(self) -> None:
        machine = self.machine(quote=False)
        interaction_ns = 10 * 1_000_000_000
        self.add_shelves(machine, target_created=interaction_ns)
        self.seed_approach(machine)
        machine.sequence = 9
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
            ),
        )
        self.assertIsNone(plan)
        self.assertTrue(
            any(
                event.reason_code == "NO_PREEXISTING_OPPOSING_LIQUIDITY_SHELF"
                for event in events
            ),
        )

    def test_shelf_formation_uses_only_completed_prior_window(self) -> None:
        params = replace(
            self.params(),
            formation_seconds=2,
            minimum_feature_windows=1,
        )
        machine = LiquidityResponseStateMachine(
            params,
            tick_size=0.1,
            instrument_id="BTCUSDT-PERP.BINANCE",
        )
        machine.formation_abs_flow.append(1.0)
        machine.formation_efficiency.append(1.0)
        machine.formation_dominance.append(0.1)
        for value in (1.0, 1.0):
            machine.abs_trade_quote_history.append(value)
            machine.abs_ofi_history.append(1.0)
            machine.range_history.append(0.1)
            machine.spread_history.append(0.1)
            machine.depth_history.append(2.0)
            machine.notional_history.append(10.0)
        machine.on_bar(bar(1, mid=100.0, total_quote=10.0, buy_quote=10.0))
        machine.on_bar(bar(2, mid=100.0, total_quote=10.0, buy_quote=10.0))
        self.assertEqual(len(machine.shelves), 0)
        machine.on_bar(bar(3, mid=99.9, total_quote=1.0, buy_quote=0.5))
        self.assertEqual(len(machine.shelves), 1)
        self.assertLess(machine.shelves[0].created_ns, 3 * 1_000_000_000)


if __name__ == "__main__":
    unittest.main()

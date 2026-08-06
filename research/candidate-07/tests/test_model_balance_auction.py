from __future__ import annotations

import unittest

from model import Direction, ScenarioKind
from model_balance_auction import (
    BalanceInitiativeRouter,
    BalanceLogicConfig,
    InitiativeBranch,
)
from model_positioning import PositioningSignalBar


FIVE_MINUTES_NS = 300_000_000_000


def make_bar(
    index: int,
    *,
    open_: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.0,
    buy: float = 50.0,
    oi: float = 1000.0,
) -> PositioningSignalBar:
    return PositioningSignalBar(
        ts_event_ns=(index + 1) * FIVE_MINUTES_NS,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        taker_buy_volume=buy,
        open_interest=oi,
        open_interest_value=oi * close,
        global_long_short_ratio=1.0,
    )


def config(*, use_open_interest: bool = True) -> BalanceLogicConfig:
    return BalanceLogicConfig(
        atr_period=6,
        flow_period=6,
        oi_period=6,
        balance_bars=6,
        target_lookback=20,
        target_pivot_radius=1,
        min_history=20,
        rearm_bars=6,
        use_open_interest=use_open_interest,
    )


def warmed(
    *,
    use_open_interest: bool = True,
) -> tuple[BalanceInitiativeRouter, int, float]:
    router = BalanceInitiativeRouter(config(use_open_interest=use_open_interest))
    oi = 1000.0
    for index in range(20):
        oi += 0.5
        high = 104.0 if index == 8 else 101.0
        router.observe(
            make_bar(
                index,
                high=high,
                close=100.1 if index % 2 == 0 else 99.9,
                buy=49.0 if index % 2 == 0 else 51.0,
                oi=oi,
            ),
            index,
            eligible=False,
        )
    return router, 20, oi


class BalanceInitiativeTests(unittest.TestCase):
    def test_new_inventory_break_and_hold_routes_continuation(self) -> None:
        router, index, oi = warmed()
        contact = router.observe(
            make_bar(
                index,
                open_=100.0,
                high=102.0,
                low=100.0,
                close=101.8,
                buy=80.0,
                oi=oi + 20.0,
            ),
            index,
        )
        self.assertTrue(
            any(
                item.reason_code == "NEW_INVENTORY_INITIATIVE_BREAK"
                for item in contact.transitions
            )
        )
        confirmed = router.observe(
            make_bar(
                index + 1,
                open_=101.7,
                high=102.4,
                low=101.5,
                close=102.2,
                buy=70.0,
                oi=oi + 20.5,
            ),
            index + 1,
        )
        self.assertIsNotNone(confirmed.plan)
        assert confirmed.plan is not None
        self.assertEqual(confirmed.plan.direction, Direction.LONG)
        self.assertEqual(
            confirmed.plan.kind,
            ScenarioKind.ACCEPTANCE_CONTINUATION,
        )
        self.assertEqual(
            confirmed.plan.details["branch"],
            InitiativeBranch.ACCEPTED_INITIATIVE.value,
        )
        self.assertGreaterEqual(
            confirmed.plan.expected_rr,
            router.config.minimum_rr,
        )

    def test_failed_new_inventory_break_routes_unwind(self) -> None:
        router, index, oi = warmed()
        router.observe(
            make_bar(
                index,
                open_=100.0,
                high=102.0,
                low=100.0,
                close=101.8,
                buy=80.0,
                oi=oi + 20.0,
            ),
            index,
        )
        failed = router.observe(
            make_bar(
                index + 1,
                open_=101.8,
                high=102.0,
                low=100.5,
                close=100.8,
                buy=25.0,
                oi=oi - 5.0,
            ),
            index + 1,
        )
        self.assertIsNotNone(failed.plan)
        assert failed.plan is not None
        self.assertEqual(failed.plan.direction, Direction.SHORT)
        self.assertEqual(
            failed.plan.kind,
            ScenarioKind.ABSORPTION_RECLAIM,
        )
        self.assertEqual(
            failed.plan.details["branch"],
            InitiativeBranch.FAILED_INITIATIVE.value,
        )

    def test_break_without_new_inventory_is_not_traded(self) -> None:
        router, index, oi = warmed()
        observed = router.observe(
            make_bar(
                index,
                open_=100.0,
                high=102.0,
                low=100.0,
                close=101.8,
                buy=80.0,
                oi=oi - 20.0,
            ),
            index,
        )
        self.assertIsNone(observed.plan)
        self.assertTrue(
            any(
                item.reason_code == "BREAK_WITHOUT_NEW_INVENTORY"
                for item in observed.transitions
            )
        )
        self.assertIsNone(router.active_scenario_id)

    def test_oi_ablation_allows_same_break_without_oi_build(self) -> None:
        router, index, oi = warmed(use_open_interest=False)
        observed = router.observe(
            make_bar(
                index,
                open_=100.0,
                high=102.0,
                low=100.0,
                close=101.8,
                buy=80.0,
                oi=oi - 20.0,
            ),
            index,
        )
        self.assertTrue(
            any(
                item.reason_code == "NEW_INVENTORY_INITIATIVE_BREAK"
                for item in observed.transitions
            )
        )

    def test_data_gap_terminates_active_balance(self) -> None:
        router, index, oi = warmed()
        router.observe(
            make_bar(
                index,
                open_=100.0,
                high=102.0,
                low=100.0,
                close=101.8,
                buy=80.0,
                oi=oi + 20.0,
            ),
            index,
        )
        transitions = router.invalidate_data_gap(
            index=index + 1,
            event_time_ns=(index + 2) * FIVE_MINUTES_NS,
            reference_price=101.7,
            reason_code="POSITIONING_DATA_GAP",
        )
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0].reason_code, "POSITIONING_DATA_GAP")
        self.assertFalse(transitions[0].details["synthetic_positioning_used"])
        self.assertIsNone(router.active_scenario_id)


if __name__ == "__main__":
    unittest.main()

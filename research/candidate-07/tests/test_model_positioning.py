from __future__ import annotations

import unittest

from model import Direction, ScenarioKind
from model_positioning import (
    AuctionBranch,
    PositioningAuctionRouter,
    PositioningLogicConfig,
    PositioningSignalBar,
)


FIVE_MINUTES_NS = 300_000_000_000


def make_bar(
    index: int,
    *,
    open_: float = 95.0,
    high: float = 95.5,
    low: float = 94.5,
    close: float = 95.0,
    volume: float = 100.0,
    taker_buy: float = 50.0,
    oi: float = 1000.0,
) -> PositioningSignalBar:
    return PositioningSignalBar(
        ts_event_ns=(index + 1) * FIVE_MINUTES_NS,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        taker_buy_volume=taker_buy,
        open_interest=oi,
        open_interest_value=oi * close,
        top_trader_account_ratio=1.2,
        top_trader_position_ratio=1.1,
        global_long_short_ratio=1.0,
        taker_long_short_ratio=1.0,
    )


def warm_router(config: PositioningLogicConfig) -> tuple[PositioningAuctionRouter, int, float]:
    router = PositioningAuctionRouter(config)
    oi = 1000.0
    for index in range(300):
        oi += 0.2 if index % 2 == 0 else -0.1
        high = 95.5
        low = 94.5
        close = 95.0 + (0.05 if index % 3 == 0 else -0.05)
        open_ = 95.0 - (0.05 if index % 3 == 0 else -0.05)
        if index == 100:
            high = 104.0
        if index == 101:
            high = 96.0
        if index == 270:
            high = 100.0
        if index == 280:
            low = 90.0
        router.observe(
            make_bar(
                index,
                open_=open_,
                high=high,
                low=low,
                close=close,
                taker_buy=49.0 if index % 2 == 0 else 51.0,
                oi=oi,
            ),
            index,
            eligible=False,
        )
    return router, 300, oi


class PositioningRouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = PositioningLogicConfig()

    def test_upper_liquidation_release_routes_short_reversal(self) -> None:
        router, index, oi = warm_router(self.config)
        observed = router.observe(
            make_bar(
                index,
                open_=99.8,
                high=100.55,
                low=99.2,
                close=99.45,
                volume=220.0,
                taker_buy=170.0,
                oi=oi - 20.0,
            ),
            index,
        )
        self.assertTrue(observed.transitions)
        self.assertEqual(
            observed.transitions[0].details["branch"],
            AuctionBranch.LIQUIDATION_REVERSAL.value,
        )
        routed = router.observe(
            make_bar(
                index + 1,
                open_=99.4,
                high=99.5,
                low=98.4,
                close=98.6,
                volume=220.0,
                taker_buy=55.0,
                oi=oi - 20.2,
            ),
            index + 1,
        )
        self.assertIsNotNone(routed.plan)
        assert routed.plan is not None
        self.assertEqual(routed.plan.direction, Direction.SHORT)
        self.assertEqual(routed.plan.kind, ScenarioKind.ABSORPTION_RECLAIM)
        self.assertGreaterEqual(routed.plan.expected_rr, self.config.minimum_rr)

    def test_upper_inventory_build_routes_long_acceptance(self) -> None:
        router, index, oi = warm_router(self.config)
        observed = router.observe(
            make_bar(
                index,
                open_=99.6,
                high=100.8,
                low=99.5,
                close=100.65,
                volume=230.0,
                taker_buy=180.0,
                oi=oi + 20.0,
            ),
            index,
        )
        self.assertTrue(observed.transitions)
        self.assertEqual(
            observed.transitions[0].details["branch"],
            AuctionBranch.INVENTORY_ACCEPTANCE.value,
        )
        routed = router.observe(
            make_bar(
                index + 1,
                open_=100.6,
                high=101.25,
                low=100.55,
                close=101.15,
                volume=200.0,
                taker_buy=145.0,
                oi=oi + 20.4,
            ),
            index + 1,
        )
        self.assertIsNotNone(routed.plan)
        assert routed.plan is not None
        self.assertEqual(routed.plan.direction, Direction.LONG)
        self.assertEqual(routed.plan.kind, ScenarioKind.ACCEPTANCE_CONTINUATION)
        self.assertLess(routed.plan.stop_price, routed.plan.liquidity_level)
        self.assertGreater(routed.plan.target_price, routed.plan.entry_reference)

    def test_neutral_open_interest_does_not_force_a_branch(self) -> None:
        router, index, oi = warm_router(self.config)
        observed = router.observe(
            make_bar(
                index,
                open_=99.8,
                high=100.55,
                low=99.2,
                close=99.45,
                volume=220.0,
                taker_buy=170.0,
                oi=oi + 0.01,
            ),
            index,
        )
        self.assertFalse(observed.transitions)
        self.assertIsNone(observed.plan)

    def test_ablation_routes_same_rejection_without_open_interest(self) -> None:
        config = PositioningLogicConfig(use_open_interest=False)
        router, index, oi = warm_router(config)
        observed = router.observe(
            make_bar(
                index,
                open_=99.8,
                high=100.55,
                low=99.2,
                close=99.45,
                volume=220.0,
                taker_buy=170.0,
                oi=oi + 0.01,
            ),
            index,
        )
        self.assertTrue(observed.transitions)
        self.assertEqual(
            observed.transitions[0].details["branch"],
            AuctionBranch.REJECTION_ABLATION.value,
        )

    def test_pool_is_consumed_on_first_contact(self) -> None:
        router, index, oi = warm_router(self.config)
        router.observe(
            make_bar(
                index,
                open_=99.8,
                high=100.55,
                low=99.2,
                close=99.45,
                volume=220.0,
                taker_buy=170.0,
                oi=oi - 20.0,
            ),
            index,
        )
        router.observe(
            make_bar(
                index + 1,
                open_=100.4,
                high=101.0,
                low=100.2,
                close=100.8,
                volume=160.0,
                taker_buy=110.0,
                oi=oi - 19.9,
            ),
            index + 1,
        )
        before = router.consumed_pool_count
        again = router.observe(
            make_bar(
                index + 2,
                open_=100.0,
                high=100.7,
                low=99.1,
                close=99.4,
                volume=220.0,
                taker_buy=170.0,
                oi=oi - 40.0,
            ),
            index + 2,
        )
        self.assertEqual(router.consumed_pool_count, before)
        self.assertFalse(again.transitions)


if __name__ == "__main__":
    unittest.main()

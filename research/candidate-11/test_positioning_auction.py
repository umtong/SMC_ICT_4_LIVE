from __future__ import annotations

import unittest

from logic import BarObs, Direction, Side
from positioning_auction import (
    LocalPool,
    PositioningAuction,
    PositioningAuctionConfig,
    PositioningObs,
    PositioningUnwindAuctionEngine,
)

MINUTE_NS = 60_000_000_000


def bar(
    minute: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    flow: float = 0.0,
) -> BarObs:
    volume = 100.0
    taker = volume * (flow + 1.0) / 2.0
    return BarObs(minute * MINUTE_NS, open_, high, low, close, volume, taker)


def pos(
    minute: int,
    oi: float,
    taker_ratio: float = 1.1,
    premium: float = 0.001,
) -> PositioningObs:
    return PositioningObs(
        ts_ns=minute * MINUTE_NS,
        open_interest=oi,
        open_interest_value=oi * 100.0,
        taker_ratio=taker_ratio,
        account_ratio=1.05,
        top_position_ratio=1.10,
        premium_close=premium,
    )


class PositioningAuctionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = PositioningAuctionConfig(
            atr_period=2,
            volume_period=2,
            structure_tf_bars=2,
            pivot_wing=1,
            min_relative_volume=0.5,
        )
        self.engine = PositioningUnwindAuctionEngine(
            self.config,
            "BTCUSDT-PERP.BINANCE",
        )

    def test_future_positioning_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            self.engine.on_bar(bar(1, 100, 101, 99, 100), pos(2, 100))

    def test_position_build_requires_oi_price_and_crowd_alignment(self) -> None:
        for item in (
            bar(1, 99, 100, 98, 99),
            bar(31, 109, 111, 108, 110, flow=0.2),
        ):
            self.engine.bars.append(item)
        self.engine.positioning.append(pos(1, 100))
        self.engine.positioning.append(pos(31, 110))
        passed, current, prior, details = self.engine._positioning_build(
            self.engine.bars[-1],
            Side.HIGH,
        )
        self.assertTrue(passed)
        self.assertIsNotNone(current)
        self.assertIsNotNone(prior)
        self.assertGreater(details["oi_build_log_change"], 0.0)
        self.assertGreater(details["price_build_directional_log_change"], 0.0)

        failed, *_ = self.engine._positioning_build(
            self.engine.bars[-1],
            Side.LOW,
        )
        self.assertFalse(failed)

    def _active_high_sweep(
        self,
        confirm_oi: float,
    ) -> tuple[PositioningAuction, BarObs]:
        prior_two = bar(38, 100.8, 101.0, 100.5, 100.7, flow=0.1)
        prior_one = bar(39, 100.7, 100.9, 100.4, 100.6, flow=0.0)
        confirmation = bar(40, 100.4, 100.45, 98.8, 99.0, flow=-0.5)
        self.engine.bars = [prior_two, prior_one, confirmation]
        self.engine._index = 2
        self.engine.positioning.append(pos(35, 110))
        self.engine.positioning.append(
            pos(40, confirm_oi, taker_ratio=0.8, premium=-0.001),
        )
        pool = LocalPool(
            scenario_id="BTC-POSITIONING-R1-HIGH",
            range_id="BTC-POSITIONING-R1",
            side=Side.HIGH,
            level=100.0,
            paired_level=95.0,
            candidate_ts_ns=30 * MINUTE_NS,
            confirmed_ts_ns=35 * MINUTE_NS,
            confirmed_index=0,
            expiry_index=100,
            consumed=True,
        )
        sweep = bar(35, 99.8, 101.0, 99.7, 100.5, flow=0.5)
        auction = PositioningAuction(
            pool=pool,
            sweep=sweep,
            sweep_index=0,
            initial_sweep_ts_ns=sweep.ts_ns,
            atr=1.0,
            internal_level=99.5,
            target_level=95.0,
            sweep_extreme=101.0,
            sweep_open_interest=110.0,
            prior_open_interest=100.0,
            positioning_ts_ns=35 * MINUTE_NS,
            oi_peak=110.0,
            reclaim_seen=True,
        )
        self.engine.active = auction
        return auction, confirmation

    def test_far_plan_requires_open_interest_unwind(self) -> None:
        auction, confirmation = self._active_high_sweep(confirm_oi=105.0)
        plan = self.engine._confirm_far(auction, confirmation)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.direction, Direction.SHORT)
        self.assertEqual(plan.target_price, 95.0)
        self.assertGreaterEqual(plan.net_r, self.config.min_net_r)
        self.assertEqual(plan.details["draw_method"], "POSITION_BUILD_AND_UNWIND")

    def test_no_plan_when_open_interest_does_not_contract(self) -> None:
        auction, confirmation = self._active_high_sweep(confirm_oi=111.0)
        self.assertIsNone(self.engine._confirm_far(auction, confirmation))


if __name__ == "__main__":
    unittest.main()

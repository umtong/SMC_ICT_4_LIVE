import unittest

from domain_v3 import Candle, Side
from market_v7 import SessionLiquidityRange
from source_footprints import SourceOrderBlock

from market_v15 import (
    AuctionState,
    BoundaryEngineConfig,
    PeerBoundaryObservation,
    RoleRoutedBoundaryEngine,
    classify_auction_state,
)


def c(index, o, h, l, cl):
    return Candle(index * 10, index * 10 + 9, o, h, l, cl)


def r():
    return SessionLiquidityRange(
        range_id="R",
        reference_family="TEST",
        trade_window="TEST_WINDOW",
        observed_time_ns=0,
        trade_start_ns=0,
        trade_end_ns=1000,
        high=110.0,
        low=100.0,
    )


class BoundaryEngineTests(unittest.TestCase):
    def engine(self, **kwargs):
        return RoleRoutedBoundaryEngine(
            "BTCUSDT",
            [r()],
            BoundaryEngineConfig(tick_size=0.1, **kwargs),
        )

    def test_immediate_fakeout_arms_boundary_retest(self):
        engine = self.engine()
        update = engine.on_close(c(0, 101, 102, 98, 100.5), 0)
        self.assertEqual(len(update.setups), 1)
        setup = update.setups[0]
        self.assertEqual(setup.side, Side.LONG)
        self.assertAlmostEqual(setup.entry, 100.0)
        self.assertAlmostEqual(setup.stop, 97.9)
        self.assertAlmostEqual(setup.initial_target, 110.0)

    def test_plain_delayed_reclaim_without_wm_is_unresolved(self):
        engine = self.engine(enable_immediate_fakeout=False)
        self.assertFalse(engine.on_close(c(0, 101, 102, 98, 99), 0).setups)
        update = engine.on_close(c(1, 99, 101, 98.5, 100.2), 1)
        self.assertFalse(update.setups)
        self.assertEqual(engine.diagnostics.get("delayed_reclaim_not_wm_unresolved"), 1)

    def test_wm_requires_rebound_distinct_second_leg_then_reclaim(self):
        engine = self.engine(enable_immediate_fakeout=False)
        engine.on_close(c(0, 101, 102, 98, 99), 0)        # outside
        engine.on_close(c(1, 99, 99.8, 98.6, 99.6), 1)   # rebound, still outside
        engine.on_close(c(2, 99.6, 99.7, 97.5, 98.4), 2) # second leg
        update = engine.on_close(c(3, 98.4, 101, 98.2, 100.4), 3) # separate reclaim
        self.assertEqual(len(update.setups), 1)
        self.assertIn("WM_TRAP", update.setups[0].family)

    def test_predictive_outside_order_block_arms_before_reclaim(self):
        engine = self.engine(
            enable_immediate_fakeout=False,
            enable_wm_trap=False,
            enable_accepted_break_retest=False,
        )
        engine.on_close(c(0, 101, 102, 97, 99), 0)
        ob = SourceOrderBlock(
            footprint_id="OB1",
            symbol="BTCUSDT",
            side=Side.LONG,
            pattern="TWO_CANDLE_BODY_ENGULF",
            timeframe_minutes=15,
            observed_time_ns=19,
            formation_start_ns=0,
            formation_end_ns=19,
            zone_low=98.0,
            zone_high=99.0,
            invalidation=97.0,
            formation_low=97.0,
            formation_high=102.0,
            engulfed_body=0.5,
            engulfing_body=1.2,
            body_ratio=2.4,
            source_two_x_quality=True,
            exact_doji_exception=False,
            numeric_doji_boundary_status="SOURCE_UNDEFINED",
        )
        engine.ingest_footprints([ob])
        update = engine.on_close(c(1, 99, 99.5, 97.5, 99.2), 1)
        self.assertEqual(len(update.setups), 1)
        setup = update.setups[0]
        self.assertIn("PREDICTIVE_OUTSIDE_FOOTPRINT", setup.family)
        self.assertAlmostEqual(setup.entry, 99.0)

    def test_accepted_lower_break_uses_lower_boundary_for_short_retest(self):
        engine = self.engine(
            enable_immediate_fakeout=False,
            enable_wm_trap=False,
            enable_predictive_outside_footprint=False,
        )
        engine.on_close(c(0, 101, 102, 98, 99), 0)
        update = engine.on_close(c(1, 99, 99.5, 96, 97), 1)
        self.assertEqual(len(update.setups), 1)
        setup = update.setups[0]
        self.assertEqual(setup.side, Side.SHORT)
        self.assertAlmostEqual(setup.entry, 100.0)
        self.assertIn("ACCEPTED_BREAK", setup.family)


class AuctionStateTests(unittest.TestCase):
    def obs(self, symbol, low, close):
        return PeerBoundaryObservation(
            symbol=symbol,
            side=Side.LONG,
            range_low=100,
            range_high=110,
            excursion_low=low,
            excursion_high=105,
            close=close,
        )

    def test_routes_isolated_coordinated_rejection_and_repricing(self):
        candidate = self.obs("BTC", 95, 101)
        isolated = classify_auction_state(
            candidate=candidate,
            peers=[
                self.obs("ETH", 101, 103),
                self.obs("SOL", 100.5, 102),
                self.obs("XRP", 100.1, 101),
            ],
        )
        self.assertEqual(isolated.state, AuctionState.ISOLATED_RAID)

        rejection = classify_auction_state(
            candidate=candidate,
            peers=[
                self.obs("ETH", 98, 101),
                self.obs("SOL", 99, 100.5),
                self.obs("XRP", 101, 101),
            ],
        )
        self.assertEqual(rejection.state, AuctionState.COORDINATED_REJECTION)

        repricing = classify_auction_state(
            candidate=self.obs("BTC", 95, 97),
            peers=[
                self.obs("ETH", 98, 99),
                self.obs("SOL", 99, 98.5),
                self.obs("XRP", 101, 101),
            ],
        )
        self.assertEqual(repricing.state, AuctionState.COORDINATED_REPRICING)


if __name__ == "__main__":
    unittest.main()

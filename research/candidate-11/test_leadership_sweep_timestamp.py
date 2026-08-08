from __future__ import annotations

import unittest

from logic import (
    Auction,
    BarObs,
    CausalAuctionEngine,
    Direction,
    LogicConfig,
    Pool,
    Scenario,
    Side,
)


def bar(ts_ns: int, open_: float, high: float, low: float, close: float) -> BarObs:
    return BarObs(
        ts_ns=ts_ns,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        taker_buy_volume=60.0,
    )


class InitialSweepTimestampTests(unittest.TestCase):
    def test_plan_keeps_initial_sweep_after_final_raid_updates(self) -> None:
        engine = CausalAuctionEngine(LogicConfig(), "TEST")
        pool = Pool(
            scenario_id="TEST-R1-LOW",
            side=Side.LOW,
            level=95.0,
            source="TEST_SESSION",
            candidate_ts_ns=10,
            confirmed_ts_ns=20,
            confirmed_index=0,
            expiry_index=100,
        )
        auction = Auction(
            pool=pool,
            # The mutable sweep bar represents a later, deeper raid.
            sweep=bar(200, 94.0, 96.0, 92.0, 95.0),
            sweep_index=0,
            atr=10.0,
            internal_level=96.0,
            sweep_extreme=92.0,
            rejection_seed=True,
            acceptance_seed=False,
            state="FAR_CONFIRMED",
            scenario=Scenario.FAR,
            direction=Direction.LONG,
            stop_price=90.0,
            target_price=120.0,
            zone_low=99.0,
            zone_high=100.0,
            initial_sweep_ts_ns=100,
        )
        plan = engine._costed_limit_plan(
            auction,
            bar(300, 104.0, 106.0, 103.0, 105.0),
            "TEST_INITIAL_SWEEP",
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.details["sweep_ts_ns"], 100)
        self.assertNotEqual(plan.details["sweep_ts_ns"], auction.sweep.ts_ns)


if __name__ == "__main__":
    unittest.main()

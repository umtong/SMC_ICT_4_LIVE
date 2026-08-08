from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from logic import Auction, BarObs, LogicConfig, Pool, Side

from c10_v40_overlay import source_equilibrium_detector_enabled
from c10_v40_state import SourceEquilibriumFailedAuctionEngine


def obs(
    ts: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    buy_fraction: float,
) -> BarObs:
    volume = 100.0
    return BarObs(
        ts_ns=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        taker_buy_volume=volume * buy_fraction,
    )


class CapturingEngine(SourceEquilibriumFailedAuctionEngine):
    def __init__(self) -> None:
        super().__init__(LogicConfig(), "TEST-PERP.BINANCE")
        self.captured: dict[str, float | str] | None = None

    def _costed_limit_plan(self, auction, bar, reason):  # type: ignore[no-untyped-def]
        self.captured = {
            "target": float(auction.target_price),
            "stop": float(auction.stop_price),
            "reason": reason,
        }
        return None


class V40DetectorSeparationTest(unittest.TestCase):
    def test_environment_ablation_is_exact(self) -> None:
        with patch.dict(os.environ, {"C10_V40_SOURCE_EQUILIBRIUM_DETECTOR": "0"}):
            self.assertFalse(source_equilibrium_detector_enabled())
        with patch.dict(os.environ, {"C10_V40_SOURCE_EQUILIBRIUM_DETECTOR": "1"}):
            self.assertTrue(source_equilibrium_detector_enabled())

    def test_failed_auction_confirms_without_external_target_pool(self) -> None:
        engine = CapturingEngine()
        source = Pool(
            scenario_id="SOURCE-LOW",
            side=Side.LOW,
            level=100.0,
            source="NYAM_0700_1000_NY",
            candidate_ts_ns=0,
            confirmed_ts_ns=50,
            confirmed_index=0,
            expiry_index=1000,
            range_id="RANGE-1",
            opposite_level=110.0,
            triggerable=True,
            trigger_start_ts_ns=0,
            trigger_end_ts_ns=1000,
        )
        sweep = obs(100, 100.2, 100.3, 99.0, 99.4, 0.20)
        confirmation = obs(200, 100.4, 101.4, 100.2, 101.2, 0.80)
        auction = Auction(
            pool=source,
            sweep=sweep,
            sweep_index=0,
            atr=1.0,
            internal_level=100.5,
            sweep_extreme=99.0,
            rejection_seed=True,
            acceptance_seed=False,
            initial_sweep_ts_ns=100,
            framed_draw_method="SOURCE_EQUILIBRIUM_PRIMARY_DECOUPLED",
        )
        engine.pools = [source]
        engine.bars = [sweep, confirmation]
        engine._index = 1
        engine.active = auction

        with patch.dict(os.environ, {"C10_V40_SOURCE_EQUILIBRIUM_DETECTOR": "1"}):
            result = engine._confirm_far(auction, confirmation)

        self.assertIsNone(result)
        self.assertIsNotNone(engine.captured)
        assert engine.captured is not None
        self.assertEqual(engine.captured["target"], 105.0)
        self.assertEqual(
            engine.captured["reason"],
            "SOURCE_EQUILIBRIUM_FAILED_AUCTION_FIRST_DISPLACEMENT",
        )
        self.assertIsNone(auction.reversal_target_pool_id)
        self.assertIsNone(auction.reversal_target_level)
        self.assertEqual(
            engine.events[-1].event_type,
            "SOURCE_EQUILIBRIUM_FAILED_AUCTION_CONFIRMED",
        )
        self.assertFalse(
            engine.events[-1].details["external_draw_required"],
        )

    def test_decoupled_cascade_does_not_reframe_target(self) -> None:
        engine = CapturingEngine()
        source = Pool(
            scenario_id="SOURCE-HIGH",
            side=Side.HIGH,
            level=100.0,
            source="LONDON_0200_0500_NY",
            candidate_ts_ns=0,
            confirmed_ts_ns=50,
            confirmed_index=0,
            expiry_index=1000,
            range_id="RANGE-2",
            opposite_level=90.0,
            triggerable=True,
        )
        nested = Pool(
            scenario_id="NESTED-HIGH",
            side=Side.HIGH,
            level=101.0,
            source="COMPLETED_4H_AUCTION",
            candidate_ts_ns=0,
            confirmed_ts_ns=50,
            confirmed_index=0,
            expiry_index=1000,
            triggerable=False,
        )
        sweep = obs(100, 99.5, 100.5, 99.0, 100.2, 0.80)
        auction = Auction(
            pool=source,
            sweep=sweep,
            sweep_index=0,
            atr=1.0,
            internal_level=99.0,
            sweep_extreme=100.5,
            rejection_seed=True,
            acceptance_seed=False,
            framed_draw_method="SOURCE_EQUILIBRIUM_PRIMARY_DECOUPLED",
        )
        engine.pools = [source, nested]
        engine._index = 2
        extension = obs(200, 100.3, 101.5, 100.0, 101.2, 0.75)
        with patch.dict(os.environ, {"C10_V40_SOURCE_EQUILIBRIUM_DETECTOR": "1"}):
            engine._update_cascade_map(auction, extension)
        self.assertTrue(nested.consumed)
        self.assertIsNone(auction.framed_target_level)
        self.assertIsNone(auction.reversal_target_level)


if __name__ == "__main__":
    unittest.main()

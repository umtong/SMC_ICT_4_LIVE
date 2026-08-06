from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from auction_structural_stop_engine import RollingAuctionStructuralStopEngine
from lrb_types import BarObservation, PrimitiveSnapshot
from session_engine import _SessionEpisode


def snapshot() -> PrimitiveSnapshot:
    observation = BarObservation(
        ts_ns=1_000,
        open=101.0,
        high=103.0,
        low=100.5,
        close=102.0,
        volume=1000.0,
        taker_buy_volume=600.0,
        trades=100,
    )
    return PrimitiveSnapshot(
        index=10,
        observation=observation,
        ready=True,
        atr=1.0,
        rel_volume=1.5,
        flow_ratio=0.2,
        body_atr=1.0,
        range_atr=2.5,
        upper_wick_fraction=0.4,
        lower_wick_fraction=0.2,
        close_location=0.6,
        upper_fast=103.0,
        lower_fast=99.0,
        upper_slow=105.0,
        lower_slow=95.0,
        slow_mid=100.0,
        range_position=0.7,
        upper_pool_touches=2,
        lower_pool_touches=2,
    )


def episode() -> _SessionEpisode:
    return _SessionEpisode(
        scenario_id="test",
        state="UPPER_SESSION_SAC_RETEST",
        side="UPPER",
        level_name="PREVIOUS_HOUR_HIGH",
        level=100.0,
        family="SAC",
        direction="LONG",
        extreme=103.0,
        sweep_midpoint=100.0,
        started_index=0,
        started_ts_ns=0,
        atr_at_start=1.0,
        window="ROLLING_HOURLY_AUCTION",
        range_high=100.0,
        range_low=90.0,
    )


class StructuralStopTests(unittest.TestCase):
    def params(self, mode: str):
        params = json.loads((HERE / "config.json").read_text(encoding="utf-8"))["logic"]
        params.update(
            {
                "continuation_stop_mode": mode,
                "minimum_structural_rr": 0.75,
                "session_projection_fraction": 1.0,
                "stop_buffer_atr": 0.10,
            },
        )
        return params

    def test_impulse_origin_is_beyond_acceptance_and_retest(self) -> None:
        engine = RollingAuctionStructuralStopEngine(self.params("ACCEPTANCE_IMPULSE_ORIGIN"))
        item = episode()
        engine._episode = item
        engine._acceptance_origins[item.scenario_id] = (99.5, 102.5)
        step = engine._arm_continuation(snapshot(), item)
        self.assertIsNotNone(step.signal)
        assert step.signal is not None
        self.assertAlmostEqual(step.signal.stop_price, 99.4)
        self.assertLess(step.signal.stop_price, 99.5)
        self.assertEqual(
            step.transitions[-1].reason_code,
            "SESSION_ACCEPTANCE_RETEST_HELD_WITH_IMPULSE_ORIGIN_INVALIDATION",
        )

    def test_retest_boundary_mode_preserves_existing_stop(self) -> None:
        engine = RollingAuctionStructuralStopEngine(self.params("RETEST_BOUNDARY"))
        item = episode()
        engine._episode = item
        engine._acceptance_origins[item.scenario_id] = (99.5, 102.5)
        step = engine._arm_continuation(snapshot(), item)
        self.assertIsNotNone(step.signal)
        assert step.signal is not None
        self.assertAlmostEqual(step.signal.stop_price, 99.9)

    def test_missing_impulse_origin_abstains(self) -> None:
        engine = RollingAuctionStructuralStopEngine(self.params("ACCEPTANCE_IMPULSE_ORIGIN"))
        item = episode()
        engine._episode = item
        step = engine._arm_continuation(snapshot(), item)
        self.assertIsNone(step.signal)
        self.assertEqual(step.transitions[-1].reason_code, "ACCEPTANCE_IMPULSE_ORIGIN_MISSING")


if __name__ == "__main__":
    unittest.main()

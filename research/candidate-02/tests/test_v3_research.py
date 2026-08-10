from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import unittest

import numpy as np


CANDIDATE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_DIR))

from v3_research import (  # noqa: E402
    AcceptanceSetup,
    AuctionSource,
    CandidateV3Config,
    CostModel,
    FailedAcceptanceDetector,
    SignalCandidate,
    WeekData,
    _effective_stop_fill,
    execute_candidate,
)


def _week(
    *,
    five_open: list[float],
    five_high: list[float],
    five_low: list[float],
    five_close: list[float],
    buy_share: list[float] | None = None,
    volume: list[float] | None = None,
    trades: list[float] | None = None,
    atr: list[float] | None = None,
    one_open: list[float] | None = None,
    one_high: list[float] | None = None,
    one_low: list[float] | None = None,
    one_close: list[float] | None = None,
) -> WeekData:
    n = len(five_close)
    five_time = np.arange(n, dtype=np.int64) * 5 * 60_000_000_000
    one_open = one_open or [five_open[-1]] * max(1, n * 5)
    one_high = one_high or one_open
    one_low = one_low or one_open
    one_close = one_close or one_open
    one_time = np.arange(len(one_open), dtype=np.int64) * 60_000_000_000
    return WeekData(
        name="synthetic",
        evaluation_start_ns=0,
        evaluation_end_ns=int(one_time[-1] + 60_000_000_000),
        one_minute_time_ns=one_time,
        one_minute_open=np.asarray(one_open, dtype=float),
        one_minute_high=np.asarray(one_high, dtype=float),
        one_minute_low=np.asarray(one_low, dtype=float),
        one_minute_close=np.asarray(one_close, dtype=float),
        five_minute_time_ns=five_time,
        five_minute_open=np.asarray(five_open, dtype=float),
        five_minute_high=np.asarray(five_high, dtype=float),
        five_minute_low=np.asarray(five_low, dtype=float),
        five_minute_close=np.asarray(five_close, dtype=float),
        five_minute_volume=np.asarray(volume or [100.0] * n, dtype=float),
        five_minute_trade_count=np.asarray(trades or [100.0] * n, dtype=float),
        five_minute_buy_share=np.asarray(buy_share or [0.5] * n, dtype=float),
        prior_atr=np.asarray(atr or [1.0] * n, dtype=float),
        prior_flow_high=np.asarray([0.65] * n, dtype=float),
        prior_flow_low=np.asarray([0.35] * n, dtype=float),
        prior_volume_threshold=np.asarray([50.0] * n, dtype=float),
        prior_trade_threshold=np.asarray([50.0] * n, dtype=float),
    )


class CostAndExecutionTest(unittest.TestCase):
    def test_stop_budget_uses_cost_inclusive_geometry(self) -> None:
        week = _week(
            five_open=[100.0] * 5,
            five_high=[101.0] * 5,
            five_low=[99.0] * 5,
            five_close=[100.0] * 5,
        )
        detector = FailedAcceptanceDetector(
            week,
            CandidateV3Config(auction_horizons_5m=(4,)),
            CostModel(),
        )
        per_loss, reward, ratio, _, _ = detector._cost_geometry(100.0, 95.0, 115.0, 1)
        self.assertGreater(per_loss, 5.0)
        self.assertGreater(reward, 0.0)
        self.assertAlmostEqual(ratio, reward / per_loss)

    def test_same_minute_stop_and_target_resolves_stop_first(self) -> None:
        config = CandidateV3Config(
            auction_horizons_5m=(4,),
            risk_fraction=0.06,
            maximum_holding_minutes=10,
        )
        costs = CostModel()
        one_open = [100.0] * 30
        one_high = [100.0] * 30
        one_low = [100.0] * 30
        one_close = [100.0] * 30
        one_high[5] = 102.0
        one_low[5] = 98.0
        week = _week(
            five_open=[100.0] * 7,
            five_high=[101.0] * 7,
            five_low=[99.0] * 7,
            five_close=[100.0] * 7,
            one_open=one_open,
            one_high=one_high,
            one_low=one_low,
            one_close=one_close,
        )
        detector = FailedAcceptanceDetector(week, config, costs)
        per_loss, reward, ratio, _, _ = detector._cost_geometry(100.0, 99.0, 101.0, 1)
        source = AuctionSource(4, "LOW", 0, 0, 99.5, 101.0, 4, 3, 1.0, 0.1, 2.0)
        setup = AcceptanceSetup(source, 0, -1, 99.0, 0.5)
        candidate = SignalCandidate(4, setup, 1, 1, 100.0, 99.0, 101.0, per_loss, reward, ratio)
        result = execute_candidate(week, candidate, 100_000.0, config, costs)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.outcome, "STOP")
        self.assertAlmostEqual(result.planned_loss, 6_000.0)
        self.assertLess(result.pnl, 0.0)

    def test_gap_stop_uses_worse_bar_open(self) -> None:
        costs = CostModel()
        regular = _effective_stop_fill(side=1, stop=95.0, bar_open=100.0, costs=costs)
        gapped = _effective_stop_fill(side=1, stop=95.0, bar_open=90.0, costs=costs)
        self.assertLess(gapped, regular)


class StateMachineTest(unittest.TestCase):
    def test_source_window_does_not_include_current_bar(self) -> None:
        config = CandidateV3Config(
            auction_horizons_5m=(4,),
            minimum_source_age_bars=1,
            minimum_tested_boundary_touches=2,
            maximum_source_path_efficiency=1.0,
        )
        week = _week(
            five_open=[8, 8, 8, 8, 50],
            five_high=[10, 10, 9, 9, 100],
            five_low=[5, 5, 6, 6, 4],
            five_close=[7, 8, 8, 8, 50],
        )
        detector = FailedAcceptanceDetector(week, config, CostModel())
        source = detector._source(4, 4, "HIGH")
        self.assertIsNotNone(source)
        assert source is not None
        self.assertEqual(source.tested_level, 10.0)
        detector.consumed[4].add((source.boundary, source.source_index))
        self.assertIsNone(detector._source(4, 4, "HIGH"))

    def test_acceptance_then_failure_emits_short_signal(self) -> None:
        config = CandidateV3Config(
            auction_horizons_5m=(4,),
            minimum_source_age_bars=1,
            minimum_tested_boundary_touches=2,
            maximum_source_path_efficiency=1.0,
            minimum_excursion_atr=0.05,
            maximum_excursion_atr=3.0,
            acceptance_consecutive_closes=2,
            acceptance_deadline_bars=4,
            minimum_failure_depth_atr=0.1,
            minimum_cost_after_reward_risk=0.1,
        )
        week = _week(
            five_open=[7, 8, 8, 8, 10.0, 10.2, 10.2],
            five_high=[10, 10, 9, 9, 10.5, 10.5, 10.4],
            five_low=[5, 5, 6, 6, 9.9, 10.0, 9.5],
            five_close=[7, 8, 8, 8, 10.2, 10.3, 9.8],
            buy_share=[0.5, 0.5, 0.5, 0.5, 0.8, 0.8, 0.4],
            one_open=[9.8] * 40,
            one_high=[9.8] * 40,
            one_low=[9.8] * 40,
            one_close=[9.8] * 40,
        )
        detector = FailedAcceptanceDetector(week, config, CostModel())
        self.assertEqual(detector.update(4), [])
        self.assertEqual(detector.update(5), [])
        candidates = detector.update(6)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].trade_side, -1)
        self.assertEqual(candidates[0].target_price, 5.0)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from pathlib import Path

from derive_nt_lvcfr_v22_signals import (
    FiveBar,
    aggregate_five,
    find_choch_fvg,
    find_fvg_retrace_defense,
    latest_confirmed_internal_pivot,
    sweep_reversal_direction,
)
from nt_lvcfr_data import CandidateConfig, MinuteFact


def five(
    end_minute: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
    flow: float = 0.0,
) -> FiveBar:
    notional = 100.0
    return FiveBar(
        end_minute=end_minute,
        open=open_price,
        high=high,
        low=low,
        close=close,
        notional=notional,
        signed_notional=flow * notional,
    )


class V22SweepChochFvgTests(unittest.TestCase):
    def test_five_minute_aggregation_preserves_flow_and_ohlc(self) -> None:
        rows = [
            MinuteFact(
                minute_index=index,
                open=100.0 + index,
                high=101.0 + index,
                low=99.0 + index,
                close=100.5 + index,
                notional=10.0,
                signed_notional=2.0,
            )
            for index in range(5)
        ]
        result = aggregate_five(rows)
        self.assertEqual(set(result), {5})
        bar = result[5]
        self.assertEqual(bar.open, 100.0)
        self.assertEqual(bar.close, 104.5)
        self.assertEqual(bar.high, 105.0)
        self.assertEqual(bar.low, 99.0)
        self.assertAlmostEqual(bar.flow, 0.2)

    def test_external_high_and_low_sweeps_are_mutually_exclusive(self) -> None:
        high_sweep = five(65, 104.0, 106.0, 103.0, 104.5)
        self.assertEqual(
            sweep_reversal_direction(
                high_sweep,
                prior_high=105.0,
                prior_low=95.0,
            ),
            -1,
        )
        low_sweep = five(70, 96.0, 97.0, 94.0, 95.5)
        self.assertEqual(
            sweep_reversal_direction(
                low_sweep,
                prior_high=105.0,
                prior_low=95.0,
            ),
            1,
        )

    def test_pivot_is_known_only_after_right_hand_confirmation(self) -> None:
        bars = [
            five(5, 10.0, 11.0, 9.5, 10.5),
            five(10, 10.5, 10.8, 9.0, 9.5),
            five(15, 9.5, 10.0, 8.0, 8.5),
            five(20, 8.5, 9.8, 8.8, 9.4),
            five(25, 9.4, 10.5, 9.2, 10.0),
            five(30, 10.0, 10.7, 9.8, 10.2),
        ]
        pivot = latest_confirmed_internal_pivot(
            bars,
            before_index=5,
            direction=-1,
            lookback_bars=5,
            pivot_span=2,
        )
        self.assertEqual(pivot, (2, 8.0))

    def test_spot_supported_choch_finds_immediate_bearish_fvg(self) -> None:
        futures = [
            five(5, 104.0, 104.4, 103.5, 103.9),
            five(10, 103.9, 104.1, 103.2, 103.6),
            five(15, 103.0, 104.0, 102.0, 103.0),
            five(20, 103.0, 103.5, 98.0, 99.0),
            five(25, 99.0, 100.5, 97.0, 98.5),
            five(30, 98.5, 101.0, 98.0, 100.0),
        ]
        spot = [
            five(bar.end_minute, bar.open, bar.high, bar.low, bar.close, -0.2)
            for bar in futures
        ]
        formed_index, gap, baseline = find_choch_fvg(
            futures,
            spot,
            start_index=3,
            direction=-1,
            pivot_level=100.0,
            choch_expiry_bars=2,
            displacement_baseline_bars=3,
            fvg_formation_bars=3,
        )
        self.assertEqual(formed_index, 4)
        self.assertIsNotNone(gap)
        assert gap is not None
        self.assertEqual(gap.direction, -1)
        self.assertEqual(gap.lower, 100.5)
        self.assertEqual(gap.upper, 102.0)
        self.assertGreater(float(baseline), 0.0)

    def test_fvg_retrace_requires_midpoint_defense(self) -> None:
        futures = [
            five(5, 103.0, 104.0, 102.0, 103.0),
            five(10, 103.0, 103.5, 98.0, 99.0),
            five(15, 99.0, 100.5, 97.0, 98.5),
            five(20, 101.5, 101.8, 100.0, 100.7),
        ]
        from derive_nt_lvcfr_v20_signals import FairValueGap

        gap = FairValueGap(direction=-1, formed_minute=15, lower=100.5, upper=102.0)
        index, bar, touches = find_fvg_retrace_defense(
            futures,
            gap=gap,
            start_index=3,
            expiry_bars=1,
        )
        self.assertEqual(index, 3)
        self.assertEqual(touches, 1)
        assert bar is not None
        self.assertLess(bar.close, gap.midpoint)

    def test_native_execution_and_fixed_risk_contract_remain_unchanged(self) -> None:
        root = Path(__file__).resolve().parent
        config = CandidateConfig.load(root / "nt_lvcfr_v19_config.json")
        self.assertEqual(config.risk_fraction, 0.03)
        source = (root / "derive_nt_lvcfr_v22_signals.py").read_text(
            encoding="utf-8"
        )
        strategy = (root / "nt_lvcfr_strategy.py").read_text(encoding="utf-8")
        runner = (root / "run_nt_lvcfr.py").read_text(encoding="utf-8")
        self.assertIn("confirm_ns = defense_bar.end_minute * NS_PER_MINUTE", source)
        self.assertIn("self.submit_order(order)", strategy)
        self.assertIn("BacktestNode", runner)
        self.assertNotIn("simulate_fill", source)
        self.assertNotIn("synthetic_nav", source)
        self.assertNotIn("episode_pnl", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

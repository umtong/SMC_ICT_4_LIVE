from __future__ import annotations

import unittest
from pathlib import Path

from derive_nt_lvcfr_v24_signals import (
    FiveBar,
    OneBar,
    find_micro_choch_fvg,
    find_micro_fvg_defense,
    latest_confirmed_pivot,
    sweep_reversal_direction,
)
from nt_lvcfr_data import CandidateConfig


def one(
    start_minute: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
    flow: float = 0.0,
) -> OneBar:
    notional = 100.0
    return OneBar(
        start_minute=start_minute,
        open=open_price,
        high=high,
        low=low,
        close=close,
        notional=notional,
        signed_notional=flow * notional,
    )


class V24MtfSweepMicroEntryTests(unittest.TestCase):
    def test_five_minute_sweep_context_is_symmetric(self) -> None:
        high = FiveBar(65, 104.0, 106.0, 103.0, 104.5, 100.0, 0.0)
        low = FiveBar(70, 96.0, 97.0, 94.0, 95.5, 100.0, 0.0)
        self.assertEqual(sweep_reversal_direction(high, prior_high=105.0, prior_low=95.0), -1)
        self.assertEqual(sweep_reversal_direction(low, prior_high=105.0, prior_low=95.0), 1)

    def test_micro_pivot_is_confirmed_before_sweep_start(self) -> None:
        bars = [
            one(0, 10.0, 11.0, 9.5, 10.5),
            one(1, 10.5, 10.8, 9.0, 9.5),
            one(2, 9.5, 10.0, 8.0, 8.5),
            one(3, 8.5, 9.8, 8.8, 9.4),
            one(4, 9.4, 10.5, 9.2, 10.0),
            one(5, 10.0, 10.7, 9.8, 10.2),
        ]
        self.assertEqual(
            latest_confirmed_pivot(
                bars,
                before_index=5,
                direction=-1,
                lookback_bars=5,
                pivot_span=2,
            ),
            (2, 8.0),
        )

    def test_one_minute_choch_finds_bearish_fvg(self) -> None:
        futures = [
            one(0, 104.0, 104.4, 103.5, 103.9),
            one(1, 103.9, 104.1, 103.2, 103.6),
            one(2, 103.0, 104.0, 102.0, 103.0),
            one(3, 103.0, 103.5, 98.0, 99.0),
            one(4, 99.0, 100.5, 97.0, 98.5),
            one(5, 98.5, 101.0, 98.0, 100.0),
        ]
        spot = [
            one(bar.start_minute, bar.open, bar.high, bar.low, bar.close, -0.2)
            for bar in futures
        ]
        formed_index, gap, baseline = find_micro_choch_fvg(
            futures,
            spot,
            start_index=3,
            direction=-1,
            pivot_level=100.0,
            choch_expiry_minutes=2,
            displacement_baseline_minutes=3,
            fvg_formation_minutes=3,
        )
        self.assertEqual(formed_index, 4)
        self.assertIsNotNone(gap)
        assert gap is not None
        self.assertEqual(gap.lower, 100.5)
        self.assertEqual(gap.upper, 102.0)
        self.assertGreater(float(baseline), 0.0)

    def test_micro_retrace_defense_uses_completed_body(self) -> None:
        from derive_nt_lvcfr_v20_signals import FairValueGap

        gap = FairValueGap(direction=-1, formed_minute=3, lower=100.5, upper=102.0)
        bars = [one(3, 101.5, 101.8, 100.0, 100.7)]
        index, bar, touches = find_micro_fvg_defense(
            bars,
            gap=gap,
            start_index=0,
            expiry_minutes=1,
        )
        self.assertEqual(index, 0)
        self.assertEqual(touches, 1)
        assert bar is not None
        self.assertLess(bar.close, gap.midpoint)
        self.assertLess(bar.close, bar.open)

    def test_native_cost_gate_and_risk_contract_are_required(self) -> None:
        root = Path(__file__).resolve().parent
        config = CandidateConfig.load(root / "nt_lvcfr_v19_config.json")
        self.assertEqual(config.risk_fraction, 0.03)
        source = (root / "derive_nt_lvcfr_v24_signals.py").read_text(encoding="utf-8")
        patch = (root / "apply_nt_lvcfr_cost_viability_patch.py").read_text(encoding="utf-8")
        strategy = (root / "nt_lvcfr_strategy.py").read_text(encoding="utf-8")
        runner = (root / "run_nt_lvcfr.py").read_text(encoding="utf-8")
        self.assertIn("confirm_ns = defense_bar.end_minute * NS_PER_MINUTE", source)
        self.assertIn("STRUCTURAL_OBJECTIVE_DOES_NOT_CLEAR_COSTS", patch)
        self.assertIn("self.submit_order(order)", strategy)
        self.assertIn("BacktestNode", runner)
        self.assertNotIn("simulate_fill", source)
        self.assertNotIn("synthetic_nav", source)
        self.assertNotIn("episode_pnl", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

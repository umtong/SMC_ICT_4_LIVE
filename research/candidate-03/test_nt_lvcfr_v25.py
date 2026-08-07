from __future__ import annotations

import unittest
from pathlib import Path

from derive_nt_lvcfr_v25_signals import (
    OneBar,
    find_absorption_confirmation,
    make_flow_window,
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


class V25ExternalAbsorptionTests(unittest.TestCase):
    def test_flow_window_preserves_direction_and_low_response(self) -> None:
        bars = [
            one(0, 100.0, 100.2, 99.9, 100.1, 0.8),
            one(1, 100.1, 100.3, 100.0, 100.1, 0.8),
            one(2, 100.1, 100.2, 99.8, 100.0, 0.8),
        ]
        window = make_flow_window(bars, 0, 3)
        self.assertIsNotNone(window)
        assert window is not None
        self.assertEqual(window.direction, 1)
        self.assertGreater(window.absolute_flow, 0.0)
        self.assertLessEqual(window.directional_progress_bp, 0.0)
        self.assertLessEqual(window.response_score, 0.0)

    def test_bearish_confirmation_requires_midpoint_cross_body_and_flow(self) -> None:
        event_bars = [
            one(0, 100.0, 101.0, 99.8, 100.8, 0.7),
            one(1, 100.8, 101.5, 100.5, 101.0, 0.7),
            one(2, 101.0, 101.2, 100.2, 100.5, 0.7),
        ]
        confirmation = one(3, 100.5, 100.6, 99.7, 99.9, -0.4)
        bars = [*event_bars, confirmation]
        event = make_flow_window(bars, 0, 3)
        assert event is not None
        index, row = find_absorption_confirmation(
            bars,
            event=event,
            reversal_direction=-1,
            expiry_minutes=1,
        )
        self.assertEqual(index, 3)
        self.assertIs(row, confirmation)

    def test_bullish_confirmation_is_symmetric(self) -> None:
        event_bars = [
            one(0, 100.0, 100.2, 99.0, 99.2, -0.7),
            one(1, 99.2, 99.5, 98.5, 99.0, -0.7),
            one(2, 99.0, 99.8, 98.8, 99.5, -0.7),
        ]
        confirmation = one(3, 99.5, 100.4, 99.4, 100.2, 0.4)
        bars = [*event_bars, confirmation]
        event = make_flow_window(bars, 0, 3)
        assert event is not None
        index, row = find_absorption_confirmation(
            bars,
            event=event,
            reversal_direction=1,
            expiry_minutes=1,
        )
        self.assertEqual(index, 3)
        self.assertIs(row, confirmation)

    def test_native_cost_gate_and_fixed_risk_contract_are_required(self) -> None:
        root = Path(__file__).resolve().parent
        config = CandidateConfig.load(root / "nt_lvcfr_v19_config.json")
        self.assertEqual(config.risk_fraction, 0.03)
        source = (root / "derive_nt_lvcfr_v25_signals.py").read_text(encoding="utf-8")
        patch = (root / "apply_nt_lvcfr_cost_viability_patch.py").read_text(encoding="utf-8")
        strategy = (root / "nt_lvcfr_strategy.py").read_text(encoding="utf-8")
        runner = (root / "run_nt_lvcfr.py").read_text(encoding="utf-8")
        self.assertIn("ROLLING_4H_HORIZON_MATCHED_FLOW_BASELINE", source)
        self.assertIn("STRUCTURAL_OBJECTIVE_DOES_NOT_CLEAR_COSTS", patch)
        self.assertIn("self.submit_order(order)", strategy)
        self.assertIn("BacktestNode", runner)
        self.assertNotIn("simulate_fill", source)
        self.assertNotIn("synthetic_nav", source)
        self.assertNotIn("episode_pnl", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

from __future__ import annotations

import unittest
from pathlib import Path

from derive_nt_lvcfr_v23_signals import (
    BasisBar,
    FiveBar,
    dislocation_direction,
    find_compression_confirmation,
    tukey_fences,
)
from nt_lvcfr_data import CandidateConfig


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


def basis(
    end_minute: int,
    futures_close: float,
    spot_close: float,
    *,
    futures_open: float | None = None,
    futures_flow: float = 0.0,
) -> BasisBar:
    f_open = futures_close if futures_open is None else futures_open
    futures = five(
        end_minute,
        f_open,
        max(f_open, futures_close) + 1.0,
        min(f_open, futures_close) - 1.0,
        futures_close,
        futures_flow,
    )
    spot = five(end_minute, spot_close, spot_close + 0.5, spot_close - 0.5, spot_close)
    return BasisBar(
        end_minute=end_minute,
        futures=futures,
        spot=spot,
        basis_bp=(futures_close / spot_close - 1.0) * 10_000.0,
    )


class V23BasisDislocationTests(unittest.TestCase):
    def test_tukey_fence_uses_only_supplied_causal_distribution(self) -> None:
        values = [float(value) for value in range(1, 9)]
        med, q1, q3, lower, upper = tukey_fences(values)
        self.assertAlmostEqual(med, 4.5)
        self.assertLess(lower, q1)
        self.assertGreater(upper, q3)

    def test_premium_and_discount_dislocations_are_symmetric(self) -> None:
        self.assertEqual(
            dislocation_direction(8.0, lower_fence=-5.0, upper_fence=5.0),
            1,
        )
        self.assertEqual(
            dislocation_direction(-8.0, lower_fence=-5.0, upper_fence=5.0),
            -1,
        )
        self.assertEqual(
            dislocation_direction(2.0, lower_fence=-5.0, upper_fence=5.0),
            0,
        )

    def test_premium_compression_requires_fence_reentry_flow_and_price_reversal(self) -> None:
        event = basis(5, 105.0, 100.0, futures_open=101.0, futures_flow=0.4)
        confirmation = basis(10, 101.0, 100.0, futures_open=104.0, futures_flow=-0.3)
        index, row = find_compression_confirmation(
            [event, confirmation],
            event_index=0,
            dislocation=1,
            lower_fence=-20.0,
            upper_fence=150.0,
            expiry_bars=1,
        )
        self.assertEqual(index, 1)
        self.assertIs(row, confirmation)

    def test_discount_compression_requires_bullish_reversal(self) -> None:
        event = basis(5, 95.0, 100.0, futures_open=99.0, futures_flow=-0.4)
        confirmation = basis(10, 99.5, 100.0, futures_open=96.0, futures_flow=0.3)
        index, row = find_compression_confirmation(
            [event, confirmation],
            event_index=0,
            dislocation=-1,
            lower_fence=-150.0,
            upper_fence=20.0,
            expiry_bars=1,
        )
        self.assertEqual(index, 1)
        self.assertIs(row, confirmation)

    def test_native_execution_and_fixed_risk_contract_remain_unchanged(self) -> None:
        root = Path(__file__).resolve().parent
        config = CandidateConfig.load(root / "nt_lvcfr_v19_config.json")
        self.assertEqual(config.risk_fraction, 0.03)
        source = (root / "derive_nt_lvcfr_v23_signals.py").read_text(encoding="utf-8")
        strategy = (root / "nt_lvcfr_strategy.py").read_text(encoding="utf-8")
        runner = (root / "run_nt_lvcfr.py").read_text(encoding="utf-8")
        self.assertIn("confirm_ns = confirm_bar.end_minute * NS_PER_MINUTE", source)
        self.assertIn("self.submit_order(order)", strategy)
        self.assertIn("BacktestNode", runner)
        self.assertNotIn("simulate_fill", source)
        self.assertNotIn("synthetic_nav", source)
        self.assertNotIn("episode_pnl", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

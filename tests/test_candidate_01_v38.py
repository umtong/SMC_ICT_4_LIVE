from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "research" / "candidate-01"
SRC = ROOT / "src"
for item in (CANDIDATE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from aggtrade_clock import VolumeBar
from core import Side
from directional_change_failed_sweep_week import DirectionalChangeEvent
from impact_regime_probe import EventFeature
from paired_liquidity_transfer_v38 import PairedLiquidityTransferStateMachine


def feature(
    index: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    signed: float,
    quote: float = 100.0,
    true_range: float | None = None,
) -> EventFeature:
    bar = VolumeBar(
        index=index,
        start_time_ns=index * 1_000_000_000,
        end_time_ns=(index + 1) * 1_000_000_000,
        open=open_,
        high=high,
        low=low,
        close=close,
        base_quantity=1.0,
        quote_notional=quote,
        signed_quote_notional=signed,
        aggressive_buy_quote=max(signed, 0.0),
        aggressive_sell_quote=max(-signed, 0.0),
        aggregate_trades=1,
        first_agg_trade_id=index,
        last_agg_trade_id=index,
        target_quote_notional=quote,
    )
    return EventFeature(
        bar=bar,
        true_range=(high - low if true_range is None else true_range),
        atr=1.0,
        imbalance_z=signed / quote,
    )


class CandidateV38Test(unittest.TestCase):
    def test_terminal_effort_without_result_requires_more_effort_less_extension(self) -> None:
        rows = [
            feature(0, open_=99.8, high=100.0, low=99.5, close=99.9, signed=5.0),
            feature(1, open_=99.9, high=101.0, low=99.8, close=100.8, signed=45.0),
            feature(2, open_=100.8, high=102.0, low=100.7, close=101.7, signed=55.0),
            feature(3, open_=101.7, high=102.4, low=101.4, close=101.8, signed=65.0),
        ]
        event = DirectionalChangeEvent(
            event_type="DOWN",
            confirmation_index=4,
            confirmation_time_ns=5_000_000_000,
            confirmation_price=101.0,
            pivot_index=3,
            pivot_time_ns=4_000_000_000,
            pivot_price=102.4,
            trend_start_index=1,
            trend_flow_imbalance=0.5,
            reversal_flow_imbalance=-0.4,
            path_high=102.4,
            path_low=99.8,
        )
        evidence = PairedLiquidityTransferStateMachine._terminal_effort_without_result(
            event,
            rows,
        )
        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertGreaterEqual(evidence.terminal_effort, evidence.prior_effort_median)
        self.assertLess(evidence.terminal_progress, evidence.prior_progress_median)

    def test_terminal_extension_rejects_false_absorption(self) -> None:
        rows = [
            feature(0, open_=99.8, high=100.0, low=99.5, close=99.9, signed=5.0),
            feature(1, open_=99.9, high=100.5, low=99.8, close=100.4, signed=45.0),
            feature(2, open_=100.4, high=101.0, low=100.3, close=100.9, signed=55.0),
            feature(3, open_=100.9, high=102.5, low=100.8, close=102.2, signed=65.0),
        ]
        event = DirectionalChangeEvent(
            event_type="DOWN",
            confirmation_index=4,
            confirmation_time_ns=5_000_000_000,
            confirmation_price=101.0,
            pivot_index=3,
            pivot_time_ns=4_000_000_000,
            pivot_price=102.5,
            trend_start_index=1,
            trend_flow_imbalance=0.5,
            reversal_flow_imbalance=-0.4,
            path_high=102.5,
            path_low=99.8,
        )
        self.assertIsNone(
            PairedLiquidityTransferStateMachine._terminal_effort_without_result(
                event,
                rows,
            ),
        )

    def test_fvg_requires_true_outer_bar_non_overlap(self) -> None:
        bullish = [
            feature(0, open_=100.0, high=101.0, low=99.0, close=100.5, signed=10.0),
            feature(1, open_=100.5, high=103.0, low=100.4, close=102.8, signed=50.0),
            feature(2, open_=102.8, high=104.0, low=101.5, close=103.6, signed=40.0),
        ]
        self.assertEqual(
            PairedLiquidityTransferStateMachine._fvg(
                bullish,
                index=2,
                side=Side.LONG,
            ),
            (101.0, 101.5),
        )
        self.assertIsNone(
            PairedLiquidityTransferStateMachine._fvg(
                bullish,
                index=2,
                side=Side.SHORT,
            ),
        )

    def test_displacement_baseline_is_causal(self) -> None:
        rows = [
            feature(
                index,
                open_=100.0,
                high=101.0,
                low=100.0,
                close=100.5,
                signed=10.0,
                true_range=1.0,
            )
            for index in range(20)
        ]
        rows.append(
            feature(
                20,
                open_=100.0,
                high=102.0,
                low=100.0,
                close=101.8,
                signed=50.0,
                true_range=2.0,
            ),
        )
        before = PairedLiquidityTransferStateMachine._range_expanded(rows, index=20)
        rows.append(
            feature(
                21,
                open_=100.0,
                high=200.0,
                low=90.0,
                close=150.0,
                signed=90.0,
                true_range=110.0,
            ),
        )
        after = PairedLiquidityTransferStateMachine._range_expanded(rows, index=20)
        self.assertEqual(before, after)
        self.assertEqual(before, (True, 1.0))


if __name__ == "__main__":
    unittest.main()

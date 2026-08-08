from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

CANDIDATE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_DIR))

from model import Direction, LogicConfig, SignalBar  # noqa: E402
from model_internal_mss import InternalBoundaryMSSRouter  # noqa: E402


class InternalBoundaryMSSTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = InternalBoundaryMSSRouter(LogicConfig())

    def _bar(self, *, open_: float, close: float) -> SignalBar:
        return SignalBar(
            ts_event_ns=1,
            open=open_,
            high=max(open_, close) + 1.0,
            low=min(open_, close) - 1.0,
            close=close,
            volume=10.0,
        )

    def test_short_reclaim_below_external_but_above_internal_is_not_mss(self) -> None:
        episode = SimpleNamespace(
            direction=Direction.SHORT,
            liquidity_level=100.0,
            trigger_price=96.0,
        )
        bar = self._bar(open_=101.0, close=99.0)
        self.assertFalse(
            self.router._reversal_confirmed(episode, bar, atr=5.0)
        )

    def test_short_close_through_pre_sweep_internal_low_confirms_mss(self) -> None:
        episode = SimpleNamespace(
            direction=Direction.SHORT,
            liquidity_level=100.0,
            trigger_price=96.0,
        )
        bar = self._bar(open_=99.0, close=95.0)
        self.assertTrue(
            self.router._reversal_confirmed(episode, bar, atr=5.0)
        )

    def test_long_close_through_pre_sweep_internal_high_confirms_mss(self) -> None:
        episode = SimpleNamespace(
            direction=Direction.LONG,
            liquidity_level=100.0,
            trigger_price=104.0,
        )
        bar = self._bar(open_=101.0, close=105.0)
        self.assertTrue(
            self.router._reversal_confirmed(episode, bar, atr=5.0)
        )

    def test_internal_break_without_required_body_is_not_confirmation(self) -> None:
        episode = SimpleNamespace(
            direction=Direction.SHORT,
            liquidity_level=100.0,
            trigger_price=99.5,
        )
        bar = self._bar(open_=99.6, close=99.4)
        self.assertFalse(
            self.router._reversal_confirmed(episode, bar, atr=5.0)
        )


if __name__ == "__main__":
    unittest.main()

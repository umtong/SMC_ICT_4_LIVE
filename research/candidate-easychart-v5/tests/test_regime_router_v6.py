from __future__ import annotations

import unittest

from contracts_v5 import Pivot
from domain import Candle, Side
from scenario_regime_router_v6 import (
    CausalMacroSwingObserver,
    MacroSwingState,
    OppositionVetoMicroBundleV6,
    StrictAlignmentMicroBundleV6,
)

NS = 60_000_000_000


def pivot(pivot_id: str, side: str, price: float, index: int) -> Pivot:
    return Pivot(
        pivot_id=pivot_id,
        side=side,
        price=price,
        index=index,
        event_time_ns=index * NS,
        observed_index=index + 2,
        observed_time_ns=(index + 2) * NS,
        span=2,
        strength_ratio=2.0,
    )


class MacroRegimeRouterTests(unittest.TestCase):
    def test_confirmed_higher_high_and_higher_low_define_bull(self) -> None:
        observer = CausalMacroSwingObserver("TEST", 0.1)
        observer.book.pivots.extend(
            [
                pivot("H1", "HIGH", 105.0, 1),
                pivot("L1", "LOW", 95.0, 2),
                pivot("H2", "HIGH", 110.0, 3),
                pivot("L2", "LOW", 100.0, 4),
            ],
        )
        observer.last_bar_time_ns = 10 * NS
        snapshot = observer.snapshot()
        self.assertIs(snapshot.state, MacroSwingState.BULL)
        self.assertEqual(snapshot.previous_high, 105.0)
        self.assertEqual(snapshot.latest_low, 100.0)
        self.assertEqual(snapshot.observed_time_ns, 10 * NS)

    def test_opposition_veto_only_rejects_clear_opposition(self) -> None:
        self.assertTrue(OppositionVetoMicroBundleV6.allows(MacroSwingState.BULL, Side.LONG))
        self.assertFalse(OppositionVetoMicroBundleV6.allows(MacroSwingState.BULL, Side.SHORT))
        self.assertTrue(OppositionVetoMicroBundleV6.allows(MacroSwingState.BEAR, Side.SHORT))
        self.assertFalse(OppositionVetoMicroBundleV6.allows(MacroSwingState.BEAR, Side.LONG))
        self.assertTrue(OppositionVetoMicroBundleV6.allows(MacroSwingState.EXPANDING, Side.LONG))
        self.assertTrue(OppositionVetoMicroBundleV6.allows(MacroSwingState.UNKNOWN, Side.SHORT))

    def test_strict_alignment_rejects_unresolved_state(self) -> None:
        self.assertTrue(StrictAlignmentMicroBundleV6.allows(MacroSwingState.BULL, Side.LONG))
        self.assertTrue(StrictAlignmentMicroBundleV6.allows(MacroSwingState.BEAR, Side.SHORT))
        for state in (
            MacroSwingState.EXPANDING,
            MacroSwingState.CONTRACTING,
            MacroSwingState.FLAT,
            MacroSwingState.UNKNOWN,
        ):
            self.assertFalse(StrictAlignmentMicroBundleV6.allows(state, Side.LONG))
            self.assertFalse(StrictAlignmentMicroBundleV6.allows(state, Side.SHORT))

    def test_observer_uses_only_completed_60_minute_bars(self) -> None:
        observer = CausalMacroSwingObserver("TEST", 0.1)
        bar = Candle(NS, 100.0, 101.0, 99.0, 100.5, 1.0)
        observer.on_bar(bar)
        self.assertEqual(observer.last_bar_time_ns, NS)
        self.assertEqual(len(observer.book.bars), 1)
        self.assertIs(observer.snapshot().state, MacroSwingState.UNKNOWN)


if __name__ == "__main__":
    unittest.main()

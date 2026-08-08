from __future__ import annotations

from pathlib import Path
import sys
import unittest

import pandas as pd

CANDIDATE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_DIR))

from diagnose_initiative_flow_transition import (  # noqa: E402
    OrientedFlowSummary,
    exact_completed_window,
    transfer_rules,
)
from diagnose_initiative_flow_transition_v2 import (  # noqa: E402
    NS_PER_MINUTE,
    completed_minutes_between,
)


class InitiativeFlowTransitionV2Tests(unittest.TestCase):
    def _summary(
        self,
        *,
        imbalance: float,
        minute_fraction: float,
        terminal: float,
    ) -> OrientedFlowSummary:
        return OrientedFlowSummary(
            bars=5,
            oriented_imbalance=imbalance,
            oriented_volume_share=(imbalance + 1.0) / 2.0,
            oriented_minute_fraction=minute_fraction,
            terminal_oriented_imbalance=terminal,
            oriented_price_move_atr=0.2,
            oriented_path_efficiency=0.3,
            directional_volume=60.0,
            counter_volume=40.0,
        )

    def test_variable_confirmation_horizon_accepts_three_signal_bars(self) -> None:
        start = 1_000_000_000_000
        end = start + 15 * NS_PER_MINUTE
        self.assertEqual(completed_minutes_between(start, end), 15)

        timestamps = [start + minute * NS_PER_MINUTE for minute in range(1, 16)]
        frame = pd.DataFrame(
            {
                "timestamp_ns": timestamps,
                "open": [100.0] * 15,
                "high": [101.0] * 15,
                "low": [99.0] * 15,
                "close": [100.0] * 15,
                "volume": [10.0] * 15,
                "taker_buy_base": [6.0] * 15,
                "taker_sell_base": [4.0] * 15,
                "signed_base": [2.0] * 15,
            }
        )
        selected = exact_completed_window(
            frame,
            start_exclusive_ns=start,
            end_inclusive_ns=end,
            expected_bars=15,
        )
        self.assertEqual(len(selected.index), 15)

    def test_non_minute_aligned_horizon_is_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            completed_minutes_between(1_000, 1_000 + NS_PER_MINUTE + 1)

    def test_strict_majority_and_terminal_flow_are_both_required(self) -> None:
        source = self._summary(imbalance=0.2, minute_fraction=0.8, terminal=0.1)
        tie = self._summary(imbalance=0.1, minute_fraction=0.5, terminal=0.1)
        rules = transfer_rules(source=source, confirmation=tie, entry=source)
        self.assertTrue(rules["confirmation_flow_sign_flip"])
        self.assertFalse(rules["persistent_confirmation_transfer"])

        wrong_terminal = self._summary(
            imbalance=0.1,
            minute_fraction=0.8,
            terminal=-0.1,
        )
        rules = transfer_rules(
            source=source,
            confirmation=wrong_terminal,
            entry=source,
        )
        self.assertFalse(rules["persistent_confirmation_transfer"])

    def test_entry_minute_must_retain_transferred_side(self) -> None:
        source = self._summary(imbalance=0.2, minute_fraction=0.8, terminal=0.1)
        confirmation = self._summary(
            imbalance=0.1,
            minute_fraction=0.8,
            terminal=0.1,
        )
        entry = self._summary(imbalance=-0.1, minute_fraction=0.2, terminal=-0.1)
        rules = transfer_rules(
            source=source,
            confirmation=confirmation,
            entry=entry,
        )
        self.assertTrue(rules["persistent_confirmation_transfer"])
        self.assertFalse(rules["entry_minute_confirms_transfer"])


if __name__ == "__main__":
    unittest.main()

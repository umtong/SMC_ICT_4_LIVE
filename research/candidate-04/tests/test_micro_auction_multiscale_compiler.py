from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

import micro_auction_balance_transition_compiler as raw
import micro_auction_balance_transition_compiler_v2 as repaired
import micro_auction_multiscale_compiler as candidate


class MultiscaleContracts(unittest.TestCase):
    def test_same_state_machine_runs_at_each_scale_and_restores_globals(self) -> None:
        observed: list[tuple[int, int, int]] = []

        def fake_collect(*_args, **_kwargs):
            scale = raw.BALANCE_BARS
            observed.append(
                (scale, raw.BALANCE_MAX_AGE_BARS, raw.COOLDOWN_BARS)
            )
            intent = SimpleNamespace(
                signal_index=scale,
                scenario="TEST",
                details={},
            )
            return [intent], {"scale": scale}

        original = (
            raw.BALANCE_BARS,
            repaired.BALANCE_BARS,
            raw.BALANCE_MAX_AGE_BARS,
            raw.COOLDOWN_BARS,
        )
        with patch.object(raw, "collect_signals", side_effect=fake_collect):
            intents, summary = candidate.collect_multiscale(
                pd.DataFrame(),
                pd.Timestamp("2023-01-01", tz="UTC"),
                pd.Timestamp("2023-01-02", tz="UTC"),
                object(),
                object(),
                object(),
            )
        self.assertEqual([item[0] for item in observed], [60, 30])
        self.assertEqual(len(intents), 2)
        self.assertEqual(
            {item.details["micro_balance_bars"] for item in intents},
            {30, 60},
        )
        self.assertFalse(summary["market_logic_relaxed"])
        self.assertEqual(
            (
                raw.BALANCE_BARS,
                repaired.BALANCE_BARS,
                raw.BALANCE_MAX_AGE_BARS,
                raw.COOLDOWN_BARS,
            ),
            original,
        )


if __name__ == "__main__":
    unittest.main()

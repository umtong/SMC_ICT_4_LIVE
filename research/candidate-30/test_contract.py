from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from analyze_continuous import _policy
from analyze_continuous import _state
from analyze_continuous_v2 import _daily_prior_thresholds


class Candidate30ContractTest(unittest.TestCase):
    def test_state_uses_pre_shock_positioning_roles(self) -> None:
        state, flags = _state(
            direction=-1,
            oi_change_4h=0.02,
            oi_expand_cut=0.01,
            premium=0.001,
            premium_abs_cut=0.0005,
            account_ratio=1.20,
            account_median=1.00,
        )
        self.assertEqual(state, "ENDOGENOUS_CROWD")
        self.assertEqual(
            flags,
            {
                "oi_expanded": True,
                "crowd_premium": True,
                "crowd_accounts": True,
            },
        )

    def test_pre_registered_policy_map(self) -> None:
        self.assertEqual(
            _policy("ENDOGENOUS_CROWD", "REVERSAL"),
            "LEVERAGE_CLEARANCE_REVERSAL",
        )
        self.assertEqual(
            _policy("POSITIONING_BUILDUP", "CONTINUATION"),
            "CROWD_PERSISTENCE_CONTINUATION",
        )
        self.assertEqual(
            _policy("EXOGENOUS_SHOCK", "CONTINUATION"),
            "EXOGENOUS_DISCOVERY_CONTINUATION",
        )
        self.assertEqual(_policy("MIXED_STATE", "REVERSAL"), "NO_POLICY")

    def test_daily_cut_cannot_see_current_day_at_preserved_resolution(self) -> None:
        times = pd.date_range(
            "2024-01-01",
            periods=16 * 24 * 60,
            freq="1min",
            tz="UTC",
        )
        # Force the lower-resolution representation which made .asi8 incompatible
        # with Timestamp.value under pandas 3.
        if hasattr(times, "as_unit"):
            times = times.as_unit("us")
        size = len(times)
        base = np.ones(size, dtype=float)
        final_day = times.floor("D") == pd.Timestamp("2024-01-16", tz="UTC")
        abs_return = base.copy()
        abs_return[final_day] = 1_000_000.0
        series = {
            "abs_ret_1m_bps": abs_return,
            "quote_volume": base,
            "oi_change_4h": base * 0.01,
            "premium_index": base * 0.001,
            "account_ratio": base,
            "oi_change_15m": base * -0.01,
        }
        cuts = _daily_prior_thresholds(times=times, series=series)
        first_final = int(np.flatnonzero(final_day)[0])
        self.assertAlmostEqual(cuts["shock_return_cut"][first_final], 1.0)
        self.assertTrue(
            np.all(
                cuts["shock_return_cut"][final_day]
                == cuts["shock_return_cut"][first_final],
            ),
        )


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Causal and locked-rule regressions for Candidate-02 V155."""
from __future__ import annotations

from decimal import Decimal
import math
import unittest

import pandas as pd

from v53_nt_core import CostConfig, cost_after_reward_risk
from v155_informed_inventory_core import (
    InformedInventoryConfig,
    informed_inventory_candidate_mask,
    prior_robust_z,
    solve_cost_after_target,
)


class InformedInventoryV155Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = InformedInventoryConfig()
        self.costs = CostConfig(
            entry_fee_rate=Decimal("0.00050"),
            target_fee_rate=Decimal("0.00020"),
            stop_fee_rate=Decimal("0.00050"),
            entry_slippage_rate=Decimal("0.00015"),
            stop_slippage_rate=Decimal("0.00025"),
            market_impact_rate=Decimal("0.00005"),
            funding_rate_allowance=Decimal("0.00010"),
        )

    def _valid_state(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "price_direction": [1.0],
                "current_candle_direction": [1.0],
                "price_move_atr": [1.25],
                "oi_change": [0.01],
                "oi_change_z": [1.50],
                "top_position_directional_change": [0.02],
                "top_position_directional_z": [1.25],
                "top_account_directional_z": [-0.50],
                "taker_directional_change": [0.03],
                "taker_directional_z": [0.10],
                "broad_herding_rejected": [False],
            },
            index=pd.DatetimeIndex(["2025-01-01T00:30:00Z"]),
        )

    def test_prior_robust_z_excludes_current_observation(self) -> None:
        index = pd.date_range("2025-01-01", periods=11, freq="5min", tz="UTC")
        original = pd.Series([float(value) for value in range(10)] + [100.0], index=index)
        changed = original.copy()
        changed.iloc[-1] = 10_000.0
        first = prior_robust_z(original, history_bars=10, minimum_observations=10)
        second = prior_robust_z(changed, history_bars=10, minimum_observations=10)
        self.assertAlmostEqual(first.iloc[-1]["prior_location"], 4.5)
        self.assertAlmostEqual(first.iloc[-1]["prior_mad"], 2.5)
        self.assertAlmostEqual(first.iloc[-1]["prior_location"], second.iloc[-1]["prior_location"])
        self.assertAlmostEqual(first.iloc[-1]["prior_mad"], second.iloc[-1]["prior_mad"])
        self.assertNotEqual(first.iloc[-1]["robust_z"], second.iloc[-1]["robust_z"])

    def test_valid_locked_state_is_accepted(self) -> None:
        self.assertTrue(bool(informed_inventory_candidate_mask(self._valid_state(), self.config).iloc[0]))

    def test_open_interest_decline_is_rejected(self) -> None:
        state = self._valid_state()
        state.loc[:, "oi_change"] = -0.01
        self.assertFalse(bool(informed_inventory_candidate_mask(state, self.config).iloc[0]))

    def test_broad_account_herding_is_rejected(self) -> None:
        state = self._valid_state()
        state.loc[:, "broad_herding_rejected"] = True
        self.assertFalse(bool(informed_inventory_candidate_mask(state, self.config).iloc[0]))

    def test_extreme_top_account_contradiction_is_rejected(self) -> None:
        state = self._valid_state()
        state.loc[:, "top_account_directional_z"] = -2.01
        self.assertFalse(bool(informed_inventory_candidate_mask(state, self.config).iloc[0]))

    def test_cost_after_target_solver_preserves_locked_r(self) -> None:
        for side, entry, stop in (("BUY", 100.0, 98.0), ("SELL", 100.0, 102.0)):
            target = solve_cost_after_target(
                entry=entry,
                stop=stop,
                side=side,
                target_r=self.config.cost_after_target_r,
                costs=self.costs,
            )
            rr = cost_after_reward_risk(
                entry=entry,
                stop=stop,
                target=target,
                side=side,
                costs=self.costs,
            )
            self.assertTrue(math.isclose(rr, 0.75, rel_tol=1e-10, abs_tol=1e-10))
            self.assertGreater(target, entry) if side == "BUY" else self.assertLess(target, entry)

    def test_lock_rejects_parameter_drift(self) -> None:
        with self.assertRaises(ValueError):
            InformedInventoryConfig(observation_bars=7)
        with self.assertRaises(ValueError):
            InformedInventoryConfig(robust_history_bars=144)


if __name__ == "__main__":
    unittest.main(verbosity=2)

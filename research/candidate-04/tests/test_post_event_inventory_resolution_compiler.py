from __future__ import annotations

from types import SimpleNamespace
import unittest

import pandas as pd

import post_event_inventory_resolution_compiler as candidate


class PastOnlyThresholdTests(unittest.TestCase):
    def test_current_outlier_does_not_enter_its_own_cutoff(self) -> None:
        values = pd.Series([1.0, 2.0, 3.0, 1000.0])
        cutoff = candidate.shifted_quantile(
            values,
            0.50,
            window=3,
            minimum=3,
        )
        self.assertEqual(float(cutoff.iloc[3]), 2.0)


class InventoryRouteTests(unittest.TestCase):
    def test_attack_time_contraction_is_liquidation(self) -> None:
        route = candidate.classify_attack_inventory(
            100.0,
            99.0,
            98.0,
            0.002,
        )
        self.assertEqual(route, candidate.LIQUIDATION_REVERSAL)

    def test_created_inventory_must_unwind_before_reversal(self) -> None:
        retained = candidate.classify_attack_inventory(
            100.0,
            102.0,
            102.5,
            0.01,
        )
        unwound = candidate.classify_attack_inventory(
            100.0,
            102.0,
            101.0,
            0.01,
        )
        self.assertIsNone(retained)
        self.assertEqual(unwound, candidate.TRAPPED_INVENTORY_REVERSAL)

    def test_marginal_positive_oi_is_not_failed_inventory(self) -> None:
        route = candidate.classify_attack_inventory(
            100.0,
            100.2,
            100.1,
            0.01,
        )
        self.assertIsNone(route)


class InformedEventTests(unittest.TestCase):
    def _frame(self) -> pd.DataFrame:
        rows = 20
        return pd.DataFrame(
            {
                "flow_300s": [0.8] * rows,
                "ret_300s_bps": [12.0] * rows,
                "flow_sign_persistence_300s": [0.9] * rows,
                "eff_300s": [0.8] * rows,
                "notional_burst_60s": [2.0] * rows,
                "basis_change_15m": [4.0] * rows,
                "metric_sum_open_interest": [100.0] * 5 + [101.0] * 15,
            }
        )

    def _thresholds(self, rows: int) -> candidate.Thresholds:
        constant = lambda value: pd.Series([value] * rows)
        return candidate.Thresholds(
            abs_flow_60_q70=constant(0.4),
            abs_return_60_q60=constant(3.0),
            notional_burst_60_q65=constant(1.2),
            abs_flow_300_q75=constant(0.6),
            abs_return_300_q65=constant(8.0),
            persistence_300_q60=constant(0.7),
            efficiency_300_q60=constant(0.6),
            efficiency_60_q50=constant(0.5),
            positive_oi_step_median=constant(0.005),
        )

    def test_informed_state_requires_material_inventory_creation(self) -> None:
        frame = self._frame()
        thresholds = self._thresholds(len(frame))
        accepted = candidate.informed_event_state(frame, 19, thresholds)
        self.assertIsNotNone(accepted)

        frame.loc[19, "metric_sum_open_interest"] = 100.1
        rejected = candidate.informed_event_state(frame, 19, thresholds)
        self.assertIsNone(rejected)

    def test_low_efficiency_event_is_not_continuation(self) -> None:
        frame = self._frame()
        thresholds = self._thresholds(len(frame))
        frame.loc[19, "eff_300s"] = 0.4
        self.assertIsNone(candidate.informed_event_state(frame, 19, thresholds))


class EffortDirectionTests(unittest.TestCase):
    def test_directional_effort_respects_trade_side(self) -> None:
        row = pd.Series(
            {
                "flow_60s": -0.5,
                "notional_60s": 1_000.0,
            }
        )
        self.assertEqual(candidate._directional_effort(row, -1), 500.0)
        self.assertEqual(candidate._directional_effort(row, 1), -500.0)


class ConstantsTests(unittest.TestCase):
    def test_pullback_contract_is_bounded_and_inventory_retaining(self) -> None:
        self.assertGreater(candidate.PULLBACK_MIN_RETRACE, 0.0)
        self.assertLess(candidate.PULLBACK_MAX_RETRACE, 1.0)
        self.assertLess(
            candidate.MAX_COUNTER_EFFORT_FRACTION,
            1.0,
        )
        self.assertGreater(candidate.MIN_INVENTORY_RETENTION, 0.99)


if __name__ == "__main__":
    unittest.main()

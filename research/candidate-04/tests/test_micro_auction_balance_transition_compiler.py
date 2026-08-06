from __future__ import annotations

from types import SimpleNamespace
import unittest

import pandas as pd

import micro_auction_balance_transition_compiler as candidate


class PastOnlyTests(unittest.TestCase):
    def test_current_outlier_is_excluded_from_cutoff(self) -> None:
        series = pd.Series([1.0, 2.0, 3.0, 1000.0])
        result = candidate.shifted_quantile(
            series,
            0.50,
            window=3,
            minimum=3,
        )
        self.assertEqual(float(result.iloc[3]), 2.0)


class BalanceTests(unittest.TestCase):
    def test_frozen_balance_uses_completed_window_only(self) -> None:
        rows = candidate.BALANCE_BARS + 1
        data = pd.DataFrame(
            {
                "high": [101.0] * candidate.BALANCE_BARS + [500.0],
                "low": [99.0] * candidate.BALANCE_BARS + [1.0],
                "close": [100.0] * rows,
                "atr": [2.0] * rows,
                "metric_sum_open_interest": [100.0] * rows,
            }
        )
        metrics = candidate.build_balance_metrics(data)
        frozen = candidate.freeze_balance(
            1,
            candidate.BALANCE_BARS - 1,
            data,
            metrics,
        )
        self.assertIsNotNone(frozen)
        assert frozen is not None
        self.assertEqual(frozen.high, 101.0)
        self.assertEqual(frozen.low, 99.0)
        self.assertEqual(frozen.created_index, candidate.BALANCE_BARS)

    def test_balance_requires_rotational_low_efficiency_state(self) -> None:
        series = lambda value: pd.Series([value])
        metrics = candidate.BalanceMetrics(
            high=series(101.0),
            low=series(99.0),
            width_atr=series(1.0),
            path_to_width=series(4.0),
            net_efficiency=series(0.10),
            oi_dispersion=series(0.01),
            close_location=series(0.50),
        )
        thresholds = candidate.Thresholds(
            width_atr_q45=series(1.2),
            path_to_width_q55=series(3.0),
            net_efficiency_q45=series(0.20),
            oi_dispersion_q60=series(0.02),
            abs_flow_q65=series(0.4),
            abs_return_q65=series(2.0),
            notional_burst_q60=series(1.2),
            impact_efficiency_q55=series(0.5),
            positive_oi_step_median=series(0.005),
        )
        self.assertTrue(candidate.balance_qualifies(0, metrics, thresholds))
        directional = candidate.BalanceMetrics(
            **{
                field: getattr(metrics, field)
                for field in metrics.__dataclass_fields__
                if field != "net_efficiency"
            },
            net_efficiency=series(0.50),
        )
        self.assertFalse(candidate.balance_qualifies(0, directional, thresholds))


class InventoryRouteTests(unittest.TestCase):
    def test_material_expansion_and_contraction_are_distinct(self) -> None:
        expansion = candidate.classify_inventory_route(100.0, 101.0, 0.005)
        contraction = candidate.classify_inventory_route(100.0, 99.0, 0.005)
        marginal = candidate.classify_inventory_route(100.0, 100.1, 0.005)
        self.assertEqual(expansion, ("NEW_INVENTORY", 0.01))
        self.assertEqual(contraction, ("LIQUIDATION", -0.01))
        self.assertIsNone(marginal)


class BoundaryTests(unittest.TestCase):
    def _state(self, side: int) -> candidate.BreakState:
        balance = candidate.FrozenBalance(
            balance_id=1,
            start_index=0,
            end_index=29,
            created_index=30,
            expires_index=75,
            high=101.0,
            low=99.0,
            midpoint=100.0,
            width=2.0,
            atr=2.0,
            width_atr=1.0,
            path_to_width=4.0,
            net_efficiency=0.1,
            oi_dispersion=0.01,
        )
        return candidate.BreakState(
            balance=balance,
            index=30,
            side=side,
            boundary=101.0 if side > 0 else 99.0,
            close=101.5 if side > 0 else 98.5,
            atr=2.0,
            penetration_atr=0.25,
            effort=1000.0,
            oi_before=100.0,
            oi_at_break=101.0,
            oi_change=0.01,
            inventory_route="NEW_INVENTORY",
        )

    def test_reentry_is_exact_and_directional(self) -> None:
        long_break = self._state(1)
        short_break = self._state(-1)
        self.assertTrue(candidate.boundary_reentered(100.9, long_break))
        self.assertFalse(candidate.boundary_reentered(101.1, long_break))
        self.assertTrue(candidate.boundary_reentered(99.1, short_break))
        self.assertFalse(candidate.boundary_reentered(98.9, short_break))

    def test_retest_requires_touch_and_outside_close(self) -> None:
        long_break = self._state(1)
        self.assertTrue(
            candidate.boundary_retest_holds(
                high=101.5,
                low=101.1,
                close=101.2,
                break_state=long_break,
            )
        )
        self.assertFalse(
            candidate.boundary_retest_holds(
                high=101.5,
                low=101.1,
                close=100.9,
                break_state=long_break,
            )
        )


class StopTests(unittest.TestCase):
    def test_stop_uses_complete_excursion_and_existing_buffer_name(self) -> None:
        data = pd.DataFrame(
            {
                "low": [99.0, 98.0, 98.5],
                "high": [101.0, 102.0, 101.5],
                "atr": [2.0, 2.0, 2.0],
            }
        )
        long_stop = candidate.excursion_stop(
            data,
            0,
            2,
            1,
            SimpleNamespace(sweep_stop_buffer_atr=0.10),
        )
        short_stop = candidate.excursion_stop(
            data,
            0,
            2,
            -1,
            SimpleNamespace(stop_buffer_atr=0.10),
        )
        self.assertEqual(long_stop, 97.8)
        self.assertEqual(short_stop, 102.2)


class StructuralConstantsTests(unittest.TestCase):
    def test_retest_and_inventory_contracts_are_bounded(self) -> None:
        self.assertGreater(candidate.MIN_BREAK_ATR, 0.0)
        self.assertLess(candidate.MAX_BREAK_ATR, 1.0)
        self.assertLess(candidate.MAX_COUNTER_EFFORT_FRACTION, 1.0)
        self.assertGreater(candidate.NEW_INVENTORY_RETENTION, 0.99)
        self.assertGreaterEqual(candidate.LIQUIDATION_REBUILD_TOLERANCE, 1.0)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from inventory_repricing_logic import inventory_trap_confirmed
from inventory_repricing_logic import quarter_context_accepted
from inventory_repricing_logic import quarter_context_invalidated
from inventory_repricing_logic import quarter_hour_repricing_direction
from inventory_repricing_logic import quarter_internal_sweep_eligible


class InventoryRepricingLogicTest(unittest.TestCase):
    def test_quarter_hour_signal_is_directionally_symmetric(self) -> None:
        self.assertEqual(
            quarter_hour_repricing_direction(
                minute_of_hour=15,
                flow_open_10s=0.40,
                notional_open_10s_burst=1.6,
                ret_60s_bps=3.0,
                efficiency_60s=0.25,
            ),
            1,
        )
        self.assertEqual(
            quarter_hour_repricing_direction(
                minute_of_hour=45,
                flow_open_10s=-0.40,
                notional_open_10s_burst=1.6,
                ret_60s_bps=-3.0,
                efficiency_60s=0.25,
            ),
            -1,
        )
        self.assertEqual(
            quarter_hour_repricing_direction(
                minute_of_hour=14,
                flow_open_10s=0.9,
                notional_open_10s_burst=5.0,
                ret_60s_bps=10.0,
                efficiency_60s=0.9,
            ),
            0,
        )

    def test_external_inventory_trap_requires_all_components(self) -> None:
        args = dict(
            side=1,
            penetration_atr=0.40,
            flow_15s=0.30,
            flow_60s=-0.25,
            depth_imbalance=0.25,
            close=101.0,
            trade_vwap=100.5,
            external_or_clustered=True,
        )
        self.assertTrue(inventory_trap_confirmed(**args))
        self.assertFalse(inventory_trap_confirmed(**{**args, "depth_imbalance": 0.19}))
        self.assertFalse(inventory_trap_confirmed(**{**args, "close": 100.0}))
        self.assertTrue(
            inventory_trap_confirmed(
                **{
                    **args,
                    "side": -1,
                    "flow_15s": -0.30,
                    "flow_60s": 0.25,
                    "depth_imbalance": -0.25,
                    "close": 99.0,
                    "trade_vwap": 99.5,
                },
            ),
        )

    def test_internal_inventory_trap_requires_material_tail_and_penetration(self) -> None:
        long_base = dict(
            side=1,
            penetration_atr=0.70,
            flow_60s=-0.18,
            depth_imbalance=0.17,
            close=101.0,
            trade_vwap=100.5,
            external_or_clustered=False,
        )
        self.assertFalse(
            inventory_trap_confirmed(
                **long_base,
                flow_15s=0.22,
            ),
        )
        self.assertTrue(
            inventory_trap_confirmed(
                **long_base,
                flow_15s=0.40,
            ),
        )
        self.assertFalse(
            inventory_trap_confirmed(
                **{
                    **long_base,
                    "penetration_atr": 0.25,
                    "flow_15s": 0.40,
                },
            ),
        )
        short_base = dict(
            side=-1,
            penetration_atr=0.70,
            flow_60s=0.18,
            depth_imbalance=-0.17,
            close=99.0,
            trade_vwap=99.5,
            external_or_clustered=False,
        )
        self.assertFalse(
            inventory_trap_confirmed(
                **short_base,
                flow_15s=-0.20,
            ),
        )
        self.assertTrue(
            inventory_trap_confirmed(
                **{
                    **short_base,
                    "flow_15s": -0.40,
                    "flow_60s": 0.11,
                },
            ),
        )

    def test_internal_sweep_needs_accepted_aged_context(self) -> None:
        self.assertFalse(
            quarter_internal_sweep_eligible(
                setup_side=1,
                context_direction=1,
                context_age_bars=14,
                context_accepted=True,
            ),
        )
        self.assertTrue(
            quarter_internal_sweep_eligible(
                setup_side=1,
                context_direction=1,
                context_age_bars=15,
                context_accepted=True,
            ),
        )
        self.assertFalse(
            quarter_internal_sweep_eligible(
                setup_side=-1,
                context_direction=1,
                context_age_bars=30,
                context_accepted=True,
            ),
        )

    def test_context_acceptance_and_invalidation_are_symmetric(self) -> None:
        self.assertTrue(
            quarter_context_accepted(
                direction=1,
                boundary_close=100.0,
                favorable_extreme=101.0,
                atr=2.0,
            ),
        )
        self.assertTrue(
            quarter_context_accepted(
                direction=-1,
                boundary_close=100.0,
                favorable_extreme=99.0,
                atr=2.0,
            ),
        )
        self.assertTrue(
            quarter_context_invalidated(
                direction=1,
                boundary_low=99.0,
                boundary_high=101.0,
                current_close=98.5,
                atr=2.0,
            ),
        )
        self.assertTrue(
            quarter_context_invalidated(
                direction=-1,
                boundary_low=99.0,
                boundary_high=101.0,
                current_close=101.5,
                atr=2.0,
            ),
        )


if __name__ == "__main__":
    unittest.main()

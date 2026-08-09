from __future__ import annotations

import unittest

from failed_inventory_acceptance_logic import accepted_level_closed_back_inside
from failed_inventory_acceptance_logic import accepted_level_retest_touched
from failed_inventory_acceptance_logic import failed_inventory_acceptance_ready


class FailedInventoryAcceptanceLogicTests(unittest.TestCase):
    def _ready(self, side: int) -> bool:
        level = 100.0
        return failed_inventory_acceptance_ready(
            side=side,
            level=level,
            open_price=100.1 if side > 0 else 99.9,
            high=101.0,
            low=99.0,
            close=100.7 if side > 0 else 99.3,
            atr=1.0,
            flow_15s=0.20 * side,
            flow_60s=0.15 * side,
            efficiency_60s=0.55,
            bid_depth_change_1m=-0.03 if side < 0 else 0.02,
            ask_depth_change_1m=-0.03 if side > 0 else 0.02,
            minimum_close_atr=0.05,
            minimum_flow=0.10,
            minimum_efficiency=0.45,
            minimum_depth_withdrawal=0.01,
            minimum_close_location=0.62,
        )

    def test_acceptance_is_mirror_symmetric(self) -> None:
        self.assertTrue(self._ready(1))
        self.assertTrue(self._ready(-1))

    def test_each_causal_component_can_veto_acceptance(self) -> None:
        base = {
            "side": 1,
            "level": 100.0,
            "open_price": 100.1,
            "high": 101.0,
            "low": 99.0,
            "close": 100.7,
            "atr": 1.0,
            "flow_15s": 0.20,
            "flow_60s": 0.15,
            "efficiency_60s": 0.55,
            "bid_depth_change_1m": 0.02,
            "ask_depth_change_1m": -0.03,
            "minimum_close_atr": 0.05,
            "minimum_flow": 0.10,
            "minimum_efficiency": 0.45,
            "minimum_depth_withdrawal": 0.01,
            "minimum_close_location": 0.62,
        }
        mutations = (
            {"close": 100.01},
            {"flow_15s": -0.20},
            {"flow_60s": 0.05},
            {"efficiency_60s": 0.20},
            {"ask_depth_change_1m": 0.03},
            {"close": 100.1, "high": 101.0, "low": 99.0},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                values = {**base, **mutation}
                self.assertFalse(failed_inventory_acceptance_ready(**values))

    def test_first_retest_and_range_reentry_are_symmetric(self) -> None:
        self.assertTrue(
            accepted_level_retest_touched(side=1, level=100.0, high=100.5, low=99.9),
        )
        self.assertTrue(
            accepted_level_retest_touched(side=-1, level=100.0, high=100.1, low=99.5),
        )
        self.assertTrue(accepted_level_closed_back_inside(side=1, level=100.0, close=99.9))
        self.assertTrue(accepted_level_closed_back_inside(side=-1, level=100.0, close=100.1))
        self.assertFalse(accepted_level_closed_back_inside(side=1, level=100.0, close=100.1))
        self.assertFalse(accepted_level_closed_back_inside(side=-1, level=100.0, close=99.9))

    def test_invalid_side_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            failed_inventory_acceptance_ready(
                side=0,
                level=100.0,
                open_price=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                atr=1.0,
                flow_15s=0.2,
                flow_60s=0.2,
                efficiency_60s=0.5,
                bid_depth_change_1m=0.0,
                ask_depth_change_1m=-0.1,
                minimum_close_atr=0.05,
                minimum_flow=0.1,
                minimum_efficiency=0.45,
                minimum_depth_withdrawal=0.01,
                minimum_close_location=0.62,
            )


if __name__ == "__main__":
    unittest.main()

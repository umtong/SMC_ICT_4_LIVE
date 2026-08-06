from __future__ import annotations

import unittest

from nt_expected_fill_only_risk_sizing import capped_signal_target
from nt_liquidity_strategy import net_r_at_price


class ExpectedFillOnlyTargetControlTests(unittest.TestCase):
    def test_reference_above_cap_is_still_capped(self) -> None:
        selected = capped_signal_target(
            signal_entry=100.0,
            stop_trigger=98.0,
            side=1,
            cost_rate=0.00075,
            requested_target_net_r=1.8,
            target_reference=120.0,
            minimum_reference_net_r=1.2,
            maximum_reference_net_r=2.4,
        )
        self.assertIsNotNone(selected)
        assert selected is not None
        target, reference_r, planned_loss = selected
        self.assertIsNotNone(reference_r)
        self.assertGreater(float(reference_r), 2.4)
        self.assertAlmostEqual(
            net_r_at_price(100.0, target, 1, planned_loss, 0.00075),
            2.4,
        )

    def test_reference_below_minimum_remains_rejected(self) -> None:
        selected = capped_signal_target(
            signal_entry=100.0,
            stop_trigger=98.0,
            side=1,
            cost_rate=0.00075,
            requested_target_net_r=1.8,
            target_reference=101.0,
            minimum_reference_net_r=1.2,
            maximum_reference_net_r=2.4,
        )
        self.assertIsNone(selected)

    def test_no_reference_preserves_requested_fixed_r(self) -> None:
        selected = capped_signal_target(
            signal_entry=100.0,
            stop_trigger=98.0,
            side=1,
            cost_rate=0.00075,
            requested_target_net_r=1.8,
            target_reference=None,
            minimum_reference_net_r=1.2,
            maximum_reference_net_r=2.4,
        )
        self.assertIsNotNone(selected)
        assert selected is not None
        target, reference_r, planned_loss = selected
        self.assertIsNone(reference_r)
        self.assertAlmostEqual(
            net_r_at_price(100.0, target, 1, planned_loss, 0.00075),
            1.8,
        )


if __name__ == "__main__":
    unittest.main()

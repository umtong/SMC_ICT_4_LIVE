from __future__ import annotations

import unittest

from nt_exact_causal_target_risk_sizing import select_exact_causal_target
from nt_liquidity_strategy import net_r_at_price


class ExactCausalTargetSelectionTests(unittest.TestCase):
    def test_distant_valid_reference_is_not_capped(self) -> None:
        entry = 100.0
        stop = 98.0
        cost = 0.00075
        reference = 120.0
        selected = select_exact_causal_target(
            signal_entry=entry,
            stop_trigger=stop,
            side=1,
            cost_rate=cost,
            fallback_target_net_r=2.0,
            target_reference=reference,
            minimum_reference_net_r=1.2,
        )
        self.assertIsNotNone(selected)
        assert selected is not None
        target, reference_r = selected
        self.assertEqual(target, reference)
        self.assertIsNotNone(reference_r)
        self.assertGreater(float(reference_r), 2.4)

    def test_reference_below_existing_minimum_is_rejected(self) -> None:
        selected = select_exact_causal_target(
            signal_entry=100.0,
            stop_trigger=98.0,
            side=1,
            cost_rate=0.00075,
            fallback_target_net_r=2.0,
            target_reference=101.0,
            minimum_reference_net_r=1.2,
        )
        self.assertIsNone(selected)

    def test_no_reference_preserves_fixed_r_fallback(self) -> None:
        entry = 100.0
        stop = 98.0
        cost = 0.00075
        selected = select_exact_causal_target(
            signal_entry=entry,
            stop_trigger=stop,
            side=1,
            cost_rate=cost,
            fallback_target_net_r=2.0,
            target_reference=None,
            minimum_reference_net_r=1.2,
        )
        self.assertIsNotNone(selected)
        assert selected is not None
        target, reference_r = selected
        self.assertIsNone(reference_r)
        planned_loss = entry - stop + cost * (entry + stop)
        self.assertAlmostEqual(
            net_r_at_price(entry, target, 1, planned_loss, cost),
            2.0,
        )

    def test_wrong_side_reference_is_rejected(self) -> None:
        selected = select_exact_causal_target(
            signal_entry=100.0,
            stop_trigger=102.0,
            side=-1,
            cost_rate=0.00075,
            fallback_target_net_r=2.0,
            target_reference=101.0,
            minimum_reference_net_r=1.2,
        )
        self.assertIsNone(selected)


if __name__ == "__main__":
    unittest.main()

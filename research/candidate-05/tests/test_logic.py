from __future__ import annotations

import unittest

from logic import ClassificationThresholds
from logic import Pool
from logic import SweepEvidence
from logic import choose_liquidity_target
from logic import classify_sweep
from logic import cost_aware_target
from logic import is_confirmed_pivot
from logic import mirrored_evidence
from logic import net_r_at_price
from logic import planned_loss_per_unit


THRESHOLDS = ClassificationThresholds(
    min_penetration_atr=0.08,
    min_notional_burst=1.05,
    rejection_flow_min=0.12,
    rejection_efficiency_max=0.38,
    rejection_depth_refill_min=0.01,
    acceptance_flow_min=0.10,
    acceptance_efficiency_min=0.45,
    acceptance_depth_withdrawal_min=0.01,
    acceptance_close_atr=0.05,
    acceptance_close_location=0.62,
)


class LogicTest(unittest.TestCase):
    def test_rejection_and_mirror_are_symmetric(self) -> None:
        high = SweepEvidence(
            kind="HIGH",
            pool_level=100.0,
            high=100.5,
            low=99.7,
            close=99.9,
            open=99.8,
            atr=2.0,
            flow_15s=0.25,
            flow_60s=0.18,
            notional_burst=1.4,
            efficiency_60s=0.20,
            depth_imbalance_1=-0.10,
            bid_depth_change_1m=-0.02,
            ask_depth_change_1m=0.04,
        )
        self.assertEqual(classify_sweep(high, THRESHOLDS), "REJECTION")
        self.assertEqual(classify_sweep(mirrored_evidence(high), THRESHOLDS), "REJECTION")

    def test_acceptance_and_mirror_are_symmetric(self) -> None:
        high = SweepEvidence(
            kind="HIGH",
            pool_level=100.0,
            high=101.0,
            low=99.8,
            close=100.9,
            open=100.0,
            atr=2.0,
            flow_15s=0.28,
            flow_60s=0.20,
            notional_burst=1.5,
            efficiency_60s=0.70,
            depth_imbalance_1=0.20,
            bid_depth_change_1m=0.03,
            ask_depth_change_1m=-0.05,
        )
        self.assertEqual(classify_sweep(high, THRESHOLDS), "ACCEPTANCE")
        self.assertEqual(classify_sweep(mirrored_evidence(high), THRESHOLDS), "ACCEPTANCE")

    def test_unresolved_when_flow_and_price_disagree(self) -> None:
        evidence = SweepEvidence(
            kind="HIGH",
            pool_level=100.0,
            high=100.5,
            low=99.8,
            close=100.3,
            open=100.0,
            atr=2.0,
            flow_15s=-0.20,
            flow_60s=-0.18,
            notional_burst=1.5,
            efficiency_60s=0.7,
            depth_imbalance_1=0.0,
            bid_depth_change_1m=0.0,
            ask_depth_change_1m=-0.04,
        )
        self.assertIsNone(classify_sweep(evidence, THRESHOLDS))

    def test_risk_and_target_algebra(self) -> None:
        loss = planned_loss_per_unit(
            entry=100.0,
            stop=99.0,
            side=1,
            cost_rate=0.00075,
            adverse_slippage_rate=0.00025,
        )
        self.assertGreater(loss, 1.0)
        target = cost_aware_target(100.0, 1, loss, 2.0, 0.00075)
        self.assertAlmostEqual(net_r_at_price(100.0, target, 1, loss, 0.00075), 2.0, places=9)

    def test_nearest_valid_pool_is_used(self) -> None:
        loss = 1.0
        pools = [
            Pool("near", "HIGH", 100.5, 1, 1, "SWING"),
            Pool("valid", "HIGH", 102.0, 1, 1, "SESSION"),
        ]
        target, source, r_value = choose_liquidity_target(
            entry=100.0,
            side=1,
            pools=pools,
            planned_loss=loss,
            cost_rate=0.0,
            min_net_r=1.0,
            max_net_r=3.0,
            fallback_net_r=1.8,
        )
        self.assertEqual(target, 102.0)
        self.assertEqual(source, "POOL:valid")
        self.assertEqual(r_value, 2.0)

    def test_pivot_requires_completed_unique_center(self) -> None:
        self.assertTrue(is_confirmed_pivot([1, 2, 5, 3, 2], span=2, kind="HIGH"))
        self.assertTrue(is_confirmed_pivot([5, 3, 1, 2, 4], span=2, kind="LOW"))
        self.assertFalse(is_confirmed_pivot([1, 5, 5, 3, 2], span=2, kind="HIGH"))


if __name__ == "__main__":
    unittest.main()

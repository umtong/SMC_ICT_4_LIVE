from __future__ import annotations

from decimal import Decimal
import os
from types import SimpleNamespace
import unittest

from c10_v51_overlay import size_dependent_reward_certificate


def plan(gain: str = "3", loss: str = "1") -> SimpleNamespace:
    return SimpleNamespace(
        gain_per_unit=float(gain),
        loss_per_unit=float(loss),
    )


def solution(
    *,
    impact: str,
    loss: str,
    quantity: str = "10",
) -> SimpleNamespace:
    return SimpleNamespace(
        impact_per_side=Decimal(impact),
        per_unit_loss=Decimal(loss),
        quantity=Decimal(quantity),
        participation=Decimal("0.01"),
        liquidity_notional=Decimal("100000"),
        atr=Decimal("2"),
    )


class SizeDependentRewardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = os.environ.get("C10_V51_SIZE_DEPENDENT_REWARD")
        os.environ["C10_V51_SIZE_DEPENDENT_REWARD"] = "1"

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("C10_V51_SIZE_DEPENDENT_REWARD", None)
        else:
            os.environ["C10_V51_SIZE_DEPENDENT_REWARD"] = self.previous

    def test_same_impact_is_charged_on_entry_and_target(self) -> None:
        decision = size_dependent_reward_certificate(
            plan("3", "1"),
            solution(impact="0.5", loss="2"),
            minimum_net_r=1.0,
        )
        self.assertTrue(decision.approved)
        self.assertEqual(decision.impact_adjusted_gain_per_unit, Decimal("2.0"))
        self.assertEqual(decision.impact_adjusted_loss_per_unit, Decimal("2"))
        self.assertEqual(decision.impact_adjusted_net_r, Decimal("1.0"))

    def test_existing_minimum_r_is_enforced_after_impact(self) -> None:
        decision = size_dependent_reward_certificate(
            plan("3", "1"),
            solution(impact="0.5", loss="2"),
            minimum_net_r=1.25,
        )
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "INSUFFICIENT_SIZE_DEPENDENT_ALL_COST_R",
        )

    def test_nonpositive_target_gain_fails_closed(self) -> None:
        decision = size_dependent_reward_certificate(
            plan("1", "1"),
            solution(impact="0.5", loss="2"),
            minimum_net_r=1.0,
        )
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "NONPOSITIVE_SIZE_DEPENDENT_ALL_COST_GAIN",
        )

    def test_disabled_variant_is_exact_ablation(self) -> None:
        os.environ["C10_V51_SIZE_DEPENDENT_REWARD"] = "0"
        decision = size_dependent_reward_certificate(
            plan("1", "1"),
            solution(impact="0.5", loss="2"),
            minimum_net_r=10.0,
        )
        self.assertTrue(decision.approved)
        self.assertEqual(
            decision.reason,
            "SIZE_DEPENDENT_REWARD_CERTIFICATE_DISABLED",
        )


if __name__ == "__main__":
    unittest.main()

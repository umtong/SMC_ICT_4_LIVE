"""Contracts for the predeclared candidate-08 family-ablation selector."""

from __future__ import annotations

import unittest

from auction_family_ablation_decision import (
    FAILED_AUCTION_FAMILY,
    IMPLEMENTATION_REVISION,
    INITIATIVE_FAMILY,
    select_single_family_ablation,
)


def summary(
    *,
    initiative_trades: int = 2,
    initiative_pnl: float = 100.0,
    failed_trades: int = 2,
    failed_pnl: float = -50.0,
    suite: str = "first",
) -> dict:
    return {
        "suite": suite,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "auction_family_mode": "both",
        "diagnostic_family_ablation": False,
        "scenario_attribution_passed": True,
        "suite_gate_passed": False,
        "closed_trades": initiative_trades + failed_trades,
        "scenario_family_results": {
            INITIATIVE_FAMILY: {
                "signals": initiative_trades + 1,
                "closed_trades": initiative_trades,
                "wins": int(initiative_pnl > 0),
                "realized_pnl_usdt": initiative_pnl,
            },
            FAILED_AUCTION_FAMILY: {
                "signals": failed_trades + 1,
                "closed_trades": failed_trades,
                "wins": int(failed_pnl > 0),
                "realized_pnl_usdt": failed_pnl,
            },
        },
    }


class FamilyAblationDecisionContracts(unittest.TestCase):
    def test_positive_initiative_and_negative_failed_auction_selects_one_mode(self) -> None:
        decision = select_single_family_ablation(summary())
        self.assertTrue(decision.selected)
        self.assertEqual(decision.family_mode, "initiative_only")
        self.assertEqual(decision.retained_family, INITIATIVE_FAMILY)
        self.assertEqual(decision.removed_family, FAILED_AUCTION_FAMILY)

    def test_positive_failed_auction_and_negative_initiative_selects_opposite_mode(self) -> None:
        decision = select_single_family_ablation(
            summary(initiative_pnl=-100.0, failed_pnl=50.0)
        )
        self.assertTrue(decision.selected)
        self.assertEqual(decision.family_mode, "failed_auction_only")
        self.assertEqual(decision.retained_family, FAILED_AUCTION_FAMILY)
        self.assertEqual(decision.removed_family, INITIATIVE_FAMILY)

    def test_both_negative_has_no_cherry_picked_ablation(self) -> None:
        decision = select_single_family_ablation(
            summary(initiative_pnl=-100.0, failed_pnl=-50.0)
        )
        self.assertFalse(decision.selected)
        self.assertEqual(decision.reason, "BOTH_FAMILIES_ECONOMICALLY_NEGATIVE")

    def test_both_positive_has_no_destructive_family(self) -> None:
        decision = select_single_family_ablation(
            summary(initiative_pnl=100.0, failed_pnl=50.0)
        )
        self.assertFalse(decision.selected)
        self.assertEqual(decision.reason, "NO_DESTRUCTIVE_FAMILY_TO_ABLATE")

    def test_untraded_other_family_cannot_be_claimed_as_survivor(self) -> None:
        decision = select_single_family_ablation(
            summary(
                initiative_trades=2,
                initiative_pnl=-100.0,
                failed_trades=0,
                failed_pnl=0.0,
            )
        )
        self.assertFalse(decision.selected)
        self.assertEqual(
            decision.reason,
            "SURVIVING_FAMILY_NOT_INDEPENDENTLY_EXECUTED",
        )

    def test_no_trades_is_opportunity_failure_not_ablation_path(self) -> None:
        decision = select_single_family_ablation(
            summary(
                initiative_trades=0,
                initiative_pnl=0.0,
                failed_trades=0,
                failed_pnl=0.0,
            )
        )
        self.assertFalse(decision.selected)
        self.assertEqual(decision.reason, "NO_EXECUTED_FAMILY_OPPORTUNITY")

    def test_passed_base_is_never_ablated(self) -> None:
        value = summary()
        value["suite_gate_passed"] = True
        decision = select_single_family_ablation(value)
        self.assertFalse(decision.selected)
        self.assertEqual(decision.reason, "BASE_GATE_ALREADY_PASSED")

    def test_nested_ablation_is_rejected(self) -> None:
        value = summary()
        value["auction_family_mode"] = "initiative_only"
        value["diagnostic_family_ablation"] = True
        decision = select_single_family_ablation(value)
        self.assertFalse(decision.selected)
        self.assertEqual(decision.reason, "BASE_DID_NOT_INCLUDE_BOTH_FAMILIES")

    def test_incomplete_attribution_is_rejected(self) -> None:
        value = summary()
        value["scenario_attribution_passed"] = False
        decision = select_single_family_ablation(value)
        self.assertFalse(decision.selected)
        self.assertEqual(decision.reason, "SCENARIO_ATTRIBUTION_INCOMPLETE")

    def test_wrong_implementation_revision_cannot_drive_ablation(self) -> None:
        value = summary()
        value["implementation_revision"] = "some-other-code"
        decision = select_single_family_ablation(value)
        self.assertFalse(decision.selected)
        self.assertEqual(decision.reason, "IMPLEMENTATION_REVISION_NOT_EXACT")

    def test_family_count_mismatch_is_rejected(self) -> None:
        value = summary()
        value["closed_trades"] += 1
        decision = select_single_family_ablation(value)
        self.assertFalse(decision.selected)
        self.assertEqual(decision.reason, "FAMILY_CLOSED_TRADE_COUNT_MISMATCH")

    def test_screen_suite_uses_the_same_predeclared_rule(self) -> None:
        decision = select_single_family_ablation(summary(suite="screen"))
        self.assertTrue(decision.selected)
        self.assertEqual(decision.suite, "screen")


if __name__ == "__main__":
    unittest.main(verbosity=2)

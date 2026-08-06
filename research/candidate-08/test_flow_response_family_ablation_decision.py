"""Contracts for the single permitted flow-response family diagnostic."""

from __future__ import annotations

import unittest

from flow_response_family_ablation_decision import (
    ABSORPTION_FAMILY,
    IMPLEMENTATION_REVISION,
    INITIATIVE_FAMILY,
    select_single_family_ablation,
)


def _summary(
    *,
    initiative_pnl: float = 500.0,
    absorption_pnl: float = -300.0,
    initiative_trades: int = 3,
    absorption_trades: int = 2,
) -> dict:
    closed = initiative_trades + absorption_trades
    return {
        "suite": "first",
        "implementation_revision": IMPLEMENTATION_REVISION,
        "flow_response_family_mode": "both",
        "diagnostic_family_ablation": False,
        "scenario_attribution_passed": True,
        "suite_gate_passed": False,
        "suite_gate_checks": {
            "complete_auction_scenario_attribution": True,
            "complete_post_run_trade_path_diagnostics": True,
            "base_contract_includes_both_auction_families": True,
            "base_contract_includes_both_flow_response_families": True,
        },
        "closed_trades": closed,
        "trade_path_diagnostic_summary": {
            "records": closed,
            "complete_records": closed,
        },
        "scenario_family_results": {
            INITIATIVE_FAMILY: {
                "signals": 5,
                "closed_trades": initiative_trades,
                "wins": 2,
                "realized_pnl_usdt": initiative_pnl,
            },
            ABSORPTION_FAMILY: {
                "signals": 4,
                "closed_trades": absorption_trades,
                "wins": 1,
                "realized_pnl_usdt": absorption_pnl,
            },
        },
    }


class FlowResponseAblationDecisionContracts(unittest.TestCase):
    def test_one_positive_and_one_negative_family_selects_only_the_positive_family(self) -> None:
        decision = select_single_family_ablation(_summary())
        self.assertTrue(decision.selected)
        self.assertEqual(decision.family_mode, "initiative_only")
        self.assertEqual(decision.retained_family, INITIATIVE_FAMILY)
        self.assertEqual(decision.removed_family, ABSORPTION_FAMILY)

    def test_absorption_can_be_retained_under_the_same_frozen_rule(self) -> None:
        decision = select_single_family_ablation(
            _summary(initiative_pnl=-100.0, absorption_pnl=250.0)
        )
        self.assertTrue(decision.selected)
        self.assertEqual(decision.family_mode, "absorption_only")
        self.assertEqual(decision.retained_family, ABSORPTION_FAMILY)

    def test_both_negative_is_a_discard_not_a_less_bad_family_search(self) -> None:
        decision = select_single_family_ablation(
            _summary(initiative_pnl=-100.0, absorption_pnl=-50.0)
        )
        self.assertFalse(decision.selected)
        self.assertEqual(decision.reason, "BOTH_FAMILIES_ECONOMICALLY_NEGATIVE")

    def test_both_positive_does_not_remove_a_working_family(self) -> None:
        decision = select_single_family_ablation(
            _summary(initiative_pnl=100.0, absorption_pnl=50.0)
        )
        self.assertFalse(decision.selected)
        self.assertEqual(decision.reason, "NO_DESTRUCTIVE_FAMILY_TO_ABLATE")

    def test_untraded_family_is_not_treated_as_independently_negative(self) -> None:
        decision = select_single_family_ablation(
            _summary(absorption_trades=0, absorption_pnl=0.0)
        )
        self.assertFalse(decision.selected)
        self.assertEqual(
            decision.reason,
            "SURVIVING_FAMILY_NOT_INDEPENDENTLY_EXECUTED",
        )

    def test_invalid_evidence_contracts_block_selection(self) -> None:
        cases = (
            ("implementation_revision", "wrong", "IMPLEMENTATION_REVISION_NOT_EXACT"),
            ("flow_response_family_mode", "initiative_only", "BASE_DID_NOT_INCLUDE_BOTH_FAMILIES"),
            ("diagnostic_family_ablation", True, "NESTED_ABLATION_FORBIDDEN"),
            ("scenario_attribution_passed", False, "SCENARIO_ATTRIBUTION_INCOMPLETE"),
            ("suite_gate_passed", True, "BASE_GATE_ALREADY_PASSED"),
        )
        for key, value, reason in cases:
            with self.subTest(key=key):
                summary = _summary()
                summary[key] = value
                decision = select_single_family_ablation(summary)
                self.assertFalse(decision.selected)
                self.assertEqual(decision.reason, reason)

    def test_incomplete_path_or_attribution_evidence_blocks_selection(self) -> None:
        summary = _summary()
        summary["suite_gate_checks"]["complete_post_run_trade_path_diagnostics"] = False
        decision = select_single_family_ablation(summary)
        self.assertFalse(decision.selected)
        self.assertEqual(decision.reason, "BASE_EVIDENCE_CHECKS_INCOMPLETE")

        summary = _summary()
        summary["trade_path_diagnostic_summary"]["complete_records"] -= 1
        decision = select_single_family_ablation(summary)
        self.assertFalse(decision.selected)
        self.assertEqual(decision.reason, "TRADE_PATH_DIAGNOSTIC_COUNTS_INCOMPLETE")

        summary = _summary()
        summary["suite_gate_checks"] = None
        decision = select_single_family_ablation(summary)
        self.assertFalse(decision.selected)
        self.assertEqual(decision.reason, "BASE_SUITE_GATE_CHECKS_MISSING")

    def test_family_count_mismatch_blocks_selection(self) -> None:
        summary = _summary()
        summary["closed_trades"] += 1
        summary["trade_path_diagnostic_summary"]["records"] += 1
        summary["trade_path_diagnostic_summary"]["complete_records"] += 1
        decision = select_single_family_ablation(summary)
        self.assertFalse(decision.selected)
        self.assertEqual(decision.reason, "FAMILY_CLOSED_TRADE_COUNT_MISMATCH")


if __name__ == "__main__":
    unittest.main(verbosity=2)

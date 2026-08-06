"""Contracts for candidate-08 single-family diagnostic evaluation."""

from __future__ import annotations

import unittest

from auction_diagnostic_evaluation import evaluate_diagnostic_summary
from auction_family_ablation_decision import (
    FAILED_AUCTION_FAMILY,
    IMPLEMENTATION_REVISION,
    INITIATIVE_FAMILY,
)


def _summary(*, mode: str = "initiative_only") -> dict:
    retained = (
        INITIATIVE_FAMILY
        if mode == "initiative_only"
        else FAILED_AUCTION_FAMILY
    )
    removed = (
        FAILED_AUCTION_FAMILY
        if retained == INITIATIVE_FAMILY
        else INITIATIVE_FAMILY
    )
    return {
        "implementation_revision": IMPLEMENTATION_REVISION,
        "auction_family_mode": mode,
        "diagnostic_family_ablation": True,
        "promotable": False,
        "suite_gate_passed": False,
        "scenario_attribution_passed": True,
        "closed_trades": 3,
        "suite_gate_checks": {
            "all_signal_times_processed": True,
            "all_submitted_entries_observed": True,
            "closed_trades_matched_to_intents": True,
            "cost_after_total_return_positive": True,
            "entry_causality": True,
            "fill_adjusted_risk_budget_respected": True,
            "funding_cost_state_is_causal_and_complete": True,
            "minimum_closed_trades": True,
            "no_execution_failures": True,
            "no_residual_exposure": True,
            "planned_risk_budget_respected": True,
            "position_exit_causality": True,
            "realized_loss_budget_respected": True,
            "complete_auction_scenario_attribution": True,
            "base_contract_includes_both_auction_families": False,
        },
        "scenario_family_results": {
            retained: {
                "signals": 3,
                "closed_trades": 3,
                "wins": 2,
                "realized_pnl_usdt": 500.0,
            },
            removed: {
                "signals": 0,
                "closed_trades": 0,
                "wins": 0,
                "realized_pnl_usdt": 0.0,
            },
        },
    }


class DiagnosticEvaluationContracts(unittest.TestCase):
    def test_valid_diagnostic_supports_rebuild_but_never_promotion(self) -> None:
        evaluation = evaluate_diagnostic_summary(
            _summary(),
            expected_mode="initiative_only",
        )
        self.assertTrue(evaluation["evidence_contract_passed"])
        self.assertTrue(evaluation["economic_checks_passed"])
        self.assertTrue(evaluation["new_base_rebuild_supported"])
        self.assertFalse(evaluation["promotion_permitted"])
        self.assertEqual(evaluation["retained_family"], INITIATIVE_FAMILY)
        self.assertEqual(evaluation["removed_family"], FAILED_AUCTION_FAMILY)

    def test_wrong_expected_mode_blocks_evidence_contract(self) -> None:
        evaluation = evaluate_diagnostic_summary(
            _summary(mode="initiative_only"),
            expected_mode="failed_auction_only",
        )
        self.assertFalse(
            evaluation["evidence_contract_checks"]["family_mode_exact"]
        )
        self.assertFalse(evaluation["evidence_contract_passed"])
        self.assertFalse(evaluation["new_base_rebuild_supported"])

    def test_promotable_or_open_suite_gate_is_invalid_diagnostic_evidence(self) -> None:
        value = _summary()
        value["promotable"] = True
        value["suite_gate_passed"] = True
        evaluation = evaluate_diagnostic_summary(
            value,
            expected_mode="initiative_only",
        )
        checks = evaluation["evidence_contract_checks"]
        self.assertFalse(checks["not_promotable"])
        self.assertFalse(checks["suite_gate_remains_closed"])
        self.assertFalse(evaluation["evidence_contract_passed"])

    def test_removed_family_signal_or_trade_leak_blocks_rebuild(self) -> None:
        value = _summary()
        value["scenario_family_results"][FAILED_AUCTION_FAMILY]["signals"] = 1
        value["scenario_family_results"][FAILED_AUCTION_FAMILY]["closed_trades"] = 1
        evaluation = evaluate_diagnostic_summary(
            value,
            expected_mode="initiative_only",
        )
        checks = evaluation["evidence_contract_checks"]
        self.assertFalse(checks["removed_family_has_no_signals"])
        self.assertFalse(checks["removed_family_has_no_closed_trades"])
        self.assertFalse(evaluation["new_base_rebuild_supported"])

    def test_zero_opportunity_retained_family_blocks_rebuild(self) -> None:
        value = _summary()
        value["closed_trades"] = 0
        retained = value["scenario_family_results"][INITIATIVE_FAMILY]
        retained["signals"] = 0
        retained["closed_trades"] = 0
        evaluation = evaluate_diagnostic_summary(
            value,
            expected_mode="initiative_only",
        )
        checks = evaluation["evidence_contract_checks"]
        self.assertFalse(checks["retained_family_has_signals"])
        self.assertFalse(checks["retained_family_has_closed_trades"])
        self.assertFalse(evaluation["new_base_rebuild_supported"])

    def test_extra_or_unclassified_family_blocks_rebuild(self) -> None:
        value = _summary()
        value["scenario_family_results"]["UNCLASSIFIED_AUCTION_SCENARIO"] = {
            "signals": 0,
            "closed_trades": 0,
        }
        evaluation = evaluate_diagnostic_summary(
            value,
            expected_mode="initiative_only",
        )
        self.assertFalse(
            evaluation["evidence_contract_checks"]["scenario_family_set_exact"]
        )
        self.assertFalse(evaluation["new_base_rebuild_supported"])

    def test_reported_trade_count_mismatch_blocks_rebuild(self) -> None:
        value = _summary()
        value["closed_trades"] = 4
        evaluation = evaluate_diagnostic_summary(
            value,
            expected_mode="initiative_only",
        )
        self.assertFalse(
            evaluation["evidence_contract_checks"][
                "reported_closed_trades_match_retained_family"
            ]
        )
        self.assertFalse(evaluation["new_base_rebuild_supported"])

    def test_wrong_implementation_revision_blocks_rebuild(self) -> None:
        value = _summary()
        value["implementation_revision"] = "other-revision"
        evaluation = evaluate_diagnostic_summary(
            value,
            expected_mode="initiative_only",
        )
        self.assertFalse(
            evaluation["evidence_contract_checks"][
                "implementation_revision_exact"
            ]
        )
        self.assertFalse(evaluation["new_base_rebuild_supported"])

    def test_empty_economic_check_set_is_not_vacuously_true(self) -> None:
        value = _summary()
        value["suite_gate_checks"] = {
            "base_contract_includes_both_auction_families": False
        }
        evaluation = evaluate_diagnostic_summary(
            value,
            expected_mode="initiative_only",
        )
        self.assertFalse(
            evaluation["evidence_contract_checks"][
                "economic_check_set_nonempty"
            ]
        )
        self.assertFalse(evaluation["economic_checks_passed"])
        self.assertFalse(evaluation["new_base_rebuild_supported"])

    def test_any_failed_economic_contract_blocks_rebuild(self) -> None:
        value = _summary()
        value["suite_gate_checks"]["cost_after_total_return_positive"] = False
        evaluation = evaluate_diagnostic_summary(
            value,
            expected_mode="initiative_only",
        )
        self.assertTrue(evaluation["evidence_contract_passed"])
        self.assertFalse(evaluation["economic_checks_passed"])
        self.assertFalse(evaluation["new_base_rebuild_supported"])

    def test_failed_auction_mode_maps_families_exactly(self) -> None:
        evaluation = evaluate_diagnostic_summary(
            _summary(mode="failed_auction_only"),
            expected_mode="failed_auction_only",
        )
        self.assertEqual(evaluation["retained_family"], FAILED_AUCTION_FAMILY)
        self.assertEqual(evaluation["removed_family"], INITIATIVE_FAMILY)
        self.assertTrue(evaluation["new_base_rebuild_supported"])

    def test_unknown_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            evaluate_diagnostic_summary(
                _summary(),
                expected_mode="fit_best_family",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

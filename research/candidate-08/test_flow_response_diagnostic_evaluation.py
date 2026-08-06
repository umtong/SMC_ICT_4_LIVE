"""Contracts for flow-response single-family diagnostic evaluation."""

from __future__ import annotations

import unittest

from aggtrade_flow_response_auction_signals_v2 import (
    ABSORPTION_FAMILY,
    IMPLEMENTATION_REVISION,
    INITIATIVE_FAMILY,
)
from flow_response_diagnostic_evaluation import evaluate_diagnostic_summary


def _summary(*, mode: str = "initiative_only") -> dict:
    retained = INITIATIVE_FAMILY if mode == "initiative_only" else ABSORPTION_FAMILY
    removed = ABSORPTION_FAMILY if retained == INITIATIVE_FAMILY else INITIATIVE_FAMILY
    return {
        "implementation_revision": IMPLEMENTATION_REVISION,
        "flow_response_family_mode": mode,
        "diagnostic_family_ablation": True,
        "promotable": False,
        "suite_gate_passed": False,
        "scenario_attribution_passed": True,
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
            "base_contract_includes_both_flow_response_families": False,
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


class FlowResponseDiagnosticEvaluationContracts(unittest.TestCase):
    def test_valid_diagnostic_supports_rebuild_but_never_direct_promotion(self) -> None:
        evaluation = evaluate_diagnostic_summary(
            _summary(),
            expected_mode="initiative_only",
        )
        self.assertTrue(evaluation["evidence_contract_passed"])
        self.assertTrue(evaluation["economic_checks_passed"])
        self.assertTrue(evaluation["new_base_rebuild_supported"])
        self.assertFalse(evaluation["promotion_permitted"])

    def test_wrong_mode_or_revision_blocks_rebuild(self) -> None:
        evaluation = evaluate_diagnostic_summary(
            _summary(),
            expected_mode="absorption_only",
        )
        self.assertFalse(evaluation["evidence_contract_passed"])
        value = _summary()
        value["implementation_revision"] = "wrong"
        self.assertFalse(
            evaluate_diagnostic_summary(
                value,
                expected_mode="initiative_only",
            )["new_base_rebuild_supported"]
        )

    def test_removed_family_leak_blocks_rebuild(self) -> None:
        value = _summary()
        value["scenario_family_results"][ABSORPTION_FAMILY]["signals"] = 1
        evaluation = evaluate_diagnostic_summary(
            value,
            expected_mode="initiative_only",
        )
        self.assertFalse(
            evaluation["evidence_contract_checks"]["removed_family_has_no_signals"]
        )
        self.assertFalse(evaluation["new_base_rebuild_supported"])

    def test_any_failed_underlying_economic_contract_blocks_rebuild(self) -> None:
        value = _summary()
        value["suite_gate_checks"]["cost_after_total_return_positive"] = False
        evaluation = evaluate_diagnostic_summary(
            value,
            expected_mode="initiative_only",
        )
        self.assertTrue(evaluation["evidence_contract_passed"])
        self.assertFalse(evaluation["economic_checks_passed"])
        self.assertFalse(evaluation["new_base_rebuild_supported"])

    def test_open_gate_or_promotable_flag_invalidates_diagnostic_evidence(self) -> None:
        value = _summary()
        value["suite_gate_passed"] = True
        value["promotable"] = True
        evaluation = evaluate_diagnostic_summary(
            value,
            expected_mode="initiative_only",
        )
        self.assertFalse(evaluation["evidence_contract_passed"])

    def test_unknown_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            evaluate_diagnostic_summary(
                _summary(),
                expected_mode="fit_best_family",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

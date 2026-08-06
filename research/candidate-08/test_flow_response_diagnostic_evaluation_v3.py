"""V3 evidence contracts for single-family diagnostic evaluation."""

from __future__ import annotations

import unittest

from aggtrade_flow_response_auction_signals_v3 import (
    ABSORPTION_FAMILY,
    IMPLEMENTATION_REVISION,
    INITIATIVE_FAMILY,
)
from flow_response_diagnostic_evaluation import evaluate_diagnostic_summary
from flow_response_trade_path_diagnostics_v2 import DIAGNOSTIC_REVISION


def _summary(*, mode: str = "initiative_only", economic_pass: bool = True) -> dict:
    retained = INITIATIVE_FAMILY if mode == "initiative_only" else ABSORPTION_FAMILY
    removed = ABSORPTION_FAMILY if retained == INITIATIVE_FAMILY else INITIATIVE_FAMILY
    closed = 3
    return {
        "implementation_revision": IMPLEMENTATION_REVISION,
        "ten_second_cadence_contract": "EXACT_CONSECUTIVE_10_SECONDS",
        "trade_path_diagnostic_revision": DIAGNOSTIC_REVISION,
        "flow_response_family_mode": mode,
        "diagnostic_family_ablation": True,
        "promotable": False,
        "suite_gate_passed": False,
        "scenario_attribution_passed": True,
        "closed_trades": closed,
        "trade_path_diagnostic_summary": {
            "records": closed,
            "complete_records": closed,
            "diagnostic_revision_counts": {DIAGNOSTIC_REVISION: closed},
            "expected_diagnostic_revision": DIAGNOSTIC_REVISION,
        },
        "suite_gate_checks": {
            "all_signal_times_processed": True,
            "all_submitted_entries_observed": True,
            "closed_trades_matched_to_intents": True,
            "cost_after_total_return_positive": economic_pass,
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
            "complete_post_run_trade_path_diagnostics": True,
            "base_contract_includes_both_auction_families": False,
            "base_contract_includes_both_flow_response_families": False,
        },
        "scenario_family_results": {
            retained: {
                "signals": 3,
                "closed_trades": closed,
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


class FlowResponseV3DiagnosticEvaluationContracts(unittest.TestCase):
    def test_complete_v3_diagnostic_supports_rebuild_but_not_promotion(self) -> None:
        evaluation = evaluate_diagnostic_summary(
            _summary(),
            expected_mode="initiative_only",
        )
        self.assertTrue(evaluation["evidence_contract_passed"])
        self.assertTrue(evaluation["economic_checks_passed"])
        self.assertTrue(evaluation["new_base_rebuild_supported"])
        self.assertFalse(evaluation["promotion_permitted"])
        self.assertEqual(
            evaluation["trade_path_diagnostic_revision"],
            DIAGNOSTIC_REVISION,
        )

    def test_cadence_or_path_revision_mismatch_is_evidence_failure(self) -> None:
        for key, value, check in (
            (
                "ten_second_cadence_contract",
                "ALLOW_GAPS",
                "ten_second_cadence_exact",
            ),
            (
                "trade_path_diagnostic_revision",
                "old",
                "path_diagnostic_revision_exact",
            ),
        ):
            with self.subTest(key=key):
                summary = _summary()
                summary[key] = value
                evaluation = evaluate_diagnostic_summary(
                    summary,
                    expected_mode="initiative_only",
                )
                self.assertFalse(evaluation["evidence_contract_checks"][check])
                self.assertFalse(evaluation["new_base_rebuild_supported"])

    def test_path_revision_count_mismatch_is_not_economic_failure(self) -> None:
        summary = _summary()
        summary["trade_path_diagnostic_summary"]["diagnostic_revision_counts"] = {
            DIAGNOSTIC_REVISION: 2,
            "old": 1,
        }
        evaluation = evaluate_diagnostic_summary(
            summary,
            expected_mode="initiative_only",
        )
        self.assertFalse(
            evaluation["evidence_contract_checks"]["trade_path_revision_counts_exact"]
        )
        self.assertFalse(evaluation["evidence_contract_passed"])
        self.assertTrue(evaluation["economic_checks_passed"])

    def test_clean_negative_economics_fails_rebuild_without_corrupting_evidence(self) -> None:
        evaluation = evaluate_diagnostic_summary(
            _summary(economic_pass=False),
            expected_mode="initiative_only",
        )
        self.assertTrue(evaluation["evidence_contract_passed"])
        self.assertFalse(evaluation["economic_checks_passed"])
        self.assertFalse(evaluation["new_base_rebuild_supported"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

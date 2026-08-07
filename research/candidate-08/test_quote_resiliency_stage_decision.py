"""Pure decision contracts for staged quote-resiliency evidence."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from aggtrade_acceptance_risk_v2 import RISK_ACCOUNTING_REVISION
from flow_response_trade_path_diagnostics_v2 import DIAGNOSTIC_REVISION
from quote_resiliency_data_v2 import DATA_REVISION
from quote_resiliency_features_v3 import IMPLEMENTATION_REVISION as FEATURE_REVISION
from quote_resiliency_signals import (
    CONTINUATION_FAMILY,
    REVERSAL_FAMILY,
    SIGNAL_REVISION,
)
from quote_resiliency_stage_decision import (
    DECISION_REVISION,
    build_decision,
)
from quote_resiliency_strategy import EXECUTION_ADAPTER_REVISION
from run_quote_resiliency_nautilus import (
    BASE_ABLATION,
    CONFIG_IMPLEMENTATION_REVISION,
    OFI_ABLATION,
    RUNNER_REVISION,
)


class QuoteResiliencyStageDecisionContracts(unittest.TestCase):
    @staticmethod
    def _write(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    @staticmethod
    def _summary(*, diagnostic: bool, passed: bool, signals: int = 4, closed: int = 4) -> dict:
        path_summary = {
            "records": closed,
            "complete_records": closed,
            "diagnostic_revision_counts": {DIAGNOSTIC_REVISION: closed},
            "target_after_actual_close_count": 0,
            "target_after_invalidation_count": 0,
        }
        checks = {
            "all_signal_times_processed": True,
            "all_submitted_entries_observed": True,
            "base_contract_includes_both_auction_families": True,
            "base_contract_not_ablated": not diagnostic,
            "closed_trades_matched_to_intents": True,
            "complete_auction_scenario_attribution": True,
            "complete_post_run_trade_path_diagnostics": True,
            "cost_after_total_return_positive": passed,
            "entry_causality": True,
            "fill_adjusted_risk_budget_respected": True,
            "funding_cost_state_is_causal_and_complete": True,
            "minimum_closed_trades": closed >= 3,
            "no_execution_failures": True,
            "no_realized_risk_budget_breach": True,
            "no_residual_exposure": True,
            "no_unexpected_or_liquidation_closes": True,
            "planned_risk_budget_respected": True,
            "position_exit_causality": True,
            "realized_loss_budget_respected": True,
            "base_quote_ofi_confirmation_contract": not diagnostic,
            "both_quote_resiliency_families_enabled": True,
        }
        return {
            "candidate": "candidate-08-external-liquidity-quote-resiliency-v1",
            "suite": "first",
            "implementation_revision": CONFIG_IMPLEMENTATION_REVISION,
            "runner_revision": RUNNER_REVISION,
            "signal_revision": SIGNAL_REVISION,
            "feature_revision": FEATURE_REVISION,
            "data_revision": DATA_REVISION,
            "execution_adapter_revision": EXECUTION_ADAPTER_REVISION,
            "risk_accounting_revision": RISK_ACCOUNTING_REVISION,
            "trade_path_diagnostic_revision": DIAGNOSTIC_REVISION,
            "ten_second_cadence_contract": "EXACT_CONSECUTIVE_COMPLETED_10_SECONDS",
            "ablation": OFI_ABLATION if diagnostic else BASE_ABLATION,
            "diagnostic_only_ablation": diagnostic,
            "quote_ofi_confirmation_required": not diagnostic,
            "promotable": not diagnostic,
            "scenario_attribution_passed": True,
            "scenario_family_results": {
                REVERSAL_FAMILY: {
                    "signals": signals // 2,
                    "closed_trades": closed // 2,
                    "wins": closed // 2 if passed else 0,
                    "losses": 0 if passed else closed // 2,
                    "realized_pnl_usdt": 1000.0 if passed else -1000.0,
                },
                CONTINUATION_FAMILY: {
                    "signals": signals - signals // 2,
                    "closed_trades": closed - closed // 2,
                    "wins": closed - closed // 2 if passed else 0,
                    "losses": 0 if passed else closed - closed // 2,
                    "realized_pnl_usdt": 1000.0 if passed else -1000.0,
                },
            },
            "closed_trades": closed,
            "wins": closed if passed else 0,
            "combined_daily_geometric_growth": 0.02 if passed else -0.01,
            "suite_gate_checks": checks,
            "suite_gate_passed": passed and not diagnostic,
            "trade_path_diagnostic_summary": path_summary,
            "windows": [
                {
                    "name": "frozen-week-01",
                    "start": "2023-10-15T00:00:00Z",
                    "end": "2023-10-22T00:00:00Z",
                }
            ],
        }

    @staticmethod
    def _metrics(*, passed: bool, closed: int = 4) -> dict:
        return {
            "window": {"name": "frozen-week-01"},
            "position_metrics": {"closed_trades": closed},
            "total_return": 0.1 if passed else -0.1,
            "open_positions_after_run": 0,
            "open_orders_after_run": 0,
            "unprocessed_signal_times": 0,
            "first_window_gate_checks": {
                "all_signal_times_processed": True,
                "all_submitted_entries_observed": True,
                "closed_trades_matched_to_intents": True,
                "entry_causality": True,
                "funding_cost_state_is_causal_and_complete": True,
                "planned_risk_budget_respected": True,
                "position_exit_causality": True,
                "realized_loss_budget_respected": True,
                "no_residual_exposure": True,
                "no_unexpected_or_liquidation_closes": True,
            },
        }

    def _root(self, directory: str, summary: dict, metrics: dict) -> Path:
        root = Path(directory)
        self._write(root / "suite_metrics.json", summary)
        self._write(root / "frozen-week-01" / "metrics.json", metrics)
        return root

    def test_clean_positive_base_advances_to_screen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(
                directory,
                self._summary(diagnostic=False, passed=True),
                self._metrics(passed=True),
            )
            decision = build_decision(base_root=root)
            self.assertEqual(decision["decision_revision"], DECISION_REVISION)
            self.assertEqual(decision["decision"], "ADVANCE_TO_FROZEN_SCREEN_WEEKS")
            self.assertFalse(decision["single_ablation_permitted"])

    def test_clean_negative_base_permits_only_single_ablation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(
                directory,
                self._summary(diagnostic=False, passed=False),
                self._metrics(passed=False),
            )
            decision = build_decision(base_root=root)
            self.assertEqual(
                decision["decision"],
                "CLEAN_LOGIC_ECONOMIC_FAILURE_RUN_SINGLE_ABLATION",
            )
            self.assertTrue(decision["single_ablation_permitted"])

    def test_zero_signal_base_is_opportunity_failure_not_implementation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = self._summary(
                diagnostic=False,
                passed=False,
                signals=0,
                closed=0,
            )
            root = self._root(directory, summary, self._metrics(passed=False, closed=0))
            decision = build_decision(base_root=root)
            self.assertEqual(
                decision["decision"],
                "CLEAN_LOGIC_OPPORTUNITY_FAILURE_RUN_SINGLE_ABLATION",
            )

    def test_missing_path_evidence_blocks_ablation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = self._summary(diagnostic=False, passed=False)
            summary["trade_path_diagnostic_summary"]["complete_records"] = 3
            root = self._root(directory, summary, self._metrics(passed=False))
            decision = build_decision(base_root=root)
            self.assertEqual(decision["decision"], "BASE_EVIDENCE_CONTRACT_FAILURE")
            self.assertFalse(decision["single_ablation_permitted"])

    def test_clean_positive_diagnostic_requires_new_base_and_never_promotes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base_root = self._root(
                str(Path(directory) / "base"),
                self._summary(diagnostic=False, passed=False),
                self._metrics(passed=False),
            )
            ablation_summary = self._summary(diagnostic=True, passed=True)
            # A diagnostic suite gate is deliberately closed, while the retained economic checks
            # are all true.
            ablation_root = self._root(
                str(Path(directory) / "ablation"),
                ablation_summary,
                self._metrics(passed=True),
            )
            decision = build_decision(
                base_root=base_root,
                ablation_root=ablation_root,
            )
            self.assertEqual(
                decision["decision"],
                "ABLATION_SUPPORTS_NEW_BASE_REBUILD_NOT_PROMOTION",
            )
            self.assertFalse(decision["promotion_permitted_from_ablation"])
            self.assertFalse(decision["ablation"]["promotion_permitted"])

    def test_execution_risk_failure_does_not_open_logic_ablation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            metrics = self._metrics(passed=False)
            metrics["execution_contract_classification"] = {
                "candidate_gate_failure": True,
                "implementation_failure": False,
            }
            root = self._root(
                directory,
                self._summary(diagnostic=False, passed=False),
                metrics,
            )
            decision = build_decision(base_root=root)
            self.assertEqual(decision["decision"], "BASE_EXECUTION_RISK_FAILURE")
            self.assertFalse(decision["single_ablation_permitted"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

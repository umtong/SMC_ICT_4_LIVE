"""V3 exact-cadence orchestration contracts for staged flow-response validation."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from aggtrade_flow_response_auction_signals_v3 import (
    ABSORPTION_FAMILY,
    IMPLEMENTATION_REVISION,
    INITIATIVE_FAMILY,
)
from flow_response_trade_path_diagnostics_v2 import DIAGNOSTIC_REVISION
from run_flow_response_staged_validation_v2 import (
    PROTOCOL_REVISION,
    execute_staged_validation,
    validate_base_summary,
)


def _base_summary(*, suite: str, gate: bool, initiative_pnl: float, absorption_pnl: float) -> dict:
    closed = 5
    return {
        "suite": suite,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "ten_second_cadence_contract": "EXACT_CONSECUTIVE_10_SECONDS",
        "trade_path_diagnostic_revision": DIAGNOSTIC_REVISION,
        "flow_response_family_mode": "both",
        "auction_family_mode": "both",
        "diagnostic_family_ablation": False,
        "promotable": True,
        "scenario_attribution_passed": True,
        "scenario_attribution_checks": {
            "all_signals_attributed": True,
            "all_closed_trades_attributed": True,
            "no_unclassified_signals": True,
            "no_unclassified_closed_trades": True,
        },
        "suite_gate_passed": gate,
        "suite_gate_checks": {
            "complete_auction_scenario_attribution": True,
            "complete_post_run_trade_path_diagnostics": True,
            "base_contract_includes_both_auction_families": True,
            "base_contract_includes_both_flow_response_families": True,
        },
        "closed_trades": closed,
        "wins": 2,
        "combined_daily_geometric_growth": 0.02 if gate else -0.01,
        "trade_path_diagnostic_summary": {
            "records": closed,
            "complete_records": closed,
            "diagnostic_revision_counts": {DIAGNOSTIC_REVISION: closed},
            "expected_diagnostic_revision": DIAGNOSTIC_REVISION,
        },
        "scenario_family_results": {
            INITIATIVE_FAMILY: {
                "signals": 4,
                "closed_trades": 3,
                "wins": 2,
                "realized_pnl_usdt": initiative_pnl,
            },
            ABSORPTION_FAMILY: {
                "signals": 3,
                "closed_trades": 2,
                "wins": 0,
                "realized_pnl_usdt": absorption_pnl,
            },
        },
    }


def _diagnostic_summary(*, mode: str, economic_pass: bool) -> dict:
    retained = INITIATIVE_FAMILY if mode == "initiative_only" else ABSORPTION_FAMILY
    removed = ABSORPTION_FAMILY if retained == INITIATIVE_FAMILY else INITIATIVE_FAMILY
    closed = 3
    return {
        "suite": "first",
        "implementation_revision": IMPLEMENTATION_REVISION,
        "ten_second_cadence_contract": "EXACT_CONSECUTIVE_10_SECONDS",
        "trade_path_diagnostic_revision": DIAGNOSTIC_REVISION,
        "flow_response_family_mode": mode,
        "auction_family_mode": mode,
        "diagnostic_family_ablation": True,
        "promotable": False,
        "scenario_attribution_passed": True,
        "scenario_attribution_checks": {
            "all_signals_attributed": True,
            "all_closed_trades_attributed": True,
            "no_unclassified_signals": True,
            "no_unclassified_closed_trades": True,
        },
        "suite_gate_passed": False,
        "closed_trades": closed,
        "wins": 2,
        "combined_daily_geometric_growth": 0.02 if economic_pass else -0.01,
        "trade_path_diagnostic_summary": {
            "records": closed,
            "complete_records": closed,
            "diagnostic_revision_counts": {DIAGNOSTIC_REVISION: closed},
            "expected_diagnostic_revision": DIAGNOSTIC_REVISION,
        },
        "suite_gate_checks": {
            "minimum_closed_trades": True,
            "cost_after_total_return_positive": economic_pass,
            "no_execution_failures": True,
            "no_residual_exposure": True,
            "entry_causality": True,
            "planned_risk_budget_respected": True,
            "fill_adjusted_risk_budget_respected": True,
            "realized_loss_budget_respected": True,
            "funding_cost_state_is_causal_and_complete": True,
            "complete_auction_scenario_attribution": True,
            "complete_post_run_trade_path_diagnostics": True,
            "base_contract_includes_both_auction_families": False,
            "base_contract_includes_both_flow_response_families": False,
        },
        "scenario_family_results": {
            retained: {
                "signals": 4,
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


class _FakeRunner:
    def __init__(self, summaries: dict[tuple[str, str], dict]):
        self.summaries = summaries
        self.calls: list[tuple[str, str, bool]] = []

    def __call__(self, **kwargs):
        suite = str(kwargs["suite"])
        mode = str(kwargs["family_mode"])
        self.calls.append((suite, mode, kwargs.get("reuse_first_dir") is not None))
        output = Path(kwargs["output"])
        output.mkdir(parents=True, exist_ok=True)
        (output / "suite_metrics.json").write_text(
            json.dumps(self.summaries[(suite, mode)]),
            encoding="utf-8",
        )
        return 0


class FlowResponseV3StagedContracts(unittest.TestCase):
    def _execute(self, fake: _FakeRunner):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "evidence"
        placeholder = Path(temporary.name) / "placeholder"
        placeholder.write_text("x", encoding="utf-8")
        status, decision = execute_staged_validation(
            config=placeholder,
            pattern_config=placeholder,
            root=root,
            data_cache=Path(temporary.name) / "cache",
            runner=placeholder,
            run_suite=fake,
        )
        persisted = json.loads((root / "stage_decision.json").read_text(encoding="utf-8"))
        return status, decision, persisted

    def test_valid_base_summary_requires_exact_v3_and_path_revisions(self) -> None:
        summary = _base_summary(
            suite="first",
            gate=False,
            initiative_pnl=-100.0,
            absorption_pnl=-50.0,
        )
        self.assertEqual(validate_base_summary(summary, expected_suite="first"), ())
        summary["ten_second_cadence_contract"] = "ALLOW_GAPS"
        errors = validate_base_summary(summary, expected_suite="first")
        self.assertIn("TEN_SECOND_CADENCE_CONTRACT_NOT_EXACT", errors)

    def test_clean_both_negative_failure_is_persisted_as_v3_discard(self) -> None:
        fake = _FakeRunner(
            {
                ("first", "both"): _base_summary(
                    suite="first",
                    gate=False,
                    initiative_pnl=-100.0,
                    absorption_pnl=-50.0,
                )
            }
        )
        status, decision, persisted = self._execute(fake)
        self.assertEqual(status, 0)
        self.assertEqual(
            decision["decision"],
            "FIRST_WEEK_LOGIC_FAILURE_DISCARD_FLOW_RESPONSE_V3",
        )
        self.assertEqual(persisted["decision"], decision["decision"])
        self.assertEqual(persisted["protocol_revision"], PROTOCOL_REVISION)
        self.assertEqual(persisted["implementation_revision"], IMPLEMENTATION_REVISION)

    def test_one_positive_family_runs_one_diagnostic_but_never_promotes_it(self) -> None:
        fake = _FakeRunner(
            {
                ("first", "both"): _base_summary(
                    suite="first",
                    gate=False,
                    initiative_pnl=500.0,
                    absorption_pnl=-300.0,
                ),
                ("first", "initiative_only"): _diagnostic_summary(
                    mode="initiative_only",
                    economic_pass=True,
                ),
            }
        )
        status, decision, _persisted = self._execute(fake)
        self.assertEqual(status, 0)
        self.assertEqual(
            decision["decision"],
            "FIRST_WEEK_ABLATION_SUPPORTS_NEW_SINGLE_FAMILY_BASE_REBUILD",
        )
        self.assertFalse(decision["promotion_permitted_from_ablation"])
        self.assertEqual(
            fake.calls,
            [("first", "both", False), ("first", "initiative_only", False)],
        )

    def test_path_revision_count_mismatch_is_evidence_failure_before_ablation(self) -> None:
        summary = _base_summary(
            suite="first",
            gate=False,
            initiative_pnl=500.0,
            absorption_pnl=-300.0,
        )
        summary["trade_path_diagnostic_summary"]["diagnostic_revision_counts"] = {
            DIAGNOSTIC_REVISION: 4,
            "old": 1,
        }
        fake = _FakeRunner({("first", "both"): summary})
        status, decision, _persisted = self._execute(fake)
        self.assertEqual(status, 1)
        self.assertEqual(decision["decision"], "FIRST_WEEK_EVIDENCE_CONTRACT_FAILURE")
        self.assertIn(
            "TRADE_PATH_DIAGNOSTIC_REVISION_COUNTS_NOT_EXACT",
            decision["first_evidence_contract_errors"],
        )
        self.assertEqual(fake.calls, [("first", "both", False)])


if __name__ == "__main__":
    unittest.main(verbosity=2)

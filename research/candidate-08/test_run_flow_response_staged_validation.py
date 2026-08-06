"""Pure orchestration contracts for staged flow-response Nautilus validation."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from aggtrade_flow_response_auction_signals_v2 import (
    ABSORPTION_FAMILY,
    IMPLEMENTATION_REVISION,
    INITIATIVE_FAMILY,
)
from run_flow_response_staged_validation import execute_staged_validation


def _attribution_checks() -> dict[str, bool]:
    return {
        "all_signals_attributed": True,
        "all_closed_trades_attributed": True,
        "no_unclassified_signals": True,
        "no_unclassified_closed_trades": True,
    }


def _base_summary(
    *,
    suite: str,
    gate: bool,
    initiative_pnl: float,
    absorption_pnl: float,
) -> dict:
    return {
        "suite": suite,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "flow_response_family_mode": "both",
        "auction_family_mode": "both",
        "diagnostic_family_ablation": False,
        "promotable": True,
        "scenario_attribution_passed": True,
        "scenario_attribution_checks": _attribution_checks(),
        "suite_gate_passed": gate,
        "closed_trades": 5,
        "wins": 2,
        "combined_daily_geometric_growth": 0.02 if gate else -0.01,
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


def _diagnostic_summary(*, suite: str, mode: str, economic_pass: bool) -> dict:
    retained = INITIATIVE_FAMILY if mode == "initiative_only" else ABSORPTION_FAMILY
    removed = ABSORPTION_FAMILY if retained == INITIATIVE_FAMILY else INITIATIVE_FAMILY
    return {
        "suite": suite,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "flow_response_family_mode": mode,
        "auction_family_mode": mode,
        "diagnostic_family_ablation": True,
        "promotable": False,
        "scenario_attribution_passed": True,
        "scenario_attribution_checks": _attribution_checks(),
        "suite_gate_passed": False,
        "closed_trades": 3,
        "wins": 2,
        "combined_daily_geometric_growth": 0.02 if economic_pass else -0.01,
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
            "base_contract_includes_both_auction_families": False,
            "base_contract_includes_both_flow_response_families": False,
        },
        "scenario_family_results": {
            retained: {
                "signals": 4,
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


class _FakeRunner:
    def __init__(self, summaries: dict[tuple[str, str], dict], failures: set[tuple[str, str]] = set()):
        self.summaries = summaries
        self.failures = failures
        self.calls: list[tuple[str, str, bool]] = []

    def __call__(self, **kwargs):
        suite = str(kwargs["suite"])
        mode = str(kwargs["family_mode"])
        output = Path(kwargs["output"])
        self.calls.append((suite, mode, kwargs.get("reuse_first_dir") is not None))
        output.mkdir(parents=True, exist_ok=True)
        if (suite, mode) in self.failures:
            return 9
        summary = self.summaries[(suite, mode)]
        (output / "suite_metrics.json").write_text(
            json.dumps(summary),
            encoding="utf-8",
        )
        return 0


class FlowResponseStagedValidationContracts(unittest.TestCase):
    def _execute(self, fake: _FakeRunner):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "evidence"
        placeholder = Path(temporary.name) / "placeholder"
        placeholder.write_text("x", encoding="utf-8")
        return execute_staged_validation(
            config=placeholder,
            pattern_config=placeholder,
            root=root,
            data_cache=Path(temporary.name) / "cache",
            runner=placeholder,
            run_suite=fake,
        )

    def test_first_week_pass_runs_screen_and_promotes_only_after_screen_pass(self) -> None:
        fake = _FakeRunner(
            {
                ("first", "both"): _base_summary(
                    suite="first",
                    gate=True,
                    initiative_pnl=500.0,
                    absorption_pnl=100.0,
                ),
                ("screen", "both"): _base_summary(
                    suite="screen",
                    gate=True,
                    initiative_pnl=900.0,
                    absorption_pnl=200.0,
                ),
            }
        )
        status, decision = self._execute(fake)
        self.assertEqual(status, 0)
        self.assertEqual(decision["decision"], "PROMOTE_TO_PREDECLARED_LONG_EVALUATION")
        self.assertEqual(fake.calls, [("first", "both", False), ("screen", "both", True)])

    def test_both_negative_first_week_is_clean_logic_discard_without_ablation(self) -> None:
        fake = _FakeRunner(
            {
                ("first", "both"): _base_summary(
                    suite="first",
                    gate=False,
                    initiative_pnl=-500.0,
                    absorption_pnl=-100.0,
                )
            }
        )
        status, decision = self._execute(fake)
        self.assertEqual(status, 0)
        self.assertEqual(
            decision["decision"],
            "FIRST_WEEK_LOGIC_FAILURE_DISCARD_FLOW_RESPONSE_V2",
        )
        self.assertFalse(decision["ablation_selected"])
        self.assertEqual(len(fake.calls), 1)

    def test_clean_family_split_runs_exactly_one_diagnostic_without_promotion(self) -> None:
        fake = _FakeRunner(
            {
                ("first", "both"): _base_summary(
                    suite="first",
                    gate=False,
                    initiative_pnl=500.0,
                    absorption_pnl=-300.0,
                ),
                ("first", "initiative_only"): _diagnostic_summary(
                    suite="first",
                    mode="initiative_only",
                    economic_pass=True,
                ),
            }
        )
        status, decision = self._execute(fake)
        self.assertEqual(status, 0)
        self.assertTrue(decision["ablation_selected"])
        self.assertTrue(decision["ablation_new_base_rebuild_supported"])
        self.assertFalse(decision["promotion_permitted_from_ablation"])
        self.assertEqual(
            decision["decision"],
            "FIRST_WEEK_ABLATION_SUPPORTS_NEW_SINGLE_FAMILY_BASE_REBUILD",
        )
        self.assertEqual(
            fake.calls,
            [("first", "both", False), ("first", "initiative_only", False)],
        )

    def test_runner_failure_is_implementation_failure(self) -> None:
        fake = _FakeRunner({}, failures={("first", "both")})
        status, decision = self._execute(fake)
        self.assertEqual(status, 1)
        self.assertEqual(decision["decision"], "FIRST_WEEK_IMPLEMENTATION_FAILURE")


if __name__ == "__main__":
    unittest.main(verbosity=2)

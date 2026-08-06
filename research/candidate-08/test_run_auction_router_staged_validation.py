"""Pure control-flow contracts for candidate-08 staged Nautilus validation."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from auction_family_ablation_decision import (
    FAILED_AUCTION_FAMILY,
    IMPLEMENTATION_REVISION,
    INITIATIVE_FAMILY,
)
from run_auction_router_staged_validation import (
    FIRST_NAME,
    SCREEN_NAME,
    clear_staged_outputs,
    execute_staged_validation,
    validate_base_summary,
)


def _attribution_checks() -> dict[str, bool | int]:
    return {
        "signals_attributed": 4,
        "reported_signals": 4,
        "all_signals_attributed": True,
        "closed_trades_attributed": 2,
        "reported_closed_trades": 2,
        "all_closed_trades_attributed": True,
        "no_unclassified_signals": True,
        "no_unclassified_closed_trades": True,
    }


def _base_summary(
    *,
    suite: str,
    gate_passed: bool,
    initiative_pnl: float = -100.0,
    failed_pnl: float = -50.0,
) -> dict:
    return {
        "suite": suite,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "auction_family_mode": "both",
        "diagnostic_family_ablation": False,
        "promotable": True,
        "suite_gate_passed": gate_passed,
        "scenario_attribution_passed": True,
        "scenario_attribution_checks": _attribution_checks(),
        "suite_gate_checks": {
            "minimum_closed_trades": True,
            "cost_after_total_return_positive": gate_passed,
            "no_execution_failures": True,
            "no_residual_exposure": True,
            "complete_auction_scenario_attribution": True,
            "base_contract_includes_both_auction_families": True,
        },
        "closed_trades": 2,
        "wins": int(initiative_pnl > 0) + int(failed_pnl > 0),
        "combined_daily_geometric_growth": 0.02 if gate_passed else -0.01,
        "scenario_family_results": {
            INITIATIVE_FAMILY: {
                "signals": 2,
                "closed_trades": 1,
                "wins": int(initiative_pnl > 0),
                "losses": int(initiative_pnl <= 0),
                "realized_pnl_usdt": initiative_pnl,
            },
            FAILED_AUCTION_FAMILY: {
                "signals": 2,
                "closed_trades": 1,
                "wins": int(failed_pnl > 0),
                "losses": int(failed_pnl <= 0),
                "realized_pnl_usdt": failed_pnl,
            },
        },
    }


def _diagnostic_summary(
    *,
    suite: str,
    mode: str,
    economic_passed: bool = True,
) -> dict:
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
        "suite": suite,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "auction_family_mode": mode,
        "diagnostic_family_ablation": True,
        "promotable": False,
        "suite_gate_passed": False,
        "scenario_attribution_passed": True,
        "scenario_attribution_checks": {
            **_attribution_checks(),
            "signals_attributed": 3,
            "reported_signals": 3,
            "closed_trades_attributed": 3,
            "reported_closed_trades": 3,
        },
        "suite_gate_checks": {
            "minimum_closed_trades": True,
            "cost_after_total_return_positive": economic_passed,
            "no_execution_failures": True,
            "no_residual_exposure": True,
            "entry_causality": True,
            "position_exit_causality": True,
            "planned_risk_budget_respected": True,
            "fill_adjusted_risk_budget_respected": True,
            "realized_loss_budget_respected": True,
            "funding_cost_state_is_causal_and_complete": True,
            "complete_auction_scenario_attribution": True,
            "base_contract_includes_both_auction_families": False,
        },
        "closed_trades": 3,
        "wins": 2 if economic_passed else 0,
        "combined_daily_geometric_growth": 0.02 if economic_passed else -0.01,
        "scenario_family_results": {
            retained: {
                "signals": 3,
                "closed_trades": 3,
                "wins": 2 if economic_passed else 0,
                "realized_pnl_usdt": 500.0 if economic_passed else -500.0,
            },
            removed: {
                "signals": 0,
                "closed_trades": 0,
                "wins": 0,
                "realized_pnl_usdt": 0.0,
            },
        },
    }


class FakeSuiteRunner:
    def __init__(self, plans: list[tuple[int, dict | None]]) -> None:
        self.plans = list(plans)
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> int:
        self.calls.append(dict(kwargs))
        if not self.plans:
            raise AssertionError("unexpected extra suite execution")
        status, summary = self.plans.pop(0)
        output = Path(kwargs["output"])
        if output.exists():
            shutil.rmtree(output)
        output.mkdir(parents=True, exist_ok=True)
        (output / "runner_exit_status.txt").write_text(f"{status}\n", encoding="utf-8")
        if summary is not None:
            (output / "suite_metrics.json").write_text(
                json.dumps(summary, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        return status


def _execute(root: Path, fake: FakeSuiteRunner):
    return execute_staged_validation(
        config=root / "config.json",
        pattern_config=root / "pattern.json",
        root=root,
        data_cache=root / "cache",
        runner=root / "runner.py",
        run_suite=fake,
    )


class StagedValidationControlFlowContracts(unittest.TestCase):
    def test_first_logic_failure_with_both_families_negative_discards_without_ablation(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            fake = FakeSuiteRunner(
                [(0, _base_summary(suite="first", gate_passed=False))]
            )
            status, decision = _execute(root, fake)

            self.assertEqual(status, 0)
            self.assertEqual(len(fake.calls), 1)
            self.assertFalse(decision["ablation_selected"])
            self.assertEqual(
                decision["decision"],
                "FIRST_WEEK_LOGIC_FAILURE_NO_VALID_FAMILY_ABLATION_DISCARD_ROUTER_V1",
            )
            self.assertEqual(
                decision["ablation_selection"]["reason"],
                "BOTH_FAMILIES_ECONOMICALLY_NEGATIVE",
            )

    def test_first_pass_then_screen_pass_promotes_only_the_base(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            fake = FakeSuiteRunner(
                [
                    (0, _base_summary(suite="first", gate_passed=True)),
                    (0, _base_summary(suite="screen", gate_passed=True)),
                ]
            )
            status, decision = _execute(root, fake)

            self.assertEqual(status, 0)
            self.assertEqual(len(fake.calls), 2)
            self.assertEqual(fake.calls[1]["reuse_first_dir"], root / FIRST_NAME)
            self.assertEqual(
                decision["decision"],
                "PROMOTE_TO_PREDECLARED_LONG_EVALUATION",
            )
            self.assertFalse(decision["ablation_selected"])

    def test_clean_family_split_runs_exactly_one_diagnostic_without_reuse(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            fake = FakeSuiteRunner(
                [
                    (
                        0,
                        _base_summary(
                            suite="first",
                            gate_passed=False,
                            initiative_pnl=100.0,
                            failed_pnl=-50.0,
                        ),
                    ),
                    (
                        0,
                        _diagnostic_summary(
                            suite="first",
                            mode="initiative_only",
                            economic_passed=True,
                        ),
                    ),
                ]
            )
            status, decision = _execute(root, fake)

            self.assertEqual(status, 0)
            self.assertEqual(len(fake.calls), 2)
            diagnostic = fake.calls[1]
            self.assertEqual(diagnostic["family_mode"], "initiative_only")
            self.assertTrue(diagnostic["diagnostic_only"])
            self.assertIsNone(diagnostic["reuse_first_dir"])
            self.assertEqual(
                decision["decision"],
                "FIRST_WEEK_ABLATION_SUPPORTS_NEW_SINGLE_FAMILY_BASE_REBUILD",
            )
            self.assertTrue(decision["ablation_new_base_rebuild_supported"])
            self.assertFalse(decision["promotion_permitted_from_ablation"])

    def test_failed_diagnostic_economics_discards_router(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            fake = FakeSuiteRunner(
                [
                    (
                        0,
                        _base_summary(
                            suite="first",
                            gate_passed=False,
                            initiative_pnl=100.0,
                            failed_pnl=-50.0,
                        ),
                    ),
                    (
                        0,
                        _diagnostic_summary(
                            suite="first",
                            mode="initiative_only",
                            economic_passed=False,
                        ),
                    ),
                ]
            )
            status, decision = _execute(root, fake)

            self.assertEqual(status, 0)
            self.assertEqual(
                decision["decision"],
                "FIRST_WEEK_ABLATION_FAILED_DISCARD_ROUTER_V1",
            )
            self.assertFalse(decision["ablation_new_base_rebuild_supported"])

    def test_screen_implementation_failure_is_not_reclassified_as_logic(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            fake = FakeSuiteRunner(
                [
                    (0, _base_summary(suite="first", gate_passed=True)),
                    (1, None),
                ]
            )
            status, decision = _execute(root, fake)

            self.assertEqual(status, 1)
            self.assertEqual(decision["decision"], "SCREEN_IMPLEMENTATION_FAILURE")
            self.assertFalse(decision["ablation_selected"])

    def test_base_attribution_failure_is_an_evidence_contract_failure(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            broken = _base_summary(suite="first", gate_passed=False)
            broken["scenario_attribution_passed"] = False
            fake = FakeSuiteRunner([(0, broken)])
            status, decision = _execute(root, fake)

            self.assertEqual(status, 1)
            self.assertEqual(
                decision["decision"],
                "FIRST_WEEK_EVIDENCE_CONTRACT_FAILURE",
            )
            self.assertIn(
                "SCENARIO_ATTRIBUTION_INCOMPLETE",
                decision["first_evidence_contract_errors"],
            )

    def test_stale_screen_and_ablation_outputs_are_removed_before_first_run(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            stale_screen = root / SCREEN_NAME
            stale_ablation = root / "first-ablation-initiative_only-v1"
            stale_screen.mkdir(parents=True)
            stale_ablation.mkdir(parents=True)
            (stale_screen / "suite_metrics.json").write_text("{}\n", encoding="utf-8")
            (stale_ablation / "suite_metrics.json").write_text("{}\n", encoding="utf-8")
            fake = FakeSuiteRunner(
                [(0, _base_summary(suite="first", gate_passed=False))]
            )

            status, _ = _execute(root, fake)

            self.assertEqual(status, 0)
            self.assertFalse(stale_screen.exists())
            self.assertFalse(stale_ablation.exists())

    def test_clear_outputs_preserves_unowned_historical_evidence(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            historical = root / "first-base-v3"
            historical.mkdir(parents=True)
            (historical / "suite_metrics.json").write_text("{}\n", encoding="utf-8")
            (root / FIRST_NAME).mkdir()
            (root / SCREEN_NAME).mkdir()

            clear_staged_outputs(root)

            self.assertTrue(historical.exists())
            self.assertFalse((root / FIRST_NAME).exists())
            self.assertFalse((root / SCREEN_NAME).exists())

    def test_base_validation_ignores_economic_failure_but_rejects_revision_drift(self) -> None:
        value = _base_summary(suite="first", gate_passed=False)
        self.assertEqual(validate_base_summary(value, expected_suite="first"), ())
        value["implementation_revision"] = "drifted"
        self.assertIn(
            "IMPLEMENTATION_REVISION_NOT_EXACT",
            validate_base_summary(value, expected_suite="first"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

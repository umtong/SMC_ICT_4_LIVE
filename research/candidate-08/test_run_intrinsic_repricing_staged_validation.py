"""Pure orchestration contracts for intrinsic repricing validation."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from aggtrade_intrinsic_repricing_signals import (
    DIRECT_PERSISTENCE_PATH,
    IMPLEMENTATION_REVISION,
    INTRINSIC_REPRICING_FAMILY,
    REPRICE_RESUMPTION_PATH,
)
from flow_response_trade_path_diagnostics_v2 import DIAGNOSTIC_REVISION
from run_intrinsic_repricing_staged_validation import execute_staged_validation


def _path_stats(*, signals: int, trades: int, wins: int, pnl: float) -> dict:
    return {
        "signals": signals,
        "closed_trades": trades,
        "wins": wins,
        "losses": trades - wins,
        "win_rate": wins / trades if trades else 0.0,
        "realized_pnl_usdt": pnl,
    }


def _base_summary(
    *,
    suite: str,
    gate: bool,
    direct_pnl: float,
    reprice_pnl: float,
    direct_trades: int = 3,
    reprice_trades: int = 2,
) -> dict:
    closed = direct_trades + reprice_trades
    return {
        "suite": suite,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "intrinsic_repricing_path_mode": "both_paths",
        "diagnostic_path_ablation": False,
        "promotable": True,
        "single_scenario_family": INTRINSIC_REPRICING_FAMILY,
        "single_family_attribution_passed": True,
        "entry_path_attribution_passed": True,
        "ten_second_cadence_contract": "EXACT_CONSECUTIVE_10_SECONDS",
        "trade_path_diagnostic_revision": DIAGNOSTIC_REVISION,
        "suite_gate_passed": gate,
        "closed_trades": closed,
        "wins": 3 if gate else 1,
        "combined_daily_geometric_growth": 0.02 if gate else -0.01,
        "suite_gate_checks": {
            "single_intrinsic_repricing_family_attributed": True,
            "complete_intrinsic_entry_path_attribution": True,
            "complete_post_run_trade_path_diagnostics": True,
            "base_contract_includes_both_entry_paths": True,
        },
        "entry_path_results": {
            DIRECT_PERSISTENCE_PATH: _path_stats(
                signals=direct_trades,
                trades=direct_trades,
                wins=2 if direct_pnl > 0 else 0,
                pnl=direct_pnl,
            ),
            REPRICE_RESUMPTION_PATH: _path_stats(
                signals=reprice_trades,
                trades=reprice_trades,
                wins=1 if reprice_pnl > 0 else 0,
                pnl=reprice_pnl,
            ),
        },
        "trade_path_diagnostic_summary": {
            "records": closed,
            "complete_records": closed,
            "diagnostic_revision_counts": {DIAGNOSTIC_REVISION: closed},
            "expected_diagnostic_revision": DIAGNOSTIC_REVISION,
        },
    }


def _diagnostic_summary(*, suite: str, mode: str, economic_pass: bool) -> dict:
    retained = (
        DIRECT_PERSISTENCE_PATH if mode == "direct_only" else REPRICE_RESUMPTION_PATH
    )
    removed = (
        REPRICE_RESUMPTION_PATH
        if retained == DIRECT_PERSISTENCE_PATH
        else DIRECT_PERSISTENCE_PATH
    )
    return {
        "suite": suite,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "intrinsic_repricing_path_mode": mode,
        "diagnostic_path_ablation": True,
        "promotable": False,
        "single_scenario_family": INTRINSIC_REPRICING_FAMILY,
        "single_family_attribution_passed": True,
        "entry_path_attribution_passed": True,
        "ten_second_cadence_contract": "EXACT_CONSECUTIVE_10_SECONDS",
        "trade_path_diagnostic_revision": DIAGNOSTIC_REVISION,
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
            "single_intrinsic_repricing_family_attributed": True,
            "complete_intrinsic_entry_path_attribution": True,
            "complete_post_run_trade_path_diagnostics": True,
            "base_contract_includes_both_auction_families": False,
            "base_contract_includes_both_flow_response_families": False,
            "base_contract_includes_both_entry_paths": False,
        },
        "entry_path_results": {
            retained: _path_stats(
                signals=3,
                trades=3,
                wins=2,
                pnl=500.0 if economic_pass else -100.0,
            ),
            removed: _path_stats(signals=0, trades=0, wins=0, pnl=0.0),
        },
        "trade_path_diagnostic_summary": {
            "records": 3,
            "complete_records": 3,
            "diagnostic_revision_counts": {DIAGNOSTIC_REVISION: 3},
            "expected_diagnostic_revision": DIAGNOSTIC_REVISION,
        },
    }


class _FakeRunner:
    def __init__(
        self,
        summaries: dict[tuple[str, str], dict],
        failures: set[tuple[str, str]] | None = None,
    ) -> None:
        self.summaries = summaries
        self.failures = failures or set()
        self.calls: list[tuple[str, str, bool]] = []

    def __call__(self, **kwargs):
        suite = str(kwargs["suite"])
        mode = str(kwargs["path_mode"])
        output = Path(kwargs["output"])
        self.calls.append((suite, mode, kwargs.get("reuse_first_dir") is not None))
        output.mkdir(parents=True, exist_ok=True)
        if (suite, mode) in self.failures:
            return 9
        (output / "suite_metrics.json").write_text(
            json.dumps(self.summaries[(suite, mode)]),
            encoding="utf-8",
        )
        return 0


class IntrinsicRepricingStagedValidationContracts(unittest.TestCase):
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

    def test_first_and_screen_pass_are_required_before_long_promotion(self) -> None:
        fake = _FakeRunner(
            {
                ("first", "both_paths"): _base_summary(
                    suite="first",
                    gate=True,
                    direct_pnl=500.0,
                    reprice_pnl=200.0,
                ),
                ("screen", "both_paths"): _base_summary(
                    suite="screen",
                    gate=True,
                    direct_pnl=900.0,
                    reprice_pnl=400.0,
                ),
            }
        )
        status, decision = self._execute(fake)
        self.assertEqual(status, 0)
        self.assertEqual(decision["decision"], "PROMOTE_TO_PREDECLARED_LONG_EVALUATION")
        self.assertEqual(
            fake.calls,
            [("first", "both_paths", False), ("screen", "both_paths", True)],
        )

    def test_both_negative_paths_are_discarded_without_search(self) -> None:
        fake = _FakeRunner(
            {
                ("first", "both_paths"): _base_summary(
                    suite="first",
                    gate=False,
                    direct_pnl=-500.0,
                    reprice_pnl=-200.0,
                )
            }
        )
        status, decision = self._execute(fake)
        self.assertEqual(status, 0)
        self.assertEqual(
            decision["decision"],
            "FIRST_WEEK_LOGIC_FAILURE_DISCARD_INTRINSIC_REPRICING_V1",
        )
        self.assertFalse(decision["path_ablation_selected"])
        self.assertEqual(fake.calls, [("first", "both_paths", False)])

    def test_clean_path_split_runs_exactly_one_nonpromotable_diagnostic(self) -> None:
        fake = _FakeRunner(
            {
                ("first", "both_paths"): _base_summary(
                    suite="first",
                    gate=False,
                    direct_pnl=500.0,
                    reprice_pnl=-300.0,
                ),
                ("first", "direct_only"): _diagnostic_summary(
                    suite="first",
                    mode="direct_only",
                    economic_pass=True,
                ),
            }
        )
        status, decision = self._execute(fake)
        self.assertEqual(status, 0)
        self.assertTrue(decision["path_ablation_selected"])
        self.assertTrue(decision["path_ablation_new_base_rebuild_supported"])
        self.assertFalse(decision["promotion_permitted_from_ablation"])
        self.assertEqual(
            decision["decision"],
            "PATH_ABLATION_SUPPORTS_NEW_SINGLE_PATH_BASE_REBUILD",
        )
        self.assertEqual(
            fake.calls,
            [("first", "both_paths", False), ("first", "direct_only", False)],
        )

    def test_untraded_path_does_not_select_a_diagnostic(self) -> None:
        fake = _FakeRunner(
            {
                ("first", "both_paths"): _base_summary(
                    suite="first",
                    gate=False,
                    direct_pnl=200.0,
                    reprice_pnl=0.0,
                    reprice_trades=0,
                )
            }
        )
        status, decision = self._execute(fake)
        self.assertEqual(status, 0)
        self.assertFalse(decision["path_ablation_selected"])
        self.assertEqual(len(fake.calls), 1)

    def test_runner_failure_is_implementation_failure(self) -> None:
        fake = _FakeRunner({}, failures={("first", "both_paths")})
        status, decision = self._execute(fake)
        self.assertEqual(status, 1)
        self.assertEqual(decision["decision"], "FIRST_WEEK_IMPLEMENTATION_FAILURE")


if __name__ == "__main__":
    unittest.main(verbosity=2)

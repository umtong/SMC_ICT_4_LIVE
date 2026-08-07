"""Pure staged-decision contracts for delayed reacceptance."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from aggtrade_delayed_reacceptance_signals_v2 import (
    ABLATION_INITIAL_MODE,
    BASE_INITIAL_MODE,
    IMPLEMENTATION_REVISION,
    REACCEPTANCE_FAMILY,
)
from flow_response_trade_path_diagnostics_v2 import DIAGNOSTIC_REVISION
from run_delayed_reacceptance_staged_validation import execute_staged_validation


def _family_stats(*, trades: int, wins: int, pnl: float) -> dict:
    return {
        "signals": trades,
        "closed_trades": trades,
        "wins": wins,
        "losses": trades - wins,
        "win_rate": wins / trades if trades else 0.0,
        "realized_pnl_usdt": pnl,
    }


def _summary(
    *,
    suite: str,
    mode: str,
    gate: bool,
    total_return_positive: bool,
    trades: int = 4,
    pnl: float = 500.0,
) -> dict:
    diagnostic = mode == ABLATION_INITIAL_MODE
    return {
        "suite": suite,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "delayed_reacceptance_initial_mode": mode,
        "diagnostic_initial_ablation": diagnostic,
        "promotable": not diagnostic,
        "single_scenario_family": REACCEPTANCE_FAMILY,
        "single_family_attribution_passed": True,
        "scenario_attribution_passed": True,
        "scenario_attribution_checks": {
            "all_signals_attributed": True,
            "all_closed_trades_attributed": True,
            "no_unclassified_signals": True,
            "no_unclassified_closed_trades": True,
        },
        "ten_second_cadence_contract": "EXACT_CONSECUTIVE_10_SECONDS",
        "trade_path_diagnostic_revision": DIAGNOSTIC_REVISION,
        "suite_gate_passed": gate,
        "closed_trades": trades,
        "wins": 2 if total_return_positive else 0,
        "combined_daily_geometric_growth": 0.02 if gate else (-0.01 if trades else 0.0),
        "scenario_family_results": {
            REACCEPTANCE_FAMILY: _family_stats(
                trades=trades,
                wins=2 if total_return_positive else 0,
                pnl=pnl,
            ),
            "UNUSED_DELAYED_REACCEPTANCE_FAMILY": _family_stats(
                trades=0,
                wins=0,
                pnl=0.0,
            ),
        },
        "suite_gate_checks": {
            "minimum_closed_trades": trades >= 3,
            "cost_after_total_return_positive": total_return_positive,
            "no_execution_failures": True,
            "no_residual_exposure": True,
            "entry_causality": True,
            "planned_risk_budget_respected": True,
            "fill_adjusted_risk_budget_respected": True,
            "realized_loss_budget_respected": True,
            "funding_cost_state_is_causal_and_complete": True,
            "complete_auction_scenario_attribution": True,
            "single_delayed_reacceptance_family_attributed": True,
            "complete_post_run_trade_path_diagnostics": True,
            "base_contract_includes_both_auction_families": True,
            "base_contract_includes_both_flow_response_families": True,
            "base_initial_initiative_required": not diagnostic,
        },
        "trade_path_diagnostic_summary": {
            "records": trades,
            "complete_records": trades,
            "diagnostic_revision_counts": {DIAGNOSTIC_REVISION: trades},
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
        mode = str(kwargs["initial_mode"])
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


class DelayedReacceptanceStagedContracts(unittest.TestCase):
    def _execute(self, fake: _FakeRunner):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        placeholder = Path(temporary.name) / "placeholder"
        placeholder.write_text("x", encoding="utf-8")
        return execute_staged_validation(
            config=placeholder,
            pattern_config=placeholder,
            root=Path(temporary.name) / "evidence",
            data_cache=Path(temporary.name) / "cache",
            runner=placeholder,
            run_suite=fake,
        )

    def test_first_and_screen_base_gates_are_required_for_long_promotion(self) -> None:
        fake = _FakeRunner(
            {
                ("first", BASE_INITIAL_MODE): _summary(
                    suite="first",
                    mode=BASE_INITIAL_MODE,
                    gate=True,
                    total_return_positive=True,
                ),
                ("screen", BASE_INITIAL_MODE): _summary(
                    suite="screen",
                    mode=BASE_INITIAL_MODE,
                    gate=True,
                    total_return_positive=True,
                    trades=12,
                    pnl=3000.0,
                ),
            }
        )
        status, decision = self._execute(fake)
        self.assertEqual(status, 0)
        self.assertEqual(decision["decision"], "PROMOTE_TO_PREDECLARED_LONG_EVALUATION")
        self.assertEqual(
            fake.calls,
            [
                ("first", BASE_INITIAL_MODE, False),
                ("screen", BASE_INITIAL_MODE, True),
            ],
        )

    def test_clean_base_failure_runs_exactly_one_initial_state_ablation(self) -> None:
        fake = _FakeRunner(
            {
                ("first", BASE_INITIAL_MODE): _summary(
                    suite="first",
                    mode=BASE_INITIAL_MODE,
                    gate=False,
                    total_return_positive=False,
                    pnl=-500.0,
                ),
                ("first", ABLATION_INITIAL_MODE): _summary(
                    suite="first",
                    mode=ABLATION_INITIAL_MODE,
                    gate=False,
                    total_return_positive=True,
                    pnl=700.0,
                ),
            }
        )
        status, decision = self._execute(fake)
        self.assertEqual(status, 0)
        self.assertTrue(decision["ablation_selected"])
        self.assertTrue(decision["ablation_new_base_rebuild_supported"])
        self.assertFalse(decision["promotion_permitted_from_ablation"])
        self.assertEqual(decision["decision"], "SINGLE_ABLATION_SUPPORTS_NEW_BASE_REBUILD")
        self.assertEqual(
            fake.calls,
            [
                ("first", BASE_INITIAL_MODE, False),
                ("first", ABLATION_INITIAL_MODE, False),
            ],
        )

    def test_failed_ablation_discards_candidate(self) -> None:
        fake = _FakeRunner(
            {
                ("first", BASE_INITIAL_MODE): _summary(
                    suite="first",
                    mode=BASE_INITIAL_MODE,
                    gate=False,
                    total_return_positive=False,
                    pnl=-500.0,
                ),
                ("first", ABLATION_INITIAL_MODE): _summary(
                    suite="first",
                    mode=ABLATION_INITIAL_MODE,
                    gate=False,
                    total_return_positive=False,
                    pnl=-300.0,
                ),
            }
        )
        status, decision = self._execute(fake)
        self.assertEqual(status, 0)
        self.assertFalse(decision["ablation_new_base_rebuild_supported"])
        self.assertEqual(
            decision["decision"],
            "SINGLE_ABLATION_FAILED_DISCARD_DELAYED_REACCEPTANCE_V1",
        )

    def test_zero_trade_base_still_runs_the_one_predeclared_ablation(self) -> None:
        fake = _FakeRunner(
            {
                ("first", BASE_INITIAL_MODE): _summary(
                    suite="first",
                    mode=BASE_INITIAL_MODE,
                    gate=False,
                    total_return_positive=False,
                    trades=0,
                    pnl=0.0,
                ),
                ("first", ABLATION_INITIAL_MODE): _summary(
                    suite="first",
                    mode=ABLATION_INITIAL_MODE,
                    gate=False,
                    total_return_positive=False,
                    trades=0,
                    pnl=0.0,
                ),
            }
        )
        status, decision = self._execute(fake)
        self.assertEqual(status, 0)
        self.assertTrue(decision["ablation_selected"])
        self.assertEqual(len(fake.calls), 2)

    def test_runner_failure_is_implementation_failure_without_ablation(self) -> None:
        fake = _FakeRunner({}, failures={("first", BASE_INITIAL_MODE)})
        status, decision = self._execute(fake)
        self.assertEqual(status, 1)
        self.assertEqual(decision["decision"], "FIRST_WEEK_IMPLEMENTATION_FAILURE")
        self.assertFalse(decision["ablation_selected"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

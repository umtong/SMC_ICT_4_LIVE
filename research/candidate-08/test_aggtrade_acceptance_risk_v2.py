"""Contracts for causal fill-adjusted risk accounting and realized-tail classification."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from aggtrade_acceptance_risk_v2 import (
    REALIZED_BREACH_CLASSIFICATION,
    RISK_ACCOUNTING_REVISION,
    RiskCompleteAggTradeAcceptanceStrategy,
    run_window_classifying_realized_slippage,
)
from aggtrade_acceptance_strategy import AggTradeAcceptanceStrategy


class RiskAccountingContracts(unittest.TestCase):
    def test_strategy_revision_retains_signal_time_stop_slippage_reserve(self) -> None:
        self.assertTrue(
            issubclass(
                RiskCompleteAggTradeAcceptanceStrategy,
                AggTradeAcceptanceStrategy,
            )
        )
        source = Path(__file__).with_name("aggtrade_acceptance_risk_v2.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("float(signal.causal_stop_slippage_reserve)", source)
        self.assertIn('intent["stop_slippage_reserve_per_unit"]', source)
        self.assertIn('intent["fill_adjusted_expected_stop_loss"]', source)
        self.assertNotIn("risk_multiplier", source)
        self.assertNotIn("maximum_notional", source)

    def test_realized_only_breach_returns_complete_failed_gate_metrics(self) -> None:
        with TemporaryDirectory() as directory:
            output_dir = Path(directory)
            metrics_path = output_dir / "metrics.json"
            metrics = {
                "first_window_gate_passed": True,
                "first_window_gate_checks": {
                    "realized_loss_budget_respected": True,
                },
                "open_positions_after_run": 0,
                "open_orders_after_run": 0,
                "unprocessed_signal_times": 0,
                "contract_checks": {
                    "entry_fill_before_signal_count": 0,
                    "planned_loss_over_budget_count": 0,
                    "fill_adjusted_loss_over_budget_count": 0,
                    "realized_loss_over_budget_count": 1,
                    "realized_loss_over_budget_details": [
                        {
                            "scenario_id": "s1",
                            "realized_pnl": -3080.0,
                            "risk_budget": 3000.0,
                            "ratio": 1.0266666667,
                        }
                    ],
                    "maximum_realized_loss_budget_ratio": 1.0266666667,
                    "missing_entry_fill_time_count": 0,
                    "missing_entry_fill_price_count": 0,
                    "missing_funding_cost_state_count": 0,
                    "funding_observation_after_signal_count": 0,
                    "invalid_funding_reserve_count": 0,
                    "unmatched_closed_trade_count": 0,
                    "missing_position_close_time_count": 0,
                    "nonpositive_position_holding_time_count": 0,
                },
            }
            metrics_path.write_text(
                json.dumps(metrics),
                encoding="utf-8",
            )

            def realized_breach(**_kwargs):
                raise RuntimeError(
                    "realized loss exceeded the signal-time 3% shared-NAV budget"
                )

            result = run_window_classifying_realized_slippage(
                realized_breach,
                output_dir=output_dir,
            )
            rewritten = json.loads(metrics_path.read_text(encoding="utf-8"))

        self.assertIs(result, result)
        self.assertEqual(result["risk_accounting_revision"], RISK_ACCOUNTING_REVISION)
        self.assertEqual(
            result["execution_contract_classification"]["classification"],
            REALIZED_BREACH_CLASSIFICATION,
        )
        self.assertFalse(result["execution_contract_classification"]["implementation_failure"])
        self.assertTrue(result["execution_contract_classification"]["candidate_gate_failure"])
        self.assertFalse(result["first_window_gate_passed"])
        self.assertFalse(
            result["first_window_gate_checks"]["realized_loss_budget_respected"]
        )
        self.assertEqual(rewritten, result)

    def test_other_contract_or_runtime_failures_are_not_reclassified(self) -> None:
        def other_failure(**_kwargs):
            raise RuntimeError("entry causality contract failed")

        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "entry causality"):
                run_window_classifying_realized_slippage(
                    other_failure,
                    output_dir=Path(directory),
                )

    def test_realized_breach_with_other_missing_evidence_is_not_clean(self) -> None:
        with TemporaryDirectory() as directory:
            output_dir = Path(directory)
            metrics = {
                "open_positions_after_run": 0,
                "open_orders_after_run": 0,
                "unprocessed_signal_times": 0,
                "contract_checks": {
                    "realized_loss_over_budget_count": 1,
                    "missing_position_close_time_count": 1,
                },
            }
            (output_dir / "metrics.json").write_text(
                json.dumps(metrics),
                encoding="utf-8",
            )

            def mixed_failure(**_kwargs):
                raise RuntimeError(
                    "realized loss exceeded the signal-time 3% shared-NAV budget"
                )

            with self.assertRaisesRegex(RuntimeError, "realized loss exceeded"):
                run_window_classifying_realized_slippage(
                    mixed_failure,
                    output_dir=output_dir,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)

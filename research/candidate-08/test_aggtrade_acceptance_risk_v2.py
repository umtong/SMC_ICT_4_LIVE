"""Contracts for causal fill-adjusted risk accounting and execution-tail classification."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from aggtrade_acceptance_risk_v2 import (
    FILL_ADJUSTED_BREACH_CLASSIFICATION,
    REALIZED_BREACH_CLASSIFICATION,
    RISK_ACCOUNTING_REVISION,
    RiskCompleteAggTradeAcceptanceStrategy,
    run_window_classifying_execution_risk,
)
from aggtrade_acceptance_strategy import AggTradeAcceptanceStrategy


_ZERO_OTHER_COUNTS = {
    "entry_fill_before_signal_count": 0,
    "planned_loss_over_budget_count": 0,
    "missing_entry_fill_time_count": 0,
    "missing_entry_fill_price_count": 0,
    "missing_funding_cost_state_count": 0,
    "funding_observation_after_signal_count": 0,
    "invalid_funding_reserve_count": 0,
    "unmatched_closed_trade_count": 0,
    "missing_position_close_time_count": 0,
    "nonpositive_position_holding_time_count": 0,
}


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
        self.assertIn(
            "FIRST_COMPLETED_TEN_SECOND_BAR_STRICTLY_AFTER_POSITION_OPEN",
            source,
        )
        fill_handler = source[
            source.index("def on_order_filled") : source.index("def on_position_opened")
        ]
        position_handler = source[
            source.index("def on_position_opened") : source.index("def on_bar")
        ]
        bar_handler = source[source.index("def on_bar") : source.index("def on_position_closed")]
        self.assertNotIn("self._request_exit(", fill_handler)
        self.assertNotIn("self._request_exit(", position_handler)
        self.assertIn("int(bar.ts_event) <= int(pending_after_ns)", bar_handler)
        self.assertIn("self._request_exit(", bar_handler)
        self.assertNotIn("risk_multiplier", source)
        self.assertNotIn("maximum_notional", source)

    @staticmethod
    def _write_metrics(
        output_dir: Path,
        *,
        fill_adjusted_count: int,
        realized_count: int,
        missing_close_count: int = 0,
    ) -> Path:
        metrics_path = output_dir / "metrics.json"
        metrics = {
            "first_window_gate_passed": True,
            "first_window_gate_checks": {
                "fill_adjusted_loss_budget_respected": True,
                "realized_loss_budget_respected": True,
            },
            "open_positions_after_run": 0,
            "open_orders_after_run": 0,
            "unprocessed_signal_times": 0,
            "contract_checks": {
                **_ZERO_OTHER_COUNTS,
                "missing_position_close_time_count": missing_close_count,
                "fill_adjusted_loss_over_budget_count": fill_adjusted_count,
                "fill_adjusted_loss_over_budget_details": (
                    [{"scenario_id": "s-fill", "ratio": 1.0002}]
                    if fill_adjusted_count
                    else []
                ),
                "maximum_fill_adjusted_loss_budget_ratio": (
                    1.0002 if fill_adjusted_count else 0.999
                ),
                "realized_loss_over_budget_count": realized_count,
                "realized_loss_over_budget_details": (
                    [
                        {
                            "scenario_id": "s-stop",
                            "realized_pnl": -3080.0,
                            "risk_budget": 3000.0,
                            "ratio": 1.0266666667,
                        }
                    ]
                    if realized_count
                    else []
                ),
                "maximum_realized_loss_budget_ratio": (
                    1.0266666667 if realized_count else 0.95
                ),
            },
        }
        metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
        return metrics_path

    def test_realized_only_breach_returns_complete_failed_gate_metrics(self) -> None:
        with TemporaryDirectory() as directory:
            output_dir = Path(directory)
            metrics_path = self._write_metrics(
                output_dir,
                fill_adjusted_count=0,
                realized_count=1,
            )

            def realized_breach(**_kwargs):
                raise RuntimeError(
                    "realized loss exceeded the signal-time 3% shared-NAV budget"
                )

            result = run_window_classifying_execution_risk(
                realized_breach,
                output_dir=output_dir,
            )
            rewritten = json.loads(metrics_path.read_text(encoding="utf-8"))

        self.assertEqual(result["risk_accounting_revision"], RISK_ACCOUNTING_REVISION)
        self.assertEqual(
            result["execution_contract_classification"]["classifications"],
            [REALIZED_BREACH_CLASSIFICATION],
        )
        self.assertFalse(result["execution_contract_classification"]["implementation_failure"])
        self.assertTrue(result["execution_contract_classification"]["candidate_gate_failure"])
        self.assertFalse(result["first_window_gate_passed"])
        self.assertFalse(
            result["first_window_gate_checks"]["realized_loss_budget_respected"]
        )
        self.assertEqual(rewritten, result)

    def test_fill_adjusted_breach_waits_for_native_exit_and_is_cleanly_classified(self) -> None:
        with TemporaryDirectory() as directory:
            output_dir = Path(directory)
            self._write_metrics(
                output_dir,
                fill_adjusted_count=1,
                realized_count=0,
            )

            def fill_breach(**_kwargs):
                raise RuntimeError(
                    "fill-adjusted expected stop loss exceeded the 3% shared-NAV budget"
                )

            result = run_window_classifying_execution_risk(
                fill_breach,
                output_dir=output_dir,
            )

        self.assertEqual(
            result["execution_contract_classification"]["classifications"],
            [FILL_ADJUSTED_BREACH_CLASSIFICATION],
        )
        self.assertEqual(
            result["execution_contract_classification"][
                "fill_adjusted_loss_over_budget_count"
            ],
            1,
        )
        self.assertFalse(
            result["first_window_gate_checks"]["fill_adjusted_loss_budget_respected"]
        )
        self.assertFalse(result["first_window_gate_passed"])

    def test_other_contract_or_runtime_failures_are_not_reclassified(self) -> None:
        def other_failure(**_kwargs):
            raise RuntimeError("entry causality contract failed")

        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "entry causality"):
                run_window_classifying_execution_risk(
                    other_failure,
                    output_dir=Path(directory),
                )

    def test_execution_breach_with_other_missing_evidence_is_not_clean(self) -> None:
        with TemporaryDirectory() as directory:
            output_dir = Path(directory)
            self._write_metrics(
                output_dir,
                fill_adjusted_count=0,
                realized_count=1,
                missing_close_count=1,
            )

            def mixed_failure(**_kwargs):
                raise RuntimeError(
                    "realized loss exceeded the signal-time 3% shared-NAV budget"
                )

            with self.assertRaisesRegex(RuntimeError, "realized loss exceeded"):
                run_window_classifying_execution_risk(
                    mixed_failure,
                    output_dir=output_dir,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)

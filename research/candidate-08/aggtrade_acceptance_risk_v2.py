"""Risk-accounting repair and clean execution-risk classification.

This module does not change scenario signals, stops, targets, fees, funding, leverage or the
current-NAV three-percent sizing rule.  It repairs infrastructure boundaries exposed by the
predeclared delayed-reacceptance ablation:

* fill-adjusted expected loss retains the causal stop-slippage reserve used at signal time;
* a fill-adjusted breach is acted on only after the native POSITION_OPENED callback, preserving the
  real event order before the forced exit; and
* evidence-complete entry/stop slippage tails are candidate execution-risk failures, not Python or
  NautilusTrader implementation failures.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from aggtrade_acceptance_strategy import AggTradeAcceptanceStrategy
from smc_ict_4.manifest import write_json_atomic


RISK_ACCOUNTING_REVISION = "FILL_ADJUSTED_CAUSAL_STOP_RESERVE_V3_POSITION_OPEN_ORDERING"
FILL_ADJUSTED_BREACH_CLASSIFICATION = "ENTRY_FILL_SLIPPAGE_RISK_CONTRACT_FAILURE"
REALIZED_BREACH_CLASSIFICATION = "REALIZED_STOP_SLIPPAGE_TAIL_RISK_CONTRACT_FAILURE"

_FILL_ADJUSTED_ERROR = (
    "fill-adjusted expected stop loss exceeded the 3% shared-NAV budget"
)
_REALIZED_ERROR = "realized loss exceeded the signal-time 3% shared-NAV budget"


class RiskCompleteAggTradeAcceptanceStrategy(AggTradeAcceptanceStrategy):
    """Preserve the signal-time stop reserve and force exit in native callback order."""

    def on_order_filled(self, event: Any) -> None:
        order_id = str(event.client_order_id)
        entry_order_id = self.active_entry_order_id
        signal = self.active_signal
        super().on_order_filled(event)

        if order_id != entry_order_id or signal is None or not self.trade_intents:
            return
        last_px = getattr(event, "last_px", None)
        if last_px is None:
            return
        fill_price = (
            float(last_px.as_double()) if hasattr(last_px, "as_double") else float(last_px)
        )
        instrument = (
            self.instruments.get(str(self.active_instrument_id))
            if self.active_instrument_id is not None
            else None
        )
        if instrument is None:
            return

        intent = self.trade_intents[-1]
        tick = float(instrument.price_increment.as_double())
        fee_rate = float(self.config.effective_fee_rate)
        stop = float(intent["structural_stop"])
        quantity = float(intent["quantity"])
        stop_slippage_reserve = max(
            tick,
            float(signal.causal_stop_slippage_reserve),
        )
        expected_funding_crossings = int(intent.get("expected_funding_crossings", 0))
        expected_funding_rate_abs = float(intent.get("expected_funding_rate_abs", 0.0))
        fill_adjusted_funding_reserve = (
            expected_funding_crossings * expected_funding_rate_abs * fill_price
        )
        expected_loss_per_unit = (
            abs(fill_price - stop)
            + fee_rate * (fill_price + stop)
            + stop_slippage_reserve
            + fill_adjusted_funding_reserve
        )
        fill_adjusted_loss = quantity * expected_loss_per_unit
        risk_budget = float(intent["risk_budget"])

        intent["risk_accounting_revision"] = RISK_ACCOUNTING_REVISION
        intent["entry_slippage_reserve_per_unit"] = tick
        intent["stop_slippage_reserve_per_unit"] = stop_slippage_reserve
        intent["fill_adjusted_expected_funding_reserve_per_unit"] = (
            fill_adjusted_funding_reserve
        )
        intent["fill_adjusted_expected_loss_per_unit"] = expected_loss_per_unit
        intent["fill_adjusted_expected_stop_loss"] = fill_adjusted_loss
        intent["fill_adjusted_risk_budget_ratio"] = (
            fill_adjusted_loss / risk_budget if risk_budget > 0 else None
        )

        tolerance = max(1e-8, risk_budget * 1e-9)
        if fill_adjusted_loss <= risk_budget + tolerance or self.fill_adjusted_risk_violation:
            return

        self.fill_adjusted_risk_violation = True
        self.execution_failures.append(
            {
                "reason": "FILL_ADJUSTED_RISK_BUDGET_EXCEEDED",
                "classification": FILL_ADJUSTED_BREACH_CLASSIFICATION,
                "risk_accounting_revision": RISK_ACCOUNTING_REVISION,
                "client_order_id": order_id,
                "ts_event": int(event.ts_event),
                "fill_adjusted_expected_stop_loss": fill_adjusted_loss,
                "risk_budget": risk_budget,
                "ratio": fill_adjusted_loss / risk_budget if risk_budget > 0 else None,
                "stop_slippage_reserve_per_unit": stop_slippage_reserve,
            }
        )
        if self.active_instrument_id is not None:
            # Do not emit EXIT_REQUESTED from inside OrderFilled.  Nautilus has updated the
            # portfolio, but POSITION_OPENED has not yet reached the strategy.  Cancel contingent
            # children now; the inherited on_position_opened callback sees the violation flag,
            # records POSITION_OPENED first, then requests the forced exit in causal event order.
            self.cancel_all_orders(self.active_instrument_id)


def _clean_execution_risk_breach(
    metrics: dict[str, Any],
    *,
    raised_message: str,
) -> dict[str, Any]:
    checks = dict(metrics.get("contract_checks", {}))
    fill_adjusted_count = int(checks.get("fill_adjusted_loss_over_budget_count", 0))
    realized_count = int(checks.get("realized_loss_over_budget_count", 0))
    if raised_message == _FILL_ADJUSTED_ERROR and fill_adjusted_count <= 0:
        raise RuntimeError("fill-adjusted breach was raised without matching metrics evidence")
    if raised_message == _REALIZED_ERROR and realized_count <= 0:
        raise RuntimeError("realized breach was raised without matching metrics evidence")

    other_blocking_counts = {
        name: int(checks.get(name, 0))
        for name in (
            "entry_fill_before_signal_count",
            "planned_loss_over_budget_count",
            "missing_entry_fill_time_count",
            "missing_entry_fill_price_count",
            "missing_funding_cost_state_count",
            "funding_observation_after_signal_count",
            "invalid_funding_reserve_count",
            "unmatched_closed_trade_count",
            "missing_position_close_time_count",
            "nonpositive_position_holding_time_count",
        )
    }
    if any(other_blocking_counts.values()):
        raise RuntimeError(raised_message)
    if int(metrics.get("open_positions_after_run", 0)) != 0:
        raise RuntimeError(raised_message)
    if int(metrics.get("open_orders_after_run", 0)) != 0:
        raise RuntimeError(raised_message)
    if int(metrics.get("unprocessed_signal_times", 0)) != 0:
        raise RuntimeError(raised_message)

    classifications: list[str] = []
    if fill_adjusted_count:
        classifications.append(FILL_ADJUSTED_BREACH_CLASSIFICATION)
    if realized_count:
        classifications.append(REALIZED_BREACH_CLASSIFICATION)
    metrics["risk_accounting_revision"] = RISK_ACCOUNTING_REVISION
    metrics["execution_contract_classification"] = {
        "classifications": classifications,
        "candidate_gate_failure": True,
        "implementation_failure": False,
        "fill_adjusted_loss_over_budget_count": fill_adjusted_count,
        "maximum_fill_adjusted_loss_budget_ratio": checks.get(
            "maximum_fill_adjusted_loss_budget_ratio"
        ),
        "fill_adjusted_details": checks.get(
            "fill_adjusted_loss_over_budget_details", []
        ),
        "realized_loss_over_budget_count": realized_count,
        "maximum_realized_loss_budget_ratio": checks.get(
            "maximum_realized_loss_budget_ratio"
        ),
        "realized_details": checks.get("realized_loss_over_budget_details", []),
        "reason": (
            "Native replay, order, fill, position and artifact generation completed.  An observed "
            "entry or stop fill exceeded the causal signal-time execution reserve, so the "
            "candidate fails the execution-risk contract without being mislabeled as a runtime "
            "implementation failure."
        ),
    }
    metrics["first_window_gate_passed"] = False
    gate_checks = dict(metrics.get("first_window_gate_checks", {}))
    if fill_adjusted_count:
        gate_checks["fill_adjusted_loss_budget_respected"] = False
    if realized_count:
        gate_checks["realized_loss_budget_respected"] = False
    metrics["first_window_gate_checks"] = gate_checks
    return metrics


def run_window_classifying_execution_risk(
    original_run_window: Callable[..., dict[str, Any]],
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    """Return complete failed-gate metrics for evidence-complete execution-risk breaches."""

    try:
        return original_run_window(*args, **kwargs)
    except RuntimeError as exc:
        raised_message = str(exc)
        if raised_message not in {_FILL_ADJUSTED_ERROR, _REALIZED_ERROR}:
            raise

        output_dir = Path(kwargs["output_dir"])
        metrics_path = output_dir / "metrics.json"
        if not metrics_path.exists():
            raise
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        metrics = _clean_execution_risk_breach(
            metrics,
            raised_message=raised_message,
        )
        write_json_atomic(metrics_path, metrics)
        return metrics


# Backward-compatible name for the first repair revision.
run_window_classifying_realized_slippage = run_window_classifying_execution_risk


__all__ = [
    "FILL_ADJUSTED_BREACH_CLASSIFICATION",
    "REALIZED_BREACH_CLASSIFICATION",
    "RISK_ACCOUNTING_REVISION",
    "RiskCompleteAggTradeAcceptanceStrategy",
    "run_window_classifying_execution_risk",
    "run_window_classifying_realized_slippage",
]

"""Submit a newly completed causal plan without waiting one extra minute.

The base candidate creates a plan only after its signal bar is fully completed,
but historically deferred market-order submission until the next one-minute bar
callback.  That added a full minute of latency which is not part of the market
scenario and can consume the remaining target geometry.  This mixin changes no
signal, state, stop, target, quantity, risk, fee, funding, or portfolio rule.  It
submits the same immutable plan at the end of the callback which observed it.
NautilusTrader remains the sole order, fill, position, account, PnL, and NAV
engine.
"""
from __future__ import annotations

from typing import Any

from model import ScenarioState, TradePlan


class ImmediateCausalEntryMixin:
    """Remove only the inherited one-bar post-observation submission delay."""

    def on_bar(self, bar: Any) -> None:
        super().on_bar(bar)
        self._submit_newly_observed_plan(bar)

    def _submit_newly_observed_plan(self, bar: Any) -> None:
        plan: TradePlan | None = self._pending_plan
        if plan is None:
            return
        event_time_ns = int(bar.ts_event)
        if int(plan.observed_time_ns) != event_time_ns:
            return

        in_trade_window = (
            self.config.trade_start_ns
            <= event_time_ns
            < self.config.trade_end_ns
        )
        flat = self.portfolio.is_flat(self.config.instrument_id)
        eligible = (
            in_trade_window
            and flat
            and self._active_plan is None
            and not self._exit_pending
        )
        if not eligible:
            self._invalidate_pending(
                "IMMEDIATE_CAUSAL_ENTRY_SLOT_OR_WINDOW_LOST",
                event_time_ns,
            )
            return

        self._append_manual_event(
            scenario_id=plan.scenario_id,
            previous_state=ScenarioState.ENTRY_READY.value,
            next_state=ScenarioState.ENTRY_READY.value,
            reason_code="IMMEDIATE_POST_OBSERVATION_SUBMISSION",
            event_time_ns=event_time_ns,
            reference_price=plan.entry_reference,
            details={
                "observed_time_ns": int(plan.observed_time_ns),
                "submission_basis": (
                    "same completed one-minute callback after all causal state "
                    "transitions; no additional market data observed"
                ),
                "signal_kind": plan.kind.value,
                "stop_price": plan.stop_price,
                "target_price": plan.target_price,
                "expected_rr": plan.expected_rr,
            },
        )
        self._submit_pending(bar)


__all__ = ["ImmediateCausalEntryMixin"]

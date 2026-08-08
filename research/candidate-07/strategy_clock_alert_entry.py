"""Causal post-observation submission through NautilusTrader time alerts.

The base candidate observes a completed one-minute bar and creates an immutable
trade plan, but historically waits for the *next completed minute* before
submitting the market-parent bracket.  Submitting inside the same ``on_bar``
callback is invalid because the simulator can match against the already-observed
bar.  This mixin instead schedules a one-shot ``TimeEvent`` one nanosecond after
the observation.  The order therefore enters the engine between market-data
events and can only use information available at the completed signal bar.

No signal, market state, stop, target, position size, fee, funding, risk or
portfolio rule is changed.  NautilusTrader remains the only order, matching,
position, account, PnL and NAV engine.
"""
from __future__ import annotations

from typing import Any

from model import ScenarioState, TradePlan


class ClockAlertCausalEntryMixin:
    """Submit a newly observed plan through a deterministic one-shot alert."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self._entry_alert_name: str | None = None
        self._entry_alert_scenario_id: str | None = None
        self._entry_alert_bar: Any | None = None
        self._entry_alert_sequence = 0

    def on_bar(self, bar: Any) -> None:
        # A scheduled alert lies strictly before the next market-data timestamp.
        # Reaching another bar with it still armed would allow the inherited
        # delayed path to submit and must therefore fail closed.
        if self._entry_alert_name is not None:
            plan: TradePlan | None = self._pending_plan
            scenario_id = (
                None if plan is None else str(plan.scenario_id)
            )
            self._clear_entry_alert(cancel=True)
            if plan is not None:
                self._invalidate_pending(
                    "CAUSAL_ENTRY_ALERT_DID_NOT_FIRE_BEFORE_NEXT_BAR",
                    int(bar.ts_event),
                )
            raise RuntimeError(
                "causal entry alert survived until the next bar: "
                f"scenario={scenario_id}, bar={int(bar.ts_event)}"
            )

        super().on_bar(bar)
        self._schedule_newly_observed_plan(bar)

    def on_stop(self) -> None:
        self._clear_entry_alert(cancel=True)
        super().on_stop()

    def _schedule_newly_observed_plan(self, bar: Any) -> None:
        plan: TradePlan | None = self._pending_plan
        if plan is None:
            return
        observed_ns = int(plan.observed_time_ns)
        if observed_ns != int(bar.ts_event):
            # An older pending plan belongs to the inherited fallback path.  In
            # this candidate that would mean the alert contract was not used.
            self._invalidate_pending(
                "PENDING_PLAN_NOT_CREATED_ON_CURRENT_OBSERVATION",
                int(bar.ts_event),
            )
            raise RuntimeError(
                "clock-alert candidate found a stale pending plan: "
                f"scenario={plan.scenario_id}, observed={observed_ns}, "
                f"bar={int(bar.ts_event)}"
            )

        in_window = (
            self.config.trade_start_ns
            <= observed_ns
            < self.config.trade_end_ns
        )
        eligible = (
            in_window
            and self.portfolio.is_flat(self.config.instrument_id)
            and self._active_plan is None
            and not self._exit_pending
        )
        if not eligible:
            self._invalidate_pending(
                "CAUSAL_ENTRY_ALERT_SLOT_OR_WINDOW_LOST",
                observed_ns,
            )
            return

        self._entry_alert_sequence += 1
        name = f"C07-ENTRY-{self._entry_alert_sequence}"
        self._entry_alert_name = name
        self._entry_alert_scenario_id = str(plan.scenario_id)
        self._entry_alert_bar = bar
        alert_ns = observed_ns + 1
        self._append_manual_event(
            scenario_id=plan.scenario_id,
            previous_state=ScenarioState.ENTRY_READY.value,
            next_state=ScenarioState.ENTRY_READY.value,
            reason_code="CAUSAL_ENTRY_ALERT_SCHEDULED",
            event_time_ns=observed_ns,
            reference_price=plan.entry_reference,
            details={
                "alert_name": name,
                "alert_time_ns": alert_ns,
                "observed_time_ns": observed_ns,
                "submission_basis": (
                    "one-shot NautilusTrader TimeEvent strictly after the "
                    "completed observation and before the next market-data event"
                ),
                "stop_price": plan.stop_price,
                "target_price": plan.target_price,
                "expected_rr": plan.expected_rr,
            },
        )
        self.clock.set_time_alert_ns(
            name,
            alert_ns,
            callback=self._on_causal_entry_alert,
            allow_past=False,
        )

    def _on_causal_entry_alert(self, event: Any) -> None:
        name = str(getattr(event, "name", ""))
        if name != self._entry_alert_name:
            raise RuntimeError(
                "unexpected causal entry alert: "
                f"received={name}, expected={self._entry_alert_name}"
            )
        plan: TradePlan | None = self._pending_plan
        bar = self._entry_alert_bar
        expected_scenario = self._entry_alert_scenario_id
        event_ns = int(event.ts_event)
        self._clear_entry_alert(cancel=False)

        if plan is None or bar is None:
            raise RuntimeError("causal entry alert fired without a pending plan")
        if str(plan.scenario_id) != expected_scenario:
            self._invalidate_pending(
                "CAUSAL_ENTRY_ALERT_SCENARIO_CHANGED",
                event_ns,
            )
            raise RuntimeError(
                "pending scenario changed before causal alert: "
                f"actual={plan.scenario_id}, expected={expected_scenario}"
            )
        if event_ns <= int(plan.observed_time_ns):
            self._invalidate_pending(
                "CAUSAL_ENTRY_ALERT_NOT_AFTER_OBSERVATION",
                event_ns,
            )
            raise RuntimeError(
                "causal alert did not follow observation: "
                f"alert={event_ns}, observed={plan.observed_time_ns}"
            )

        eligible = (
            self.config.trade_start_ns
            <= int(plan.observed_time_ns)
            < self.config.trade_end_ns
            and self.portfolio.is_flat(self.config.instrument_id)
            and self._active_plan is None
            and not self._exit_pending
        )
        if not eligible:
            self._invalidate_pending(
                "CAUSAL_ENTRY_ALERT_SLOT_OR_WINDOW_LOST_AT_FIRE",
                event_ns,
            )
            return

        self._append_manual_event(
            scenario_id=plan.scenario_id,
            previous_state=ScenarioState.ENTRY_READY.value,
            next_state=ScenarioState.ENTRY_READY.value,
            reason_code="CAUSAL_ENTRY_ALERT_FIRED",
            event_time_ns=event_ns,
            reference_price=plan.entry_reference,
            details={
                "alert_time_ns": event_ns,
                "observed_time_ns": int(plan.observed_time_ns),
                "nanoseconds_after_observation": (
                    event_ns - int(plan.observed_time_ns)
                ),
                "pricing_reference": "last completed signal-bar close",
                "matching_owner": "NautilusTrader BacktestEngine",
            },
        )
        self._submit_pending(bar)

    def _clear_entry_alert(self, *, cancel: bool) -> None:
        name = self._entry_alert_name
        self._entry_alert_name = None
        self._entry_alert_scenario_id = None
        self._entry_alert_bar = None
        if cancel and name is not None:
            try:
                self.clock.cancel_timer(name)
            except Exception:
                # Cancel is cleanup only; alert/order validity is enforced by
                # the explicit state checks above.
                pass


__all__ = ["ClockAlertCausalEntryMixin"]

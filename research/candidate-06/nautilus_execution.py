"""Fixed-risk bracket entry and forced-exit handling for candidate-06."""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import Any

from entry_confirmation import DefenseCheck, continuation_defense_passes
from logic import PrimitiveSnapshot, ScenarioSignal
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_strategy_common import utc_day as _utc_day, utc_hour as _utc_hour


class NautilusExecutionMixin:
    """Execute approved scenarios without changing their fixed three-percent risk."""

    def _attempt_entry(self, signal: ScenarioSignal, snapshot: PrimitiveSnapshot) -> None:
        assert self._instrument is not None
        entry = snapshot.observation.close
        stop = signal.stop_price
        target = signal.target_price
        direction = signal.direction
        reason: str | None = None
        if direction == "LONG" and not (stop < entry < target):
            reason = "DELAYED_PRICE_OUTSIDE_LONG_BRACKET"
        elif direction == "SHORT" and not (target < entry < stop):
            reason = "DELAYED_PRICE_OUTSIDE_SHORT_BRACKET"

        confirmation_mode = str(self._logic_params.get("sac_entry_confirmation", "NONE"))
        confirmation_details: dict[str, Any] = {}
        if signal.family == "SAC" and confirmation_mode.upper() != "NONE":
            check = DefenseCheck(
                mode=confirmation_mode,
                direction=direction,
                boundary=float(signal.liquidity_level),
                signal_reference=float(signal.reference_entry),
                open=float(snapshot.observation.open),
                close=float(snapshot.observation.close),
                flow_ratio=float(snapshot.flow_ratio),
            )
            passed = continuation_defense_passes(check)
            confirmation_details = {
                "confirmation_mode": confirmation_mode,
                "boundary": check.boundary,
                "signal_reference": check.signal_reference,
                "delayed_open": check.open,
                "delayed_close": check.close,
                "delayed_flow_ratio": check.flow_ratio,
                "boundary_held": check.boundary_held,
                "directional_body": check.directional_body,
                "directional_flow": check.directional_flow,
                "reference_held": check.reference_held,
                "passed": passed,
            }
            self.diagnostics.setdefault("sac_entry_candidates", []).append(
                {
                    "scenario_id": signal.scenario_id,
                    "direction": direction,
                    **confirmation_details,
                },
            )
            confirmation_counts = self.diagnostics.setdefault("sac_entry_confirmation_counts", {})
            key = "passed" if passed else "failed"
            confirmation_counts[key] = int(confirmation_counts.get(key, 0)) + 1
            if reason is None and not passed:
                reason = "SAC_NEXT_COMPLETED_BAR_DEFENSE_FAILED"

        favorable_drift = (
            entry - signal.reference_entry if direction == "LONG" else signal.reference_entry - entry
        )
        enforce_drift_guard = bool(self._logic_params.get("enforce_favorable_drift_guard", True))
        if (
            reason is None
            and enforce_drift_guard
            and favorable_drift > float(self.config.max_entry_drift_atr) * signal.atr
        ):
            reason = "FAVORABLE_MOVE_ALREADY_CONSUMED"

        tick = float(self._instrument.price_increment)
        fee = float(self.config.effective_fee_rate)
        loss_distance = abs(entry - stop)
        reward_distance = abs(target - entry)
        slippage_loss = 2.0 * tick if self.config.one_tick_slippage_per_fill else 0.0
        loss_per_unit = loss_distance + entry * fee + stop * fee + slippage_loss
        reward_after_cost = reward_distance - entry * fee - target * fee - slippage_loss
        net_rr = reward_after_cost / loss_per_unit if loss_per_unit > 0.0 else -1.0
        if reason is None and net_rr < float(self.config.min_net_rr_after_delay):
            reason = "NET_REWARD_RISK_ERODED_AFTER_DELAY"
        if reason is not None:
            self._abstain_signal(
                signal,
                snapshot,
                reason,
                {
                    "net_rr": net_rr,
                    "entry": entry,
                    "favorable_drift_atr": favorable_drift / signal.atr if signal.atr > 0.0 else None,
                    "favorable_drift_guard_enabled": enforce_drift_guard,
                    **confirmation_details,
                },
            )
            return

        equity = self._current_equity()
        planned_loss_budget = equity * float(self.config.risk_fraction)
        raw_qty = Decimal(str(planned_loss_budget / loss_per_unit))
        increment = self._instrument.size_increment.as_decimal()
        qty_decimal = (raw_qty / increment).to_integral_value(rounding=ROUND_DOWN) * increment
        if qty_decimal < self._instrument.min_quantity.as_decimal():
            self._abstain_signal(signal, snapshot, "RISK_SIZE_BELOW_MINIMUM_QUANTITY", {})
            return
        notional = qty_decimal * Decimal(str(entry))
        min_notional = self._instrument.min_notional.as_decimal()
        if notional < min_notional:
            self._abstain_signal(signal, snapshot, "RISK_SIZE_BELOW_MINIMUM_NOTIONAL", {})
            return

        side = OrderSide.BUY if direction == "LONG" else OrderSide.SELL
        quantity = self._instrument.make_qty(qty_decimal)
        stop_price = self._instrument.make_price(Decimal(str(stop)))
        target_price = self._instrument.make_price(Decimal(str(target)))
        tags = [f"scenario={signal.scenario_id}", f"family={signal.family}"]
        try:
            order_list = self.order_factory.bracket(
                instrument_id=self.config.instrument_id,
                order_side=side,
                quantity=quantity,
                time_in_force=TimeInForce.GTC,
                entry_post_only=False,
                entry_tags=tags,
                tp_price=target_price,
                tp_post_only=True,
                tp_tags=tags,
                sl_trigger_price=stop_price,
                sl_tags=tags,
            )
            self._active_trade = {
                "scenario_id": signal.scenario_id,
                "family": signal.family,
                "direction": direction,
                "signal_ts_ns": signal.observed_ts_ns,
                "signal_day_utc": _utc_day(signal.observed_ts_ns),
                "signal_hour_utc": _utc_hour(signal.observed_ts_ns),
                "submission_ts_ns": snapshot.observation.ts_ns,
                "reference_entry_price": signal.reference_entry,
                "expected_entry_price": entry,
                "stop_price": float(stop_price),
                "target_price": float(target_price),
                "target_reason": signal.target_reason,
                "liquidity_level": signal.liquidity_level,
                "atr_at_signal": signal.atr,
                "quantity": float(quantity),
                "equity_before_entry": equity,
                "planned_loss_budget": planned_loss_budget,
                "loss_per_unit": loss_per_unit,
                "net_rr_at_submission": net_rr,
                "fee_rate_per_fill": fee,
                "sac_entry_confirmation": confirmation_mode if signal.family == "SAC" else None,
                "favorable_drift_guard_enabled": enforce_drift_guard,
            }
            self._entry_inflight = True
            self.diagnostics["entries_submitted"] += 1
            self._record_external_transition(
                scenario_id=signal.scenario_id,
                previous_state="ENTRY_ARMED",
                next_state="ORDER_SUBMITTED",
                reason="DELAYED_MARKET_ENTRY_WITH_STRUCTURAL_BRACKET",
                ts_ns=snapshot.observation.ts_ns,
                reference_price=entry,
                details={
                    "quantity": str(quantity),
                    "planned_loss_budget": planned_loss_budget,
                    "loss_per_unit": loss_per_unit,
                    "net_rr_after_cost": net_rr,
                    "stop_price": float(stop_price),
                    "target_price": float(target_price),
                    "favorable_drift_atr": favorable_drift / signal.atr if signal.atr > 0.0 else None,
                    "favorable_drift_guard_enabled": enforce_drift_guard,
                    **confirmation_details,
                },
            )
            self.submit_order_list(order_list)
        except Exception as exc:
            self.errors.append(f"entry construction/submission failed: {type(exc).__name__}: {exc}")
            self._entry_inflight = False
            self._active_trade = None
            self._record_external_transition(
                scenario_id=signal.scenario_id,
                previous_state="ENTRY_ARMED",
                next_state="RESET",
                reason="ORDER_CONSTRUCTION_FAILED",
                ts_ns=snapshot.observation.ts_ns,
                reference_price=entry,
                details={"error": f"{type(exc).__name__}: {exc}"},
            )

    def _manage_open_position(self, snapshot: PrimitiveSnapshot) -> None:
        trade = self._active_trade
        if trade is None or self._exit_inflight:
            return
        opened_index = trade.get("opened_bar_index")
        if opened_index is None:
            return
        if snapshot.index - int(opened_index) >= int(self.config.max_holding_bars):
            trade["forced_exit_reason"] = "TIMEOUT"
            self._exit_inflight = True
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)

    def _finalize_at_boundary(self, snapshot: PrimitiveSnapshot) -> None:
        if self._pending_signal is not None:
            signal = self._pending_signal
            self._pending_signal = None
            self._pending_created_index = None
            self._abstain_signal(signal, snapshot, "EVALUATION_BOUNDARY", {})
        aborted = self._scenario_engine.abort_active(snapshot, "EVALUATION_BOUNDARY")
        self._record_transitions(aborted.transitions, snapshot.observation.ts_ns)
        if not self.portfolio.is_flat(self.config.instrument_id) and not self._exit_inflight:
            if self._active_trade is not None:
                self._active_trade["forced_exit_reason"] = "BOUNDARY_EXIT"
            self._exit_inflight = True
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)

    def _handle_order_failure(self, event: Any, code: str) -> None:
        reason_text = str(getattr(event, "reason", "unknown"))
        trade = self._active_trade
        if trade is None:
            self.errors.append(f"{code} without active trade: {reason_text}")
            self._entry_inflight = False
            return
        scenario_id = trade["scenario_id"]
        state = self._scenario_states.get(scenario_id, "UNKNOWN")
        if state == "POSITION":
            self.errors.append(f"protective order failed while position open: {code}: {reason_text}")
            trade["forced_exit_reason"] = "PROTECTION_FAILURE"
            if not self._exit_inflight:
                self._exit_inflight = True
                self.cancel_all_orders(self.config.instrument_id)
                self.close_all_positions(self.config.instrument_id)
            return
        if state == "ORDER_SUBMITTED":
            self._record_external_transition(
                scenario_id=scenario_id,
                previous_state="ORDER_SUBMITTED",
                next_state="RESET",
                reason=code,
                ts_ns=int(event.ts_event),
                reference_price=None,
                details={"venue_reason": reason_text},
            )
        self.errors.append(f"{code}: {reason_text}")
        self._active_trade = None
        self._entry_inflight = False
        self._exit_inflight = False

"""Truthful NautilusTrader execution labels for quote-resiliency signals.

The verified candidate-08 shared-account strategy remains authoritative for availability checks,
causal funding state, rounded cost geometry, current-NAV three-percent sizing, native OUO brackets,
callbacks and risk-accounting repair.  This subclass changes only scenario-specific order tags and
reporting metadata so reversal and continuation evidence cannot be mislabeled as the incumbent
breakout-acceptance family.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR
from typing import Any

from aggtrade_acceptance_risk_v2 import (
    RISK_ACCOUNTING_REVISION,
    RiskCompleteAggTradeAcceptanceStrategy,
)
from aggtrade_acceptance_strategy import OrderSide, OrderType, TimeInForce
from logic import risk_sized_quantity
from quote_resiliency_native_quotes import COMPLETION_DELAY_NS
from quote_resiliency_signals import QuoteResiliencySignal


EXECUTION_ADAPTER_REVISION = "QUOTE_RESILIENCY_NATIVE_EXECUTION_LABELS_V3_QUOTE_CALLBACK_EXACT_FILL"


def fill_adjusted_exit_is_causal(
    reason: str,
    requested_time_ns: int,
    position_open_time_ns: int | None,
) -> bool:
    """Allow fill-adjusted emergency exit only after a native position-open event."""

    if reason != "FILL_ADJUSTED_RISK_BUDGET_EXCEEDED":
        return True
    return (
        position_open_time_ns is not None
        and int(requested_time_ns) > int(position_open_time_ns)
    )


def expected_one_tick_entry_fill(
    quote_reference: float,
    direction: int,
    tick: float,
) -> float:
    """Expected market fill under the configured one-tick adverse fill model."""

    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or +1")
    if quote_reference <= 0.0 or tick <= 0.0:
        raise ValueError("quote_reference and tick must be positive")
    return quote_reference + tick if direction > 0 else quote_reference - tick


class QuoteResiliencyExecutionStrategy(RiskCompleteAggTradeAcceptanceStrategy):
    """Use the incumbent native execution mechanics with quote-scenario evidence labels."""

    def on_start(self) -> None:
        self._quote_ready_signal_times: set[int] = set()
        self._quote_signal_instruments_seen: dict[int, set[str]] = {}
        self._completion_quote_by_signal_time: dict[int, dict[str, dict[str, float]]] = {}
        self.native_quote_submission_events: list[dict[str, Any]] = []
        super().on_start()
        for instrument_id in self.instrument_ids:
            self.subscribe_quote_ticks(instrument_id)

    def on_bar(self, bar: Any) -> None:
        signal_time_ns = int(bar.ts_event)
        signals = self.signals_by_time_ns.get(signal_time_ns, ())
        removed = None
        if signals and signal_time_ns not in self.processed_signal_times:
            removed = self.signals_by_time_ns.pop(signal_time_ns, None)
        try:
            super().on_bar(bar)
        finally:
            if removed is not None:
                self.signals_by_time_ns[signal_time_ns] = removed
        if not signals or signal_time_ns in self.processed_signal_times:
            return
        seen = self.signal_instruments_seen.setdefault(signal_time_ns, set())
        seen.add(str(bar.bar_type.instrument_id))
        required = {signal.instrument_id for signal in signals}
        if required.issubset(seen):
            self.signal_instruments_seen.pop(signal_time_ns, None)
            self._quote_ready_signal_times.add(signal_time_ns)

    def on_quote_tick(self, tick: Any) -> None:
        quote_time_ns = int(tick.ts_event)
        signal_time_ns = quote_time_ns - COMPLETION_DELAY_NS
        if signal_time_ns not in self._quote_ready_signal_times:
            return
        signals = self.signals_by_time_ns.get(signal_time_ns, ())
        if not signals:
            raise RuntimeError("completion QuoteTick had no frozen signal bundle")
        instrument_key = str(tick.instrument_id)
        bid = float(tick.bid_price.as_double())
        ask = float(tick.ask_price.as_double())
        bid_size = float(tick.bid_size.as_double())
        ask_size = float(tick.ask_size.as_double())
        snapshots = self._completion_quote_by_signal_time.setdefault(signal_time_ns, {})
        snapshots[instrument_key] = {
            "bid": bid,
            "ask": ask,
            "bid_size": bid_size,
            "ask_size": ask_size,
        }
        for signal in signals:
            if signal.instrument_id != instrument_key:
                continue
            observed_entry_side = ask if signal.direction > 0 else bid
            instrument = self.instruments[instrument_key]
            tolerance = 0.5 * float(instrument.price_increment.as_double()) + 1e-12
            if abs(observed_entry_side - float(signal.entry_reference)) > tolerance:
                raise RuntimeError(
                    "completion QuoteTick did not match the signal's executable L1 reference"
                )
        seen = self._quote_signal_instruments_seen.setdefault(signal_time_ns, set())
        seen.add(instrument_key)
        required = {signal.instrument_id for signal in signals}
        if not required.issubset(seen):
            return
        if signal_time_ns in self.processed_signal_times:
            raise RuntimeError("quote-completed signal time processed more than once")
        self.processed_signal_times.add(signal_time_ns)
        self._quote_ready_signal_times.discard(signal_time_ns)
        self._quote_signal_instruments_seen.pop(signal_time_ns, None)
        self.native_quote_submission_events.append(
            {
                "signal_time_ns": signal_time_ns,
                "quote_time_ns": quote_time_ns,
                "delay_ns": quote_time_ns - signal_time_ns,
                "required_instruments": sorted(required),
                "seen_instruments": sorted(seen),
                "execution_adapter_revision": EXECUTION_ADAPTER_REVISION,
            }
        )
        try:
            self._process_signal_time(
                signal_time_ns,
                observed_time_ns=quote_time_ns,
            )
        finally:
            self._completion_quote_by_signal_time.pop(signal_time_ns, None)

    def _rounded_geometry(
        self,
        signal: QuoteResiliencySignal,
        funding_state: dict[str, float | int],
    ) -> dict[str, float | int] | None:
        instrument = self.instruments.get(signal.instrument_id)
        if instrument is None:
            raise RuntimeError(f"signal instrument unavailable: {signal.instrument_id}")
        tick = float(instrument.price_increment.as_double())
        fee_rate = float(self.config.effective_fee_rate)
        quote_reference = float(signal.entry_reference)
        unrounded_fill = expected_one_tick_entry_fill(
            quote_reference,
            signal.direction,
            tick,
        )
        entry = float(instrument.make_price(unrounded_fill).as_double())
        if signal.direction > 0:
            stop = self._round_price(instrument, signal.structural_stop, ROUND_FLOOR)
            target = self._round_price(instrument, signal.external_target, ROUND_FLOOR)
            valid = stop < entry < target
            gross_gain = target - entry
        else:
            stop = self._round_price(instrument, signal.structural_stop, ROUND_CEILING)
            target = self._round_price(instrument, signal.external_target, ROUND_CEILING)
            valid = target < entry < stop
            gross_gain = entry - target
        if not valid:
            return None
        stop_slippage_reserve = max(
            tick,
            float(signal.causal_stop_slippage_reserve),
        )
        expected_funding_crossings = int(funding_state["expected_funding_crossings"])
        expected_funding_rate_abs = float(funding_state["expected_funding_rate_abs"])
        funding_reserve = (
            expected_funding_crossings * expected_funding_rate_abs * entry
        )
        loss = (
            abs(entry - stop)
            + fee_rate * (entry + stop)
            + stop_slippage_reserve
            + funding_reserve
        )
        # Entry slippage is embedded in ``entry``; retain one adverse tick for target execution.
        gain = gross_gain - fee_rate * (entry + target) - tick
        if loss <= 0 or gain <= 0:
            return None
        return {
            **funding_state,
            "entry_quote_reference": quote_reference,
            "entry_reference": entry,
            "expected_entry_fill": entry,
            "stop": stop,
            "target": target,
            "expected_loss_per_unit": loss,
            "entry_slippage_reserve_per_unit": abs(entry - quote_reference),
            "stop_slippage_reserve_per_unit": stop_slippage_reserve,
            "expected_gain_per_unit": gain,
            "net_reward_risk": gain / loss,
            "expected_funding_reserve_per_unit": funding_reserve,
        }

    def _request_exit(
        self,
        reason: str,
        ts_event_ns: int,
        close: float,
    ) -> None:
        if not fill_adjusted_exit_is_causal(
            reason,
            ts_event_ns,
            self.position_open_time_ns,
        ):
            if reason == "FILL_ADJUSTED_RISK_BUDGET_EXCEEDED":
                if self.position_open_time_ns is None:
                    raise RuntimeError(
                        "fill-adjusted exit requested before POSITION_OPENED evidence"
                    )
                # Preserve the pending exit for the first separately completed bar.  This guard
                # also protects against synchronous native callback nesting at the entry stamp.
                self._deferred_fill_adjusted_exit_after_ns = int(
                    self.position_open_time_ns
                )
                if self.trade_intents:
                    intent = self.trade_intents[-1]
                    intent["premature_fill_adjusted_exit_blocked_count"] = int(
                        intent.get("premature_fill_adjusted_exit_blocked_count", 0)
                    ) + 1
                    intent["last_blocked_fill_adjusted_exit_time_ns"] = int(
                        ts_event_ns
                    )
                    intent["fill_adjusted_exit_guard_revision"] = (
                        EXECUTION_ADAPTER_REVISION
                    )
                return
        super()._request_exit(reason, ts_event_ns, close)

    def _submit_signal(
        self,
        signal: QuoteResiliencySignal,
        geometry: dict[str, float | int],
        ts_event_ns: int,
    ) -> None:
        instrument_id = self.instrument_ids_by_str[signal.instrument_id]
        instrument = self.instruments[signal.instrument_id]
        account = self.cache.account_for_venue(instrument_id.venue)
        if account is None:
            raise RuntimeError("shared Binance margin account was unavailable")
        balance = account.balance_total(self.usdt)
        if balance is None:
            raise RuntimeError("shared account total USDT balance was unavailable")
        nav = float(balance.as_double())
        quantity_value, planned_loss = risk_sized_quantity(
            nav=nav,
            risk_fraction=float(self.config.risk_fraction),
            expected_loss_per_unit=geometry["expected_loss_per_unit"],
            size_increment=float(instrument.size_increment.as_double()),
        )
        if quantity_value <= 0:
            self._record_skip(
                signal,
                "QUANTITY_ROUNDED_TO_ZERO",
                ts_event_ns,
                {"nav": nav},
            )
            return
        quantity = instrument.make_qty(float(quantity_value))
        snapshot = self._completion_quote_by_signal_time.get(
            signal.signal_time_ns, {}
        ).get(signal.instrument_id)
        if snapshot is None:
            raise RuntimeError("native completion quote was unavailable at order submission")
        visible_entry_side_qty = (
            float(snapshot["ask_size"]) if signal.direction > 0 else float(snapshot["bid_size"])
        )
        if visible_entry_side_qty <= 0.0:
            raise RuntimeError("native completion quote had nonpositive entry-side size")
        quantity_to_visible_l1_ratio = float(quantity.as_double()) / visible_entry_side_qty
        if instrument.min_quantity is not None and quantity < instrument.min_quantity:
            self._record_skip(
                signal,
                "BELOW_VENUE_MINIMUM_QUANTITY",
                ts_event_ns,
                {"quantity": quantity_value},
            )
            return
        if (
            instrument.min_notional is not None
            and quantity.as_double() * float(geometry["entry_reference"])
            < instrument.min_notional.as_double()
        ):
            self._record_skip(
                signal,
                "BELOW_VENUE_MINIMUM_NOTIONAL",
                ts_event_ns,
                {"quantity": quantity_value},
            )
            return

        order_side = OrderSide.BUY if signal.direction > 0 else OrderSide.SELL
        stop_price = instrument.make_price(float(geometry["stop"]))
        target_price = instrument.make_price(float(geometry["target"]))
        orders = self.order_factory.bracket(
            instrument_id=instrument_id,
            order_side=order_side,
            quantity=quantity,
            entry_order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
            tp_price=target_price,
            tp_post_only=False,
            sl_trigger_price=stop_price,
            entry_tags=[
                signal.scenario_id,
                signal.scenario_family,
                signal.direction_name,
                "COMPLETED_TEN_SECOND_MARKET_ENTRY",
                EXECUTION_ADAPTER_REVISION,
            ],
            tp_tags=[
                signal.scenario_id,
                "ACTIVE_COMPLETED_EXTERNAL_TARGET",
                signal.target_source,
            ],
            sl_tags=[
                signal.scenario_id,
                signal.stop_reference_source,
                "STRUCTURAL_INVALIDATION",
            ],
        )
        if not isinstance(orders, list) or len(orders) != 3:
            raise RuntimeError(
                f"pinned NautilusTrader bracket contract changed: {type(orders)!r}"
            )
        entry_order, stop_order, target_order = orders
        self.active_signal = signal
        self.active_instrument_id = instrument_id
        self.active_scenario_state = "CONFIRMED"
        self.active_entry_order_id = str(entry_order.client_order_id)
        self.active_stop_order_id = str(stop_order.client_order_id)
        self.active_target_order_id = str(target_order.client_order_id)
        self.active_exit_order_ids = {
            self.active_stop_order_id,
            self.active_target_order_id,
        }
        self.active_position_id = None
        self.position_open_time_ns = None
        self.entry_inflight = True
        self.entry_cancel_requested = False
        self.exit_requested = False
        self.exit_request_reason = None
        self.last_fill_order_id = None
        self.fill_adjusted_risk_violation = False

        logic_details = {
            **dict(signal.details),
            "scenario_family": signal.scenario_family,
            "signal_revision": signal.details.get("signal_revision"),
            "execution_adapter_revision": EXECUTION_ADAPTER_REVISION,
            "risk_accounting_revision": RISK_ACCOUNTING_REVISION,
            "native_quote_submission_time_ns": int(ts_event_ns),
            "native_quote_submission_delay_ns": int(ts_event_ns) - int(signal.signal_time_ns),
            "native_quote_execution_contract": "BAR_CLOSE_THEN_COMPLETION_QUOTE_AT_PLUS_1NS",
            "entry_quote_reference": geometry["entry_quote_reference"],
            "expected_entry_fill": geometry["expected_entry_fill"],
            "visible_entry_side_qty": visible_entry_side_qty,
            "quantity_to_visible_l1_ratio": quantity_to_visible_l1_ratio,
            "interaction_time_ns": signal.interaction_time_ns,
            "response_time_ns": signal.response_time_ns,
            "retest_time_ns": signal.retest_time_ns,
            "stop_reference": signal.stop_reference,
            "stop_reference_source": signal.stop_reference_source,
        }
        self.trade_intents.append(
            {
                "scenario_id": signal.scenario_id,
                "scenario_family": signal.scenario_family,
                "symbol": signal.symbol,
                "instrument_id": signal.instrument_id,
                "direction": signal.direction_name,
                "signal_time_ns": signal.signal_time_ns,
                "native_quote_submission_time_ns": int(ts_event_ns),
                "native_quote_submission_delay_ns": int(ts_event_ns) - int(signal.signal_time_ns),
                "native_quote_execution_contract": "BAR_CLOSE_THEN_COMPLETION_QUOTE_AT_PLUS_1NS",
                "entry_quote_reference": geometry["entry_quote_reference"],
                "expected_entry_fill": geometry["expected_entry_fill"],
                "visible_entry_side_qty": visible_entry_side_qty,
                "quantity_to_visible_l1_ratio": quantity_to_visible_l1_ratio,
                "interaction_time_ns": signal.interaction_time_ns,
                "response_time_ns": signal.response_time_ns,
                "retest_time_ns": signal.retest_time_ns,
                "boundary_id": signal.boundary_id,
                "boundary_source": signal.boundary_source,
                "boundary_level": signal.boundary_level,
                "target_id": signal.target_id,
                "target_source": signal.target_source,
                "external_target": geometry["target"],
                "entry_reference": geometry["entry_reference"],
                "structural_stop": geometry["stop"],
                "stop_reference": signal.stop_reference,
                "stop_reference_source": signal.stop_reference_source,
                "quantity": float(quantity.as_double()),
                "nav_at_signal": nav,
                "risk_fraction": float(self.config.risk_fraction),
                "risk_budget": nav * float(self.config.risk_fraction),
                "planned_stop_loss": planned_loss,
                "expected_loss_per_unit": geometry["expected_loss_per_unit"],
                "entry_slippage_reserve_per_unit": geometry[
                    "entry_slippage_reserve_per_unit"
                ],
                "stop_slippage_reserve_per_unit": geometry[
                    "stop_slippage_reserve_per_unit"
                ],
                "expected_gain_per_unit": geometry["expected_gain_per_unit"],
                "net_reward_risk": geometry["net_reward_risk"],
                "funding_observed_time_ns": geometry["funding_observed_time_ns"],
                "funding_rate_observed": geometry["funding_rate_observed"],
                "expected_funding_rate_abs": geometry["expected_funding_rate_abs"],
                "funding_interval_minutes": geometry["funding_interval_minutes"],
                "minutes_to_next_funding": geometry["minutes_to_next_funding"],
                "expected_funding_crossings": geometry["expected_funding_crossings"],
                "expected_funding_reserve_per_unit": geometry[
                    "expected_funding_reserve_per_unit"
                ],
                "entry_order_id": str(entry_order.client_order_id),
                "stop_order_id": str(stop_order.client_order_id),
                "target_order_id": str(target_order.client_order_id),
                "logic_details": logic_details,
                "execution_adapter_revision": EXECUTION_ADAPTER_REVISION,
                "risk_accounting_revision": RISK_ACCOUNTING_REVISION,
            }
        )
        self._emit(
            signal,
            event_type="MARKET_OUO_BRACKET_SUBMITTED",
            observed_time_ns=ts_event_ns,
            previous_state="CONFIRMED",
            next_state="ORDER_SUBMITTED",
            reason_code="SHARED_NAV_RISK_SIZED_MARKET_OUO",
            reference_price=float(geometry["entry_reference"]),
            details={
                "scenario_family": signal.scenario_family,
                "quantity": float(quantity.as_double()),
                "planned_stop_loss": planned_loss,
                "net_reward_risk": geometry["net_reward_risk"],
                "expected_funding_crossings": geometry[
                    "expected_funding_crossings"
                ],
                "expected_funding_reserve_per_unit": geometry[
                    "expected_funding_reserve_per_unit"
                ],
                "execution_adapter_revision": EXECUTION_ADAPTER_REVISION,
            },
        )
        self.active_scenario_state = "ORDER_SUBMITTED"
        self.submit_order_list(orders)

    def _record_skip(
        self,
        signal: QuoteResiliencySignal,
        reason: str,
        ts_event_ns: int,
        details: dict[str, Any],
    ) -> None:
        before = len(self.skipped_setups)
        super()._record_skip(signal, reason, ts_event_ns, details)
        if len(self.skipped_setups) != before + 1:
            raise RuntimeError("inherited skip evidence contract changed")
        self.skipped_setups[-1]["scenario_family"] = signal.scenario_family
        self.skipped_setups[-1]["execution_adapter_revision"] = (
            EXECUTION_ADAPTER_REVISION
        )
        if self.execution_events:
            event_details = dict(self.execution_events[-1].get("details", {}))
            self.execution_events[-1]["details"] = {
                "scenario_family": signal.scenario_family,
                "execution_adapter_revision": EXECUTION_ADAPTER_REVISION,
                **event_details,
            }

    def on_position_closed(self, event: Any) -> None:
        signal = self.active_signal
        before = len(self.position_outcomes)
        super().on_position_closed(event)
        if signal is None:
            return
        if len(self.position_outcomes) != before + 1:
            raise RuntimeError("inherited position-close evidence contract changed")
        self.position_outcomes[-1]["scenario_family"] = signal.scenario_family
        self.position_outcomes[-1]["execution_adapter_revision"] = (
            EXECUTION_ADAPTER_REVISION
        )
        self.position_outcomes[-1]["stop_reference_source"] = (
            signal.stop_reference_source
        )


__all__ = [
    "EXECUTION_ADAPTER_REVISION",
    "QuoteResiliencyExecutionStrategy",
    "expected_one_tick_entry_fill",
    "fill_adjusted_exit_is_causal",
]

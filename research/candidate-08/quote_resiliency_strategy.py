"""Truthful NautilusTrader execution labels for quote-resiliency signals.

The verified candidate-08 shared-account strategy remains authoritative for availability checks,
causal funding state, rounded cost geometry, current-NAV three-percent sizing, native OUO brackets,
callbacks and risk-accounting repair.  This subclass changes only scenario-specific order tags and
reporting metadata so reversal and continuation evidence cannot be mislabeled as the incumbent
breakout-acceptance family.
"""

from __future__ import annotations

from typing import Any

from aggtrade_acceptance_risk_v2 import (
    RISK_ACCOUNTING_REVISION,
    RiskCompleteAggTradeAcceptanceStrategy,
)
from aggtrade_acceptance_strategy import OrderSide, OrderType, TimeInForce
from logic import risk_sized_quantity
from quote_resiliency_signals import QuoteResiliencySignal


EXECUTION_ADAPTER_REVISION = "QUOTE_RESILIENCY_NATIVE_EXECUTION_LABELS_V2_CAUSAL_FILL_EXIT"


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


class QuoteResiliencyExecutionStrategy(RiskCompleteAggTradeAcceptanceStrategy):
    """Use the incumbent native execution mechanics with quote-scenario evidence labels."""

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
    "fill_adjusted_exit_is_causal",
]

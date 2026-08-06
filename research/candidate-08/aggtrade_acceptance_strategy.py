"""Shared-account NautilusTrader execution adapter for candidate-08 acceptance signals."""

from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Currency
from nautilus_trader.trading.strategy import Strategy

from aggtrade_acceptance_funding import (
    FundingObservation,
    causal_funding_cost_state,
)
from aggtrade_acceptance_signals import AcceptanceSignal
from logic import risk_sized_quantity


_SOURCE_RANK = {"FOUR_HOUR": 1, "DAY": 2, "WEEK": 3}


class AggTradeAcceptanceStrategyConfig(StrategyConfig, frozen=True):
    trading_start_ns: int
    trading_end_ns: int
    risk_fraction: Decimal
    effective_fee_rate: Decimal
    minimum_net_reward_risk: Decimal
    maximum_hold_minutes: int
    funding_avoidance_minutes: int


class AggTradeAcceptanceStrategy(Strategy):
    """Trade one market-entry acceptance scenario at a time across all allowed instruments."""

    def __init__(
        self,
        config: AggTradeAcceptanceStrategyConfig,
        *,
        instrument_ids: tuple[InstrumentId, ...],
        bar_types: tuple[BarType, ...],
        signals_by_time_ns: dict[int, tuple[AcceptanceSignal, ...]],
        funding_observations_by_instrument: dict[str, tuple[FundingObservation, ...]],
    ) -> None:
        super().__init__(config=config)
        self.instrument_ids = instrument_ids
        self.bar_types = bar_types
        self.signals_by_time_ns = signals_by_time_ns
        self.funding_observations_by_instrument = funding_observations_by_instrument
        self.instruments: dict[str, Any] = {}
        self.instrument_ids_by_str = {str(item): item for item in instrument_ids}
        self.usdt = Currency.from_str("USDT")
        self.processed_signal_times: set[int] = set()
        self.signal_instruments_seen: dict[int, set[str]] = {}
        self.last_close: dict[str, float] = {}

        self.active_signal: AcceptanceSignal | None = None
        self.active_instrument_id: InstrumentId | None = None
        self.active_scenario_state: str | None = None
        self.active_entry_order_id: str | None = None
        self.active_exit_order_ids: set[str] = set()
        self.active_stop_order_id: str | None = None
        self.active_target_order_id: str | None = None
        self.active_position_id: str | None = None
        self.position_open_time_ns: int | None = None
        self.entry_inflight = False
        self.entry_cancel_requested = False
        self.exit_requested = False
        self.exit_request_reason: str | None = None
        self.last_fill_order_id: str | None = None
        self.fill_adjusted_risk_violation = False

        self.execution_events: list[dict[str, Any]] = []
        self.trade_intents: list[dict[str, Any]] = []
        self.position_outcomes: list[dict[str, Any]] = []
        self.execution_failures: list[dict[str, Any]] = []
        self.skipped_setups: list[dict[str, Any]] = []

    def on_start(self) -> None:
        for instrument_id in self.instrument_ids:
            instrument = self.cache.instrument(instrument_id)
            if instrument is None:
                raise RuntimeError(f"instrument not found: {instrument_id}")
            self.instruments[str(instrument_id)] = instrument
        for bar_type in self.bar_types:
            self.subscribe_bars(bar_type)

    def on_bar(self, bar: Bar) -> None:
        ts_event_ns = int(bar.ts_event)
        instrument_id = bar.bar_type.instrument_id
        instrument_key = str(instrument_id)
        close = float(bar.close.as_double())
        self.last_close[instrument_key] = close

        # A precomputed signal is not actionable until the confirmation bar for every candidate at
        # that timestamp has actually reached the event-driven strategy. This prevents same-time
        # cross-instrument ordering from exposing another instrument's not-yet-delivered close.
        signals = self.signals_by_time_ns.get(ts_event_ns, ())
        if signals and ts_event_ns not in self.processed_signal_times:
            seen = self.signal_instruments_seen.setdefault(ts_event_ns, set())
            seen.add(instrument_key)
            required = {signal.instrument_id for signal in signals}
            if required.issubset(seen):
                self.processed_signal_times.add(ts_event_ns)
                self.signal_instruments_seen.pop(ts_event_ns, None)
                self._process_signal_time(ts_event_ns)

        if self.active_instrument_id is None or instrument_id != self.active_instrument_id:
            return

        within_position = not self.portfolio.is_flat(self.active_instrument_id)
        if within_position:
            if ts_event_ns >= self.config.trading_end_ns:
                self._request_exit("EVALUATION_WINDOW_END", ts_event_ns, close)
            elif (
                self.position_open_time_ns is not None
                and ts_event_ns - self.position_open_time_ns
                >= int(self.config.maximum_hold_minutes) * 60 * 1_000_000_000
            ):
                self._request_exit("EVENT_TIME_TIMEOUT", ts_event_ns, close)
        elif self.entry_inflight and ts_event_ns >= self.config.trading_end_ns:
            self._cancel_inflight_entry("EVALUATION_WINDOW_END_BEFORE_ENTRY", ts_event_ns, close)

    def _process_signal_time(self, ts_event_ns: int) -> None:
        signals = self.signals_by_time_ns.get(ts_event_ns, ())
        if not signals:
            return
        if not (self.config.trading_start_ns <= ts_event_ns < self.config.trading_end_ns):
            for signal in signals:
                self._record_skip(signal, "OUTSIDE_EVALUATION_WINDOW", ts_event_ns, {})
            return
        if not self._globally_available():
            for signal in signals:
                self._record_skip(signal, "GLOBAL_PORTFOLIO_OR_ORDER_NOT_AVAILABLE", ts_event_ns, {})
            return
        evaluations: list[tuple[float, AcceptanceSignal, dict[str, float | int]]] = []
        for signal in signals:
            funding_state = self._funding_cost_state(signal)
            if funding_state is None:
                self._record_skip(signal, "MISSING_CAUSAL_FUNDING_STATE", ts_event_ns, {})
                continue
            if float(funding_state["minutes_to_next_funding"]) <= int(
                self.config.funding_avoidance_minutes
            ):
                self._record_skip(
                    signal,
                    "FUNDING_BOUNDARY_TOO_CLOSE",
                    ts_event_ns,
                    dict(funding_state),
                )
                continue
            geometry = self._rounded_geometry(signal, funding_state)
            if geometry is None:
                self._record_skip(signal, "INVALID_ROUNDED_OR_COST_AFTER_GEOMETRY", ts_event_ns, {})
                continue
            if geometry["net_reward_risk"] < float(self.config.minimum_net_reward_risk):
                self._record_skip(
                    signal,
                    "INSUFFICIENT_COST_AFTER_EXTERNAL_TARGET",
                    ts_event_ns,
                    geometry,
                )
                continue
            evaluations.append((geometry["net_reward_risk"], signal, geometry))

        if not evaluations:
            return
        evaluations.sort(
            key=lambda item: (
                item[0],
                _SOURCE_RANK.get(item[1].boundary_source, 0),
                _SOURCE_RANK.get(item[1].target_source, 0),
                item[1].symbol,
            ),
            reverse=True,
        )
        _, selected, geometry = evaluations[0]
        for _, alternate, alternate_geometry in evaluations[1:]:
            self._record_skip(
                alternate,
                "LOWER_PRIORITY_SIMULTANEOUS_GLOBAL_SCENARIO",
                ts_event_ns,
                alternate_geometry,
            )
        self._submit_signal(selected, geometry, ts_event_ns)

    def _globally_available(self) -> bool:
        if self.active_signal is not None or self.entry_inflight or self.exit_requested:
            return False
        for instrument_id in self.instrument_ids:
            if not self.portfolio.is_flat(instrument_id):
                return False
            if self.cache.orders_open(instrument_id=instrument_id):
                return False
        return True

    def _funding_cost_state(self, signal: AcceptanceSignal) -> dict[str, float | int] | None:
        observations = self.funding_observations_by_instrument.get(signal.instrument_id, ())
        return causal_funding_cost_state(
            observations,
            signal_time_ns=signal.signal_time_ns,
            entry_price=float(signal.entry_reference),
            maximum_hold_minutes=int(self.config.maximum_hold_minutes),
        )

    def _rounded_geometry(
        self,
        signal: AcceptanceSignal,
        funding_state: dict[str, float | int],
    ) -> dict[str, float | int] | None:
        instrument = self.instruments.get(signal.instrument_id)
        if instrument is None:
            raise RuntimeError(f"signal instrument unavailable: {signal.instrument_id}")
        tick = float(instrument.price_increment.as_double())
        fee_rate = float(self.config.effective_fee_rate)
        entry = float(signal.entry_reference)
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
        funding_reserve = float(funding_state["expected_funding_reserve_per_unit"])
        stop_slippage_reserve = max(
            tick,
            float(signal.causal_stop_slippage_reserve),
        )
        loss = (
            abs(entry - stop)
            + fee_rate * (entry + stop)
            + tick
            + stop_slippage_reserve
            + funding_reserve
        )
        gain = gross_gain - fee_rate * (entry + target) - 2.0 * tick
        if loss <= 0 or gain <= 0:
            return None
        return {
            "entry_reference": entry,
            "stop": stop,
            "target": target,
            "expected_loss_per_unit": loss,
            "entry_slippage_reserve_per_unit": tick,
            "stop_slippage_reserve_per_unit": stop_slippage_reserve,
            "expected_gain_per_unit": gain,
            "net_reward_risk": gain / loss,
            **funding_state,
        }

    def _submit_signal(
        self,
        signal: AcceptanceSignal,
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
            self._record_skip(signal, "QUANTITY_ROUNDED_TO_ZERO", ts_event_ns, {"nav": nav})
            return
        quantity = instrument.make_qty(Decimal(str(quantity_value)))
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
            and quantity.as_double() * geometry["entry_reference"]
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
        stop_price = instrument.make_price(Decimal(str(geometry["stop"])))
        target_price = instrument.make_price(Decimal(str(geometry["target"])))
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
                "BREAKOUT_ACCEPTANCE_CONTINUATION",
                signal.direction_name,
                "TEN_SECOND_MARKET_ENTRY",
            ],
            tp_tags=[signal.scenario_id, "ACTIVE_COMPLETED_EXTERNAL_TARGET"],
            sl_tags=[signal.scenario_id, "OBSERVED_RETEST_INVALIDATION"],
        )
        entry_order, stop_order, target_order = orders.orders
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
        self.trade_intents.append(
            {
                "scenario_id": signal.scenario_id,
                "scenario_family": "BREAKOUT_ACCEPTANCE_CONTINUATION",
                "symbol": signal.symbol,
                "instrument_id": signal.instrument_id,
                "direction": signal.direction_name,
                "signal_time_ns": signal.signal_time_ns,
                "boundary_id": signal.boundary_id,
                "boundary_source": signal.boundary_source,
                "boundary_level": signal.boundary_level,
                "target_id": signal.target_id,
                "target_source": signal.target_source,
                "external_target": geometry["target"],
                "entry_reference": geometry["entry_reference"],
                "structural_stop": geometry["stop"],
                "quantity": float(quantity.as_double()),
                "nav_at_signal": nav,
                "risk_fraction": float(self.config.risk_fraction),
                "risk_budget": nav * float(self.config.risk_fraction),
                "planned_stop_loss": planned_loss,
                "expected_loss_per_unit": geometry["expected_loss_per_unit"],
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
                "logic_details": signal.details,
            }
        )
        self._emit(
            signal,
            event_type="MARKET_OUO_BRACKET_SUBMITTED",
            observed_time_ns=ts_event_ns,
            previous_state="CONFIRMED",
            next_state="ORDER_SUBMITTED",
            reason_code="SHARED_NAV_RISK_SIZED_MARKET_OUO",
            reference_price=geometry["entry_reference"],
            details={
                "quantity": float(quantity.as_double()),
                "planned_stop_loss": planned_loss,
                "net_reward_risk": geometry["net_reward_risk"],
                "expected_funding_crossings": geometry["expected_funding_crossings"],
                "expected_funding_reserve_per_unit": geometry[
                    "expected_funding_reserve_per_unit"
                ],
            },
        )
        self.active_scenario_state = "ORDER_SUBMITTED"
        self.submit_order_list(orders)

    def _request_exit(self, reason: str, ts_event_ns: int, close: float) -> None:
        if self.exit_requested or self.active_instrument_id is None:
            return
        self.exit_requested = True
        self.exit_request_reason = reason
        self.cancel_all_orders(self.active_instrument_id)
        self.close_all_positions(self.active_instrument_id)
        if self.active_signal is not None:
            self._emit(
                self.active_signal,
                event_type="EXIT_REQUESTED",
                observed_time_ns=ts_event_ns,
                previous_state=self.active_scenario_state or "POSITION_OPEN",
                next_state="EXIT_REQUESTED",
                reason_code=reason,
                reference_price=close,
            )
            self.active_scenario_state = "EXIT_REQUESTED"

    def _cancel_inflight_entry(self, reason: str, ts_event_ns: int, close: float) -> None:
        if self.entry_cancel_requested or self.active_instrument_id is None:
            return
        self.cancel_all_orders(self.active_instrument_id)
        self.entry_cancel_requested = True
        if self.active_signal is not None:
            self._emit(
                self.active_signal,
                event_type="ENTRY_CANCEL_REQUESTED",
                observed_time_ns=ts_event_ns,
                previous_state=self.active_scenario_state or "ORDER_SUBMITTED",
                next_state="ENTRY_CANCEL_REQUESTED",
                reason_code=reason,
                reference_price=close,
            )
            self.active_scenario_state = "ENTRY_CANCEL_REQUESTED"

    def on_order_filled(self, event: Any) -> None:
        order_id = str(event.client_order_id)
        self.last_fill_order_id = order_id
        if order_id == self.active_entry_order_id:
            self.entry_inflight = False
            self.entry_cancel_requested = False
            if self.trade_intents:
                self.trade_intents[-1]["entry_fill_time_ns"] = int(event.ts_event)
                last_px = getattr(event, "last_px", None)
                if last_px is not None:
                    fill_price = (
                        float(last_px.as_double()) if hasattr(last_px, "as_double") else float(last_px)
                    )
                    intent = self.trade_intents[-1]
                    intent["entry_fill_price"] = fill_price
                    instrument = (
                        self.instruments.get(str(self.active_instrument_id))
                        if self.active_instrument_id is not None
                        else None
                    )
                    if instrument is not None and self.active_signal is not None:
                        tick = float(instrument.price_increment.as_double())
                        fee_rate = float(self.config.effective_fee_rate)
                        stop = float(intent["structural_stop"])
                        quantity = float(intent["quantity"])
                        # Entry slippage is now observed. One adverse tick remains reserved for the
                        # eventual stop fill, so this is the fill-adjusted expected stop loss.
                        expected_funding_crossings = int(
                            intent.get("expected_funding_crossings", 0)
                        )
                        expected_funding_rate_abs = float(
                            intent.get("expected_funding_rate_abs", 0.0)
                        )
                        fill_adjusted_funding_reserve = (
                            expected_funding_crossings
                            * expected_funding_rate_abs
                            * fill_price
                        )
                        actual_loss_per_unit = (
                            abs(fill_price - stop)
                            + fee_rate * (fill_price + stop)
                            + float(intent.get("stop_slippage_reserve_per_unit", tick))
                            + fill_adjusted_funding_reserve
                        )
                        fill_adjusted_loss = quantity * actual_loss_per_unit
                        risk_budget = float(intent["risk_budget"])
                        intent["entry_adverse_slippage_from_reference"] = (
                            self.active_signal.direction
                            * (fill_price - float(intent["entry_reference"]))
                        )
                        intent["fill_adjusted_expected_funding_reserve_per_unit"] = (
                            fill_adjusted_funding_reserve
                        )
                        intent["fill_adjusted_expected_loss_per_unit"] = actual_loss_per_unit
                        intent["fill_adjusted_expected_stop_loss"] = fill_adjusted_loss
                        intent["fill_adjusted_risk_budget_ratio"] = (
                            fill_adjusted_loss / risk_budget if risk_budget > 0 else None
                        )
                        tolerance = max(1e-8, risk_budget * 1e-9)
                        if fill_adjusted_loss > risk_budget + tolerance:
                            self.fill_adjusted_risk_violation = True
                            failure = {
                                "reason": "FILL_ADJUSTED_RISK_BUDGET_EXCEEDED",
                                "client_order_id": order_id,
                                "ts_event": int(event.ts_event),
                                "fill_adjusted_expected_stop_loss": fill_adjusted_loss,
                                "risk_budget": risk_budget,
                                "ratio": fill_adjusted_loss / risk_budget if risk_budget > 0 else None,
                            }
                            self.execution_failures.append(failure)
                            if self.active_instrument_id is not None:
                                self.cancel_all_orders(self.active_instrument_id)
                                if not self.portfolio.is_flat(self.active_instrument_id):
                                    close_reference = self.last_close.get(
                                        str(self.active_instrument_id),
                                        fill_price,
                                    )
                                    self._request_exit(
                                        "FILL_ADJUSTED_RISK_BUDGET_EXCEEDED",
                                        int(event.ts_event),
                                        close_reference,
                                    )

    def on_order_canceled(self, event: Any) -> None:
        if str(event.client_order_id) != self.active_entry_order_id:
            return
        if self.active_signal is not None:
            self._emit(
                self.active_signal,
                event_type="ENTRY_CANCELED",
                observed_time_ns=int(event.ts_event),
                previous_state=self.active_scenario_state or "ENTRY_CANCEL_REQUESTED",
                next_state="CANCELED",
                reason_code="UNFILLED_MARKET_ENTRY_CANCELED",
            )
        self._reset_active_state()

    def on_order_expired(self, event: Any) -> None:
        if str(event.client_order_id) != self.active_entry_order_id:
            return
        if self.active_signal is not None:
            self._emit(
                self.active_signal,
                event_type="ENTRY_EXPIRED",
                observed_time_ns=int(event.ts_event),
                previous_state=self.active_scenario_state or "ORDER_SUBMITTED",
                next_state="CANCELED",
                reason_code="UNFILLED_MARKET_ENTRY_EXPIRED",
            )
        self._reset_active_state()

    def on_position_opened(self, event: Any) -> None:
        self.active_position_id = str(event.position_id)
        self.position_open_time_ns = int(event.ts_event)
        if self.trade_intents:
            self.trade_intents[-1]["position_open_time_ns"] = int(event.ts_event)
            self.trade_intents[-1]["position_id"] = str(event.position_id)
        self.entry_inflight = False
        self.entry_cancel_requested = False
        if self.active_signal is not None:
            self._emit(
                self.active_signal,
                event_type="POSITION_OPENED",
                observed_time_ns=int(event.ts_event),
                previous_state=self.active_scenario_state or "ORDER_SUBMITTED",
                next_state="POSITION_OPEN",
                reason_code="TEN_SECOND_MARKET_ENTRY_FILLED",
                reference_price=float(event.avg_px_open),
                details={"position_id": str(event.position_id)},
            )
            self.active_scenario_state = "POSITION_OPEN"
        if self.fill_adjusted_risk_violation and self.active_instrument_id is not None:
            close_reference = self.last_close.get(
                str(self.active_instrument_id),
                float(event.avg_px_open),
            )
            self._request_exit(
                "FILL_ADJUSTED_RISK_BUDGET_EXCEEDED",
                int(event.ts_event),
                close_reference,
            )

    def on_position_closed(self, event: Any) -> None:
        signal = self.active_signal
        reason = self.exit_request_reason
        if reason is None:
            if self.last_fill_order_id == self.active_stop_order_id:
                reason = "STRUCTURAL_STOP"
            elif self.last_fill_order_id == self.active_target_order_id:
                reason = "EXTERNAL_TARGET"
            elif self.last_fill_order_id in self.active_exit_order_ids:
                reason = "BRACKET_EXIT_UNCLASSIFIED"
            else:
                reason = "UNEXPECTED_CLOSE_OR_LIQUIDATION"
        self.position_outcomes.append(
            {
                "scenario_id": signal.scenario_id if signal is not None else "unknown-position",
                "scenario_family": "BREAKOUT_ACCEPTANCE_CONTINUATION",
                "symbol": signal.symbol if signal is not None else None,
                "instrument_id": str(self.active_instrument_id) if self.active_instrument_id else None,
                "direction": signal.direction_name if signal is not None else None,
                "position_id": str(event.position_id),
                "close_reason": reason,
                "ts_event": int(event.ts_event),
                "last_fill_order_id": self.last_fill_order_id,
            }
        )
        if signal is not None:
            self._emit(
                signal,
                event_type="POSITION_CLOSED",
                observed_time_ns=int(event.ts_event),
                previous_state=self.active_scenario_state or "POSITION_OPEN",
                next_state="CLOSED",
                reason_code=reason,
                reference_price=float(event.avg_px_close),
                details={"position_id": str(event.position_id)},
            )
        self._reset_active_state()

    def on_order_denied(self, event: Any) -> None:
        self._handle_execution_failure("ORDER_DENIED", event)

    def on_order_rejected(self, event: Any) -> None:
        self._handle_execution_failure("ORDER_REJECTED", event)

    def _handle_execution_failure(self, reason: str, event: Any) -> None:
        record = {
            "reason": reason,
            "client_order_id": str(event.client_order_id),
            "ts_event": int(event.ts_event),
            "event": str(event),
        }
        self.execution_failures.append(record)
        if self.active_signal is None or self.active_instrument_id is None:
            return
        if not self.portfolio.is_flat(self.active_instrument_id):
            self.cancel_all_orders(self.active_instrument_id)
            self.close_all_positions(self.active_instrument_id)
            self.exit_requested = True
            self.exit_request_reason = reason
            next_state = "EXIT_REQUESTED"
        else:
            # A denied/rejected parent can leave contingent children cached on some venue paths.
            # Cancel them before clearing the global active state so the one-entry contract remains
            # true even under an execution failure.
            self.cancel_all_orders(self.active_instrument_id)
            next_state = "FAILED"
        self._emit(
            self.active_signal,
            event_type=reason,
            observed_time_ns=int(event.ts_event),
            previous_state=self.active_scenario_state or "ORDER_SUBMITTED",
            next_state=next_state,
            reason_code=reason,
            details=record,
        )
        self.active_scenario_state = next_state
        if next_state == "FAILED":
            self._reset_active_state()

    def _record_skip(
        self,
        signal: AcceptanceSignal,
        reason: str,
        ts_event_ns: int,
        details: dict[str, Any],
    ) -> None:
        record = {
            "scenario_id": signal.scenario_id,
            "scenario_family": "BREAKOUT_ACCEPTANCE_CONTINUATION",
            "symbol": signal.symbol,
            "instrument_id": signal.instrument_id,
            "direction": signal.direction_name,
            "signal_time_ns": signal.signal_time_ns,
            "boundary_source": signal.boundary_source,
            "target_source": signal.target_source,
            "reason": reason,
            **details,
        }
        self.skipped_setups.append(record)
        self.execution_events.append(
            {
                "scenario_id": signal.scenario_id,
                "symbol": signal.symbol,
                "instrument_id": signal.instrument_id,
                "event_type": "SETUP_SKIPPED",
                "event_time_ns": ts_event_ns,
                "observed_time_ns": ts_event_ns,
                "previous_state": "CONFIRMED",
                "next_state": "SKIPPED",
                "reason_code": reason,
                "reference_price": signal.entry_reference,
                "details": dict(details),
                "sequence": 90,
            }
        )

    def _emit(
        self,
        signal: AcceptanceSignal,
        *,
        event_type: str,
        observed_time_ns: int,
        previous_state: str,
        next_state: str,
        reason_code: str,
        reference_price: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        sequence_map = {
            "MARKET_OUO_BRACKET_SUBMITTED": 40,
            "POSITION_OPENED": 50,
            "EXIT_REQUESTED": 60,
            "ENTRY_CANCEL_REQUESTED": 60,
            "ENTRY_CANCELED": 70,
            "ENTRY_EXPIRED": 70,
            "ORDER_DENIED": 70,
            "ORDER_REJECTED": 70,
            "POSITION_CLOSED": 80,
        }
        self.execution_events.append(
            {
                "scenario_id": signal.scenario_id,
                "symbol": signal.symbol,
                "instrument_id": signal.instrument_id,
                "event_type": event_type,
                "event_time_ns": observed_time_ns,
                "observed_time_ns": observed_time_ns,
                "previous_state": previous_state,
                "next_state": next_state,
                "reason_code": reason_code,
                "reference_price": reference_price,
                "details": dict(details or {}),
                "sequence": sequence_map.get(event_type, 75),
            }
        )

    def _reset_active_state(self) -> None:
        self.active_signal = None
        self.active_instrument_id = None
        self.active_scenario_state = None
        self.active_entry_order_id = None
        self.active_exit_order_ids.clear()
        self.active_stop_order_id = None
        self.active_target_order_id = None
        self.active_position_id = None
        self.position_open_time_ns = None
        self.entry_inflight = False
        self.entry_cancel_requested = False
        self.exit_requested = False
        self.exit_request_reason = None
        self.last_fill_order_id = None
        self.fill_adjusted_risk_violation = False

    def on_stop(self) -> None:
        for instrument_id in self.instrument_ids:
            self.cancel_all_orders(instrument_id)
            if not self.portfolio.is_flat(instrument_id):
                self.close_all_positions(instrument_id)

    @staticmethod
    def _round_price(instrument: Any, value: float, rounding: str) -> float:
        tick = instrument.price_increment.as_decimal()
        units = (Decimal(str(value)) / tick).to_integral_value(rounding=rounding)
        return float(units * tick)

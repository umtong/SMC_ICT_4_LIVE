"""NautilusTrader strategy adapter for candidate-09 v12 market and limit signals."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, MutableSequence

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce
from nautilus_trader.model.events import OrderCanceled, OrderFilled, OrderRejected, PositionClosed, PositionOpened
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

from state_engine import DiagnosticEvent, EngineConfig, FlowBar, LiquidityStateEngine, Signal, risk_based_quantity


class Candidate09StrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    risk_fraction: float = 0.03
    starting_nav: float = 100_000.0
    composite_cost_per_fill: float = 0.00075
    maximum_holding_bars: int = 240
    flat_before_utc_midnight_minutes: int = 5


class Candidate09Strategy(Strategy):
    """One-position strategy whose decisions are made only on completed bars."""

    def __init__(
        self,
        config: Candidate09StrategyConfig,
        *,
        engine_config: EngineConfig,
        flow_bars: Mapping[int, FlowBar],
        diagnostic_events: MutableSequence[dict[str, Any]],
        trade_records: MutableSequence[dict[str, Any]],
        fill_records: MutableSequence[dict[str, Any]],
    ) -> None:
        super().__init__(config)
        self.instrument = None
        self.logic = LiquidityStateEngine(engine_config)
        self.flow_bars = flow_bars
        self.diagnostic_events = diagnostic_events
        self.trade_records = trade_records
        self.fill_records = fill_records
        self.adjusted_nav = float(config.starting_nav)
        self._active_signal: Signal | None = None
        self._planned_loss = 0.0
        self._active_commissions = 0.0
        self._active_extra_cost = 0.0
        self._opened_ns: int | None = None
        self._entry_pending = False
        self._entry_pending_bars = 0
        self._entry_timeout_bars = 0
        self._entry_cancel_requested = False
        self._entry_order_type = "MARKET"
        self._bars_held = 0
        self.rejected_orders = 0
        self.time_exits = 0
        self.missing_feature_bars = 0

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            raise RuntimeError(f"instrument not found: {self.config.instrument_id}")
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        observed_ns = int(bar.ts_init)
        flow_bar = self.flow_bars.get(observed_ns)
        if flow_bar is None:
            self.missing_feature_bars += 1
            raise RuntimeError(f"no causal flow observation for Nautilus bar at {observed_ns}")

        is_flat = self.portfolio.is_flat(self.config.instrument_id)
        if not is_flat:
            self._bars_held += 1
            if self._must_flatten(flow_bar) or self._bars_held >= int(self.config.maximum_holding_bars):
                self.cancel_all_orders(self.config.instrument_id)
                self.close_all_positions(self.config.instrument_id)
                self.time_exits += 1
            result = self.logic.on_bar(flow_bar)
            self._record_events(result.events)
            if result.signal is not None:
                self._record_skipped_signal(result.signal, "POSITION_ALREADY_OPEN")
            return

        if self._entry_pending:
            self._entry_pending_bars += 1
            timed_out = self._entry_timeout_bars > 0 and self._entry_pending_bars >= self._entry_timeout_bars
            day_end = self._entry_blackout(flow_bar)
            if (timed_out or day_end) and not self._entry_cancel_requested:
                self._entry_cancel_requested = True
                self.cancel_all_orders(self.config.instrument_id)
                self.diagnostic_events.append(
                    {
                        "scenario_id": self._active_signal.scenario_id if self._active_signal else "pending-entry",
                        "event_type": "ENTRY_ORDER_CANCEL_REQUESTED",
                        "event_time_ns": observed_ns,
                        "observed_time_ns": observed_ns,
                        "previous_state": "ENTRY_PENDING",
                        "next_state": "CANCEL_PENDING",
                        "reason_code": "ENTRY_LIMIT_TIMEOUT" if timed_out else "UTC_DAY_END_ENTRY_BLACKOUT",
                        "reference_price": self._active_signal.entry_reference if self._active_signal else None,
                        "details": {
                            "entry_order_type": self._entry_order_type,
                            "pending_bars": self._entry_pending_bars,
                            "timeout_bars": self._entry_timeout_bars,
                        },
                    },
                )
            result = self.logic.on_bar(flow_bar)
            self._record_events(result.events)
            if result.signal is not None:
                self._record_skipped_signal(result.signal, "ENTRY_ORDER_ALREADY_PENDING")
            return

        result = self.logic.on_bar(flow_bar)
        self._record_events(result.events)
        if result.signal is None or self._entry_blackout(flow_bar):
            if result.signal is not None:
                self._record_skipped_signal(result.signal, "UTC_DAY_END_ENTRY_BLACKOUT")
            return
        self._submit_signal(result.signal)

    def _submit_signal(self, signal: Signal) -> None:
        assert self.instrument is not None
        sizing = risk_based_quantity(
            nav=Decimal(str(self.adjusted_nav)),
            risk_fraction=Decimal(str(self.config.risk_fraction)),
            entry_price=Decimal(str(signal.entry_reference)),
            stop_price=Decimal(str(signal.stop_price)),
            cost_rate_per_fill=Decimal(str(self.config.composite_cost_per_fill)),
            quantity_increment=self.instrument.size_increment.as_decimal(),
        )
        side = OrderSide.BUY if signal.side == "BUY" else OrderSide.SELL
        quantity = self.instrument.make_qty(sizing.quantity)
        stop = self.instrument.make_price(signal.stop_price)
        target = self.instrument.make_price(signal.target_price)
        requested_entry_type = str(signal.details.get("entry_order_type", "MARKET")).upper()
        if requested_entry_type not in {"MARKET", "LIMIT"}:
            raise ValueError(f"unsupported signal entry_order_type: {requested_entry_type}")
        entry_order_type = OrderType.LIMIT if requested_entry_type == "LIMIT" else OrderType.MARKET
        entry_price = self.instrument.make_price(signal.entry_reference) if requested_entry_type == "LIMIT" else None
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=quantity,
            time_in_force=TimeInForce.GTC,
            entry_order_type=entry_order_type,
            entry_price=entry_price,
            entry_post_only=False,
            sl_order_type=OrderType.STOP_MARKET,
            sl_trigger_price=stop,
            tp_order_type=OrderType.LIMIT,
            tp_price=target,
        )
        self._active_signal = signal
        self._planned_loss = float(sizing.planned_loss)
        self._active_commissions = 0.0
        self._active_extra_cost = 0.0
        self._opened_ns = None
        self._bars_held = 0
        self._entry_pending = True
        self._entry_pending_bars = 0
        self._entry_timeout_bars = int(signal.details.get("entry_timeout_bars", 0))
        self._entry_cancel_requested = False
        self._entry_order_type = requested_entry_type
        self.submit_order_list(order_list)

    def on_order_filled(self, event: OrderFilled) -> None:
        if self._active_signal is None:
            return
        price = _number(event.last_px)
        quantity = _number(event.last_qty)
        commission = _money(event.commission)
        notional = price * quantity
        native_rate = commission / notional if notional > 0.0 else 0.0
        extra_rate = max(0.0, float(self.config.composite_cost_per_fill) - native_rate)
        extra_cost = notional * extra_rate
        self._active_commissions += commission
        self._active_extra_cost += extra_cost
        self.fill_records.append(
            {
                "scenario_id": self._active_signal.scenario_id,
                "ts_event_ns": int(event.ts_event),
                "client_order_id": str(event.client_order_id),
                "order_side": str(event.order_side),
                "last_price": price,
                "last_quantity": quantity,
                "native_commission": commission,
                "extra_cost_to_composite_model": extra_cost,
                "composite_cost_rate": float(self.config.composite_cost_per_fill),
            },
        )

    def on_position_opened(self, event: PositionOpened) -> None:
        if self._active_signal is None:
            return
        self._entry_pending = False
        self._entry_pending_bars = 0
        self._entry_timeout_bars = 0
        self._entry_cancel_requested = False
        self._opened_ns = int(event.ts_event)
        self._bars_held = 0

    def on_position_closed(self, event: PositionClosed) -> None:
        if self._active_signal is None:
            return
        native_net_realized_pnl = _money(event.realized_pnl)
        gross_pnl = native_net_realized_pnl + self._active_commissions
        net_pnl = native_net_realized_pnl - self._active_extra_cost
        nav_before = self.adjusted_nav
        self.adjusted_nav += net_pnl
        record = {
            "scenario_id": self._active_signal.scenario_id,
            "branch": self._active_signal.branch,
            "side": self._active_signal.side,
            "signal_observed_ns": self._active_signal.observed_time_ns,
            "opened_ns": self._opened_ns,
            "closed_ns": int(event.ts_event),
            "entry_reference": self._active_signal.entry_reference,
            "entry_order_type": self._entry_order_type,
            "stop_price": self._active_signal.stop_price,
            "target_price": self._active_signal.target_price,
            "planned_loss": self._planned_loss,
            "gross_realized_pnl": gross_pnl,
            "native_net_realized_pnl": native_net_realized_pnl,
            "native_commissions": self._active_commissions,
            "extra_composite_cost": self._active_extra_cost,
            "net_pnl": net_pnl,
            "realized_r": net_pnl / self._planned_loss if self._planned_loss > 0.0 else None,
            "nav_before": nav_before,
            "nav_after": self.adjusted_nav,
            "net_reward_to_risk_at_signal": self._active_signal.net_reward_to_risk,
            "reason_code": self._active_signal.reason_code,
        }
        self.trade_records.append(record)
        self._reset_pending_trade()

    def on_order_rejected(self, event: OrderRejected) -> None:
        self.rejected_orders += 1
        self.diagnostic_events.append(
            {
                "scenario_id": self._active_signal.scenario_id if self._active_signal else "order-without-scenario",
                "event_type": "ORDER_REJECTED",
                "event_time_ns": int(event.ts_event),
                "observed_time_ns": int(event.ts_event),
                "previous_state": "ENTERABLE",
                "next_state": "NO_TRADE",
                "reason_code": str(event.reason),
                "reference_price": None,
                "details": {"client_order_id": str(event.client_order_id)},
            },
        )
        if self.portfolio.is_flat(self.config.instrument_id):
            self._reset_pending_trade()

    def on_order_canceled(self, event: OrderCanceled) -> None:
        if self._entry_pending and self.portfolio.is_flat(self.config.instrument_id):
            self._reset_pending_trade()

    def on_stop(self) -> None:
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.diagnostic_events.append(
                {
                    "scenario_id": self._active_signal.scenario_id if self._active_signal else "unknown-open-position",
                    "event_type": "OPEN_POSITION_AT_ENGINE_STOP",
                    "event_time_ns": self.clock.timestamp_ns(),
                    "observed_time_ns": self.clock.timestamp_ns(),
                    "previous_state": "POSITION_OPEN",
                    "next_state": "IMPLEMENTATION_ERROR",
                    "reason_code": "DAY_TRADING_FLATNESS_CONTRACT_BROKEN",
                    "reference_price": None,
                    "details": {},
                },
            )

    def _record_events(self, events: tuple[DiagnosticEvent, ...]) -> None:
        for event in events:
            payload = asdict(event)
            payload["details"] = dict(event.details)
            self.diagnostic_events.append(payload)

    def _record_skipped_signal(self, signal: Signal, reason: str) -> None:
        self.diagnostic_events.append(
            {
                "scenario_id": signal.scenario_id,
                "event_type": "ENTRY_SKIPPED",
                "event_time_ns": signal.observed_time_ns,
                "observed_time_ns": signal.observed_time_ns,
                "previous_state": "ENTERABLE",
                "next_state": "NO_TRADE",
                "reason_code": reason,
                "reference_price": signal.entry_reference,
                "details": {"branch": signal.branch, "side": signal.side},
            },
        )

    def _entry_blackout(self, bar: FlowBar) -> bool:
        stamp = datetime.fromtimestamp(bar.ts_ns / 1e9, tz=timezone.utc)
        minutes = stamp.hour * 60 + stamp.minute
        return minutes >= 24 * 60 - int(self.config.flat_before_utc_midnight_minutes)

    def _must_flatten(self, bar: FlowBar) -> bool:
        return self._entry_blackout(bar)

    def _reset_pending_trade(self) -> None:
        self._active_signal = None
        self._planned_loss = 0.0
        self._active_commissions = 0.0
        self._active_extra_cost = 0.0
        self._opened_ns = None
        self._entry_pending = False
        self._entry_pending_bars = 0
        self._entry_timeout_bars = 0
        self._entry_cancel_requested = False
        self._entry_order_type = "MARKET"
        self._bars_held = 0


def _number(value: Any) -> float:
    if hasattr(value, "as_double"):
        return float(value.as_double())
    if hasattr(value, "as_decimal"):
        return float(value.as_decimal())
    return float(value)


def _money(value: Any) -> float:
    if value is None:
        return 0.0
    if hasattr(value, "as_double"):
        return float(value.as_double())
    text = str(value).split()[0]
    return float(text)

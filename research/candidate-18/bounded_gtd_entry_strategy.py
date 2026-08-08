"""Candidate 18 v6: bounded GTD price-cap entry on native trade ticks.

V5 proved the market-state policy survives realistic order timing but a one-shot
IOC captures only the single aggTrade available at insertion. This module keeps
the same signal, worst-fill price cap, three-percent risk sizing and fill-managed
protection, while allowing one native LIMIT order to participate for a strictly
bounded three-second micro-auction window.

The entry never chases beyond its original cap. Every actual fill is protected
immediately by the inherited LAST_PRICE-emulated stop and reduce-only target.
"""
from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from datetime import timezone
import math
from typing import Any

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce

from logic import choose_liquidity_target
from logic import floor_quantity
from logic import planned_loss_per_unit
from strategy_base import PendingSetup
from trade_tick_emulated_protection_strategy import Candidate18Config as _Candidate18V5Config
from trade_tick_emulated_protection_strategy import Candidate18Strategy as _Candidate18V5Strategy
from managed_protection_ioc_strategy import _number


class Candidate18Config(_Candidate18V5Config, frozen=True):
    entry_fill_window_seconds: float = 3.0


class Candidate18Strategy(_Candidate18V5Strategy):
    """Accumulate fills inside one short, non-chasing native GTD order."""

    def __init__(self, config: Candidate18Config) -> None:
        super().__init__(config=config)
        if not 0.25 <= config.entry_fill_window_seconds <= 10.0:
            raise ValueError("entry_fill_window_seconds must be in [0.25, 10]")
        self._entry_requested_qty = 0.0
        self._entry_filled_qty = 0.0
        self._entry_window_recorded = False
        self._entry_expire_time_ns = 0
        self.diagnostics.update(
            {
                "candidate18_v6_bounded_gtd_entries": 0,
                "candidate18_v6_requested_entry_qty": 0.0,
                "candidate18_v6_filled_entry_qty": 0.0,
                "candidate18_v6_entry_window_full": 0,
                "candidate18_v6_entry_window_partial": 0,
                "candidate18_v6_entry_window_unfilled": 0,
                "candidate18_v6_entry_rejections": 0,
                "candidate18_v6_order_rejection_events": [],
                "candidate18_v6_entry_windows": [],
            },
        )

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        if hasattr(self, "_entry_requested_qty"):
            self._entry_requested_qty = 0.0
            self._entry_filled_qty = 0.0
            self._entry_window_recorded = False
            self._entry_expire_time_ns = 0

    def _record_entry_window(self, reason: str, event: Any | None = None) -> None:
        if self._entry_window_recorded or self._entry_requested_qty <= 0.0:
            return
        fraction = min(
            1.0,
            max(0.0, self._entry_filled_qty / self._entry_requested_qty),
        )
        if fraction >= 1.0 - 1e-12:
            bucket = "candidate18_v6_entry_window_full"
        elif self._entry_filled_qty > 1e-12:
            bucket = "candidate18_v6_entry_window_partial"
        else:
            bucket = "candidate18_v6_entry_window_unfilled"
        self.diagnostics[bucket] = int(self.diagnostics[bucket]) + 1
        windows = list(self.diagnostics["candidate18_v6_entry_windows"])
        windows.append(
            {
                "scenario_id": str(self.current_scenario_id or "UNKNOWN"),
                "entry_client_order_id": str(self._managed_entry_id or ""),
                "reason": reason,
                "requested_qty": self._entry_requested_qty,
                "filled_qty": self._entry_filled_qty,
                "fill_fraction": fraction,
                "expire_time_ns": self._entry_expire_time_ns,
                "event": str(event) if event is not None else None,
            },
        )
        self.diagnostics["candidate18_v6_entry_windows"] = windows
        self._entry_window_recorded = True

    def _submit_entry(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
    ) -> bool:
        atr = self._atr()
        side = setup.side
        signal_close = float(row["close"])
        if setup.branch == "REJECTION":
            stop = setup.sweep_extreme - side * self.config.stop_buffer_atr * atr
            fallback_r = self.config.rejection_target_net_r
        else:
            if side > 0:
                stop = min(
                    setup.pool_level - self.config.stop_buffer_atr * atr,
                    float(row["low"]) - 0.25 * self.config.stop_buffer_atr * atr,
                )
            else:
                stop = max(
                    setup.pool_level + self.config.stop_buffer_atr * atr,
                    float(row["high"]) + 0.25 * self.config.stop_buffer_atr * atr,
                )
            fallback_r = self.config.acceptance_target_net_r

        structural_risk = abs(signal_close - stop)
        if not math.isfinite(structural_risk) or structural_risk <= 0.0:
            self._expire_pending(row, "INVALID_STOP_GEOMETRY")
            return False
        cap_distance = max(
            self.config.entry_rearm_atr * atr,
            self.config.entry_limit_risk_expansion * structural_risk,
        )
        entry_limit = signal_close + side * cap_distance
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        adverse_slippage_rate = (
            self.config.adverse_slippage_bps_each_side / 10_000.0
        )
        planned_loss = planned_loss_per_unit(
            entry_limit,
            stop,
            side,
            cost_rate,
            adverse_slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self._expire_pending(row, "INVALID_BOUNDED_GTD_GEOMETRY")
            return False
        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        quantity_value = floor_quantity(
            risk_budget / planned_loss,
            int(self.instrument.size_precision),
        )
        if quantity_value <= 0.0 or quantity_value * entry_limit < 10.0:
            self._expire_pending(row, "QUANTITY_BELOW_INSTRUMENT_MINIMUM")
            return False

        target, target_source, target_r = choose_liquidity_target(
            entry=entry_limit,
            side=side,
            pools=list(self.active_pools.values()),
            planned_loss=planned_loss,
            cost_rate=cost_rate,
            min_net_r=self.config.min_target_net_r,
            max_net_r=self.config.max_target_net_r,
            fallback_net_r=fallback_r,
        )
        if side > 0 and not (stop < signal_close < entry_limit < target):
            self._expire_pending(row, "INVALID_LONG_BOUNDED_GTD")
            return False
        if side < 0 and not (target < entry_limit < signal_close < stop):
            self._expire_pending(row, "INVALID_SHORT_BOUNDED_GTD")
            return False

        expire_time = datetime.fromtimestamp(
            int(row["ts"]) / 1_000_000_000,
            tz=timezone.utc,
        ) + timedelta(seconds=self.config.entry_fill_window_seconds)
        order_side = OrderSide.BUY if side > 0 else OrderSide.SELL
        entry_order = self.order_factory.limit(
            instrument_id=self.config.instrument_id,
            order_side=order_side,
            quantity=self.instrument.make_qty(quantity_value),
            price=self.instrument.make_price(entry_limit),
            time_in_force=TimeInForce.GTD,
            expire_time=expire_time,
            post_only=False,
            reduce_only=False,
            tags=["ENTRY", "CANDIDATE18_BOUNDED_GTD_ENTRY"],
        )
        self._managed_entry_id = str(entry_order.client_order_id)
        self._managed_side = side
        self._managed_stop = stop
        self._managed_target = target
        self._entry_requested_qty = quantity_value
        self._entry_filled_qty = 0.0
        self._entry_window_recorded = False
        self._entry_expire_time_ns = int(expire_time.timestamp() * 1_000_000_000)
        self.submit_order(entry_order)
        self.entry_pending = True
        self.entry_pending_index = self.bar_index
        self.last_entry_index = self.bar_index
        self.current_scenario_id = setup.scenario_id
        self.current_branch = setup.branch
        self.current_pool_level = setup.pool_level
        self.pending = None
        self.diagnostics["entry_submissions"] = int(
            self.diagnostics["entry_submissions"],
        ) + 1
        self.diagnostics["candidate18_v6_bounded_gtd_entries"] = int(
            self.diagnostics["candidate18_v6_bounded_gtd_entries"],
        ) + 1
        self.diagnostics["candidate18_v6_requested_entry_qty"] = float(
            self.diagnostics["candidate18_v6_requested_entry_qty"],
        ) + quantity_value
        self.diagnostics["max_simultaneous_entry_intents"] = max(
            int(self.diagnostics["max_simultaneous_entry_intents"]),
            1,
        )
        self._transition(
            setup.scenario_id,
            "ENTRY_SUBMITTED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_PENDING",
            "BOUNDED_GTD_PRICE_CAP_WITH_FILL_MANAGED_PROTECTION",
            entry_limit,
            {
                **setup.details,
                "candidate18_version": "v6-bounded-gtd",
                "branch": setup.branch,
                "side": side,
                "signal_close": signal_close,
                "entry_limit_worst_fill": entry_limit,
                "entry_time_in_force": "GTD",
                "entry_fill_window_seconds": self.config.entry_fill_window_seconds,
                "entry_expire_time_ns": self._entry_expire_time_ns,
                "entry_client_order_id": self._managed_entry_id,
                "stop": stop,
                "target": target,
                "target_source": target_source,
                "target_net_r": target_r,
                "quantity": quantity_value,
                "equity": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit_at_worst_fill": planned_loss,
                "planned_account_loss_at_worst_fill": quantity_value * planned_loss,
            },
        )
        return True

    def on_order_filled(self, event: Any) -> None:
        client_order_id = str(getattr(event, "client_order_id", ""))
        if client_order_id == self._managed_entry_id:
            fill_qty = _number(getattr(event, "last_qty", 0.0))
            self._entry_filled_qty += fill_qty
            self.diagnostics["candidate18_v6_filled_entry_qty"] = float(
                self.diagnostics["candidate18_v6_filled_entry_qty"],
            ) + fill_qty
            super().on_order_filled(event)
            if self._entry_filled_qty + 1e-12 >= self._entry_requested_qty:
                self._record_entry_window("FULLY_FILLED", event)
            return
        super().on_order_filled(event)

    def _handle_entry_window_closed(self, event: Any, reason: str) -> None:
        client_order_id = str(getattr(event, "client_order_id", ""))
        if client_order_id != self._managed_entry_id:
            return
        self.entry_pending = False
        self._record_entry_window(reason, event)
        self._managed_entry_id = None
        if (
            self.portfolio.is_flat(self.config.instrument_id)
            and self._managed_open_qty <= 1e-12
            and self._entry_filled_qty <= 1e-12
        ):
            if (
                self.current_scenario_id is not None
                and self.scenario_states.get(self.current_scenario_id) != "CLOSED"
            ):
                ts = int(getattr(event, "ts_event", self.bars[-1]["ts"]))
                self._transition(
                    self.current_scenario_id,
                    "ENTRY_WINDOW_CLOSED",
                    ts,
                    ts,
                    "CLOSED",
                    f"{reason}_WITHOUT_FILL",
                    float(self.bars[-1]["close"]),
                    {
                        "requested_qty": self._entry_requested_qty,
                        "filled_qty": self._entry_filled_qty,
                    },
                )
            self._clear_trade_state()

    def on_order_expired(self, event: Any) -> None:
        self._handle_entry_window_closed(event, "BOUNDED_GTD_EXPIRED")
        super().on_order_expired(event)

    def on_order_canceled(self, event: Any) -> None:
        self._handle_entry_window_closed(event, "BOUNDED_GTD_CANCELED")
        super().on_order_canceled(event)

    def on_order_rejected(self, event: Any) -> None:
        client_order_id = str(getattr(event, "client_order_id", ""))
        events = list(self.diagnostics["candidate18_v6_order_rejection_events"])
        events.append(
            {
                "client_order_id": client_order_id,
                "is_entry": client_order_id == self._managed_entry_id,
                "event": str(event),
            },
        )
        self.diagnostics["candidate18_v6_order_rejection_events"] = events
        if client_order_id == self._managed_entry_id:
            self.diagnostics["candidate18_v6_entry_rejections"] = int(
                self.diagnostics["candidate18_v6_entry_rejections"],
            ) + 1
            self._record_entry_window("BOUNDED_GTD_REJECTED", event)
        super().on_order_rejected(event)


__all__ = ["Candidate18Config", "Candidate18Strategy"]

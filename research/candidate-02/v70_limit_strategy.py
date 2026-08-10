"""NautilusTrader passive limit-entry adapter for candidate-02 v70.

This is a Strategy implementation, not a backtest engine. NautilusTrader owns
order state, limit fills, contingent TP/SL activation, fees, positions and NAV.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce

from core import size_by_planned_loss
from v53_nt_strategy import ScheduledSignal, V53RotationStrategy

NS_MINUTE = 60_000_000_000


class V70LimitPullbackStrategy(V53RotationStrategy):
    """Submit prelocked passive LIMIT brackets and expire unfilled entries."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self._pending_expiry_ns: int | None = None

    def on_bar(self, bar: Bar) -> None:
        observed_ns = int(bar.ts_init)
        if (
            self._entry_pending
            and self._pending_expiry_ns is not None
            and observed_ns >= self._pending_expiry_ns
            and self.portfolio.is_flat(self.config.instrument_id)
        ):
            selected = self._selected_signal
            self.cancel_all_orders(self.config.instrument_id)
            if selected is not None:
                self.signal_records.append(
                    {
                        "status": "ENTRY_EXPIRED_UNFILLED",
                        "expiration_time_ns": observed_ns,
                        **selected.to_dict(),
                    }
                )
            self._reset_unfilled_entry()
            self._increment("ENTRY_LIMIT_EXPIRED_UNFILLED")
            self._record_nav(observed_ns)
            return
        if observed_ns >= self.config.trade_end_ns and self._entry_pending:
            self.cancel_all_orders(self.config.instrument_id)
            self._reset_unfilled_entry()
            self._increment("ENTRY_LIMIT_CANCELED_AT_EVALUATION_END")
        super().on_bar(bar)

    def on_position_opened(self, event: Any) -> None:
        self._pending_expiry_ns = None
        super().on_position_opened(event)

    def on_position_closed(self, event: Any) -> None:
        self._pending_expiry_ns = None
        super().on_position_closed(event)

    def on_order_canceled(self, event: Any) -> None:
        if self._entry_pending and self.portfolio.is_flat(self.config.instrument_id):
            self._reset_unfilled_entry()
            self._increment("ENTRY_LIMIT_CANCELED")

    def on_order_expired(self, event: Any) -> None:
        if self._entry_pending and self.portfolio.is_flat(self.config.instrument_id):
            self._reset_unfilled_entry()
            self._increment("ENTRY_LIMIT_EXPIRED")

    def on_order_rejected(self, event: Any) -> None:
        self._pending_expiry_ns = None
        super().on_order_rejected(event)

    def on_order_denied(self, event: Any) -> None:
        self._pending_expiry_ns = None
        super().on_order_denied(event)

    def _reset_unfilled_entry(self) -> None:
        self._entry_pending = False
        self._selected_signal = None
        self._entry_time_ns = None
        self._nav_before_entry = None
        self._forced_exit_reason = None
        self._pending_expiry_ns = None

    def _submit_signal(self, signal: ScheduledSignal, bar: Bar) -> None:
        instrument = self.cache.instrument(self.config.instrument_id)
        if instrument is None:
            self._reject(signal, int(bar.ts_init), "INSTRUMENT_MISSING")
            return
        current_price = bar.close.as_double()
        limit_price = float(signal.entry_reference)
        passive = limit_price < current_price if signal.side == "BUY" else limit_price > current_price
        if not passive:
            self._reject(signal, int(bar.ts_init), "LIMIT_NOT_PASSIVE_AT_SUBMISSION")
            return
        geometry_valid = (
            signal.stop_price < limit_price < signal.target_price
            if signal.side == "BUY"
            else signal.target_price < limit_price < signal.stop_price
        )
        if not geometry_valid:
            self._reject(signal, int(bar.ts_init), "INVALID_LIMIT_GEOMETRY")
            return

        account = self.cache.account_for_venue(self.config.instrument_id.venue)
        if account is None:
            self._reject(signal, int(bar.ts_init), "ACCOUNT_MISSING")
            return
        balance = account.balance_total(self._quote_currency)
        if balance is None:
            self._reject(signal, int(bar.ts_init), "QUOTE_BALANCE_MISSING")
            return
        nav = Decimal(str(balance.as_double()))
        quantity_step = Decimal(str(instrument.size_increment.as_double()))
        minimum_quantity = (
            Decimal(str(instrument.min_quantity.as_double()))
            if instrument.min_quantity is not None
            else Decimal("0")
        )
        minimum_notional = (
            Decimal(str(instrument.min_notional.as_double()))
            if instrument.min_notional is not None
            else Decimal("0")
        )
        sizing = size_by_planned_loss(
            nav=nav,
            risk_fraction=self.config.risk_fraction,
            entry_price=Decimal(str(limit_price)),
            stop_price=Decimal(str(signal.stop_price)),
            entry_fee_rate=self.config.entry_fee_rate,
            stop_fee_rate=self.config.stop_fee_rate,
            entry_slippage_rate=Decimal("0"),
            stop_slippage_rate=self.config.stop_slippage_rate,
            market_impact_rate=Decimal("0"),
            funding_rate_allowance=self.config.funding_rate_allowance,
            quantity_step=quantity_step,
            minimum_quantity=minimum_quantity,
            minimum_notional=minimum_notional,
        )
        self.sizing_records.append(
            {
                "scenario_id": signal.scenario_id,
                "instrument_id": str(self.config.instrument_id),
                "observed_time_ns": int(bar.ts_init),
                "submission_market_price": current_price,
                "entry_reference": limit_price,
                "entry_order_type": "LIMIT",
                "entry_post_only": True,
                "effective_notional_multiple": (
                    float(sizing.entry_notional / nav) if nav > 0 else None
                ),
                **sizing.to_dict(),
            }
        )
        if sizing.quantity <= 0:
            self._reject(signal, int(bar.ts_init), sizing.skipped_reason or "ZERO_QUANTITY")
            return

        expiry_minutes = int(signal.details.get("entry_expiry_minutes", 5))
        if not 1 <= expiry_minutes <= 15:
            self._reject(signal, int(bar.ts_init), "INVALID_ENTRY_EXPIRY")
            return
        expiry_ns = int(bar.ts_init) + expiry_minutes * NS_MINUTE
        expiry = datetime.fromtimestamp(expiry_ns / 1_000_000_000, tz=timezone.utc)
        side = OrderSide.BUY if signal.side == "BUY" else OrderSide.SELL
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=instrument.make_qty(sizing.quantity),
            entry_order_type=OrderType.LIMIT,
            entry_price=instrument.make_price(limit_price),
            time_in_force=TimeInForce.GTD,
            expire_time=expiry,
            entry_post_only=True,
            tp_price=instrument.make_price(signal.target_price),
            sl_trigger_price=instrument.make_price(signal.stop_price),
            entry_tags=[signal.scenario_id, "candidate-02-v70", "passive-imbalance-retest"],
            tp_tags=[signal.scenario_id, "measured-displacement-liquidity"],
            sl_tags=[signal.scenario_id, "imbalance-retest-invalidation"],
        )
        self._entry_pending = True
        self._selected_signal = signal
        self._nav_before_entry = float(nav)
        self._pending_expiry_ns = expiry_ns
        self.submit_order_list(order_list)
        self.signal_records.append(
            {
                "status": "SUBMITTED",
                "submission_time_ns": int(bar.ts_init),
                "submission_market_price": current_price,
                "entry_reference": limit_price,
                "entry_order_type": "LIMIT",
                "entry_expiry_ns": expiry_ns,
                "quantity": str(sizing.quantity),
                **signal.to_dict(),
            }
        )
        self._increment("ENTRY_LIMIT_BRACKET_SUBMITTED")

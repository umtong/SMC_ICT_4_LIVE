"""NautilusTrader execution adapter for full-auction rotation v53."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Currency
from nautilus_trader.trading.strategy import Strategy

from core import size_by_planned_loss

NS_MINUTE = 60_000_000_000


@dataclass(frozen=True, slots=True)
class ScheduledSignal:
    scenario_id: str
    observed_time_ns: int
    side: str
    entry_reference: float
    stop_price: float
    target_price: float
    cost_after_reward_risk: float
    score: float
    max_hold_minutes: int
    source_feature_open_time_ns: int
    source_feature_available_time_ns: int
    source_max_market_time_ns: int
    details: dict[str, Any]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ScheduledSignal":
        return cls(**value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "observed_time_ns": self.observed_time_ns,
            "side": self.side,
            "entry_reference": self.entry_reference,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "cost_after_reward_risk": self.cost_after_reward_risk,
            "score": self.score,
            "max_hold_minutes": self.max_hold_minutes,
            "source_feature_open_time_ns": self.source_feature_open_time_ns,
            "source_feature_available_time_ns": self.source_feature_available_time_ns,
            "source_max_market_time_ns": self.source_max_market_time_ns,
            "details": dict(self.details),
        }


class V53RotationStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    signals_json: str
    risk_fraction: Decimal
    entry_fee_rate: Decimal
    stop_fee_rate: Decimal
    entry_slippage_rate: Decimal
    stop_slippage_rate: Decimal
    market_impact_rate: Decimal
    funding_rate_allowance: Decimal
    trade_start_ns: int
    trade_end_ns: int
    quote_currency: str = "USDT"


class V53RotationStrategy(Strategy):
    """Execute causal v53 signals exclusively through NautilusTrader."""

    def __init__(self, config: V53RotationStrategyConfig) -> None:
        super().__init__(config)
        if config.trade_end_ns <= config.trade_start_ns:
            raise ValueError("trade_end_ns must be after trade_start_ns")
        raw_signals = json.loads(config.signals_json)
        signals = [ScheduledSignal.from_dict(dict(value)) for value in raw_signals]
        signals.sort(key=lambda value: value.observed_time_ns)
        if len({value.observed_time_ns for value in signals}) != len(signals):
            raise ValueError("signal timestamps must be unique")
        for signal in signals:
            if signal.source_max_market_time_ns > signal.observed_time_ns:
                raise ValueError(f"future information in {signal.scenario_id}")
            if signal.source_feature_available_time_ns != signal.observed_time_ns:
                raise ValueError(f"feature availability mismatch in {signal.scenario_id}")
            if signal.side not in {"BUY", "SELL"}:
                raise ValueError(f"unknown signal side: {signal.side}")
        self._signal_by_time = {value.observed_time_ns: value for value in signals}
        self._quote_currency = Currency.from_str(config.quote_currency)

        self.signal_records: list[dict[str, Any]] = []
        self.sizing_records: list[dict[str, Any]] = []
        self.trade_records: list[dict[str, Any]] = []
        self.nav_records: list[dict[str, Any]] = []
        self.runtime_diagnostics: dict[str, int] = {}

        self._entry_pending = False
        self._selected_signal: ScheduledSignal | None = None
        self._entry_time_ns: int | None = None
        self._nav_before_entry: float | None = None
        self._forced_exit_reason: str | None = None

    def on_start(self) -> None:
        if self.cache.instrument(self.config.instrument_id) is None:
            raise RuntimeError(f"instrument not in cache: {self.config.instrument_id}")
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        observed_ns = int(bar.ts_init)
        self._record_nav(observed_ns)

        if observed_ns >= self.config.trade_end_ns:
            if not self.portfolio.is_flat(self.config.instrument_id):
                self._request_flatten("EVALUATION_END")
            return

        if self._entry_time_ns is not None and self._selected_signal is not None:
            elapsed = (observed_ns - self._entry_time_ns) // NS_MINUTE
            if elapsed >= self._selected_signal.max_hold_minutes:
                self._request_flatten("MAX_HOLD")

        signal = self._signal_by_time.get(observed_ns)
        if signal is None:
            return
        self.signal_records.append({"status": "OBSERVED", **signal.to_dict()})
        if not self.config.trade_start_ns <= observed_ns < self.config.trade_end_ns:
            self._reject(signal, observed_ns, "OUTSIDE_EVALUATION_WINDOW")
            return
        if self._global_busy():
            self._reject(signal, observed_ns, "GLOBAL_POSITION_BUSY")
            return
        self._submit_signal(signal, bar)

    def on_position_opened(self, event: Any) -> None:
        if self._selected_signal is None:
            self._increment("UNEXPECTED_POSITION_OPEN")
            self._request_flatten("UNEXPECTED_POSITION")
            return
        self._entry_pending = False
        self._entry_time_ns = int(event.ts_opened)
        self._increment("POSITION_OPENED")

    def on_position_closed(self, event: Any) -> None:
        account = self.cache.account_for_venue(self.config.instrument_id.venue)
        balance = account.balance_total(self._quote_currency) if account is not None else None
        nav_after = balance.as_double() if balance is not None else None
        gross_realized = event.realized_pnl.as_double() if event.realized_pnl is not None else 0.0
        net_after_cost = (
            nav_after - self._nav_before_entry
            if nav_after is not None and self._nav_before_entry is not None
            else gross_realized
        )
        if self._forced_exit_reason is not None:
            exit_reason = self._forced_exit_reason
        elif net_after_cost > 0:
            exit_reason = "TARGET_OR_PROFIT"
        elif net_after_cost < 0:
            exit_reason = "STOP_OR_LOSS"
        else:
            exit_reason = "FLAT"
        self.trade_records.append(
            {
                "scenario_id": self._selected_signal.scenario_id if self._selected_signal else None,
                "instrument_id": str(event.instrument_id),
                "entry_time_ns": self._entry_time_ns,
                "exit_time_ns": int(event.ts_closed),
                "avg_px_open": float(event.avg_px_open),
                "avg_px_close": float(event.avg_px_close),
                "gross_realized_pnl": gross_realized,
                "net_pnl_after_cost": net_after_cost,
                "nav_before_entry": self._nav_before_entry,
                "nav_after_exit": nav_after,
                "duration_ns": int(event.duration_ns),
                "exit_reason": exit_reason,
                "planned_signal": self._selected_signal.to_dict() if self._selected_signal else None,
            }
        )
        self.cancel_all_orders(event.instrument_id)
        self._entry_pending = False
        self._selected_signal = None
        self._entry_time_ns = None
        self._nav_before_entry = None
        self._forced_exit_reason = None
        self._increment("POSITION_CLOSED")

    def on_order_rejected(self, event: Any) -> None:
        self._handle_execution_failure(event, "ORDER_REJECTED")

    def on_order_denied(self, event: Any) -> None:
        self._handle_execution_failure(event, "ORDER_DENIED")

    def on_stop(self) -> None:
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)

    def _submit_signal(self, signal: ScheduledSignal, bar: Bar) -> None:
        instrument = self.cache.instrument(self.config.instrument_id)
        if instrument is None:
            self._reject(signal, int(bar.ts_init), "INSTRUMENT_MISSING")
            return
        entry_reference = bar.close.as_double()
        geometry_valid = (
            signal.stop_price < entry_reference < signal.target_price
            if signal.side == "BUY"
            else signal.target_price < entry_reference < signal.stop_price
        )
        if not geometry_valid:
            self._reject(signal, int(bar.ts_init), "PRICE_MOVED_THROUGH_GEOMETRY")
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
            entry_price=Decimal(str(entry_reference)),
            stop_price=Decimal(str(signal.stop_price)),
            entry_fee_rate=self.config.entry_fee_rate,
            stop_fee_rate=self.config.stop_fee_rate,
            entry_slippage_rate=self.config.entry_slippage_rate,
            stop_slippage_rate=self.config.stop_slippage_rate,
            market_impact_rate=self.config.market_impact_rate,
            funding_rate_allowance=self.config.funding_rate_allowance,
            quantity_step=quantity_step,
            minimum_quantity=minimum_quantity,
            minimum_notional=minimum_notional,
        )
        record = {
            "scenario_id": signal.scenario_id,
            "instrument_id": str(self.config.instrument_id),
            "observed_time_ns": int(bar.ts_init),
            "entry_reference": entry_reference,
            "effective_notional_multiple": (
                float(sizing.entry_notional / nav) if nav > 0 else None
            ),
            **sizing.to_dict(),
        }
        self.sizing_records.append(record)
        if sizing.quantity <= 0:
            self._reject(signal, int(bar.ts_init), sizing.skipped_reason or "ZERO_QUANTITY")
            return

        side = OrderSide.BUY if signal.side == "BUY" else OrderSide.SELL
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=instrument.make_qty(sizing.quantity),
            time_in_force=TimeInForce.GTC,
            tp_price=instrument.make_price(signal.target_price),
            sl_trigger_price=instrument.make_price(signal.stop_price),
            entry_tags=[signal.scenario_id, "candidate-02-v53"],
            tp_tags=[signal.scenario_id, "opposite-auction-liquidity"],
            sl_tags=[signal.scenario_id, "auction-rotation-invalidation"],
        )
        self._entry_pending = True
        self._selected_signal = signal
        self._nav_before_entry = float(nav)
        self.submit_order_list(order_list)
        self.signal_records.append(
            {
                "status": "SUBMITTED",
                "submission_time_ns": int(bar.ts_init),
                "entry_reference": entry_reference,
                "quantity": str(sizing.quantity),
                **signal.to_dict(),
            }
        )
        self._increment("ENTRY_BRACKET_SUBMITTED")

    def _reject(self, signal: ScheduledSignal, observed_ns: int, reason: str) -> None:
        self.signal_records.append(
            {
                "status": "REJECTED",
                "rejection_time_ns": observed_ns,
                "reason": reason,
                **signal.to_dict(),
            }
        )
        self._increment(f"SIGNAL_REJECTED_{reason}")

    def _handle_execution_failure(self, event: Any, reason: str) -> None:
        self._increment(reason)
        if self._entry_pending:
            self.cancel_all_orders(self.config.instrument_id)
            self._entry_pending = False
            self._selected_signal = None
            self._entry_time_ns = None
            self._nav_before_entry = None
            return
        if not self.portfolio.is_flat(self.config.instrument_id):
            self._request_flatten(reason)

    def _request_flatten(self, reason: str) -> None:
        if self._forced_exit_reason is not None:
            return
        self._forced_exit_reason = reason
        self.cancel_all_orders(self.config.instrument_id)
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.close_all_positions(self.config.instrument_id)
        self._increment(f"FORCED_EXIT_{reason}")

    def _global_busy(self) -> bool:
        return self._entry_pending or not self.portfolio.is_flat(self.config.instrument_id)

    def _record_nav(self, observed_ns: int) -> None:
        if not self.config.trade_start_ns <= observed_ns <= self.config.trade_end_ns:
            return
        account = self.cache.account_for_venue(self.config.instrument_id.venue)
        if account is None:
            return
        balance = account.balance_total(self._quote_currency)
        if balance is None:
            return
        realized = balance.as_double()
        unrealized = 0.0
        value = self.portfolio.unrealized_pnl(self.config.instrument_id)
        if value is not None:
            unrealized = value.as_double()
        self.nav_records.append(
            {
                "observed_time_ns": observed_ns,
                "realized_account_total": realized,
                "unrealized_pnl": unrealized,
                "nav": realized + unrealized,
            }
        )

    def _increment(self, key: str) -> None:
        self.runtime_diagnostics[key] = self.runtime_diagnostics.get(key, 0) + 1

    def diagnostic_snapshot(self) -> dict[str, Any]:
        return {
            "runtime": dict(self.runtime_diagnostics),
            "scheduled_signals": len(self._signal_by_time),
            "entry_pending": self._entry_pending,
            "position_flat": self.portfolio.is_flat(self.config.instrument_id),
            "selected_signal": self._selected_signal.scenario_id if self._selected_signal else None,
        }

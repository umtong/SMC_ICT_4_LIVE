"""NautilusTrader adapter for the candidate-02 causal engine."""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
import json
from typing import Any, Iterable

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Currency
from nautilus_trader.trading.strategy import Strategy

from core import (
    CandidateConfig,
    LiquidityCascadeEngine,
    MarketBar,
    TradeSide,
    TradeSignal,
    Transition,
    size_by_planned_loss,
)


class Candidate02StrategyConfig(StrategyConfig, frozen=True):
    """Serializable runtime configuration shared by backtest and live nodes."""

    instrument_ids: tuple[InstrumentId, ...]
    bar_types: tuple[BarType, ...]
    core_config_json: str
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


class Candidate02Strategy(Strategy):
    """One global decision maker for all allowed instruments.

    Signals observed on the same completed minute are collected and ranked.  The
    strategy waits until the next completed minute before submission so every
    instrument at that timestamp had an equal chance to contribute a signal.
    This conservative one-bar arbitration delay also prevents event-order bias.
    At most one entry bracket or one open position exists across the portfolio.
    """

    def __init__(self, config: Candidate02StrategyConfig) -> None:
        super().__init__(config)
        if len(config.instrument_ids) != len(config.bar_types):
            raise ValueError("instrument_ids and bar_types must have equal length")
        if not config.instrument_ids:
            raise ValueError("at least one instrument is required")
        if len(set(config.instrument_ids)) != len(config.instrument_ids):
            raise ValueError("instrument_ids must be unique")
        if config.trade_end_ns <= config.trade_start_ns:
            raise ValueError("trade_end_ns must be after trade_start_ns")

        core_config = CandidateConfig.from_mapping(json.loads(config.core_config_json))
        self.engines = {
            str(instrument_id): LiquidityCascadeEngine(str(instrument_id), core_config)
            for instrument_id in config.instrument_ids
        }
        self._bar_type_by_instrument = {
            str(instrument_id): bar_type
            for instrument_id, bar_type in zip(config.instrument_ids, config.bar_types, strict=True)
        }
        self._instrument_ids = tuple(config.instrument_ids)
        self._priority = {str(value): index for index, value in enumerate(config.instrument_ids)}
        self._quote_currency = Currency.from_str(config.quote_currency)

        self.transition_records: list[Transition] = []
        self.sizing_records: list[dict[str, Any]] = []
        self.signal_records: list[dict[str, Any]] = []
        self.trade_records: list[dict[str, Any]] = []
        self.nav_records: list[dict[str, Any]] = []
        self.runtime_diagnostics: dict[str, int] = {}

        self._pending_signal_ts: int | None = None
        self._pending_signals: list[TradeSignal] = []
        self._entry_pending = False
        self._selected_signal: TradeSignal | None = None
        self._active_instrument: InstrumentId | None = None
        self._entry_time_ns: int | None = None
        self._entry_reference: float | None = None
        self._forced_exit_reason: str | None = None
        self._last_observed_ns = 0

    def on_start(self) -> None:
        for instrument_id, bar_type in zip(
            self.config.instrument_ids,
            self.config.bar_types,
            strict=True,
        ):
            if self.cache.instrument(instrument_id) is None:
                raise RuntimeError(f"instrument not in cache: {instrument_id}")
            self.subscribe_bars(bar_type)

    def on_bar(self, bar: Bar) -> None:
        observed_ns = int(bar.ts_init)
        self._last_observed_ns = max(self._last_observed_ns, observed_ns)
        if self._pending_signal_ts is not None and observed_ns > self._pending_signal_ts:
            self._flush_pending_signals(observed_ns)

        instrument_id = bar.bar_type.instrument_id
        instrument_key = str(instrument_id)
        engine = self.engines[instrument_key]

        if observed_ns >= self.config.trade_end_ns:
            self._reject_pending_for_reason(observed_ns, "EVALUATION_WINDOW_CLOSED")
            if self._active_instrument is not None and instrument_id == self._active_instrument:
                self._request_flatten(instrument_id, "EVALUATION_END")

        if (
            self._active_instrument is not None
            and instrument_id == self._active_instrument
            and self._entry_time_ns is not None
        ):
            elapsed_bars = (observed_ns - self._entry_time_ns) // 60_000_000_000
            if elapsed_bars >= engine.config.max_hold_bars:
                self._request_flatten(instrument_id, "MAX_HOLD")

        self._record_nav_if_applicable(observed_ns, instrument_id)
        signal = engine.on_bar(
            MarketBar(
                instrument_id=instrument_key,
                ts_ns=observed_ns,
                open=bar.open.as_double(),
                high=bar.high.as_double(),
                low=bar.low.as_double(),
                close=bar.close.as_double(),
                volume=bar.volume.as_double(),
            ),
        )
        self._collect_transitions(engine)
        if signal is None:
            return

        self.signal_records.append(
            {
                "status": "OBSERVED",
                **self._signal_dict(signal),
            },
        )
        if not self.config.trade_start_ns <= observed_ns < self.config.trade_end_ns:
            engine.notify_entry_failed(observed_ns, "OUTSIDE_EVALUATION_WINDOW")
            self._collect_transitions(engine)
            self._increment("SIGNAL_OUTSIDE_WINDOW")
            return
        if self._global_busy():
            engine.notify_entry_failed(observed_ns, "GLOBAL_POSITION_BUSY")
            self._collect_transitions(engine)
            self._increment("SIGNAL_REJECTED_GLOBAL_BUSY")
            return

        if self._pending_signal_ts is None:
            self._pending_signal_ts = observed_ns
        if observed_ns != self._pending_signal_ts:
            # This should only happen with an irregular feed.  Resolve the old
            # timestamp first, then begin a new arbitration group.
            self._flush_pending_signals(observed_ns)
            self._pending_signal_ts = observed_ns
        self._pending_signals.append(signal)

    def on_position_opened(self, event: Any) -> None:
        instrument_id = event.instrument_id
        key = str(instrument_id)
        if self._selected_signal is None or key != self._selected_signal.instrument_id:
            self._increment("UNEXPECTED_POSITION_OPEN")
            self._request_flatten(instrument_id, "UNEXPECTED_POSITION")
            return
        self._entry_pending = False
        self._active_instrument = instrument_id
        self._entry_time_ns = int(event.ts_opened)
        self._entry_reference = float(event.avg_px_open)
        self.engines[key].notify_entry_filled(int(event.ts_opened), float(event.avg_px_open))
        self._collect_transitions(self.engines[key])
        self._increment("POSITION_OPENED")

    def on_position_closed(self, event: Any) -> None:
        key = str(event.instrument_id)
        realized = event.realized_pnl.as_double() if event.realized_pnl is not None else 0.0
        if self._forced_exit_reason is not None:
            outcome = self._forced_exit_reason
        elif realized > 0.0:
            outcome = "WIN"
        elif realized < 0.0:
            outcome = "LOSS"
        else:
            outcome = "FLAT"
        self.trade_records.append(
            {
                "scenario_id": self._selected_signal.scenario_id if self._selected_signal else None,
                "instrument_id": key,
                "entry_time_ns": self._entry_time_ns,
                "exit_time_ns": int(event.ts_closed),
                "avg_px_open": float(event.avg_px_open),
                "avg_px_close": float(event.avg_px_close),
                "realized_pnl": realized,
                "realized_return": float(event.realized_return),
                "duration_ns": int(event.duration_ns),
                "outcome": outcome,
            },
        )
        engine = self.engines.get(key)
        if engine is not None:
            engine.notify_trade_closed(
                int(event.ts_closed),
                exit_price=float(event.avg_px_close),
                outcome=outcome,
            )
            self._collect_transitions(engine)
        self.cancel_all_orders(event.instrument_id)
        self._active_instrument = None
        self._entry_time_ns = None
        self._entry_reference = None
        self._selected_signal = None
        self._entry_pending = False
        self._forced_exit_reason = None
        self._increment("POSITION_CLOSED")

    def on_order_rejected(self, event: Any) -> None:
        self._handle_execution_failure(event, "ORDER_REJECTED")

    def on_order_denied(self, event: Any) -> None:
        self._handle_execution_failure(event, "ORDER_DENIED")

    def on_stop(self) -> None:
        self._reject_pending_for_reason(self._last_observed_ns, "STRATEGY_STOPPED")
        if self._active_instrument is not None:
            self.cancel_all_orders(self._active_instrument)
            self.close_all_positions(self._active_instrument)
        for engine in self.engines.values():
            self._collect_transitions(engine)


    def _record_nav_if_applicable(self, observed_ns: int, current_instrument: InstrumentId) -> None:
        if not self.config.trade_start_ns <= observed_ns <= self.config.trade_end_ns:
            return
        if self.nav_records and self.nav_records[-1]["observed_time_ns"] == observed_ns:
            # Prefer the active instrument's mark if multiple bars share a timestamp.
            if self._active_instrument is None or current_instrument != self._active_instrument:
                return
            self.nav_records.pop()
        elif self._active_instrument is not None and current_instrument != self._active_instrument:
            return
        elif self._active_instrument is None and current_instrument != self._instrument_ids[0]:
            return

        account = self.cache.account_for_venue(current_instrument.venue)
        if account is None:
            return
        balance = account.balance_total(self._quote_currency)
        if balance is None:
            return
        realized_nav = balance.as_double()
        unrealized = 0.0
        if self._active_instrument is not None:
            value = self.portfolio.unrealized_pnl(self._active_instrument)
            if value is not None:
                unrealized = value.as_double()
        self.nav_records.append(
            {
                "observed_time_ns": observed_ns,
                "realized_account_total": realized_nav,
                "unrealized_pnl": unrealized,
                "nav": realized_nav + unrealized,
            },
        )

    def _flush_pending_signals(self, observed_ns: int) -> None:
        if not self._pending_signals:
            self._pending_signal_ts = None
            return
        candidates = self._pending_signals
        self._pending_signals = []
        self._pending_signal_ts = None
        if self._global_busy():
            for signal in candidates:
                self._reject_signal(signal, observed_ns, "GLOBAL_POSITION_BUSY")
            return

        chosen = max(
            candidates,
            key=lambda signal: (signal.score, -self._priority.get(signal.instrument_id, 999)),
        )
        for signal in candidates:
            if signal is not chosen:
                self._reject_signal(signal, observed_ns, "LOWER_CROSS_ASSET_PRIORITY")
        self._submit_signal(chosen, observed_ns)

    def _submit_signal(self, signal: TradeSignal, observed_ns: int) -> None:
        instrument_id = next(
            value for value in self._instrument_ids if str(value) == signal.instrument_id
        )
        instrument = self.cache.instrument(instrument_id)
        if instrument is None:
            self._reject_signal(signal, observed_ns, "INSTRUMENT_MISSING")
            return
        latest_bar = self.cache.bar(self._bar_type_by_instrument[signal.instrument_id])
        entry_reference = (
            latest_bar.close.as_double()
            if latest_bar is not None and int(latest_bar.ts_init) > signal.observed_time_ns
            else signal.entry_reference
        )
        if signal.side is TradeSide.BUY:
            geometry_valid = signal.stop_price < entry_reference < signal.target_price
        else:
            geometry_valid = signal.target_price < entry_reference < signal.stop_price
        if not geometry_valid:
            self._reject_signal(signal, observed_ns, "PRICE_MOVED_THROUGH_GEOMETRY")
            return
        reward_risk = abs(signal.target_price - entry_reference) / abs(entry_reference - signal.stop_price)
        core_min_rr = self.engines[signal.instrument_id].config.min_reward_risk
        if reward_risk < core_min_rr:
            self._reject_signal(signal, observed_ns, "ARBITRATION_DELAY_RR_DECAY")
            return

        account = self.cache.account_for_venue(instrument_id.venue)
        if account is None:
            self._reject_signal(signal, observed_ns, "ACCOUNT_MISSING")
            return
        balance = account.balance_total(self._quote_currency)
        if balance is None:
            self._reject_signal(signal, observed_ns, "QUOTE_BALANCE_MISSING")
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
        sizing_record = {
            "scenario_id": signal.scenario_id,
            "instrument_id": signal.instrument_id,
            "observed_time_ns": observed_ns,
            "entry_reference_after_arbitration": entry_reference,
            "reward_risk_after_arbitration": reward_risk,
            **sizing.to_dict(),
        }
        self.sizing_records.append(sizing_record)
        if sizing.quantity <= 0:
            self._reject_signal(signal, observed_ns, sizing.skipped_reason or "ZERO_QUANTITY")
            return

        side = OrderSide.BUY if signal.side is TradeSide.BUY else OrderSide.SELL
        quantity = instrument.make_qty(sizing.quantity)
        order_list = self.order_factory.bracket(
            instrument_id=instrument_id,
            order_side=side,
            quantity=quantity,
            time_in_force=TimeInForce.GTC,
            tp_price=instrument.make_price(signal.target_price),
            sl_trigger_price=instrument.make_price(signal.stop_price),
            entry_tags=[signal.scenario_id, "candidate-02"],
            tp_tags=[signal.scenario_id, "liquidity-target"],
            sl_tags=[signal.scenario_id, "causal-invalidation"],
        )
        self._selected_signal = signal
        self._entry_pending = True
        self._entry_reference = entry_reference
        self.submit_order_list(order_list)
        self.signal_records.append(
            {
                "status": "SUBMITTED",
                "submission_time_ns": observed_ns,
                "entry_reference_after_arbitration": entry_reference,
                "quantity": str(sizing.quantity),
                **self._signal_dict(signal),
            },
        )
        self._increment("ENTRY_BRACKET_SUBMITTED")

    def _reject_pending_for_reason(self, observed_ns: int, reason: str) -> None:
        candidates = self._pending_signals
        self._pending_signals = []
        self._pending_signal_ts = None
        for signal in candidates:
            self._reject_signal(signal, observed_ns, reason)

    def _reject_signal(self, signal: TradeSignal, observed_ns: int, reason: str) -> None:
        engine = self.engines[signal.instrument_id]
        engine.notify_entry_failed(observed_ns, reason)
        self._collect_transitions(engine)
        self.signal_records.append(
            {
                "status": "REJECTED",
                "rejection_time_ns": observed_ns,
                "reason": reason,
                **self._signal_dict(signal),
            },
        )
        self._increment(f"SIGNAL_REJECTED_{reason}")

    def _handle_execution_failure(self, event: Any, reason: str) -> None:
        instrument_id = event.instrument_id
        key = str(instrument_id)
        self._increment(reason)
        if self._entry_pending and self._selected_signal is not None:
            self.engines[key].notify_entry_failed(int(event.ts_event), reason)
            self._collect_transitions(self.engines[key])
            self.cancel_all_orders(instrument_id)
            self._entry_pending = False
            self._selected_signal = None
            return
        if self._active_instrument == instrument_id:
            self._request_flatten(instrument_id, reason)

    def _request_flatten(self, instrument_id: InstrumentId, reason: str) -> None:
        if self._forced_exit_reason is not None:
            return
        self._forced_exit_reason = reason
        self.cancel_all_orders(instrument_id)
        if not self.portfolio.is_flat(instrument_id):
            self.close_all_positions(instrument_id)
        self._increment(f"FORCED_EXIT_{reason}")

    def _global_busy(self) -> bool:
        if self._entry_pending or self._active_instrument is not None:
            return True
        return any(not self.portfolio.is_flat(value) for value in self._instrument_ids)

    def _collect_transitions(self, engine: LiquidityCascadeEngine) -> None:
        self.transition_records.extend(engine.drain_transitions())

    def _increment(self, key: str) -> None:
        self.runtime_diagnostics[key] = self.runtime_diagnostics.get(key, 0) + 1

    @staticmethod
    def _signal_dict(signal: TradeSignal) -> dict[str, Any]:
        payload = asdict(signal)
        payload["side"] = signal.side.value
        payload["reward_risk"] = signal.reward_risk
        return payload

    def diagnostic_snapshot(self) -> dict[str, Any]:
        return {
            "runtime": dict(self.runtime_diagnostics),
            "engines": {key: engine.state_snapshot() for key, engine in self.engines.items()},
            "pending_signal_count": len(self._pending_signals),
            "entry_pending": self._entry_pending,
            "active_instrument": str(self._active_instrument) if self._active_instrument else None,
        }

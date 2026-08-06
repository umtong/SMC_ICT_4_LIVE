"""NautilusTrader strategy for LVCFR competing continuation/failure states.

All order lifecycle, fills, fees, funding settlement, positions, account balances,
and portfolio accounting are native NautilusTrader responsibilities.  This class
only owns causal scenario state and project-prescribed risk-budget sizing.
"""
from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import FundingRateUpdate, QuoteTick
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.events import OrderDenied, OrderFilled, OrderRejected, PositionClosed
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

NS_PER_MINUTE = 60_000_000_000
NS_PER_DAY = 86_400_000_000_000


def expected_funding_debit_per_unit(
    *,
    entry_price: float,
    direction: int,
    funding_rate: float,
    entry_time_ns: int,
    max_holding_minutes: int,
    next_funding_ns: int | None,
    funding_interval_minutes: int,
) -> float:
    """Return full adverse funding settlements crossed by maximum holding.

    Perpetual funding is discrete at settlement boundaries, not prorated by
    elapsed holding time. Credits reduce realized cost but are not counted as a
    risk-budget benefit.
    """
    adverse_rate = direction * funding_rate
    if adverse_rate <= 0.0 or next_funding_ns is None:
        return 0.0
    interval_ns = max(1, funding_interval_minutes) * NS_PER_MINUTE
    holding_end_ns = entry_time_ns + max_holding_minutes * NS_PER_MINUTE
    if next_funding_ns > holding_end_ns:
        return 0.0
    settlements = 1 + max(0, holding_end_ns - next_funding_ns) // interval_ns
    return entry_price * adverse_rate * float(settlements)


def position_closed_peak_qty(event: Any) -> float:
    """Return peak position quantity across NT 1.230 Python backends."""
    value = getattr(event, "peak_qty", None)
    if value is None:
        value = getattr(event, "peak_quantity")
    return float(value)


def position_closed_duration_ns(event: Any) -> int:
    """Return closed-position duration across NT 1.230 Python backends."""
    value = getattr(event, "duration_ns", None)
    if value is None:
        value = getattr(event, "duration")
    return int(value)


VALID_ENTRY_KINDS = frozenset({"CONTINUATION", "REVERSAL"})


def signal_entry_kind(signal: dict[str, Any]) -> str:
    """Return a validated explicit entry mode, defaulting legacy signals."""
    kind = str(signal.get("entry_kind", "CONTINUATION")).upper()
    if kind not in VALID_ENTRY_KINDS:
        raise ValueError(f"unsupported entry_kind={kind!r}")
    return kind


def signal_structural_target(signal: dict[str, Any]) -> float | None:
    """Return a validated causal liquidity objective from a derived schedule."""
    raw = signal.get("structural_target")
    if raw is None:
        return None
    value = float(raw)
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"invalid structural_target={raw!r}")
    return value


def signal_structural_protection_trigger(signal: dict[str, Any]) -> float | None:
    """Return a validated causal first-objective protection level."""
    raw = signal.get("structural_protection_trigger")
    if raw is None:
        return None
    value = float(raw)
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"invalid structural_protection_trigger={raw!r}")
    return value


def native_equity_amount(portfolio: Any, venue: Any, currency: Any) -> float:
    """Return one currency's native Portfolio equity as a scalar.

    NautilusTrader 1.230 exposes ``Portfolio.equity`` as a
    ``dict[Currency, Money]`` even for a single-currency account. The explicit
    currency lookup keeps risk sizing, episode accounting, drawdown, and final
    NAV on the same native portfolio value without reconstructing PnL.
    """
    values = portfolio.equity(venue)
    if not isinstance(values, dict):
        return float(values)
    money = values.get(currency)
    if money is None:
        currency_code = str(currency)
        for key, value in values.items():
            if str(key) == currency_code:
                money = value
                break
    if money is None:
        if len(values) == 1:
            money = next(iter(values.values()))
        else:
            raise RuntimeError(
                f"native equity missing currency {currency}: available={list(values)}"
            )
    return float(money)


class NTLvcfrConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    signals_path: str
    output_dir: str
    evaluation_start_ns: int
    evaluation_end_ns: int
    risk_fraction: float = 0.03
    taker_fee_bps: float = 5.0
    slippage_impact_bps: float = 1.5
    continuation_target_net_r: float = 3.0
    continuation_protection_activate_r: float = 2.0
    continuation_protection_lock_r: float = 0.5
    continuation_trail_minutes: int = 20
    continuation_trail_buffer_atr: float = 0.05
    continuation_max_holding_minutes: int = 240
    rapid_failure_minutes: int = 10
    reversal_entry_delay_minutes: int = 1
    reversal_stop_buffer_atr: float = 0.20
    reversal_target_net_r: float = 1.5
    reversal_max_holding_minutes: int = 180


@dataclass(slots=True)
class PendingEntry:
    signal: dict[str, Any]
    kind: str
    direction: int
    eligible_time_ns: int
    stop: float
    atr: float
    observed_low: float = math.inf
    observed_high: float = -math.inf


@dataclass(slots=True)
class ActiveLeg:
    signal: dict[str, Any]
    kind: str
    direction: int
    stop: float
    atr: float
    target_r: float
    max_holding_minutes: int
    planned_loss: float
    expected_loss_per_unit: float
    maximum_expected_funding_per_unit: float
    equity_before: float
    entry_order_id: Any
    entry_time_ns: int | None = None
    entry_qty: float = 0.0
    entry_notional: float = 0.0
    entry_avg: float = 0.0
    target_price: float = 0.0
    lock_price: float = 0.0
    break_even_price: float = 0.0
    structural_protection_active: bool = False
    protection_active: bool = False
    mfe_net_r: float = -math.inf
    failure_low: float = math.inf
    failure_high: float = -math.inf
    settled_funding_cost_per_unit: float = 0.0


class NTLvcfrStrategy(Strategy):
    def __init__(self, config: NTLvcfrConfig) -> None:
        super().__init__(config=config)
        if not 0 < config.risk_fraction <= 0.03:
            raise ValueError("risk_fraction must be in (0, 0.03]")
        self.instrument = None
        self.signals: list[dict[str, Any]] = []
        self.signal_index = 0
        self.pending: PendingEntry | None = None
        self.active: ActiveLeg | None = None
        self.entry_order_id = None
        self.exit_order_id = None
        self.exit_reason: str | None = None
        self.latest_quote: QuoteTick | None = None
        self.latest_funding_rate = 0.0
        self.latest_funding_interval_minutes = 480
        self.next_funding_ns: int | None = None
        self.current_episode: dict[str, Any] | None = None
        self.episodes: list[dict[str, Any]] = []
        self.legs: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.equity_curve: list[dict[str, Any]] = []
        self.counters: dict[str, int] = {
            "signals": 0,
            "blocked_by_single_slot": 0,
            "invalidated_in_entry_buffer": 0,
            "invalid_entry_price": 0,
            "invalid_structural_target": 0,
            "structural_protection_activations": 0,
            "structural_break_even_ratchets": 0,
            "structural_trail_updates": 0,
            "structural_objective_buffer_activations": 0,
            "waypoint_structure_trail_activations": 0,
            "entries_submitted": 0,
            "entries_rejected": 0,
            "exits_submitted": 0,
            "rapid_failure_reversals": 0,
            "protection_activations": 0,
            "trail_updates": 0,
            "incomplete_at_end": 0,
        }
        self._minute = -1
        self._minute_low = math.inf
        self._minute_high = -math.inf
        self._completed_minutes: deque[tuple[int, float, float]] = deque(maxlen=600)
        self._last_equity_minute = -1
        self._evaluation_ending = False

    @property
    def busy(self) -> bool:
        return self.pending is not None or self.active is not None or self.entry_order_id is not None or self.exit_order_id is not None

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            raise RuntimeError(f"instrument missing from cache: {self.config.instrument_id}")
        self.signals = json.loads(Path(self.config.signals_path).read_text(encoding="utf-8"))
        self.signals.sort(key=lambda item: int(item["confirm_time_ns"]))
        self.counters["signals"] = len(self.signals)
        self.subscribe_quote_ticks(self.config.instrument_id)
        self.subscribe_funding_rates(self.config.instrument_id)
        self._emit(
            scenario_id="NT-LVCFR-RUN",
            event_type="STRATEGY_STARTED",
            event_time_ns=self.config.evaluation_start_ns,
            observed_time_ns=self.config.evaluation_start_ns,
            previous_state="CREATED",
            next_state="IDLE",
            reason_code="NAUTILUS_TRADER_NATIVE_PATH_ACTIVE",
            reference_price=None,
            details={"signals": len(self.signals)},
        )

    def on_funding_rate(self, update: FundingRateUpdate) -> None:
        self.latest_funding_rate = float(update.rate)
        if update.interval:
            self.latest_funding_interval_minutes = int(update.interval)
        event_ns = int(update.ts_event)
        explicit_next = update.next_funding_ns
        self.next_funding_ns = (
            int(explicit_next)
            if explicit_next is not None
            else event_ns + self.latest_funding_interval_minutes * NS_PER_MINUTE
        )
        active = self.active
        if (
            active is not None
            and active.entry_time_ns is not None
            and active.entry_qty > 0.0
            and active.entry_time_ns < event_ns
        ):
            if self.latest_quote is None:
                reference_price = active.entry_avg
            else:
                reference_price = (
                    float(self.latest_quote.bid_price) + float(self.latest_quote.ask_price)
                ) / 2.0
            active.settled_funding_cost_per_unit += (
                active.direction * reference_price * self.latest_funding_rate
            )
            self._emit(
                scenario_id=active.signal["scenario_id"],
                event_type="FUNDING_SETTLEMENT_OBSERVED",
                event_time_ns=event_ns,
                observed_time_ns=int(update.ts_init),
                previous_state=f"{active.kind}_ACTIVE",
                next_state=f"{active.kind}_ACTIVE",
                reason_code="NAUTILUS_NATIVE_DISCRETE_FUNDING_BOUNDARY",
                reference_price=reference_price,
                details={
                    "rate": self.latest_funding_rate,
                    "estimated_cost_per_unit": active.direction * reference_price * self.latest_funding_rate,
                    "cumulative_estimated_cost_per_unit": active.settled_funding_cost_per_unit,
                },
            )

    def on_quote_tick(self, tick: QuoteTick) -> None:
        self.latest_quote = tick
        timestamp_ns = int(tick.ts_init)
        bid = float(tick.bid_price)
        ask = float(tick.ask_price)
        mid = (bid + ask) / 2.0
        self._update_minute(timestamp_ns, mid)
        self._capture_equity(timestamp_ns)

        if timestamp_ns >= self.config.evaluation_end_ns:
            self._evaluation_ending = True
            if self.active is not None and self.exit_order_id is None:
                self._submit_exit("EVALUATION_END", timestamp_ns)
            self.pending = None
            return

        self._observe_due_signals(timestamp_ns)
        if self.pending is not None:
            self._process_pending(tick, mid)
        if self.active is not None and self.exit_order_id is None:
            self._manage_active(tick, mid)

    def on_order_filled(self, event: OrderFilled) -> None:
        if self.entry_order_id is not None and event.client_order_id == self.entry_order_id:
            if self.active is None:
                raise RuntimeError("entry fill received without active leg state")
            quantity = float(event.last_qty)
            price = float(event.last_px)
            self.active.entry_qty += quantity
            self.active.entry_notional += quantity * price
            self.active.entry_avg = self.active.entry_notional / self.active.entry_qty
            if self.active.entry_time_ns is None:
                self.active.entry_time_ns = int(event.ts_event)
            self._refresh_prices(self.active)
            self.active.failure_low = min(self.active.failure_low, price)
            self.active.failure_high = max(self.active.failure_high, price)
            self._emit(
                scenario_id=self.active.signal["scenario_id"],
                event_type=f"{self.active.kind}_ENTRY_FILL",
                event_time_ns=int(event.ts_event),
                observed_time_ns=int(event.ts_init),
                previous_state="ENTRY_PENDING",
                next_state=f"{self.active.kind}_ACTIVE",
                reason_code="NAUTILUS_NATIVE_ORDER_FILLED",
                reference_price=price,
                details={
                    "client_order_id": str(event.client_order_id),
                    "fill_quantity": quantity,
                    "fill_price": price,
                    "commission": str(event.commission),
                    "average_entry": self.active.entry_avg,
                    "planned_loss": self.active.planned_loss,
                    "expected_loss_per_unit": self.active.expected_loss_per_unit,
                },
            )
            order = self.cache.order(self.entry_order_id)
            if order is not None and order.is_closed:
                self.entry_order_id = None
            return

        if self.exit_order_id is not None and event.client_order_id == self.exit_order_id:
            self._emit(
                scenario_id=self.active.signal["scenario_id"] if self.active else "UNKNOWN",
                event_type="EXIT_FILL",
                event_time_ns=int(event.ts_event),
                observed_time_ns=int(event.ts_init),
                previous_state="EXIT_PENDING",
                next_state="POSITION_REDUCING",
                reason_code=self.exit_reason or "UNKNOWN",
                reference_price=float(event.last_px),
                details={
                    "fill_quantity": float(event.last_qty),
                    "fill_price": float(event.last_px),
                    "commission": str(event.commission),
                },
            )

    def on_position_closed(self, event: PositionClosed) -> None:
        active = self.active
        if active is None:
            return
        timestamp_ns = int(event.ts_closed) if event.ts_closed is not None else int(event.ts_event)
        exit_price = float(event.avg_px_close) if event.avg_px_close is not None else float(event.last_px)
        equity_after = self._equity()
        equity_before = active.equity_before
        pnl = equity_after - equity_before
        leg = {
            "scenario_id": active.signal["scenario_id"],
            "kind": active.kind,
            "direction": "LONG" if active.direction > 0 else "SHORT",
            "entry_time_ns": active.entry_time_ns,
            "exit_time_ns": timestamp_ns,
            "entry_price": active.entry_avg,
            "exit_price": exit_price,
            "quantity": position_closed_peak_qty(event),
            "planned_loss": active.planned_loss,
            "native_equity_before": equity_before,
            "native_equity_after": equity_after,
            "native_account_pnl": pnl,
            "net_r": pnl / active.planned_loss if active.planned_loss > 0 else 0.0,
            "realized_pnl_event": str(event.realized_pnl),
            "realized_return_event": float(event.realized_return),
            "duration_ns": position_closed_duration_ns(event),
            "exit_reason": self.exit_reason or "UNKNOWN",
            "target_price": active.target_price,
            "target_mode": active.signal.get("target_mode", "EXISTING_NET_R_OBJECTIVE"),
            "structural_protection_trigger": signal_structural_protection_trigger(active.signal),
            "break_even_price": active.break_even_price,
            "structural_protection_active": active.structural_protection_active,
            "structural_protection_stop": active.stop if active.structural_protection_active else None,
            "structural_trail_updates": self.counters["structural_trail_updates"],
            "protection_active": active.protection_active,
            "mfe_net_r": active.mfe_net_r,
            "settled_funding_cost_per_unit_estimate": active.settled_funding_cost_per_unit,
        }
        self.legs.append(leg)
        if self.current_episode is None:
            raise RuntimeError("position closed without episode")
        self.current_episode["legs"].append(leg)
        self._emit(
            scenario_id=active.signal["scenario_id"],
            event_type=f"{active.kind}_POSITION_CLOSED",
            event_time_ns=timestamp_ns,
            observed_time_ns=int(event.ts_init),
            previous_state=f"{active.kind}_ACTIVE",
            next_state="RAPID_FAILURE_EVALUATION" if active.kind == "CONTINUATION" else "CLOSED",
            reason_code=self.exit_reason or "UNKNOWN",
            reference_price=exit_price,
            details={"native_equity_after": equity_after, "native_account_pnl": pnl, "net_r": leg["net_r"]},
        )

        rapid_failure = (
            active.kind == "CONTINUATION"
            and (self.exit_reason or "") == "INITIAL_STOP"
            and active.entry_time_ns is not None
            and timestamp_ns - active.entry_time_ns <= self.config.rapid_failure_minutes * NS_PER_MINUTE
            and not active.protection_active
            and not active.structural_protection_active
            and not bool(active.signal.get("disable_rapid_failure_reversal", False))
            and not self._evaluation_ending
        )
        failed_direction = active.direction
        signal = active.signal
        failure_low = active.failure_low
        failure_high = active.failure_high
        at = active.atr

        self.active = None
        self.entry_order_id = None
        self.exit_order_id = None
        self.exit_reason = None

        if rapid_failure:
            eligible = ((timestamp_ns // NS_PER_MINUTE) + self.config.reversal_entry_delay_minutes) * NS_PER_MINUTE
            self.pending = PendingEntry(
                signal=signal,
                kind="REVERSAL",
                direction=-failed_direction,
                eligible_time_ns=eligible,
                stop=0.0,
                atr=at,
                observed_low=failure_low,
                observed_high=failure_high,
            )
            self.counters["rapid_failure_reversals"] += 1
            self._emit(
                scenario_id=signal["scenario_id"],
                event_type="RAPID_VACUUM_FAILURE_CONFIRMED",
                event_time_ns=timestamp_ns,
                observed_time_ns=timestamp_ns,
                previous_state="RAPID_FAILURE_EVALUATION",
                next_state="REVERSAL_BUFFER",
                reason_code="CONTINUATION_INVALIDATED_WITHIN_TEN_MINUTES",
                reference_price=None,
                details={"eligible_time_ns": eligible},
            )
        else:
            self._finalize_episode(timestamp_ns)

    def on_order_rejected(self, event: OrderRejected) -> None:
        self._handle_order_failure(event.client_order_id, event.reason, int(event.ts_init))

    def on_order_denied(self, event: OrderDenied) -> None:
        self._handle_order_failure(event.client_order_id, event.reason, int(event.ts_init))

    def on_order_canceled(self, event: Any) -> None:
        reason = getattr(event, "reason", "CANCELED")
        self._handle_order_failure(event.client_order_id, str(reason), int(event.ts_init))

    def on_order_expired(self, event: Any) -> None:
        self._handle_order_failure(event.client_order_id, "EXPIRED", int(event.ts_init))

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.counters["incomplete_at_end"] += 1
        self._capture_equity(self.config.evaluation_end_ns)
        self._write_outputs()

    def _observe_due_signals(self, timestamp_ns: int) -> None:
        while self.signal_index < len(self.signals) and int(self.signals[self.signal_index]["confirm_time_ns"]) <= timestamp_ns:
            signal = self.signals[self.signal_index]
            self.signal_index += 1
            if self.busy:
                self.counters["blocked_by_single_slot"] += 1
                self._emit(
                    scenario_id=signal["scenario_id"],
                    event_type="SCENARIO_BLOCKED",
                    event_time_ns=int(signal["confirm_time_ns"]),
                    observed_time_ns=timestamp_ns,
                    previous_state="VACUUM_CONFIRMED",
                    next_state="BLOCKED",
                    reason_code="SINGLE_NATIVE_SLOT_OCCUPIED",
                    reference_price=None,
                    details={},
                )
                continue
            kind = signal_entry_kind(signal)
            scenario_kind = str(signal.get("scenario_kind", "LIQUIDITY_VACUUM"))
            self.current_episode = {
                "scenario_id": signal["scenario_id"],
                "scenario_kind": scenario_kind,
                "entry_kind": kind,
                "signal_time_ns": int(signal["confirm_time_ns"]),
                "native_equity_before": self._equity(),
                "legs": [],
            }
            self.pending = PendingEntry(
                signal=signal,
                kind=kind,
                direction=int(signal["direction"]),
                eligible_time_ns=int(signal["eligible_time_ns"]),
                stop=float(signal["initial_stop"]),
                atr=float(signal["atr"]),
            )
            self._emit(
                scenario_id=signal["scenario_id"],
                event_type="AUCTION_STATE_CONFIRMED",
                event_time_ns=int(signal["confirm_time_ns"]),
                observed_time_ns=timestamp_ns,
                previous_state="CAUSAL_EVENT_CONFIRMED",
                next_state="ENTRY_BUFFER" if kind == "CONTINUATION" else "REVERSAL_BUFFER",
                reason_code=scenario_kind,
                reference_price=None,
                details=dict(signal["details"]),
            )

    def _process_pending(self, tick: QuoteTick, mid: float) -> None:
        pending = self.pending
        if pending is None:
            return
        timestamp_ns = int(tick.ts_init)
        pending.observed_low = min(pending.observed_low, mid)
        pending.observed_high = max(pending.observed_high, mid)

        if pending.kind == "CONTINUATION":
            invalid = mid <= pending.stop if pending.direction > 0 else mid >= pending.stop
            if timestamp_ns < pending.eligible_time_ns and invalid:
                self.counters["invalidated_in_entry_buffer"] += 1
                self._emit(
                    scenario_id=pending.signal["scenario_id"],
                    event_type="ENTRY_BUFFER_INVALIDATED",
                    event_time_ns=timestamp_ns,
                    observed_time_ns=timestamp_ns,
                    previous_state="ENTRY_BUFFER",
                    next_state="INVALIDATED",
                    reason_code="EVENT_EXTREME_FAILED_BEFORE_NATIVE_ENTRY",
                    reference_price=mid,
                    details={"stop": pending.stop},
                )
                self.pending = None
                self._finalize_episode(timestamp_ns)
                return
        elif timestamp_ns >= pending.eligible_time_ns and pending.stop == 0.0:
            pending.stop = (
                pending.observed_low - self.config.reversal_stop_buffer_atr * pending.atr
                if pending.direction > 0
                else pending.observed_high + self.config.reversal_stop_buffer_atr * pending.atr
            )

        if timestamp_ns < pending.eligible_time_ns:
            return
        executable = float(tick.ask_price) if pending.direction > 0 else float(tick.bid_price)
        if pending.direction * (executable - pending.stop) <= 0:
            self.counters["invalid_entry_price"] += 1
            self._emit(
                scenario_id=pending.signal["scenario_id"],
                event_type="ENTRY_PRICE_INVALID",
                event_time_ns=timestamp_ns,
                observed_time_ns=timestamp_ns,
                previous_state="ENTRY_BUFFER" if pending.kind == "CONTINUATION" else "REVERSAL_BUFFER",
                next_state="INVALIDATED",
                reason_code="FIRST_ELIGIBLE_NATIVE_QUOTE_BEYOND_INVALIDATION",
                reference_price=executable,
                details={"stop": pending.stop},
            )
            self.pending = None
            self._finalize_episode(timestamp_ns)
            return
        structural_target = signal_structural_target(pending.signal)
        if (
            structural_target is not None
            and pending.direction * (structural_target - executable) <= 0.0
        ):
            self.counters["invalid_structural_target"] += 1
            self._emit(
                scenario_id=pending.signal["scenario_id"],
                event_type="STRUCTURAL_TARGET_ALREADY_REACHED",
                event_time_ns=timestamp_ns,
                observed_time_ns=timestamp_ns,
                previous_state="ENTRY_BUFFER" if pending.kind == "CONTINUATION" else "REVERSAL_BUFFER",
                next_state="INVALIDATED",
                reason_code="CAUSAL_LIQUIDITY_OBJECTIVE_NOT_AHEAD_OF_EXECUTABLE_ENTRY",
                reference_price=executable,
                details={"structural_target": structural_target},
            )
            self.pending = None
            self._finalize_episode(timestamp_ns)
            return
        self._submit_entry(pending, tick)

    def _submit_entry(self, pending: PendingEntry, tick: QuoteTick) -> None:
        if self.instrument is None:
            raise RuntimeError("instrument unavailable")
        direction = pending.direction
        entry = float(tick.ask_price) if direction > 0 else float(tick.bid_price)
        bid = float(tick.bid_price); ask = float(tick.ask_price)
        half_spread_fraction = (ask - bid) / (ask + bid) if ask + bid > 0 else 0.0
        expected_stop_fill = pending.stop * (1.0 - direction * half_spread_fraction)
        fee = self.config.taker_fee_bps / 10_000.0
        hold = self.config.continuation_max_holding_minutes if pending.kind == "CONTINUATION" else self.config.reversal_max_holding_minutes
        expected_funding = expected_funding_debit_per_unit(
            entry_price=entry,
            direction=direction,
            funding_rate=self.latest_funding_rate,
            entry_time_ns=int(tick.ts_init),
            max_holding_minutes=hold,
            next_funding_ns=self.next_funding_ns,
            funding_interval_minutes=self.latest_funding_interval_minutes,
        )
        loss_per_unit = abs(entry - expected_stop_fill) + entry * fee + expected_stop_fill * fee + expected_funding
        if not math.isfinite(loss_per_unit) or loss_per_unit <= 0:
            raise RuntimeError("invalid expected loss per unit")
        equity = self._equity()
        planned_loss = equity * self.config.risk_fraction
        raw_quantity = planned_loss / loss_per_unit
        quantity = self.instrument.make_qty(Decimal(str(raw_quantity)))
        if float(quantity) <= 0:
            raise RuntimeError("risk-based quantity rounded to zero")
        side = OrderSide.BUY if direction > 0 else OrderSide.SELL
        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=quantity,
            # A Binance market order is not a one-level all-or-none order.
            # On L1 data NautilusTrader fills displayed top liquidity first and
            # completes a GTC market remainder one tick through the book. The
            # separately applied 1.5 bp impact remains the conservative fill cost.
            time_in_force=TimeInForce.GTC,
            reduce_only=False,
            tags=["NT_LVCFR", pending.kind, pending.signal["scenario_id"]],
        )
        target_r = self.config.continuation_target_net_r if pending.kind == "CONTINUATION" else self.config.reversal_target_net_r
        max_hold = self.config.continuation_max_holding_minutes if pending.kind == "CONTINUATION" else self.config.reversal_max_holding_minutes
        self.active = ActiveLeg(
            signal=pending.signal,
            kind=pending.kind,
            direction=direction,
            stop=pending.stop,
            atr=pending.atr,
            target_r=target_r,
            max_holding_minutes=max_hold,
            planned_loss=planned_loss,
            expected_loss_per_unit=loss_per_unit,
            maximum_expected_funding_per_unit=expected_funding,
            equity_before=equity,
            entry_order_id=order.client_order_id,
            failure_low=pending.observed_low,
            failure_high=pending.observed_high,
        )
        self.pending = None
        self.entry_order_id = order.client_order_id
        self.counters["entries_submitted"] += 1
        self.submit_order(order)
        self._emit(
            scenario_id=self.active.signal["scenario_id"],
            event_type=f"{self.active.kind}_ENTRY_SUBMITTED",
            event_time_ns=int(tick.ts_event),
            observed_time_ns=int(tick.ts_init),
            previous_state="ENTRY_BUFFER" if self.active.kind == "CONTINUATION" else "REVERSAL_BUFFER",
            next_state="ENTRY_PENDING",
            reason_code="PROJECT_NAV_RISK_BUDGET_NATIVE_MARKET_ORDER",
            reference_price=entry,
            details={
                "quantity": str(quantity),
                "native_equity": equity,
                "planned_loss": planned_loss,
                "expected_loss_per_unit": loss_per_unit,
                "expected_entry": entry,
                "expected_stop_fill": expected_stop_fill,
                "expected_funding_per_unit": expected_funding,
                "structural_target": signal_structural_target(self.active.signal),
                "structural_protection_trigger": signal_structural_protection_trigger(self.active.signal),
                "target_mode": self.active.signal.get("target_mode", "EXISTING_NET_R_OBJECTIVE"),
            },
        )

    def _refresh_prices(self, active: ActiveLeg) -> None:
        fee = self.config.taker_fee_bps / 10_000.0
        reward = active.target_r * active.expected_loss_per_unit + active.maximum_expected_funding_per_unit
        lock_reward = self.config.continuation_protection_lock_r * active.expected_loss_per_unit + active.maximum_expected_funding_per_unit
        structural_target = signal_structural_target(active.signal)
        funding = active.maximum_expected_funding_per_unit
        if active.direction > 0:
            generic_target = (reward + active.entry_avg * (1.0 + fee)) / (1.0 - fee)
            active.lock_price = (lock_reward + active.entry_avg * (1.0 + fee)) / (1.0 - fee)
            active.break_even_price = (funding + active.entry_avg * (1.0 + fee)) / (1.0 - fee)
        else:
            generic_target = (active.entry_avg * (1.0 - fee) - reward) / (1.0 + fee)
            active.lock_price = (active.entry_avg * (1.0 - fee) - lock_reward) / (1.0 + fee)
            active.break_even_price = (active.entry_avg * (1.0 - fee) - funding) / (1.0 + fee)
        active.target_price = structural_target if structural_target is not None else generic_target

    def _manage_active(self, tick: QuoteTick, mid: float) -> None:
        active = self.active
        if active is None or active.entry_qty <= 0 or active.entry_time_ns is None:
            return
        timestamp_ns = int(tick.ts_init)
        executable = float(tick.bid_price) if active.direction > 0 else float(tick.ask_price)
        active.failure_low = min(active.failure_low, mid)
        active.failure_high = max(active.failure_high, mid)
        net_per_unit = self._estimated_net_per_unit(active, executable, timestamp_ns)
        net_r = net_per_unit / active.expected_loss_per_unit
        active.mfe_net_r = max(active.mfe_net_r, net_r)

        structural_trigger = signal_structural_protection_trigger(active.signal)
        scenario_kind = str(active.signal.get("scenario_kind", ""))
        boundary_invalidation = scenario_kind == "VALUE_EDGE_CONTINUATION"
        if (
            structural_trigger is not None
            and not active.structural_protection_active
            and active.direction * (executable - structural_trigger) > 0.0
        ):
            active.structural_protection_active = True
            self.counters["structural_protection_activations"] += 1
            if boundary_invalidation:
                buffer = self.config.continuation_trail_buffer_atr * active.atr
                buffered_stop = structural_trigger - active.direction * buffer
                if active.direction * (executable - buffered_stop) <= 0.0:
                    raise RuntimeError("buffered structural stop is not behind executable price")
                active.stop = (
                    max(active.stop, buffered_stop)
                    if active.direction > 0
                    else min(active.stop, buffered_stop)
                )
                self.counters["structural_objective_buffer_activations"] += 1
                self._emit(
                    scenario_id=active.signal["scenario_id"],
                    event_type="VALUE_EDGE_BOUNDARY_PROTECTION_ACTIVATED",
                    event_time_ns=timestamp_ns,
                    observed_time_ns=timestamp_ns,
                    previous_state=f"{active.kind}_ACTIVE",
                    next_state=f"{active.kind}_STRUCTURALLY_PROTECTED",
                    reason_code="PRIOR_RANGE_EXTERNAL_BECAME_CAUSAL_INVALIDATION",
                    reference_price=active.stop,
                    details={
                        "scenario_kind": scenario_kind,
                        "structural_trigger": structural_trigger,
                        "structural_buffer": buffer,
                        "buffered_stop": buffered_stop,
                        "after_cost_break_even": active.break_even_price,
                        "mfe_net_r": net_r,
                    },
                )
            else:
                self.counters["waypoint_structure_trail_activations"] += 1
                self._emit(
                    scenario_id=active.signal["scenario_id"],
                    event_type="INTERMEDIATE_LIQUIDITY_WAYPOINT_TRAIL_ARMED",
                    event_time_ns=timestamp_ns,
                    observed_time_ns=timestamp_ns,
                    previous_state=f"{active.kind}_ACTIVE",
                    next_state=f"{active.kind}_STRUCTURALLY_PROTECTED",
                    reason_code="FIRST_OBJECTIVE_IS_WAYPOINT_NOT_EXACT_INVALIDATION",
                    reference_price=structural_trigger,
                    details={
                        "scenario_kind": scenario_kind,
                        "structural_trigger": structural_trigger,
                        "existing_stop": active.stop,
                        "after_cost_break_even": active.break_even_price,
                        "mfe_net_r": net_r,
                    },
                )

        if active.structural_protection_active and not boundary_invalidation:
            structural_stop = self._structural_protection_stop(active)
            if (
                structural_stop is not None
                and active.direction * (executable - structural_stop) > 0.0
            ):
                updated = (
                    max(active.stop, structural_stop)
                    if active.direction > 0
                    else min(active.stop, structural_stop)
                )
                if updated != active.stop:
                    active.stop = updated
                    self.counters["structural_trail_updates"] += 1
                    self._emit(
                        scenario_id=active.signal["scenario_id"],
                        event_type="INTERMEDIATE_WAYPOINT_TRAIL_UPDATED",
                        event_time_ns=timestamp_ns,
                        observed_time_ns=timestamp_ns,
                        previous_state=f"{active.kind}_STRUCTURALLY_PROTECTED",
                        next_state=f"{active.kind}_STRUCTURALLY_PROTECTED",
                        reason_code="COMPLETED_TWENTY_MINUTE_STRUCTURE_ADVANCED_AFTER_WAYPOINT",
                        reference_price=active.stop,
                        details={
                            "scenario_kind": scenario_kind,
                            "structural_trigger": structural_trigger,
                            "structural_stop": structural_stop,
                            "mfe_net_r": net_r,
                        },
                    )

        if (
            active.structural_protection_active
            and active.direction * (executable - active.break_even_price) > 0.0
        ):
            ratcheted = (
                max(active.stop, active.break_even_price)
                if active.direction > 0
                else min(active.stop, active.break_even_price)
            )
            if ratcheted != active.stop:
                active.stop = ratcheted
                self.counters["structural_break_even_ratchets"] += 1
                self._emit(
                    scenario_id=active.signal["scenario_id"],
                    event_type="STRUCTURAL_PROTECTION_RATCHETED_TO_AFTER_COST_BREAK_EVEN",
                    event_time_ns=timestamp_ns,
                    observed_time_ns=timestamp_ns,
                    previous_state=f"{active.kind}_STRUCTURALLY_PROTECTED",
                    next_state=f"{active.kind}_STRUCTURALLY_PROTECTED",
                    reason_code="AFTER_COST_BREAK_EVEN_TRADED_AFTER_FIRST_OBJECTIVE",
                    reference_price=active.stop,
                    details={
                        "structural_trigger": structural_trigger,
                        "after_cost_break_even": active.break_even_price,
                        "mfe_net_r": net_r,
                    },
                )

        if active.kind == "CONTINUATION" and not active.protection_active and net_r >= self.config.continuation_protection_activate_r:
            active.protection_active = True
            active.stop = max(active.stop, active.lock_price) if active.direction > 0 else min(active.stop, active.lock_price)
            self.counters["protection_activations"] += 1
            self._emit(
                scenario_id=active.signal["scenario_id"],
                event_type="PROTECTION_ACTIVATED",
                event_time_ns=timestamp_ns,
                observed_time_ns=timestamp_ns,
                previous_state="CONTINUATION_ACTIVE",
                next_state="CONTINUATION_PROTECTED",
                reason_code="NATIVE_EXECUTABLE_MFE_REACHED_TWO_R",
                reference_price=active.stop,
                details={"mfe_net_r": net_r, "lock_price": active.lock_price},
            )

        if active.kind == "CONTINUATION" and active.protection_active:
            updated = self._structural_stop(active)
            if updated is not None:
                if active.direction > 0 and updated > active.stop:
                    active.stop = updated; self.counters["trail_updates"] += 1
                elif active.direction < 0 and updated < active.stop:
                    active.stop = updated; self.counters["trail_updates"] += 1

        if active.direction > 0 and executable <= active.stop:
            reason = (
                "PROTECTED_TRAIL"
                if active.protection_active
                else "STRUCTURAL_PROTECTION"
                if active.structural_protection_active
                else "INITIAL_STOP"
            )
            self._submit_exit(reason, timestamp_ns)
        elif active.direction < 0 and executable >= active.stop:
            reason = (
                "PROTECTED_TRAIL"
                if active.protection_active
                else "STRUCTURAL_PROTECTION"
                if active.structural_protection_active
                else "INITIAL_STOP"
            )
            self._submit_exit(reason, timestamp_ns)
        elif active.direction > 0 and executable >= active.target_price:
            reason = "STRUCTURAL_TARGET" if signal_structural_target(active.signal) is not None else "TARGET"
            self._submit_exit(reason, timestamp_ns)
        elif active.direction < 0 and executable <= active.target_price:
            reason = "STRUCTURAL_TARGET" if signal_structural_target(active.signal) is not None else "TARGET"
            self._submit_exit(reason, timestamp_ns)
        elif timestamp_ns - active.entry_time_ns >= active.max_holding_minutes * NS_PER_MINUTE:
            self._submit_exit("TIME", timestamp_ns)

    def _structural_protection_stop(self, active: ActiveLeg) -> float | None:
        """Return a stop behind frozen completed structure after a waypoint."""
        if len(self._completed_minutes) < self.config.continuation_trail_minutes:
            return None
        recent = list(self._completed_minutes)[-self.config.continuation_trail_minutes :]
        if active.direction > 0:
            return min(item[1] for item in recent) - self.config.continuation_trail_buffer_atr * active.atr
        return max(item[2] for item in recent) + self.config.continuation_trail_buffer_atr * active.atr

    def _structural_stop(self, active: ActiveLeg) -> float | None:
        if len(self._completed_minutes) < self.config.continuation_trail_minutes:
            return None
        recent = list(self._completed_minutes)[-self.config.continuation_trail_minutes :]
        if active.direction > 0:
            structural = min(item[1] for item in recent) - self.config.continuation_trail_buffer_atr * active.atr
            return max(active.lock_price, structural)
        structural = max(item[2] for item in recent) + self.config.continuation_trail_buffer_atr * active.atr
        return min(active.lock_price, structural)

    def _estimated_net_per_unit(self, active: ActiveLeg, exit_price: float, timestamp_ns: int) -> float:
        fee = self.config.taker_fee_bps / 10_000.0
        return (
            active.direction * (exit_price - active.entry_avg)
            - active.entry_avg * fee
            - exit_price * fee
            - active.settled_funding_cost_per_unit
        )

    def _submit_exit(self, reason: str, timestamp_ns: int) -> None:
        if self.active is None or self.exit_order_id is not None:
            return
        if self.entry_order_id is not None:
            order = self.cache.order(self.entry_order_id)
            if order is not None and not order.is_closed:
                try:
                    self.cancel_order(order)
                except Exception:
                    pass
        signed = self.portfolio.net_position(self.config.instrument_id)
        signed_value = float(signed)
        if signed_value == 0:
            return
        side = OrderSide.SELL if signed_value > 0 else OrderSide.BUY
        quantity = self.instrument.make_qty(Decimal(str(abs(signed_value))))
        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=quantity,
            reduce_only=True,
            tags=["NT_LVCFR_EXIT", reason, self.active.signal["scenario_id"]],
        )
        self.exit_order_id = order.client_order_id
        self.exit_reason = reason
        self.counters["exits_submitted"] += 1
        self.submit_order(order)
        self._emit(
            scenario_id=self.active.signal["scenario_id"],
            event_type="EXIT_SUBMITTED",
            event_time_ns=timestamp_ns,
            observed_time_ns=timestamp_ns,
            previous_state=f"{self.active.kind}_ACTIVE",
            next_state="EXIT_PENDING",
            reason_code=reason,
            reference_price=None,
            details={"quantity": str(quantity), "reduce_only": True},
        )

    def _handle_order_failure(self, client_order_id: Any, reason: str, timestamp_ns: int) -> None:
        if client_order_id == self.entry_order_id:
            if self.active is not None and self.active.entry_qty > 0:
                # Defensive only: a venue can cancel a market remainder after
                # partial execution. Retain and manage the native position rather
                # than orphaning already filled exposure.
                self.entry_order_id = None
                return
            self.counters["entries_rejected"] += 1
            scenario = self.active.signal["scenario_id"] if self.active else "UNKNOWN"
            self._emit(
                scenario_id=scenario,
                event_type="ENTRY_REJECTED",
                event_time_ns=timestamp_ns,
                observed_time_ns=timestamp_ns,
                previous_state="ENTRY_PENDING",
                next_state="INVALIDATED",
                reason_code="NAUTILUS_RISK_OR_EXECUTION_REJECTION",
                reference_price=None,
                details={"reason": reason},
            )
            self.active = None; self.entry_order_id = None
            self._finalize_episode(timestamp_ns)
        elif client_order_id == self.exit_order_id:
            self._emit(
                scenario_id=self.active.signal["scenario_id"] if self.active else "UNKNOWN",
                event_type="EXIT_REJECTED",
                event_time_ns=timestamp_ns,
                observed_time_ns=timestamp_ns,
                previous_state="EXIT_PENDING",
                next_state="EXIT_RETRY_REQUIRED",
                reason_code="NAUTILUS_EXECUTION_REJECTION",
                reference_price=None,
                details={"reason": reason},
            )
            self.exit_order_id = None

    def _update_minute(self, timestamp_ns: int, mid: float) -> None:
        minute = timestamp_ns // NS_PER_MINUTE
        if self._minute < 0:
            self._minute = minute; self._minute_low = mid; self._minute_high = mid; return
        if minute != self._minute:
            self._completed_minutes.append((self._minute, self._minute_low, self._minute_high))
            self._minute = minute; self._minute_low = mid; self._minute_high = mid
        else:
            self._minute_low = min(self._minute_low, mid)
            self._minute_high = max(self._minute_high, mid)

    def _capture_equity(self, timestamp_ns: int) -> None:
        minute = timestamp_ns // NS_PER_MINUTE
        if minute == self._last_equity_minute:
            return
        self._last_equity_minute = minute
        try:
            equity = self._equity()
        except Exception:
            return
        self.equity_curve.append({"timestamp_ns": timestamp_ns, "equity": equity})

    def _equity(self) -> float:
        return native_equity_amount(
            self.portfolio,
            self.instrument.venue,
            self.instrument.quote_currency,
        )

    def _finalize_episode(self, timestamp_ns: int) -> None:
        if self.current_episode is None:
            return
        equity_after = self._equity()
        before = float(self.current_episode["native_equity_before"])
        self.current_episode["end_time_ns"] = timestamp_ns
        self.current_episode["native_equity_after"] = equity_after
        self.current_episode["native_account_pnl"] = equity_after - before
        self.current_episode["return"] = equity_after / before - 1.0 if before > 0 else 0.0
        self.episodes.append(self.current_episode)
        self.current_episode = None

    def _emit(
        self,
        *,
        scenario_id: str,
        event_type: str,
        event_time_ns: int,
        observed_time_ns: int,
        previous_state: str,
        next_state: str,
        reason_code: str,
        reference_price: float | None,
        details: dict[str, Any],
    ) -> None:
        self.events.append(
            {
                "scenario_id": scenario_id,
                "instrument_id": str(self.config.instrument_id),
                "event_type": event_type,
                "event_time_ns": int(event_time_ns),
                "observed_time_ns": max(int(event_time_ns), int(observed_time_ns)),
                "previous_state": previous_state,
                "next_state": next_state,
                "reason_code": reason_code,
                "reference_price": None if reference_price is None else format(reference_price, ".12g"),
                "details": details,
            }
        )

    def _write_outputs(self) -> None:
        output = Path(self.config.output_dir)
        output.mkdir(parents=True, exist_ok=True)
        with (output / "scenario_events.jsonl").open("w", encoding="utf-8") as stream:
            for event in self.events:
                stream.write(json.dumps(event, sort_keys=True, default=str) + "\n")
        summary = {
            "candidate": "candidate-03-nt-lvcfr-v1",
            "engine": "NautilusTrader Strategy native execution/accounting",
            "counters": self.counters,
            "episodes": self.episodes,
            "legs": self.legs,
            "equity_curve": self.equity_curve,
            "last_funding_rate": self.latest_funding_rate,
            "last_funding_interval_minutes": self.latest_funding_interval_minutes,
            "next_funding_ns": self.next_funding_ns,
            "final_state": {
                "pending": self.pending is not None,
                "active": self.active is not None,
                "entry_order_id": None if self.entry_order_id is None else str(self.entry_order_id),
                "exit_order_id": None if self.exit_order_id is None else str(self.exit_order_id),
            },
        }
        (output / "strategy_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")

#!/usr/bin/env python3
"""NautilusTrader-only candidate-04 liquidity-transition strategy.

All market state is evaluated from completed external one-minute bars. Orders,
fills, fees, contingent-order handling, positions, account balances, margin and
NAV are owned by NautilusTrader. This module contains only causal state
detection, scenario selection and risk-budget sizing.
"""
from __future__ import annotations

import csv
import json
import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Currency
from nautilus_trader.trading.strategy import Strategy


def _as_float(value: Any) -> float:
    if value is None:
        return float("nan")
    method = getattr(value, "as_double", None)
    if callable(method):
        return float(method())
    text = str(value).split()[0].replace("_", "")
    return float(text)


def auction_efficiency(closes: list[float]) -> float:
    """Return net displacement divided by travelled path."""
    if len(closes) < 2:
        return 0.0
    path = sum(abs(right - left) for left, right in zip(closes, closes[1:]))
    if path <= 0.0:
        return 0.0
    return abs(closes[-1] - closes[0]) / path


def cost_aware_target(
    entry: float,
    side: int,
    planned_loss_per_unit: float,
    target_net_r: float,
    cost_rate: float,
) -> float:
    """Solve a target whose post-cost PnL equals target_net_r times planned loss."""
    entry_cost = entry * cost_rate
    if side > 0:
        return (entry + target_net_r * planned_loss_per_unit + entry_cost) / (1.0 - cost_rate)
    return (entry - target_net_r * planned_loss_per_unit - entry_cost) / (1.0 + cost_rate)


def net_r_at_price(
    entry: float,
    exit_price: float,
    side: int,
    planned_loss_per_unit: float,
    cost_rate: float,
) -> float:
    if planned_loss_per_unit <= 0.0:
        return -math.inf
    net = side * (exit_price - entry) - cost_rate * (entry + exit_price)
    return net / planned_loss_per_unit


def floor_quantity(raw_quantity: float, precision: int) -> float:
    if raw_quantity <= 0.0 or not math.isfinite(raw_quantity):
        return 0.0
    scale = 10**precision
    return math.floor(raw_quantity * scale) / scale


@dataclass(slots=True)
class SessionRange:
    key: int
    high: float
    low: float
    start_ns: int
    end_ns: int


@dataclass(slots=True)
class PendingSetup:
    scenario: str
    side: int
    created_index: int
    expires_index: int
    extreme: float
    structure: float
    atr: float
    target_reference: float | None
    details: dict[str, Any]


class LiquidityTransitionConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    output_dir: str
    evaluation_start_ns: int
    evaluation_end_ns: int

    starting_nav: float = 100_000.0
    risk_fraction: float = 0.03
    all_in_cost_bps_each_side: float = 7.5

    atr_period: int = 30
    volume_window: int = 120
    session_hours: tuple[int, ...] = (0, 8, 16)
    confirmation_bars: int = 4
    pre_sweep_structure_bars: int = 5
    cooldown_bars: int = 20
    max_hold_bars: int = 180

    session_sweep_min_atr: float = 0.05
    session_body_atr: float = 0.45
    session_volume_burst: float = 1.10
    session_close_location: float = 0.65
    session_max_efficiency_240: float = 0.60
    session_target_net_r: float = 1.80
    session_min_opposite_target_r: float = 1.20
    session_max_target_r: float = 2.40

    trend_lookback_bars: int = 240
    trend_pullback_bars: int = 30
    trend_internal_pool_bars: int = 20
    trend_min_displacement_atr: float = 3.0
    trend_min_efficiency_240: float = 0.22
    trend_min_pullback_atr: float = 0.30
    trend_sweep_min_atr: float = 0.04
    trend_body_atr: float = 0.50
    trend_volume_burst: float = 1.15
    trend_close_location: float = 0.68
    trend_target_net_r: float = 1.60

    stop_buffer_atr: float = 0.10
    funding_flatten_minute: int = 50
    funding_blackout_before_minutes: int = 25
    funding_blackout_after_minutes: int = 5


class LiquidityTransitionStrategy(Strategy):
    """Two complete causal scenarios sharing one global position constraint."""

    def __init__(self, config: LiquidityTransitionConfig) -> None:
        super().__init__(config=config)
        self.instrument = None
        self.bars: deque[dict[str, float | int]] = deque(maxlen=3000)
        self.bar_index = -1

        self.current_session_key: int | None = None
        self.current_session_high = -math.inf
        self.current_session_low = math.inf
        self.current_session_start_ns = 0
        self.previous_session: SessionRange | None = None

        self.pending: PendingSetup | None = None
        self.entry_pending = False
        self.entry_pending_index = -1
        self.position_open_index = -1
        self.current_scenario: str | None = None
        self.last_entry_by_scenario: dict[str, int] = {}

        self.events: list[dict[str, Any]] = []
        self.equity: list[dict[str, Any]] = []
        self.closed_scenarios: list[dict[str, Any]] = []

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            raise RuntimeError(f"instrument not found: {self.config.instrument_id}")
        Path(self.config.output_dir).mkdir(parents=True, exist_ok=True)
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        self.bar_index += 1
        row = {
            "ts": int(bar.ts_event),
            "open": _as_float(bar.open),
            "high": _as_float(bar.high),
            "low": _as_float(bar.low),
            "close": _as_float(bar.close),
            "volume": _as_float(bar.volume),
        }
        self.bars.append(row)
        self._record_equity(int(bar.ts_event))
        self._roll_session(row)

        if not self.portfolio.is_flat(self.config.instrument_id):
            self._manage_open_position(row)
            return

        if self.entry_pending:
            if self.bar_index - self.entry_pending_index > 2:
                self.cancel_all_orders(self.config.instrument_id)
                self.entry_pending = False
                self.current_scenario = None
            return

        if not self._in_evaluation(int(bar.ts_event)):
            self.pending = None
            return

        if self._funding_blackout(int(bar.ts_event)):
            self.pending = None
            return

        if len(self.bars) < max(
            self.config.volume_window + 2,
            self.config.trend_lookback_bars + 2,
            self.config.atr_period + 2,
        ):
            return

        if self.pending is not None:
            if self._try_confirm_pending(row):
                return
            if self.bar_index > self.pending.expires_index:
                self._event("SETUP_EXPIRED", self.pending.scenario, row, self.pending.details)
                self.pending = None

        if self.pending is None:
            if self._detect_session_sweep(row):
                return
            self._detect_trend_sweep(row)

    def on_position_opened(self, event: Any) -> None:
        self.entry_pending = False
        self.position_open_index = self.bar_index
        self._event(
            "POSITION_OPENED",
            self.current_scenario or "UNKNOWN",
            self.bars[-1],
            {"event": str(event)},
        )

    def on_position_closed(self, event: Any) -> None:
        scenario = self.current_scenario or "UNKNOWN"
        realized = getattr(event, "realized_pnl", None)
        self.closed_scenarios.append(
            {
                "scenario": scenario,
                "ts_event": int(getattr(event, "ts_event", self.bars[-1]["ts"])),
                "realized_pnl": str(realized) if realized is not None else None,
                "event": str(event),
            }
        )
        self._event("POSITION_CLOSED", scenario, self.bars[-1], {"event": str(event)})
        self.current_scenario = None
        self.position_open_index = -1
        self.pending = None

    def on_order_rejected(self, event: Any) -> None:
        if self.portfolio.is_flat(self.config.instrument_id):
            self.entry_pending = False
            self.current_scenario = None
        self._event("ORDER_REJECTED", "EXECUTION", self.bars[-1], {"event": str(event)})

    def on_stop(self) -> None:
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)
        self._record_equity(int(self.bars[-1]["ts"]) if self.bars else 0)
        destination = Path(self.config.output_dir)
        (destination / "strategy_events.json").write_text(
            json.dumps(self.events, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (destination / "closed_scenarios.json").write_text(
            json.dumps(self.closed_scenarios, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if self.equity:
            with (destination / "equity.csv").open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=["ts_event", "equity"])
                writer.writeheader()
                writer.writerows(self.equity)

    def _in_evaluation(self, ts_event: int) -> bool:
        return self.config.evaluation_start_ns <= ts_event <= self.config.evaluation_end_ns

    def _event(
        self,
        event_type: str,
        scenario: str,
        row: dict[str, float | int],
        details: dict[str, Any],
    ) -> None:
        self.events.append(
            {
                "event_type": event_type,
                "scenario": scenario,
                "ts_event": int(row["ts"]),
                "reference_price": float(row["close"]),
                "details": details,
            }
        )

    def _equity_value(self) -> float:
        try:
            values = self.portfolio.equity(self.config.instrument_id.venue)
            for currency, money in values.items():
                if str(currency) == "USDT":
                    return _as_float(money)
        except Exception:
            pass

        try:
            account = self.portfolio.account(self.config.instrument_id.venue)
            usdt = Currency.from_str("USDT")
            total = account.balance_total(usdt)
            unrealized = self.portfolio.unrealized_pnl(self.config.instrument_id)
            return _as_float(total) + (0.0 if unrealized is None else _as_float(unrealized))
        except Exception:
            return self.equity[-1]["equity"] if self.equity else self.config.starting_nav

    def _record_equity(self, ts_event: int) -> None:
        if ts_event <= 0:
            return
        value = self._equity_value()
        if not math.isfinite(value) or value <= 0.0:
            return
        if self.equity and self.equity[-1]["ts_event"] == ts_event:
            self.equity[-1]["equity"] = value
        else:
            self.equity.append({"ts_event": ts_event, "equity": value})

    def _session_key(self, ts_event: int) -> int:
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        boundary = max(hour for hour in self.config.session_hours if hour <= moment.hour)
        return int(moment.strftime("%Y%m%d")) * 100 + boundary

    def _roll_session(self, row: dict[str, float | int]) -> None:
        key = self._session_key(int(row["ts"]))
        if self.current_session_key is None:
            self.current_session_key = key
            self.current_session_start_ns = int(row["ts"])
        elif key != self.current_session_key:
            self.previous_session = SessionRange(
                key=self.current_session_key,
                high=self.current_session_high,
                low=self.current_session_low,
                start_ns=self.current_session_start_ns,
                end_ns=int(row["ts"]),
            )
            self.current_session_key = key
            self.current_session_high = -math.inf
            self.current_session_low = math.inf
            self.current_session_start_ns = int(row["ts"])
            self.pending = None

        self.current_session_high = max(self.current_session_high, float(row["high"]))
        self.current_session_low = min(self.current_session_low, float(row["low"]))

    def _atr(self) -> float:
        rows = list(self.bars)
        if len(rows) < self.config.atr_period + 1:
            return float("nan")
        selected = rows[-(self.config.atr_period + 1) :]
        values: list[float] = []
        for previous, current in zip(selected, selected[1:]):
            values.append(
                max(
                    float(current["high"]) - float(current["low"]),
                    abs(float(current["high"]) - float(previous["close"])),
                    abs(float(current["low"]) - float(previous["close"])),
                )
            )
        return sum(values[-self.config.atr_period :]) / self.config.atr_period

    def _volume_burst(self) -> float:
        rows = list(self.bars)
        history = sorted(
            float(row["volume"])
            for row in rows[-(self.config.volume_window + 1) : -1]
            if float(row["volume"]) >= 0.0
        )
        if not history:
            return 0.0
        middle = len(history) // 2
        median = (
            history[middle]
            if len(history) % 2
            else (history[middle - 1] + history[middle]) / 2.0
        )
        return float(rows[-1]["volume"]) / median if median > 0.0 else 0.0

    def _efficiency(self, bars: int) -> float:
        rows = list(self.bars)
        closes = [float(row["close"]) for row in rows[-(bars + 1) :]]
        return auction_efficiency(closes)

    @staticmethod
    def _close_location(row: dict[str, float | int], side: int) -> float:
        span = max(float(row["high"]) - float(row["low"]), 1e-12)
        if side > 0:
            return (float(row["close"]) - float(row["low"])) / span
        return (float(row["high"]) - float(row["close"])) / span

    def _funding_blackout(self, ts_event: int) -> bool:
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        minute_of_day = moment.hour * 60 + moment.minute
        funding = (0, 8 * 60, 16 * 60, 24 * 60)
        distances = [point - minute_of_day for point in funding if point >= minute_of_day]
        to_next = min(distances) if distances else 24 * 60 - minute_of_day
        since_last = min(
            (minute_of_day - point for point in funding if point <= minute_of_day),
            default=minute_of_day,
        )
        return (
            to_next <= self.config.funding_blackout_before_minutes
            or since_last <= self.config.funding_blackout_after_minutes
        )

    def _manage_open_position(self, row: dict[str, float | int]) -> None:
        moment = datetime.fromtimestamp(int(row["ts"]) / 1_000_000_000, tz=timezone.utc)
        before_funding = moment.hour in (7, 15, 23) and moment.minute >= self.config.funding_flatten_minute
        timed_out = (
            self.position_open_index >= 0
            and self.bar_index - self.position_open_index >= self.config.max_hold_bars
        )
        if before_funding or timed_out or int(row["ts"]) >= self.config.evaluation_end_ns:
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)
            self._event(
                "FORCED_DAYTRADE_EXIT",
                self.current_scenario or "UNKNOWN",
                row,
                {"before_funding": before_funding, "timed_out": timed_out},
            )

    def _detect_session_sweep(self, row: dict[str, float | int]) -> bool:
        session = self.previous_session
        if session is None:
            return False
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return False

        high_penetration = (float(row["high"]) - session.high) / atr
        low_penetration = (session.low - float(row["low"])) / atr
        high_rejected = (
            high_penetration >= self.config.session_sweep_min_atr
            and float(row["close"]) < session.high
        )
        low_rejected = (
            low_penetration >= self.config.session_sweep_min_atr
            and float(row["close"]) > session.low
        )
        if high_rejected == low_rejected:
            return False
        if self._efficiency(self.config.trend_lookback_bars) > self.config.session_max_efficiency_240:
            return False

        side = -1 if high_rejected else 1
        rows = list(self.bars)
        pre = rows[-(self.config.pre_sweep_structure_bars + 1) : -1]
        structure = (
            min(float(item["low"]) for item in pre)
            if side < 0
            else max(float(item["high"]) for item in pre)
        )
        extreme = float(row["high"]) if side < 0 else float(row["low"])
        opposite = session.low if side < 0 else session.high
        self.pending = PendingSetup(
            scenario="SESSION_RANGE_FAILED_AUCTION",
            side=side,
            created_index=self.bar_index,
            expires_index=self.bar_index + self.config.confirmation_bars,
            extreme=extreme,
            structure=structure,
            atr=atr,
            target_reference=opposite,
            details={
                "session_key": session.key,
                "session_high": session.high,
                "session_low": session.low,
                "penetration_atr": high_penetration if high_rejected else low_penetration,
                "efficiency_240": self._efficiency(self.config.trend_lookback_bars),
            },
        )
        self._event("SWEEP_DETECTED", self.pending.scenario, row, self.pending.details)
        return True

    def _detect_trend_sweep(self, row: dict[str, float | int]) -> bool:
        rows = list(self.bars)
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return False
        close_now = float(row["close"])
        close_then = float(rows[-self.config.trend_lookback_bars - 1]["close"])
        raw_trend = (close_now - close_then) / atr
        if abs(raw_trend) < self.config.trend_min_displacement_atr:
            return False
        side = 1 if raw_trend > 0.0 else -1
        if self._efficiency(self.config.trend_lookback_bars) < self.config.trend_min_efficiency_240:
            return False

        pullback_start = float(rows[-self.config.trend_pullback_bars - 1]["close"])
        previous_close = float(rows[-2]["close"])
        pullback = side * (previous_close - pullback_start) / atr
        if pullback > -self.config.trend_min_pullback_atr:
            return False

        pool_rows = rows[-(self.config.trend_internal_pool_bars + 2) : -2]
        if side > 0:
            level = min(float(item["low"]) for item in pool_rows)
            penetration = (level - float(row["low"])) / atr
            rejected = (
                penetration >= self.config.trend_sweep_min_atr
                and float(row["close"]) > level
            )
            extreme = float(row["low"])
        else:
            level = max(float(item["high"]) for item in pool_rows)
            penetration = (float(row["high"]) - level) / atr
            rejected = (
                penetration >= self.config.trend_sweep_min_atr
                and float(row["close"]) < level
            )
            extreme = float(row["high"])
        if not rejected:
            return False

        pre = rows[-(self.config.pre_sweep_structure_bars + 1) : -1]
        structure = (
            max(float(item["high"]) for item in pre)
            if side > 0
            else min(float(item["low"]) for item in pre)
        )
        self.pending = PendingSetup(
            scenario="TREND_INTERNAL_LIQUIDITY_RESUMPTION",
            side=side,
            created_index=self.bar_index,
            expires_index=self.bar_index + self.config.confirmation_bars,
            extreme=extreme,
            structure=structure,
            atr=atr,
            target_reference=None,
            details={
                "trend_atr": raw_trend,
                "pullback_atr": pullback,
                "pool_level": level,
                "penetration_atr": penetration,
                "efficiency_240": self._efficiency(self.config.trend_lookback_bars),
            },
        )
        self._event("SWEEP_DETECTED", self.pending.scenario, row, self.pending.details)
        return True

    def _try_confirm_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        if setup is None or self.bar_index <= setup.created_index:
            return False

        side = setup.side
        broken = (
            float(row["close"]) > setup.structure
            if side > 0
            else float(row["close"]) < setup.structure
        )
        if not broken:
            return False

        atr = self._atr()
        body = side * (float(row["close"]) - float(row["open"])) / atr
        volume_burst = self._volume_burst()
        close_location = self._close_location(row, side)
        if setup.scenario == "SESSION_RANGE_FAILED_AUCTION":
            passed = (
                body >= self.config.session_body_atr
                and volume_burst >= self.config.session_volume_burst
                and close_location >= self.config.session_close_location
            )
            target_r = self.config.session_target_net_r
        else:
            passed = (
                body >= self.config.trend_body_atr
                and volume_burst >= self.config.trend_volume_burst
                and close_location >= self.config.trend_close_location
            )
            target_r = self.config.trend_target_net_r

        details = {
            **setup.details,
            "confirmation_body_atr": body,
            "confirmation_volume_burst": volume_burst,
            "confirmation_close_location": close_location,
            "structure": setup.structure,
        }
        if not passed:
            self._event("WEAK_FIRST_BREAK", setup.scenario, row, details)
            self.pending = None
            return False

        last_entry = self.last_entry_by_scenario.get(setup.scenario, -10**12)
        if self.bar_index - last_entry < self.config.cooldown_bars:
            self.pending = None
            return False

        submitted = self._submit_bracket(setup, row, target_r, details)
        self.pending = None
        return submitted

    def _submit_bracket(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
        target_net_r: float,
        details: dict[str, Any],
    ) -> bool:
        side = setup.side
        atr = self._atr()
        stop = setup.extreme - side * self.config.stop_buffer_atr * atr
        entry = float(row["close"])
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        stop_fill = stop
        price_loss = side * (entry - stop_fill)
        if price_loss <= 0.0:
            return False
        planned_loss = price_loss + cost_rate * (entry + stop_fill)
        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        raw_qty = risk_budget / planned_loss
        quantity_value = floor_quantity(raw_qty, int(self.instrument.size_precision))
        if quantity_value <= 0.0 or quantity_value * entry < 10.0:
            return False

        target = cost_aware_target(entry, side, planned_loss, target_net_r, cost_rate)
        if setup.target_reference is not None:
            reference_r = net_r_at_price(
                entry,
                setup.target_reference,
                side,
                planned_loss,
                cost_rate,
            )
            if reference_r < self.config.session_min_opposite_target_r:
                self._event(
                    "INSUFFICIENT_OPPOSING_LIQUIDITY",
                    setup.scenario,
                    row,
                    {**details, "reference_net_r": reference_r},
                )
                return False
            cap_target = cost_aware_target(
                entry,
                side,
                planned_loss,
                self.config.session_max_target_r,
                cost_rate,
            )
            if side > 0:
                target = min(setup.target_reference, cap_target)
            else:
                target = max(setup.target_reference, cap_target)

        order_side = OrderSide.BUY if side > 0 else OrderSide.SELL
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=order_side,
            quantity=self.instrument.make_qty(quantity_value),
            time_in_force=TimeInForce.GTC,
            tp_price=self.instrument.make_price(target),
            sl_trigger_price=self.instrument.make_price(stop),
        )
        self.submit_order_list(order_list)
        self.entry_pending = True
        self.entry_pending_index = self.bar_index
        self.current_scenario = setup.scenario
        self.last_entry_by_scenario[setup.scenario] = self.bar_index
        self._event(
            "ENTRY_SUBMITTED",
            setup.scenario,
            row,
            {
                **details,
                "estimated_entry": entry,
                "stop": stop,
                "target": target,
                "quantity": quantity_value,
                "equity": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit": planned_loss,
            },
        )
        return True

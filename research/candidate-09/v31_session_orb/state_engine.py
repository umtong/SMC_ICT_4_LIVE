"""Candidate 09 v31: institution-window opening-range delivery.

Two exogenous activity windows are used: the 08:00 UTC crypto-derivatives settlement
window and the 09:30 America/New_York cash-equity opening. A completed 15-minute range
becomes tradeable only when its volume is in the upper quartile of prior same-window
ranges. A completed close outside the range plus aligned taker flow starts delivery.
Invalidation is the range equilibrium and the target is a one-range measured objective.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any, Mapping
from zoneinfo import ZoneInfo

MINUTE_NS = 60_000_000_000
UTC = timezone.utc
NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class FlowBar:
    ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float
    trade_count: int

    @property
    def imbalance(self) -> float:
        if self.volume <= 0.0:
            return 0.0
        return (2.0 * self.taker_buy_volume - self.volume) / self.volume


@dataclass(frozen=True, slots=True)
class DiagnosticEvent:
    scenario_id: str
    event_type: str
    event_time_ns: int
    observed_time_ns: int
    previous_state: str
    next_state: str
    reason_code: str
    reference_price: float | None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Signal:
    scenario_id: str
    branch: str
    side: str
    observed_time_ns: int
    entry_reference: float
    stop_price: float
    target_price: float
    net_reward_to_risk: float
    reason_code: str


@dataclass(frozen=True, slots=True)
class EngineResult:
    signal: Signal | None
    events: tuple[DiagnosticEvent, ...]


@dataclass(frozen=True, slots=True)
class RiskSizing:
    quantity: Decimal
    planned_loss: Decimal
    loss_per_unit: Decimal


def risk_based_quantity(
    *,
    nav: Decimal,
    risk_fraction: Decimal,
    entry_price: Decimal,
    stop_price: Decimal,
    cost_rate_per_fill: Decimal,
    quantity_increment: Decimal,
) -> RiskSizing:
    budget = nav * risk_fraction
    loss_per_unit = abs(entry_price - stop_price) + (
        entry_price + stop_price
    ) * cost_rate_per_fill
    if nav <= 0 or risk_fraction <= 0 or quantity_increment <= 0 or loss_per_unit <= 0:
        raise ValueError("invalid risk inputs")
    quantity = (
        budget / loss_per_unit / quantity_increment
    ).to_integral_value(rounding=ROUND_DOWN) * quantity_increment
    if quantity <= 0:
        raise ValueError("risk budget below minimum quantity")
    return RiskSizing(quantity, quantity * loss_per_unit, loss_per_unit)


@dataclass(frozen=True, slots=True)
class EngineConfig:
    opening_minutes: int
    breakout_window_minutes: int
    volume_history: int
    minimum_volume_history: int
    volume_quantile: float
    session_shift_minutes: int
    minimum_net_reward_to_risk: float
    cost_per_fill: float
    cooldown_bars: int
    use_relative_volume: bool = True
    use_flow: bool = True

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
        *,
        ablation: str = "baseline",
    ) -> "EngineConfig":
        allowed = {"baseline", "no-relative-volume", "no-flow", "off-session"}
        if ablation not in allowed:
            raise ValueError(f"unknown ablation: {ablation}")
        logic = payload["logic"]
        return cls(
            opening_minutes=int(logic["opening_minutes"]),
            breakout_window_minutes=int(logic["breakout_window_minutes"]),
            volume_history=int(logic["volume_history"]),
            minimum_volume_history=int(logic["minimum_volume_history"]),
            volume_quantile=float(logic["volume_quantile"]),
            session_shift_minutes=(
                int(logic["off_session_shift_minutes"])
                if ablation == "off-session"
                else 0
            ),
            minimum_net_reward_to_risk=float(
                payload["trade"]["minimum_net_reward_to_risk"]
            ),
            cost_per_fill=float(payload["risk"]["composite_taker_cost_per_fill"]),
            cooldown_bars=int(payload["trade"]["cooldown_bars"]),
            use_relative_volume=ablation != "no-relative-volume",
            use_flow=ablation != "no-flow",
        )


@dataclass(slots=True)
class OpeningRange:
    scenario_id: str
    session_type: str
    anchor_ns: int
    high: float
    low: float
    volume: float
    bars: int = 1
    age_after_freeze: int = 0
    frozen: bool = False
    volume_ok: bool = False
    volume_threshold: float | None = None


class LiquidityStateEngine:
    def __init__(self, config: EngineConfig):
        self.config = config
        self.active: OpeningRange | None = None
        self.history = {
            "SETTLEMENT": deque(maxlen=config.volume_history),
            "NYSE_OPEN": deque(maxlen=config.volume_history),
        }
        self.counter = 0
        self.cooldown = 0

    @staticmethod
    def _quantile(values: deque[float], q: float) -> float:
        ordered = sorted(values)
        index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * q)))
        return ordered[index]

    def _anchor(self, bar: FlowBar) -> tuple[str, int] | None:
        source_dt = datetime.fromtimestamp(
            (bar.ts_ns - MINUTE_NS) / 1_000_000_000,
            tz=UTC,
        )
        shift = timedelta(minutes=self.config.session_shift_minutes)
        settlement = source_dt.replace(hour=8, minute=0, second=0, microsecond=0) + shift
        if source_dt == settlement:
            return "SETTLEMENT", int(settlement.timestamp() * 1_000_000_000)
        local = source_dt.astimezone(NEW_YORK)
        ny_anchor_local = local.replace(hour=9, minute=30, second=0, microsecond=0) + shift
        if local == ny_anchor_local:
            return "NYSE_OPEN", int(source_dt.timestamp() * 1_000_000_000)
        return None

    def _event(
        self,
        sid: str,
        bar: FlowBar,
        event_type: str,
        previous_state: str,
        next_state: str,
        reason: str,
        reference: float | None,
        details: Mapping[str, Any] | None = None,
    ) -> DiagnosticEvent:
        return DiagnosticEvent(
            sid,
            event_type,
            bar.ts_ns,
            bar.ts_ns,
            previous_state,
            next_state,
            reason,
            reference,
            details or {},
        )

    def _build_signal(
        self,
        active: OpeningRange,
        bar: FlowBar,
        direction: int,
    ) -> Signal | None:
        width = active.high - active.low
        midpoint = (active.high + active.low) / 2.0
        entry = bar.close
        if width <= 0:
            return None
        if direction > 0:
            side, stop, target = "BUY", midpoint, active.high + width
        else:
            side, stop, target = "SELL", midpoint, active.low - width
        risk = abs(entry - stop) + (entry + stop) * self.config.cost_per_fill
        reward = abs(target - entry) - (entry + target) * self.config.cost_per_fill
        if risk <= 0 or reward <= 0:
            return None
        ratio = reward / risk
        if ratio < self.config.minimum_net_reward_to_risk:
            return None
        if side == "BUY" and not (stop < entry < target):
            return None
        if side == "SELL" and not (target < entry < stop):
            return None
        return Signal(
            active.scenario_id,
            "SESSION_OPENING_RANGE_DELIVERY",
            side,
            bar.ts_ns,
            entry,
            stop,
            target,
            ratio,
            "INSTITUTION_WINDOW_RANGE_DELIVERY",
        )

    def on_bar(self, bar: FlowBar) -> EngineResult:
        events: list[DiagnosticEvent] = []
        if self.cooldown > 0:
            self.cooldown -= 1
        anchor = self._anchor(bar)
        if anchor is not None:
            session_type, anchor_ns = anchor
            self.counter += 1
            sid = f"ORB-{session_type}-{anchor_ns}-{self.counter}"
            self.active = OpeningRange(
                sid,
                session_type,
                anchor_ns,
                bar.high,
                bar.low,
                bar.volume,
            )
            events.append(
                self._event(
                    sid,
                    bar,
                    "OPENING_RANGE_STARTED",
                    "IDLE",
                    "FORMING_RANGE",
                    "EXOGENOUS_INSTITUTION_WINDOW_OPEN",
                    bar.close,
                    {"session_type": session_type},
                )
            )
            return EngineResult(None, tuple(events))

        active = self.active
        if active is None:
            return EngineResult(None, tuple(events))

        if not active.frozen:
            active.high = max(active.high, bar.high)
            active.low = min(active.low, bar.low)
            active.volume += bar.volume
            active.bars += 1
            if active.bars >= self.config.opening_minutes:
                history = self.history[active.session_type]
                threshold = (
                    self._quantile(history, self.config.volume_quantile)
                    if len(history) >= self.config.minimum_volume_history
                    else None
                )
                active.volume_threshold = threshold
                active.volume_ok = (
                    not self.config.use_relative_volume
                    or threshold is not None and active.volume >= threshold
                )
                history.append(active.volume)
                active.frozen = True
                events.append(
                    self._event(
                        active.scenario_id,
                        bar,
                        "OPENING_RANGE_FROZEN",
                        "FORMING_RANGE",
                        "AWAITING_BREAKOUT" if active.volume_ok else "NO_TRADE",
                        (
                            "SAME_WINDOW_RELATIVE_VOLUME_CONFIRMED"
                            if active.volume_ok
                            else "SAME_WINDOW_RELATIVE_VOLUME_NOT_CONFIRMED"
                        ),
                        (active.high + active.low) / 2.0,
                        {
                            "session_type": active.session_type,
                            "range_high": active.high,
                            "range_low": active.low,
                            "range_volume": active.volume,
                            "volume_threshold": threshold,
                        },
                    )
                )
                if not active.volume_ok:
                    self.active = None
            return EngineResult(None, tuple(events))

        active.age_after_freeze += 1
        direction = 0
        if bar.close > active.high:
            direction = 1
        elif bar.close < active.low:
            direction = -1
        flow_ok = (
            direction != 0
            and (
                not self.config.use_flow
                or bar.imbalance * direction > 0.0
            )
        )
        if direction and flow_ok and self.cooldown == 0:
            signal = self._build_signal(active, bar, direction)
            events.append(
                self._event(
                    active.scenario_id,
                    bar,
                    "OPENING_RANGE_BROKEN",
                    "AWAITING_BREAKOUT",
                    "ENTERABLE" if signal is not None else "NO_TRADE",
                    (
                        "INSTITUTION_WINDOW_RANGE_DELIVERY"
                        if signal is not None
                        else "BREAKOUT_FAILED_COSTED_GEOMETRY"
                    ),
                    active.high if direction > 0 else active.low,
                    {
                        "session_type": active.session_type,
                        "direction": direction,
                        "order_imbalance": bar.imbalance,
                        "flow_ok": flow_ok,
                        "net_reward_to_risk": (
                            signal.net_reward_to_risk if signal is not None else None
                        ),
                    },
                )
            )
            self.active = None
            if signal is not None:
                self.cooldown = self.config.cooldown_bars
            return EngineResult(signal, tuple(events))

        if active.age_after_freeze >= self.config.breakout_window_minutes:
            events.append(
                self._event(
                    active.scenario_id,
                    bar,
                    "OPENING_RANGE_EXPIRED",
                    "AWAITING_BREAKOUT",
                    "NO_TRADE",
                    "NO_DELIVERY_WITHIN_FROZEN_WINDOW",
                    (active.high + active.low) / 2.0,
                    {"session_type": active.session_type},
                )
            )
            self.active = None
        return EngineResult(None, tuple(events))

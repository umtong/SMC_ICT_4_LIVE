"""Candidate 09 v30: quarter-hour algorithmic-flow delivery.

The first completed minute of each quarter-hour is treated as a scheduled algorithmic
flow event, not a generic candle pattern. A trade requires: (1) persistent direction in
prior quarter-hour opening returns, (2) current opening return in that direction, and
(3) current taker imbalance in the same direction. Invalidation is the current opening
impulse extreme; the target is the nearest still-unconsumed completed four-hour auction
extreme in the delivery direction. All inputs are completed-bar observations.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN
from typing import Any, Mapping

MINUTE_NS = 60_000_000_000


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
    if nav <= 0 or risk_fraction <= 0 or quantity_increment <= 0:
        raise ValueError("invalid risk inputs")
    budget = nav * risk_fraction
    loss_per_unit = abs(entry_price - stop_price) + (
        entry_price + stop_price
    ) * cost_rate_per_fill
    if loss_per_unit <= 0:
        raise ValueError("non-positive loss per unit")
    quantity = (
        budget / loss_per_unit / quantity_increment
    ).to_integral_value(rounding=ROUND_DOWN) * quantity_increment
    if quantity <= 0:
        raise ValueError("risk budget below minimum quantity")
    return RiskSizing(quantity, quantity * loss_per_unit, loss_per_unit)


@dataclass(frozen=True, slots=True)
class EngineConfig:
    atr_period: int
    four_hour_minutes: int
    lag_openings: int
    minimum_lag_agreement: int
    phase_offset_minutes: int
    stop_buffer_atr: float
    minimum_net_reward_to_risk: float
    cost_per_fill: float
    cooldown_bars: int
    use_imbalance: bool = True
    use_boundary_lag: bool = True

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
        *,
        ablation: str = "baseline",
    ) -> "EngineConfig":
        allowed = {
            "baseline",
            "shifted-phase",
            "no-imbalance",
            "no-boundary-lag",
        }
        if ablation not in allowed:
            raise ValueError(f"unknown ablation: {ablation}")
        logic = payload["logic"]
        return cls(
            atr_period=int(logic["atr_period"]),
            four_hour_minutes=int(logic["four_hour_minutes"]),
            lag_openings=int(logic["lag_openings"]),
            minimum_lag_agreement=int(logic["minimum_lag_agreement"]),
            phase_offset_minutes=(
                int(logic["placebo_phase_offset_minutes"])
                if ablation == "shifted-phase"
                else 0
            ),
            stop_buffer_atr=float(logic["stop_buffer_atr"]),
            minimum_net_reward_to_risk=float(
                payload["trade"]["minimum_net_reward_to_risk"]
            ),
            cost_per_fill=float(payload["risk"]["composite_taker_cost_per_fill"]),
            cooldown_bars=int(payload["trade"]["cooldown_bars"]),
            use_imbalance=ablation != "no-imbalance",
            use_boundary_lag=ablation != "no-boundary-lag",
        )


@dataclass(slots=True)
class Auction:
    bucket: int
    high: float
    low: float


class LiquidityStateEngine:
    def __init__(self, config: EngineConfig):
        self.config = config
        self.previous: FlowBar | None = None
        self.true_ranges: deque[float] = deque(maxlen=config.atr_period)
        self.opening_signs: deque[int] = deque(maxlen=config.lag_openings)
        self.current_auction: Auction | None = None
        self.completed_auctions: deque[Auction] = deque(maxlen=96)
        self.cooldown = 0
        self.counter = 0

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

    def _atr(self) -> float:
        return sum(self.true_ranges) / len(self.true_ranges) if self.true_ranges else 0.0

    def _update_observations(self, bar: FlowBar) -> None:
        if self.previous is not None:
            self.true_ranges.append(
                max(
                    bar.high - bar.low,
                    abs(bar.high - self.previous.close),
                    abs(bar.low - self.previous.close),
                )
            )
        source_minute = bar.ts_ns // MINUTE_NS - 1
        bucket = source_minute // self.config.four_hour_minutes
        if self.current_auction is None:
            self.current_auction = Auction(bucket, bar.high, bar.low)
        elif bucket != self.current_auction.bucket:
            self.completed_auctions.append(self.current_auction)
            self.current_auction = Auction(bucket, bar.high, bar.low)
        else:
            self.current_auction.high = max(self.current_auction.high, bar.high)
            self.current_auction.low = min(self.current_auction.low, bar.low)

    def _phase_open(self, bar: FlowBar) -> bool:
        source_minute = bar.ts_ns // MINUTE_NS - 1
        return source_minute % 15 == self.config.phase_offset_minutes

    def _target(self, direction: int, entry: float) -> float | None:
        if direction > 0:
            candidates = [a.high for a in self.completed_auctions if a.high > entry]
            return min(candidates) if candidates else None
        candidates = [a.low for a in self.completed_auctions if a.low < entry]
        return max(candidates) if candidates else None

    def _build_signal(
        self,
        sid: str,
        bar: FlowBar,
        direction: int,
        atr: float,
        target: float,
    ) -> Signal | None:
        entry = bar.close
        if direction > 0:
            side = "BUY"
            stop = bar.low - self.config.stop_buffer_atr * atr
        else:
            side = "SELL"
            stop = bar.high + self.config.stop_buffer_atr * atr
        risk = abs(entry - stop) + (entry + stop) * self.config.cost_per_fill
        reward = abs(target - entry) - (entry + target) * self.config.cost_per_fill
        if risk <= 0.0 or reward <= 0.0:
            return None
        ratio = reward / risk
        if ratio < self.config.minimum_net_reward_to_risk:
            return None
        if side == "BUY" and not (stop < entry < target):
            return None
        if side == "SELL" and not (target < entry < stop):
            return None
        return Signal(
            sid,
            "QUARTER_HOUR_DELIVERY",
            side,
            bar.ts_ns,
            entry,
            stop,
            target,
            ratio,
            "PERIODIC_ALGORITHMIC_FLOW_DELIVERY",
        )

    def on_bar(self, bar: FlowBar) -> EngineResult:
        events: list[DiagnosticEvent] = []
        self._update_observations(bar)
        if self.cooldown > 0:
            self.cooldown -= 1

        if not self._phase_open(bar):
            self.previous = bar
            return EngineResult(None, tuple(events))

        opening_return = bar.close - bar.open
        current_sign = 1 if opening_return > 0 else -1 if opening_return < 0 else 0
        lag_snapshot = tuple(self.opening_signs)
        self.opening_signs.append(current_sign)
        self.counter += 1
        sid = f"QH-{bar.ts_ns}-{self.counter}"
        atr = self._atr()
        if (
            current_sign == 0
            or atr <= 0.0
            or len(self.true_ranges) < self.config.atr_period
            or not self.completed_auctions
        ):
            events.append(
                self._event(
                    sid,
                    bar,
                    "QUARTER_OPENING_OBSERVED",
                    "IDLE",
                    "NO_TRADE",
                    "INSUFFICIENT_CAUSAL_STATE",
                    bar.close,
                    {"opening_sign": current_sign},
                )
            )
            self.previous = bar
            return EngineResult(None, tuple(events))

        lag_agreement = sum(1 for sign in lag_snapshot if sign == current_sign)
        lag_ok = (
            not self.config.use_boundary_lag
            or len(lag_snapshot) == self.config.lag_openings
            and lag_agreement >= self.config.minimum_lag_agreement
        )
        imbalance_ok = (
            not self.config.use_imbalance
            or bar.imbalance * current_sign > 0.0
        )
        target = self._target(current_sign, bar.close)
        signal = None
        reason = "PERIODIC_STATE_NOT_CONFIRMED"
        if self.cooldown == 0 and lag_ok and imbalance_ok and target is not None:
            signal = self._build_signal(sid, bar, current_sign, atr, target)
            reason = (
                "PERIODIC_ALGORITHMIC_FLOW_DELIVERY"
                if signal is not None
                else "NATURAL_OBJECTIVE_FAILED_COSTED_GEOMETRY"
            )
            if signal is not None:
                self.cooldown = self.config.cooldown_bars
        events.append(
            self._event(
                sid,
                bar,
                "QUARTER_OPENING_OBSERVED",
                "IDLE",
                "ENTERABLE" if signal is not None else "NO_TRADE",
                reason,
                bar.close,
                {
                    "phase_offset_minutes": self.config.phase_offset_minutes,
                    "opening_sign": current_sign,
                    "opening_return_atr": opening_return / atr,
                    "order_imbalance": bar.imbalance,
                    "lag_signs": lag_snapshot,
                    "lag_agreement": lag_agreement,
                    "lag_ok": lag_ok,
                    "imbalance_ok": imbalance_ok,
                    "target": target,
                    "net_reward_to_risk": (
                        signal.net_reward_to_risk if signal is not None else None
                    ),
                },
            )
        )
        self.previous = bar
        return EngineResult(signal, tuple(events))

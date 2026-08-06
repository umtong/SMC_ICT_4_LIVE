"""Candidate 09 causal liquidity-state engine.

The detector never treats a liquidity breach as a direction by itself.  A breach
must resolve through one of two causal paths:

* absorption -> reclaim -> failed retest (reversal), or
* depletion -> outside acceptance -> defended retest (continuation).

Only completed one-minute observations enter this module.  Confirmed pivots retain
both their market timestamp and the later timestamp at which the algorithm could
know they were pivots.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR
from hashlib import sha256
from math import isfinite
from statistics import median
from typing import Any, Mapping


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

    def __post_init__(self) -> None:
        values = (self.open, self.high, self.low, self.close, self.volume, self.taker_buy_volume)
        if self.ts_ns < 0 or any(not isfinite(value) for value in values):
            raise ValueError("bar contains an invalid timestamp or non-finite value")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("bar low is inconsistent")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("bar high is inconsistent")
        if self.volume < 0.0 or not 0.0 <= self.taker_buy_volume <= self.volume + 1e-9:
            raise ValueError("bar volume is inconsistent")
        if self.trade_count < 0:
            raise ValueError("trade_count must be non-negative")

    @property
    def flow_imbalance(self) -> float:
        if self.volume <= 0.0:
            return 0.0
        return max(-1.0, min(1.0, (2.0 * self.taker_buy_volume - self.volume) / self.volume))


@dataclass(frozen=True, slots=True)
class EngineConfig:
    pivot_left_bars: int = 5
    pivot_right_bars: int = 5
    atr_period: int = 20
    volume_period: int = 60
    minimum_pivot_prominence_atr: float = 0.45
    maximum_active_pools_per_side: int = 24
    minimum_breach_atr: float = 0.06
    reclaim_buffer_atr: float = 0.02
    acceptance_buffer_atr: float = 0.10
    acceptance_closes: int = 2
    timeout_bars: int = 9
    retest_timeout_bars: int = 6
    retest_tolerance_atr: float = 0.12
    stop_buffer_atr: float = 0.10
    directional_imbalance: float = 0.12
    opposite_confirmation: float = -0.02
    minimum_volume_ratio: float = 0.85
    minimum_displacement_atr: float = 0.30
    absorption_max_progress_atr: float = 0.12
    absorption_min_wick_atr: float = 0.20
    minimum_net_reward_to_risk: float = 1.10
    composite_cost_per_fill: float = 0.00075
    cooldown_bars: int = 5
    use_flow_confirmation: bool = True
    require_reclaim_confirmation: bool = True
    require_acceptance_confirmation: bool = True

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
        *,
        ablation: str = "baseline",
    ) -> "EngineConfig":
        structure = payload["structure"]
        breach = payload["breach"]
        flow = payload["flow"]
        trade = payload["trade"]
        risk = payload["risk"]
        flags = {
            "use_flow_confirmation": ablation != "no-flow",
            "require_reclaim_confirmation": ablation != "no-reclaim-confirmation",
            "require_acceptance_confirmation": ablation != "no-acceptance-confirmation",
        }
        if ablation not in {
            "baseline",
            "no-flow",
            "no-reclaim-confirmation",
            "no-acceptance-confirmation",
        }:
            raise ValueError(f"unknown ablation: {ablation}")
        return cls(
            pivot_left_bars=int(structure["pivot_left_bars"]),
            pivot_right_bars=int(structure["pivot_right_bars"]),
            atr_period=int(structure["atr_period"]),
            volume_period=int(structure["volume_period"]),
            minimum_pivot_prominence_atr=float(structure["minimum_pivot_prominence_atr"]),
            maximum_active_pools_per_side=int(structure["maximum_active_pools_per_side"]),
            minimum_breach_atr=float(breach["minimum_breach_atr"]),
            reclaim_buffer_atr=float(breach["reclaim_buffer_atr"]),
            acceptance_buffer_atr=float(breach["acceptance_buffer_atr"]),
            acceptance_closes=int(breach["acceptance_closes"]),
            timeout_bars=int(breach["timeout_bars"]),
            retest_timeout_bars=int(breach["retest_timeout_bars"]),
            retest_tolerance_atr=float(breach["retest_tolerance_atr"]),
            stop_buffer_atr=float(breach["stop_buffer_atr"]),
            directional_imbalance=float(flow["directional_imbalance"]),
            opposite_confirmation=float(flow["opposite_confirmation"]),
            minimum_volume_ratio=float(flow["minimum_volume_ratio"]),
            minimum_displacement_atr=float(flow["minimum_displacement_atr"]),
            absorption_max_progress_atr=float(flow["absorption_max_progress_atr"]),
            absorption_min_wick_atr=float(flow["absorption_min_wick_atr"]),
            minimum_net_reward_to_risk=float(trade["minimum_net_reward_to_risk"]),
            composite_cost_per_fill=float(risk["composite_taker_cost_per_fill"]),
            cooldown_bars=int(trade["cooldown_bars"]),
            **flags,
        )


@dataclass(slots=True)
class LiquidityPool:
    pool_id: str
    kind: str
    price: float
    event_time_ns: int
    observed_time_ns: int
    created_index: int
    strength: int = 1
    consumed: bool = False


@dataclass(slots=True)
class PendingBreach:
    scenario_id: str
    pool: LiquidityPool
    direction: str
    state: str
    start_index: int
    extreme: float
    outside_closes: int = 0
    absorption_seen: bool = False
    directional_flow_seen: bool = False
    displacement_seen: bool = False
    reclaim_index: int | None = None
    acceptance_index: int | None = None


@dataclass(frozen=True, slots=True)
class DiagnosticEvent:
    scenario_id: str
    event_type: str
    event_time_ns: int
    observed_time_ns: int
    previous_state: str
    next_state: str
    reason_code: str
    reference_price: float | None = None
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
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EngineResult:
    events: tuple[DiagnosticEvent, ...]
    signal: Signal | None


@dataclass(frozen=True, slots=True)
class RiskSizing:
    quantity: Decimal
    loss_budget: Decimal
    per_unit_expected_loss: Decimal
    planned_loss: Decimal


def risk_based_quantity(
    *,
    nav: Decimal,
    risk_fraction: Decimal,
    entry_price: Decimal,
    stop_price: Decimal,
    cost_rate_per_fill: Decimal,
    quantity_increment: Decimal,
) -> RiskSizing:
    """Size from total NAV and complete entry/stop loss, never from notional.

    Composite fill cost contains explicit fee, expected slippage/impact and a small
    funding reserve.  Flooring to the exchange increment guarantees the planned
    loss cannot exceed the selected risk fraction through rounding.
    """

    if nav <= 0 or not Decimal("0") < risk_fraction <= Decimal("0.03"):
        raise ValueError("NAV must be positive and risk_fraction must be in (0, 0.03]")
    if entry_price <= 0 or stop_price <= 0 or quantity_increment <= 0:
        raise ValueError("prices and quantity increment must be positive")
    if cost_rate_per_fill < 0:
        raise ValueError("cost rate cannot be negative")

    budget = nav * risk_fraction
    per_unit = (
        abs(entry_price - stop_price)
        + entry_price * cost_rate_per_fill
        + stop_price * cost_rate_per_fill
    )
    if per_unit <= 0:
        raise ValueError("per-unit loss must be positive")
    raw_units = budget / per_unit
    increments = (raw_units / quantity_increment).to_integral_value(rounding=ROUND_FLOOR)
    quantity = increments * quantity_increment
    planned = quantity * per_unit
    if quantity <= 0:
        raise ValueError("risk budget is below one exchange quantity increment")
    if planned > budget:
        raise AssertionError("floored sizing exceeded the loss budget")
    return RiskSizing(quantity, budget, per_unit, planned)


class LiquidityStateEngine:
    """Streaming, causal state machine for one instrument."""

    def __init__(self, config: EngineConfig):
        self.config = config
        history_size = max(
            256,
            config.atr_period + 4,
            config.volume_period + 4,
            config.pivot_left_bars + config.pivot_right_bars + 4,
        )
        self._bars: deque[FlowBar] = deque(maxlen=history_size)
        self._true_ranges: deque[float] = deque(maxlen=config.atr_period)
        self._volumes: deque[float] = deque(maxlen=config.volume_period)
        self._pools: dict[str, list[LiquidityPool]] = {"HIGH": [], "LOW": []}
        self._pending: PendingBreach | None = None
        self._index = -1
        self._cooldown = 0
        self._atr = 0.0
        self._volume_median = 0.0
        self._last_timestamp = -1

    @property
    def active_pools(self) -> tuple[LiquidityPool, ...]:
        return tuple(
            pool
            for kind in ("HIGH", "LOW")
            for pool in self._pools[kind]
            if not pool.consumed
        )

    @property
    def atr(self) -> float:
        return self._atr

    def on_bar(self, bar: FlowBar) -> EngineResult:
        if bar.ts_ns <= self._last_timestamp:
            raise ValueError("bars must be strictly increasing by observation timestamp")
        self._last_timestamp = bar.ts_ns
        self._index += 1

        previous_close = self._bars[-1].close if self._bars else bar.close
        true_range = max(
            bar.high - bar.low,
            abs(bar.high - previous_close),
            abs(bar.low - previous_close),
        )
        self._true_ranges.append(true_range)
        self._atr = sum(self._true_ranges) / len(self._true_ranges)
        self._volume_median = median(self._volumes) if self._volumes else max(bar.volume, 1e-12)
        self._bars.append(bar)

        events: list[DiagnosticEvent] = []
        self._confirm_pivot(events)

        signal: Signal | None = None
        if self._cooldown > 0:
            self._cooldown -= 1
        elif self._pending is not None:
            signal = self._advance_pending(bar, events)
        elif self._ready:
            self._detect_breach(bar, events)

        self._volumes.append(bar.volume)
        return EngineResult(tuple(events), signal)

    @property
    def _ready(self) -> bool:
        needed = max(
            self.config.atr_period,
            self.config.volume_period,
            self.config.pivot_left_bars + self.config.pivot_right_bars + 1,
        )
        return len(self._bars) >= needed and self._atr > 0.0

    def _confirm_pivot(self, events: list[DiagnosticEvent]) -> None:
        left = self.config.pivot_left_bars
        right = self.config.pivot_right_bars
        if len(self._bars) < left + right + 1 or self._atr <= 0.0:
            return
        bars = list(self._bars)
        candidate_pos = len(bars) - right - 1
        candidate = bars[candidate_pos]
        left_bars = bars[candidate_pos - left : candidate_pos]
        right_bars = bars[candidate_pos + 1 : candidate_pos + 1 + right]
        observed = bars[-1].ts_ns
        candidate_index = self._index - right

        is_high = (
            all(candidate.high > item.high for item in left_bars)
            and all(candidate.high >= item.high for item in right_bars)
        )
        is_low = (
            all(candidate.low < item.low for item in left_bars)
            and all(candidate.low <= item.low for item in right_bars)
        )
        if is_high:
            shoulder = min(min(item.low for item in left_bars), min(item.low for item in right_bars))
            prominence = (candidate.high - shoulder) / self._atr
            if prominence >= self.config.minimum_pivot_prominence_atr:
                self._add_pool("HIGH", candidate.high, candidate.ts_ns, observed, candidate_index, prominence, events)
        if is_low:
            shoulder = max(max(item.high for item in left_bars), max(item.high for item in right_bars))
            prominence = (shoulder - candidate.low) / self._atr
            if prominence >= self.config.minimum_pivot_prominence_atr:
                self._add_pool("LOW", candidate.low, candidate.ts_ns, observed, candidate_index, prominence, events)

    def _add_pool(
        self,
        kind: str,
        price: float,
        event_time_ns: int,
        observed_time_ns: int,
        created_index: int,
        prominence: float,
        events: list[DiagnosticEvent],
    ) -> None:
        identity = sha256(f"{kind}|{event_time_ns}|{price:.10f}".encode()).hexdigest()[:16]
        if any(pool.pool_id == identity for pool in self._pools[kind]):
            return
        pool = LiquidityPool(identity, kind, price, event_time_ns, observed_time_ns, created_index)
        self._pools[kind].append(pool)
        limit = self.config.maximum_active_pools_per_side
        if len(self._pools[kind]) > limit:
            self._pools[kind] = self._pools[kind][-limit:]
        events.append(
            DiagnosticEvent(
                scenario_id=f"pool-{identity}",
                event_type="LIQUIDITY_POOL_CONFIRMED",
                event_time_ns=event_time_ns,
                observed_time_ns=observed_time_ns,
                previous_state="UNOBSERVED",
                next_state="ARMED",
                reason_code=f"CONFIRMED_{kind}_PIVOT",
                reference_price=price,
                details={"prominence_atr": prominence, "right_confirmation_bars": self.config.pivot_right_bars},
            ),
        )

    def _detect_breach(self, bar: FlowBar, events: list[DiagnosticEvent]) -> None:
        if len(self._bars) < 2:
            return
        previous = list(self._bars)[-2]
        buffer = self.config.minimum_breach_atr * self._atr
        highs = [
            pool
            for pool in self._pools["HIGH"]
            if not pool.consumed
            and pool.created_index < self._index
            and previous.close <= pool.price
            and bar.high >= pool.price + buffer
        ]
        lows = [
            pool
            for pool in self._pools["LOW"]
            if not pool.consumed
            and pool.created_index < self._index
            and previous.close >= pool.price
            and bar.low <= pool.price - buffer
        ]
        if highs and lows:
            scenario_id = f"ambiguous-{bar.ts_ns}"
            events.append(
                DiagnosticEvent(
                    scenario_id=scenario_id,
                    event_type="AMBIGUOUS_BREACH",
                    event_time_ns=bar.ts_ns,
                    observed_time_ns=bar.ts_ns,
                    previous_state="IDLE",
                    next_state="NO_TRADE",
                    reason_code="BOTH_SIDES_BREACHED_IN_ONE_OBSERVATION",
                    reference_price=bar.close,
                    details={"high_pool_count": len(highs), "low_pool_count": len(lows)},
                ),
            )
            self._cooldown = 1
            return
        if highs:
            pool = min(highs, key=lambda item: abs(item.price - previous.close))
            self._start_breach(pool, "UP", bar, events)
        elif lows:
            pool = min(lows, key=lambda item: abs(item.price - previous.close))
            self._start_breach(pool, "DOWN", bar, events)

    def _start_breach(
        self,
        pool: LiquidityPool,
        direction: str,
        bar: FlowBar,
        events: list[DiagnosticEvent],
    ) -> None:
        pool.consumed = True
        scenario_id = f"breach-{pool.pool_id}-{bar.ts_ns}"
        pending = PendingBreach(
            scenario_id=scenario_id,
            pool=pool,
            direction=direction,
            state="BREACHED",
            start_index=self._index,
            extreme=bar.high if direction == "UP" else bar.low,
        )
        pending.absorption_seen = self._is_absorption(bar, direction, pool.price)
        pending.directional_flow_seen = self._is_directional_flow(bar, direction)
        pending.displacement_seen = self._is_displacement(bar, direction)
        outside = self._is_outside(bar, direction, pool.price)
        pending.outside_closes = 1 if outside else 0
        self._pending = pending
        events.append(
            self._transition(
                pending,
                event_type="LIQUIDITY_BREACH",
                next_state="BREACHED",
                reason_code=f"{direction}_POOL_INTRUSION",
                bar=bar,
                previous_override="IDLE",
                details=self._feature_details(bar, pool.price),
            ),
        )
        if self._is_reclaimed(bar, direction, pool.price):
            previous = pending.state
            pending.state = "RECLAIMED"
            pending.reclaim_index = self._index
            events.append(
                self._transition(
                    pending,
                    event_type="RANGE_RECLAIM",
                    next_state="RECLAIMED",
                    reason_code="BREACH_BAR_CLOSED_BACK_INSIDE",
                    bar=bar,
                    previous_override=previous,
                    details=self._feature_details(bar, pool.price),
                ),
            )

    def _advance_pending(self, bar: FlowBar, events: list[DiagnosticEvent]) -> Signal | None:
        pending = self._pending
        assert pending is not None
        age = self._index - pending.start_index
        if pending.direction == "UP":
            pending.extreme = max(pending.extreme, bar.high)
        else:
            pending.extreme = min(pending.extreme, bar.low)
        pending.absorption_seen = pending.absorption_seen or self._is_absorption(
            bar, pending.direction, pending.pool.price,
        )
        pending.directional_flow_seen = pending.directional_flow_seen or self._is_directional_flow(
            bar, pending.direction,
        )
        pending.displacement_seen = pending.displacement_seen or self._is_displacement(bar, pending.direction)

        if self._is_outside(bar, pending.direction, pending.pool.price):
            pending.outside_closes += 1
        else:
            pending.outside_closes = 0

        if pending.state == "BREACHED":
            if self._is_reclaimed(bar, pending.direction, pending.pool.price):
                previous = pending.state
                pending.state = "RECLAIMED"
                pending.reclaim_index = self._index
                events.append(
                    self._transition(
                        pending,
                        event_type="RANGE_RECLAIM",
                        next_state="RECLAIMED",
                        reason_code="POST_BREACH_CLOSE_RETURNED_INSIDE",
                        bar=bar,
                        previous_override=previous,
                        details=self._feature_details(bar, pending.pool.price),
                    ),
                )
            elif self._acceptance_ready(pending, bar):
                previous = pending.state
                pending.state = "ACCEPTED"
                pending.acceptance_index = self._index
                events.append(
                    self._transition(
                        pending,
                        event_type="OUTSIDE_ACCEPTANCE",
                        next_state="ACCEPTED",
                        reason_code="DEPTH_PROXY_DEPLETION_AND_PRICE_ACCEPTANCE",
                        bar=bar,
                        previous_override=previous,
                        details={
                            **self._feature_details(bar, pending.pool.price),
                            "outside_closes": pending.outside_closes,
                        },
                    ),
                )

        if pending.state == "RECLAIMED" and pending.reclaim_index is not None:
            if self._index > pending.reclaim_index and self._reversal_confirmation(pending, bar):
                signal = self._build_signal(pending, bar, branch="REVERSAL")
                return self._finish_signal_or_reject(pending, bar, signal, events)

        if pending.state == "ACCEPTED" and pending.acceptance_index is not None:
            if self._index > pending.acceptance_index and self._continuation_retest(pending, bar):
                signal = self._build_signal(pending, bar, branch="CONTINUATION")
                return self._finish_signal_or_reject(pending, bar, signal, events)
            if self._index - pending.acceptance_index > self.config.retest_timeout_bars:
                self._expire(pending, bar, "ACCEPTED_MOVE_DID_NOT_RETEST_IN_TIME", events)
                return None

        if age > self.config.timeout_bars and pending.state != "ACCEPTED":
            self._expire(pending, bar, "BREACH_REMAINED_UNRESOLVED", events)
        return None

    def _finish_signal_or_reject(
        self,
        pending: PendingBreach,
        bar: FlowBar,
        signal: Signal | None,
        events: list[DiagnosticEvent],
    ) -> Signal | None:
        previous = pending.state
        if signal is None:
            events.append(
                self._transition(
                    pending,
                    event_type="SCENARIO_REJECTED",
                    next_state="NO_TRADE",
                    reason_code="NO_REACHABLE_OPPOSING_LIQUIDITY_WITH_POSITIVE_NET_RR",
                    bar=bar,
                    previous_override=previous,
                ),
            )
            self._pending = None
            self._cooldown = self.config.cooldown_bars
            return None
        events.append(
            self._transition(
                pending,
                event_type="ENTRY_APPROVED",
                next_state="ENTERABLE",
                reason_code=signal.reason_code,
                bar=bar,
                previous_override=previous,
                details={
                    "branch": signal.branch,
                    "side": signal.side,
                    "stop": signal.stop_price,
                    "target": signal.target_price,
                    "net_reward_to_risk": signal.net_reward_to_risk,
                },
            ),
        )
        self._pending = None
        self._cooldown = self.config.cooldown_bars
        return signal

    def _build_signal(self, pending: PendingBreach, bar: FlowBar, *, branch: str) -> Signal | None:
        entry = bar.close
        atr = self._atr
        if branch == "REVERSAL" and pending.direction == "UP":
            side = "SELL"
            stop = pending.extreme + self.config.stop_buffer_atr * atr
            targets = sorted(
                (pool.price for pool in self._pools["LOW"] if not pool.consumed and pool.price < entry),
                reverse=True,
            )
        elif branch == "REVERSAL":
            side = "BUY"
            stop = pending.extreme - self.config.stop_buffer_atr * atr
            targets = sorted(pool.price for pool in self._pools["HIGH"] if not pool.consumed and pool.price > entry)
        elif pending.direction == "UP":
            side = "BUY"
            stop = min(pending.pool.price - self.config.stop_buffer_atr * atr, bar.low - self.config.stop_buffer_atr * atr)
            targets = sorted(pool.price for pool in self._pools["HIGH"] if not pool.consumed and pool.price > entry)
        else:
            side = "SELL"
            stop = max(pending.pool.price + self.config.stop_buffer_atr * atr, bar.high + self.config.stop_buffer_atr * atr)
            targets = sorted(
                (pool.price for pool in self._pools["LOW"] if not pool.consumed and pool.price < entry),
                reverse=True,
            )
        if not targets:
            return None
        target = targets[0]
        if side == "BUY" and not stop < entry < target:
            return None
        if side == "SELL" and not target < entry < stop:
            return None
        cost = self.config.composite_cost_per_fill
        risk = abs(entry - stop) + cost * entry + cost * stop
        reward = abs(target - entry) - cost * entry - cost * target
        if risk <= 0.0 or reward <= 0.0:
            return None
        net_rr = reward / risk
        if net_rr < self.config.minimum_net_reward_to_risk:
            return None
        reason = (
            "ABSORPTION_RECLAIM_FAILED_RETEST"
            if branch == "REVERSAL"
            else "DEPLETION_ACCEPTANCE_DEFENDED_RETEST"
        )
        return Signal(
            scenario_id=pending.scenario_id,
            branch=branch,
            side=side,
            observed_time_ns=bar.ts_ns,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            net_reward_to_risk=net_rr,
            reason_code=reason,
            details={
                "pool_id": pending.pool.pool_id,
                "pool_kind": pending.pool.kind,
                "pool_price": pending.pool.price,
                "atr": atr,
                "absorption_seen": pending.absorption_seen,
                "directional_flow_seen": pending.directional_flow_seen,
                "displacement_seen": pending.displacement_seen,
            },
        )

    def _acceptance_ready(self, pending: PendingBreach, bar: FlowBar) -> bool:
        required_closes = self.config.acceptance_closes if self.config.require_acceptance_confirmation else 1
        base = pending.outside_closes >= required_closes
        if not self.config.require_acceptance_confirmation:
            return base
        flow = pending.directional_flow_seen if self.config.use_flow_confirmation else True
        return base and flow and pending.displacement_seen and self._volume_ratio(bar) >= self.config.minimum_volume_ratio

    def _reversal_confirmation(self, pending: PendingBreach, bar: FlowBar) -> bool:
        if self.config.require_reclaim_confirmation and not pending.absorption_seen:
            return False
        flow = bar.flow_imbalance
        if pending.direction == "UP":
            price_failure = bar.close < pending.pool.price and bar.close < bar.open
            flow_ok = flow <= self.config.opposite_confirmation
        else:
            price_failure = bar.close > pending.pool.price and bar.close > bar.open
            flow_ok = flow >= -self.config.opposite_confirmation
        return price_failure and (flow_ok or not self.config.use_flow_confirmation)

    def _continuation_retest(self, pending: PendingBreach, bar: FlowBar) -> bool:
        tolerance = self.config.retest_tolerance_atr * self._atr
        flow = bar.flow_imbalance
        if pending.direction == "UP":
            location = bar.low <= pending.pool.price + tolerance and bar.close > pending.pool.price
            flow_ok = flow >= -self.config.directional_imbalance
        else:
            location = bar.high >= pending.pool.price - tolerance and bar.close < pending.pool.price
            flow_ok = flow <= self.config.directional_imbalance
        return location and (flow_ok or not self.config.use_flow_confirmation)

    def _is_absorption(self, bar: FlowBar, direction: str, pool_price: float) -> bool:
        atr = max(self._atr, 1e-12)
        volume_ok = self._volume_ratio(bar) >= self.config.minimum_volume_ratio
        if direction == "UP":
            progress = max(0.0, bar.close - pool_price) / atr
            wick = (bar.high - max(bar.open, bar.close)) / atr
            flow_ok = bar.flow_imbalance >= self.config.directional_imbalance
        else:
            progress = max(0.0, pool_price - bar.close) / atr
            wick = (min(bar.open, bar.close) - bar.low) / atr
            flow_ok = bar.flow_imbalance <= -self.config.directional_imbalance
        stalled = progress <= self.config.absorption_max_progress_atr or wick >= self.config.absorption_min_wick_atr
        return volume_ok and stalled and (flow_ok or not self.config.use_flow_confirmation)

    def _is_directional_flow(self, bar: FlowBar, direction: str) -> bool:
        if not self.config.use_flow_confirmation:
            return True
        if direction == "UP":
            return bar.flow_imbalance >= self.config.directional_imbalance
        return bar.flow_imbalance <= -self.config.directional_imbalance

    def _is_displacement(self, bar: FlowBar, direction: str) -> bool:
        body = abs(bar.close - bar.open) / max(self._atr, 1e-12)
        aligned = bar.close > bar.open if direction == "UP" else bar.close < bar.open
        return aligned and body >= self.config.minimum_displacement_atr

    def _is_outside(self, bar: FlowBar, direction: str, pool_price: float) -> bool:
        buffer = self.config.acceptance_buffer_atr * self._atr
        return bar.close > pool_price + buffer if direction == "UP" else bar.close < pool_price - buffer

    def _is_reclaimed(self, bar: FlowBar, direction: str, pool_price: float) -> bool:
        buffer = self.config.reclaim_buffer_atr * self._atr
        return bar.close < pool_price - buffer if direction == "UP" else bar.close > pool_price + buffer

    def _volume_ratio(self, bar: FlowBar) -> float:
        return bar.volume / max(self._volume_median, 1e-12)

    def _feature_details(self, bar: FlowBar, pool_price: float) -> dict[str, float | int]:
        return {
            "atr": self._atr,
            "flow_imbalance": bar.flow_imbalance,
            "volume_ratio": self._volume_ratio(bar),
            "pool_price": pool_price,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "trade_count": bar.trade_count,
        }

    def _transition(
        self,
        pending: PendingBreach,
        *,
        event_type: str,
        next_state: str,
        reason_code: str,
        bar: FlowBar,
        previous_override: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> DiagnosticEvent:
        return DiagnosticEvent(
            scenario_id=pending.scenario_id,
            event_type=event_type,
            event_time_ns=bar.ts_ns,
            observed_time_ns=bar.ts_ns,
            previous_state=previous_override or pending.state,
            next_state=next_state,
            reason_code=reason_code,
            reference_price=bar.close,
            details=dict(details or {}),
        )

    def _expire(
        self,
        pending: PendingBreach,
        bar: FlowBar,
        reason: str,
        events: list[DiagnosticEvent],
    ) -> None:
        events.append(
            self._transition(
                pending,
                event_type="SCENARIO_EXPIRED",
                next_state="NO_TRADE",
                reason_code=reason,
                bar=bar,
                previous_override=pending.state,
            ),
        )
        self._pending = None
        self._cooldown = self.config.cooldown_bars

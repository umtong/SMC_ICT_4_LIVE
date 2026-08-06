"""Candidate 11 detector/scenario layer.

This module contains no backtest loop. It consumes one causal observation at a
time and emits explainable trade plans. NautilusTrader remains responsible for
clock ordering, orders, fills, positions, fees, margin and account NAV.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from enum import StrEnum
from math import isfinite
from statistics import median
from typing import Any

from smc_ict_4.contracts import ResearchEvent


class Side(StrEnum):
    HIGH = "HIGH"
    LOW = "LOW"


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class Scenario(StrEnum):
    FAR = "FAR"  # failed-auction reversal
    AAC = "AAC"  # accepted-auction continuation


@dataclass(frozen=True, slots=True)
class BarObs:
    ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float

    def __post_init__(self) -> None:
        values = (self.open, self.high, self.low, self.close, self.volume, self.taker_buy_volume)
        if self.ts_ns < 0 or not all(isfinite(v) for v in values):
            raise ValueError("invalid bar observation")
        if self.high < max(self.open, self.close, self.low) or self.low > min(self.open, self.close, self.high):
            raise ValueError("inconsistent OHLC")
        if self.volume < 0 or not 0 <= self.taker_buy_volume <= self.volume + 1e-9:
            raise ValueError("inconsistent volume")

    @property
    def span(self) -> float:
        return max(self.high - self.low, 1e-12)

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def signed_flow(self) -> float:
        if self.volume <= 0:
            return 0.0
        return max(-1.0, min(1.0, 2.0 * self.taker_buy_volume / self.volume - 1.0))

    @property
    def close_location(self) -> float:
        return (self.close - self.low) / self.span

    @property
    def upper_wick(self) -> float:
        return (self.high - max(self.open, self.close)) / self.span

    @property
    def lower_wick(self) -> float:
        return (min(self.open, self.close) - self.low) / self.span


@dataclass(frozen=True, slots=True)
class LogicConfig:
    atr_period: int = 30
    volume_period: int = 120
    pivot_wing: int = 3
    pivot_expiry_bars: int = 1440
    daily_expiry_bars: int = 10080
    max_pools_per_side: int = 80
    sweep_min_atr: float = 0.025
    sweep_max_atr: float = 1.20
    min_relative_volume: float = 0.85
    event_expiry_bars: int = 6
    retrace_expiry_bars: int = 5
    internal_lookback: int = 4
    rejection_wick_min: float = 0.25
    rejection_reclaim_atr: float = 0.05
    absorption_flow_min: float = 0.08
    displacement_body_atr: float = 0.18
    displacement_flow_min: float = 0.01
    acceptance_close_atr: float = 0.045
    acceptance_close_location: float = 0.58
    acceptance_flow_min: float = 0.02
    acceptance_hold_atr: float = 0.015
    acceptance_retest_atr: float = 0.18
    stop_buffer_atr: float = 0.06
    min_stop_atr: float = 0.12
    max_stop_atr: float = 1.35
    min_net_r: float = 1.25
    risk_fraction: float = 0.03
    effective_taker_rate: float = 0.0008
    effective_maker_rate: float = 0.0004

    def __post_init__(self) -> None:
        if self.atr_period < 2 or self.volume_period < 2 or self.pivot_wing < 1:
            raise ValueError("invalid rolling periods")
        if not 0 < self.risk_fraction <= 0.03:
            raise ValueError("risk_fraction must be in (0, 0.03]")
        if self.min_net_r <= 0:
            raise ValueError("min_net_r must be positive")


@dataclass(slots=True)
class Pool:
    scenario_id: str
    side: Side
    level: float
    source: str
    candidate_ts_ns: int
    confirmed_ts_ns: int
    confirmed_index: int
    expiry_index: int
    consumed: bool = False


@dataclass(slots=True)
class Auction:
    pool: Pool
    sweep: BarObs
    sweep_index: int
    atr: float
    internal_level: float
    sweep_extreme: float
    rejection_seed: bool
    acceptance_seed: bool
    state: str = "COMPETE"
    elapsed: int = 0
    held_outside: int = 0
    scenario: Scenario | None = None
    direction: Direction | None = None
    retrace_level: float | None = None
    stop_price: float | None = None


@dataclass(frozen=True, slots=True)
class TradePlan:
    scenario_id: str
    scenario: Scenario
    direction: Direction
    observed_ts_ns: int
    expected_entry: float
    stop_price: float
    target_price: float
    atr: float
    loss_per_unit: float
    gain_per_unit: float
    net_r: float
    reason_code: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SizeDecision:
    quantity: Decimal
    planned_loss_budget: Decimal
    expected_loss_per_unit: Decimal
    expected_total_loss: Decimal
    required_margin: Decimal
    feasible: bool
    reason: str


class RiskSizer:
    """Exact NAV-risk sizing; infeasible margin rejects rather than clips quantity."""

    def __init__(self, risk_fraction: float = 0.03) -> None:
        if not 0 < risk_fraction <= 0.03:
            raise ValueError("risk_fraction must be in (0, 0.03]")
        self.risk_fraction = Decimal(str(risk_fraction))

    @staticmethod
    def _floor(value: Decimal, increment: Decimal) -> Decimal:
        if increment <= 0:
            raise ValueError("quantity increment must be positive")
        return (value / increment).to_integral_value(rounding=ROUND_DOWN) * increment

    def size(
        self,
        *,
        nav: Decimal,
        loss_per_unit: Decimal,
        entry_price: Decimal,
        quantity_increment: Decimal,
        min_quantity: Decimal,
        min_notional: Decimal,
        margin_init: Decimal,
        free_balance: Decimal,
    ) -> SizeDecision:
        if nav <= 0 or loss_per_unit <= 0 or entry_price <= 0:
            raise ValueError("NAV, loss and entry must be positive")
        budget = nav * self.risk_fraction
        qty = self._floor(budget / loss_per_unit, quantity_increment)
        notional = qty * entry_price
        expected = qty * loss_per_unit
        margin = notional * max(margin_init, Decimal("0"))
        reason = "OK"
        feasible = True
        if qty < min_quantity:
            feasible, reason = False, "BELOW_MIN_QUANTITY"
        elif notional < min_notional:
            feasible, reason = False, "BELOW_MIN_NOTIONAL"
        elif margin > free_balance:
            feasible, reason = False, "ACTUAL_MARGIN_INFEASIBLE"
        return SizeDecision(
            quantity=qty if feasible else Decimal("0"),
            planned_loss_budget=budget,
            expected_loss_per_unit=loss_per_unit,
            expected_total_loss=expected if feasible else Decimal("0"),
            required_margin=margin,
            feasible=feasible,
            reason=reason,
        )


class CausalAuctionEngine:
    """Causal pool detector plus competing FAR/AAC scenario state machine."""

    def __init__(self, config: LogicConfig, instrument_id: str) -> None:
        self.config = config
        self.instrument_id = instrument_id
        self.bars: list[BarObs] = []
        self.true_ranges: deque[float] = deque(maxlen=config.atr_period)
        self.volumes: deque[float] = deque(maxlen=config.volume_period)
        self.pools: list[Pool] = []
        self.active: Auction | None = None
        self.active_trade_id: str | None = None
        self.active_trade_state: str | None = None
        self.events: list[ResearchEvent] = []
        self.skips: Counter[str] = Counter()
        self._index = -1
        self._pool_seq = 0
        self._day: str | None = None
        self._day_high = float("-inf")
        self._day_low = float("inf")
        self._day_first_ts = 0

    @property
    def atr(self) -> float | None:
        return sum(self.true_ranges) / len(self.true_ranges) if len(self.true_ranges) == self.config.atr_period else None

    @property
    def median_volume(self) -> float | None:
        return median(self.volumes) if len(self.volumes) == self.config.volume_period else None

    def _event(
        self,
        scenario_id: str,
        event_type: str,
        event_time_ns: int,
        observed_time_ns: int,
        previous_state: str,
        next_state: str,
        reason_code: str,
        reference_price: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.events.append(
            ResearchEvent(
                scenario_id=scenario_id,
                instrument_id=self.instrument_id,
                event_type=event_type,
                event_time_ns=event_time_ns,
                observed_time_ns=observed_time_ns,
                previous_state=previous_state,
                next_state=next_state,
                reason_code=reason_code,
                reference_price=None if reference_price is None else str(reference_price),
                details=details or {},
            ),
        )

    def _new_pool(self, side: Side, level: float, source: str, candidate_ts: int, confirmed_ts: int, expiry: int) -> None:
        atr = self.atr or 0.0
        for pool in reversed(self.pools):
            if pool.side == side and not pool.consumed and abs(pool.level - level) <= self.config.sweep_min_atr * max(atr, 1e-9):
                return
        self._pool_seq += 1
        scenario_id = f"{self.instrument_id}-{source}-{side.value}-{self._pool_seq:06d}"
        pool = Pool(scenario_id, side, level, source, candidate_ts, confirmed_ts, self._index, expiry)
        self.pools.append(pool)
        self._event(scenario_id, "LIQUIDITY_POOL_CONFIRMED", candidate_ts, confirmed_ts, "MAP", "ARMED", source, level)
        live = [p for p in self.pools if p.side == side and not p.consumed]
        if len(live) > self.config.max_pools_per_side:
            oldest = min(live, key=lambda p: p.confirmed_index)
            oldest.consumed = True
            self._event(oldest.scenario_id, "POOL_PRUNED", oldest.confirmed_ts_ns, confirmed_ts, "ARMED", "TERMINAL", "POOL_CAPACITY_PRUNE", oldest.level)

    def _update_day(self, bar: BarObs) -> None:
        day = datetime.fromtimestamp(bar.ts_ns / 1e9, tz=timezone.utc).date().isoformat()
        if self._day is None:
            self._day, self._day_first_ts = day, bar.ts_ns
        elif day != self._day:
            self._new_pool(Side.HIGH, self._day_high, "PREVIOUS_DAY_HIGH", self._day_first_ts, bar.ts_ns, self._index + self.config.daily_expiry_bars)
            self._new_pool(Side.LOW, self._day_low, "PREVIOUS_DAY_LOW", self._day_first_ts, bar.ts_ns, self._index + self.config.daily_expiry_bars)
            self._day, self._day_first_ts = day, bar.ts_ns
            self._day_high, self._day_low = float("-inf"), float("inf")
        self._day_high = max(self._day_high, bar.high)
        self._day_low = min(self._day_low, bar.low)

    def _confirm_pivot(self) -> None:
        wing = self.config.pivot_wing
        if len(self.bars) < 2 * wing + 1:
            return
        center_i = len(self.bars) - 1 - wing
        window = self.bars[center_i - wing : center_i + wing + 1]
        center = self.bars[center_i]
        confirmed_ts = self.bars[-1].ts_ns
        if center.high == max(x.high for x in window) and sum(x.high == center.high for x in window) == 1:
            self._new_pool(Side.HIGH, center.high, "CAUSAL_PIVOT_HIGH", center.ts_ns, confirmed_ts, center_i + self.config.pivot_expiry_bars)
        if center.low == min(x.low for x in window) and sum(x.low == center.low for x in window) == 1:
            self._new_pool(Side.LOW, center.low, "CAUSAL_PIVOT_LOW", center.ts_ns, confirmed_ts, center_i + self.config.pivot_expiry_bars)

    def _expire_pools(self, ts_ns: int) -> None:
        for pool in self.pools:
            if not pool.consumed and self._index > pool.expiry_index:
                pool.consumed = True
                self._event(pool.scenario_id, "POOL_EXPIRED", pool.confirmed_ts_ns, ts_ns, "ARMED", "TERMINAL", "CAUSAL_POOL_EXPIRED", pool.level)

    def _nearest_target(self, direction: Direction, price: float) -> Pool | None:
        candidates = [p for p in self.pools if not p.consumed and ((direction == Direction.LONG and p.side == Side.HIGH and p.level > price) or (direction == Direction.SHORT and p.side == Side.LOW and p.level < price))]
        return min(candidates, key=lambda p: abs(p.level - price)) if candidates else None

    def _detect_sweep(self, bar: BarObs, prev: BarObs, atr: float, rel_volume: float) -> None:
        if rel_volume < self.config.min_relative_volume or bar.high >= max((p.level for p in self.pools if p.side == Side.HIGH and not p.consumed), default=float("inf")) and bar.low <= min((p.level for p in self.pools if p.side == Side.LOW and not p.consumed), default=float("-inf")):
            return
        crossed: list[Pool] = []
        for p in self.pools:
            if p.consumed or p.confirmed_index >= self._index:
                continue
            penetration = (bar.high - p.level) if p.side == Side.HIGH else (p.level - bar.low)
            if self.config.sweep_min_atr * atr <= penetration <= self.config.sweep_max_atr * atr:
                crossed.append(p)
        if not crossed:
            return
        pool = min(crossed, key=lambda p: abs(prev.close - p.level))
        pool.consumed = True
        if pool.side == Side.HIGH:
            rejection = bar.upper_wick >= self.config.rejection_wick_min and bar.close <= pool.level + self.config.rejection_reclaim_atr * atr and bar.signed_flow >= self.config.absorption_flow_min
            acceptance = bar.close >= pool.level + self.config.acceptance_close_atr * atr and bar.close_location >= self.config.acceptance_close_location and bar.signed_flow >= self.config.acceptance_flow_min
            internal = min(x.low for x in self.bars[max(0, self._index - self.config.internal_lookback) : self._index])
            extreme = bar.high
        else:
            rejection = bar.lower_wick >= self.config.rejection_wick_min and bar.close >= pool.level - self.config.rejection_reclaim_atr * atr and bar.signed_flow <= -self.config.absorption_flow_min
            acceptance = bar.close <= pool.level - self.config.acceptance_close_atr * atr and bar.close_location <= 1.0 - self.config.acceptance_close_location and bar.signed_flow <= -self.config.acceptance_flow_min
            internal = max(x.high for x in self.bars[max(0, self._index - self.config.internal_lookback) : self._index])
            extreme = bar.low
        if not (rejection or acceptance):
            self._event(pool.scenario_id, "SWEEP_UNRESOLVED", bar.ts_ns, bar.ts_ns, "ARMED", "TERMINAL", "NO_ABSORPTION_OR_ACCEPTANCE", pool.level, {"flow": bar.signed_flow, "relative_volume": rel_volume})
            self.skips["NO_ABSORPTION_OR_ACCEPTANCE"] += 1
            return
        self.active = Auction(pool, bar, self._index, atr, internal, extreme, rejection, acceptance)
        self._event(pool.scenario_id, "LIQUIDITY_SWEEP", bar.ts_ns, bar.ts_ns, "ARMED", "COMPETE", "REAL_TRADE_THROUGH_PROXY", pool.level, {"flow": bar.signed_flow, "relative_volume": rel_volume, "rejection_seed": rejection, "acceptance_seed": acceptance})

    def _terminal(self, auction: Auction, bar: BarObs, reason: str) -> None:
        self._event(auction.pool.scenario_id, "AUCTION_TERMINAL", auction.sweep.ts_ns, bar.ts_ns, auction.state, "TERMINAL", reason, auction.pool.level)
        self.skips[reason] += 1
        self.active = None

    def _costed_plan(self, auction: Auction, scenario: Scenario, direction: Direction, bar: BarObs, entry: float, stop: float, target: float, reason: str) -> TradePlan | None:
        stop_distance = (entry - stop) if direction == Direction.LONG else (stop - entry)
        gain_distance = (target - entry) if direction == Direction.LONG else (entry - target)
        if stop_distance <= 0 or gain_distance <= 0:
            self._terminal(auction, bar, "NON_CAUSAL_PRICE_ORDER")
            return None
        stop_atr = stop_distance / auction.atr
        if not self.config.min_stop_atr <= stop_atr <= self.config.max_stop_atr:
            self._terminal(auction, bar, "STOP_OUTSIDE_STRUCTURAL_BOUNDS")
            return None
        loss = stop_distance + entry * self.config.effective_taker_rate + stop * self.config.effective_taker_rate
        gain = gain_distance - entry * self.config.effective_taker_rate - target * self.config.effective_maker_rate
        net_r = gain / loss
        if net_r < self.config.min_net_r:
            self._terminal(auction, bar, "INSUFFICIENT_COSTED_STRUCTURAL_R")
            return None
        return TradePlan(auction.pool.scenario_id, scenario, direction, bar.ts_ns, entry, stop, target, auction.atr, loss, gain, net_r, reason, {"pool_level": auction.pool.level, "sweep_extreme": auction.sweep_extreme})

    def _try_far(self, a: Auction, bar: BarObs) -> TradePlan | None:
        if not a.rejection_seed:
            return None
        if a.state == "COMPETE":
            if a.pool.side == Side.HIGH:
                confirmed = bar.close < a.internal_level and bar.body >= self.config.displacement_body_atr * a.atr and bar.signed_flow <= -self.config.displacement_flow_min
                direction = Direction.SHORT
            else:
                confirmed = bar.close > a.internal_level and bar.body >= self.config.displacement_body_atr * a.atr and bar.signed_flow >= self.config.displacement_flow_min
                direction = Direction.LONG
            if not confirmed:
                return None
            a.state, a.scenario, a.direction = "RETRACE", Scenario.FAR, direction
            a.elapsed = 0
            a.retrace_level = (bar.close + a.internal_level) / 2.0
            a.stop_price = a.sweep_extreme + self.config.stop_buffer_atr * a.atr if direction == Direction.SHORT else a.sweep_extreme - self.config.stop_buffer_atr * a.atr
            self._event(a.pool.scenario_id, "FAR_CONFIRMED", a.sweep.ts_ns, bar.ts_ns, "COMPETE", "RETRACE", "ABSORPTION_RECLAIM_MSS_DISPLACEMENT", a.pool.level, {"internal_level": a.internal_level, "retrace_level": a.retrace_level})
            return None
        if a.state != "RETRACE" or a.scenario != Scenario.FAR or a.retrace_level is None or a.stop_price is None:
            return None
        if a.direction == Direction.SHORT:
            touched = bar.high >= a.retrace_level and bar.close <= a.retrace_level and bar.signed_flow <= 0.10
        else:
            touched = bar.low <= a.retrace_level and bar.close >= a.retrace_level and bar.signed_flow >= -0.10
        if not touched:
            return None
        target_pool = self._nearest_target(a.direction, bar.close)
        if target_pool is None:
            self._terminal(a, bar, "NO_OPPOSING_LIQUIDITY_TARGET")
            return None
        plan = self._costed_plan(a, Scenario.FAR, a.direction, bar, bar.close, a.stop_price, target_pool.level, "FAR_FIRST_RETRACE")
        if plan is not None:
            self._event(a.pool.scenario_id, "TRADE_PLAN_CONFIRMED", a.sweep.ts_ns, bar.ts_ns, "RETRACE", "CONFIRMED", plan.reason_code, bar.close, {"target_pool": target_pool.scenario_id, "net_r": plan.net_r})
            a.state = "CONFIRMED"
        return plan

    def _try_aac(self, a: Auction, bar: BarObs) -> TradePlan | None:
        if not a.acceptance_seed or a.state != "COMPETE":
            return None
        level = a.pool.level
        if a.pool.side == Side.HIGH:
            outside = bar.close >= level + self.config.acceptance_hold_atr * a.atr
            if outside:
                a.held_outside += 1
            retest = bar.low <= level + self.config.acceptance_retest_atr * a.atr and outside and bar.close_location >= 0.50 and bar.signed_flow >= -0.08
            direction = Direction.LONG
            stop = min(level - self.config.stop_buffer_atr * a.atr, bar.low - self.config.stop_buffer_atr * a.atr)
        else:
            outside = bar.close <= level - self.config.acceptance_hold_atr * a.atr
            if outside:
                a.held_outside += 1
            retest = bar.high >= level - self.config.acceptance_retest_atr * a.atr and outside and bar.close_location <= 0.50 and bar.signed_flow <= 0.08
            direction = Direction.SHORT
            stop = max(level + self.config.stop_buffer_atr * a.atr, bar.high + self.config.stop_buffer_atr * a.atr)
        if a.held_outside < 1 or not retest:
            return None
        target_pool = self._nearest_target(direction, bar.close)
        if target_pool is None:
            self._terminal(a, bar, "NO_CONTINUATION_LIQUIDITY_TARGET")
            return None
        plan = self._costed_plan(a, Scenario.AAC, direction, bar, bar.close, stop, target_pool.level, "AAC_FIRST_DEFENDED_RETEST")
        if plan is not None:
            self._event(a.pool.scenario_id, "AAC_CONFIRMED", a.sweep.ts_ns, bar.ts_ns, "COMPETE", "CONFIRMED", "OUTSIDE_ACCEPTANCE_FIRST_RETEST", level, {"held_outside": a.held_outside, "target_pool": target_pool.scenario_id, "net_r": plan.net_r})
            a.state, a.scenario, a.direction = "CONFIRMED", Scenario.AAC, direction
        return plan

    def mark_submitted(self, plan: TradePlan, quantity: Decimal, details: dict[str, Any] | None = None) -> None:
        if self.active_trade_id is not None:
            raise RuntimeError("global candidate slot already occupied")
        if self.active is None or self.active.pool.scenario_id != plan.scenario_id:
            raise RuntimeError("submitted plan does not match active auction")
        self._event(plan.scenario_id, "ENTRY_ORDER_LIST_SUBMITTED", plan.observed_ts_ns, plan.observed_ts_ns, "CONFIRMED", "POSITION", plan.reason_code, plan.expected_entry, {"scenario": plan.scenario.value, "direction": plan.direction.value, "quantity": str(quantity), "net_r": plan.net_r, **(details or {})})
        self.active_trade_id, self.active_trade_state = plan.scenario_id, "POSITION"
        self.active = None

    def mark_rejected(self, plan: TradePlan, ts_ns: int, reason: str, details: dict[str, Any] | None = None) -> None:
        if self.active is None or self.active.pool.scenario_id != plan.scenario_id:
            return
        self._event(plan.scenario_id, "ENTRY_PLAN_REJECTED", plan.observed_ts_ns, ts_ns, "CONFIRMED", "TERMINAL", reason, plan.expected_entry, details or {})
        self.skips[reason] += 1
        self.active = None

    def mark_trade_terminal(self, ts_ns: int, reason: str, details: dict[str, Any] | None = None) -> None:
        if self.active_trade_id is None:
            return
        self._event(self.active_trade_id, "POSITION_TERMINAL", ts_ns, ts_ns, self.active_trade_state or "POSITION", "TERMINAL", reason, details=details or {})
        self.active_trade_id = None
        self.active_trade_state = None

    def on_bar(self, bar: BarObs, *, allow_entry: bool = True) -> TradePlan | None:
        self._index += 1
        prev = self.bars[-1] if self.bars else None
        tr = bar.high - bar.low if prev is None else max(bar.high - bar.low, abs(bar.high - prev.close), abs(bar.low - prev.close))
        self.true_ranges.append(tr)
        self.volumes.append(bar.volume)
        self.bars.append(bar)
        atr, med_volume = self.atr, self.median_volume
        if atr is None or med_volume is None or atr <= 0:
            return None
        self._update_day(bar)
        self._confirm_pivot()
        self._expire_pools(bar.ts_ns)
        if self.active_trade_id is not None or prev is None:
            return None
        rel_volume = bar.volume / max(med_volume, 1e-12)
        if self.active is None:
            self._detect_sweep(bar, prev, atr, rel_volume)
            return None
        a = self.active
        a.elapsed += 1
        if a.state == "RETRACE" and a.elapsed > self.config.retrace_expiry_bars:
            self._terminal(a, bar, "RETRACE_EXPIRED")
            return None
        if a.state == "COMPETE" and a.elapsed > self.config.event_expiry_bars:
            self._terminal(a, bar, "COMPETING_HYPOTHESES_UNRESOLVED")
            return None
        plan = self._try_far(a, bar)
        if plan is None and self.active is not None:
            plan = self._try_aac(a, bar)
        if plan is not None and not allow_entry:
            self.mark_rejected(plan, bar.ts_ns, "OUTSIDE_EVALUATION_WINDOW")
            return None
        return plan

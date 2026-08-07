"""Causal session-auction SMC/ICT state machine for Candidate 12.

Completed five-minute bars classify how price interacts with a previously
completed session range.  It emits plans only after one of two causal paths:

1. failed auction: liquidity sweep -> reclaim -> MSS displacement -> pullback -> reacceleration;
2. accepted auction: sustained closes outside -> boundary retest -> reacceleration.

Execution, fees, fills, positions, and NAV remain exclusively in NautilusTrader.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN
from enum import Enum
import math
from statistics import median
from typing import Any, Deque

from smc_ict_4.contracts import ResearchEvent

NS_MINUTE = 60_000_000_000
NS_DAY = 86_400_000_000_000


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class ScenarioKind(str, Enum):
    FAILED_AUCTION = "FAILED_AUCTION"
    ACCEPTED_AUCTION = "ACCEPTED_AUCTION"


class Side(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


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
        if not all(math.isfinite(x) for x in values):
            raise ValueError("non-finite bar")
        if self.high < max(self.open, self.close, self.low) or self.low > min(self.open, self.close, self.high):
            raise ValueError("inconsistent OHLC")
        if self.volume < 0 or self.taker_buy_volume < 0 or self.taker_buy_volume > self.volume + 1e-9:
            raise ValueError("invalid volume")

    @property
    def signed_flow(self) -> float:
        if self.volume <= 0:
            return 0.0
        return max(-1.0, min(1.0, 2.0 * self.taker_buy_volume / self.volume - 1.0))


@dataclass(frozen=True, slots=True)
class LogicConfig:
    bar_minutes: int = 5
    atr_period: int = 36
    volume_period: int = 36
    mss_lookback_bars: int = 6
    sweep_min_atr: float = 0.08
    sweep_max_atr: float = 3.0
    reclaim_buffer_atr: float = 0.01
    reclaim_close_location: float = 0.55
    mss_body_atr: float = 0.28
    mss_flow_min: float = 0.015
    pullback_min_atr: float = 0.12
    pullback_expiry_bars: int = 7
    reacceleration_body_atr: float = 0.16
    reacceleration_flow_min: float = 0.0
    acceptance_closes: int = 2
    acceptance_excursion_atr: float = 0.25
    acceptance_body_atr: float = 0.22
    retest_tolerance_atr: float = 0.18
    acceptance_pullback_fraction: float = 0.50
    retest_expiry_bars: int = 24
    max_asia_range_atr: float = 14.0
    max_london_range_atr: float = 18.0
    edge_open_fraction: float = 0.15
    stop_buffer_atr: float = 0.08
    min_stop_atr: float = 0.12
    max_stop_atr: float = 2.8
    min_net_r: float = 1.20
    max_target_atr: float = 24.0
    risk_fraction: float = 0.03
    effective_maker_rate: float = 0.0004
    effective_taker_rate: float = 0.0008
    tick_slippage_units: float = 2.0
    price_increment: float = 0.1

    def __post_init__(self) -> None:
        for name in ("bar_minutes", "atr_period", "volume_period", "mss_lookback_bars", "pullback_expiry_bars", "acceptance_closes", "retest_expiry_bars"):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        if not 0 < self.risk_fraction <= 0.03:
            raise ValueError("risk_fraction must be within (0, 0.03]")
        if not 0 < self.edge_open_fraction < 0.5:
            raise ValueError("edge_open_fraction must be in (0, 0.5)")
        if self.price_increment <= 0:
            raise ValueError("price_increment must be positive")


@dataclass(frozen=True, slots=True)
class TradePlan:
    scenario_id: str
    scenario: ScenarioKind
    direction: Direction
    observed_ts_ns: int
    expected_entry: float
    stop_price: float
    target_price: float
    loss_per_unit: float
    expected_profit_per_unit: float
    net_r: float
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SizeDecision:
    feasible: bool
    quantity: Decimal
    planned_loss_budget: Decimal
    expected_total_loss: Decimal
    required_margin: Decimal
    reason: str


class RiskSizer:
    def __init__(self, risk_fraction: float) -> None:
        value = Decimal(str(risk_fraction))
        if value <= 0 or value > Decimal("0.03"):
            raise ValueError("risk fraction must be within (0, 0.03]")
        self.risk_fraction = value

    @staticmethod
    def _floor(value: Decimal, increment: Decimal) -> Decimal:
        return (value / increment).to_integral_value(rounding=ROUND_DOWN) * increment

    def size(self, *, nav: Decimal, loss_per_unit: Decimal, entry_price: Decimal,
             quantity_increment: Decimal, min_quantity: Decimal, min_notional: Decimal,
             margin_init: Decimal, free_balance: Decimal) -> SizeDecision:
        zero = Decimal("0")
        budget = nav * self.risk_fraction
        if nav <= zero or loss_per_unit <= zero or entry_price <= zero or quantity_increment <= zero:
            return SizeDecision(False, zero, budget, zero, zero, "INVALID_RISK_INPUT")
        quantity = self._floor(budget / loss_per_unit, quantity_increment)
        expected = quantity * loss_per_unit
        if quantity < min_quantity:
            return SizeDecision(False, quantity, budget, expected, zero, "BELOW_MIN_QUANTITY")
        notional = quantity * entry_price
        if notional < min_notional:
            return SizeDecision(False, quantity, budget, expected, zero, "BELOW_MIN_NOTIONAL")
        if expected > budget:
            return SizeDecision(False, quantity, budget, expected, zero, "RISK_BUDGET_EXCEEDED")
        margin = notional * margin_init
        if margin > free_balance:
            return SizeDecision(False, quantity, budget, expected, margin, "INSUFFICIENT_MARGIN")
        return SizeDecision(True, quantity, budget, expected, margin, "OK")


@dataclass(slots=True)
class FiveBar:
    ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def close_location(self) -> float:
        return 0.5 if self.range <= 0 else (self.close - self.low) / self.range

    @property
    def signed_flow(self) -> float:
        return 0.0 if self.volume <= 0 else max(-1.0, min(1.0, 2.0 * self.taker_buy_volume / self.volume - 1.0))


@dataclass(slots=True)
class SessionRange:
    label: str
    day_bucket: int
    high: float
    low: float
    observed_ts_ns: int

    @property
    def width(self) -> float:
        return self.high - self.low


@dataclass(slots=True)
class Auction:
    scenario_id: str
    source: SessionRange
    trade_start_minute: int
    trade_end_minute: int
    window_open: float
    phase: str = "WATCH"
    preferred_side: Side | None = None
    side: Side | None = None
    direction: Direction | None = None
    extreme: float | None = None
    structure: float | None = None
    phase_index: int = 0
    outside_closes_high: int = 0
    outside_closes_low: int = 0
    displacement_close: float | None = None
    displacement_extreme: float | None = None
    pullback_extreme: float | None = None
    pullback_seen: bool = False
    retest_level: float | None = None
    crossed_high: bool = False
    crossed_low: bool = False


class CausalLiquidityAuctionEngine:
    def __init__(self, config: LogicConfig, instrument_id: str) -> None:
        self.config = config
        self.instrument_id = instrument_id
        self.events: list[ResearchEvent] = []
        self.skips: Counter[str] = Counter()
        self.scenario_counts: Counter[str] = Counter()
        self.pool_counts: Counter[str] = Counter()
        self._states: dict[str, str] = {}
        self._minute_bucket: int | None = None
        self._minute_parts: list[BarObs] = []
        self._bars: Deque[FiveBar] = deque(maxlen=600)
        self._true_ranges: Deque[float] = deque(maxlen=config.atr_period)
        self._volumes: Deque[float] = deque(maxlen=config.volume_period)
        self._previous_close: float | None = None
        self._bar_index = -1
        self._day_bucket: int | None = None
        self._day_high = -math.inf
        self._day_low = math.inf
        self._prior_days: Deque[tuple[float, float]] = deque(maxlen=3)
        self._building: dict[str, tuple[float, float]] = {}
        self._ranges: dict[tuple[int, str], SessionRange] = {}
        self._auction: Auction | None = None
        self._scenario_counter = 0

    @property
    def pools(self) -> tuple[SessionRange, ...]:
        return tuple(self._ranges.values())

    def _emit(self, scenario_id: str, event_type: str, ts_ns: int, next_state: str,
              reason: str, price: float | None = None, details: dict[str, Any] | None = None,
              event_time_ns: int | None = None) -> None:
        previous = self._states.get(scenario_id, "NONE")
        self.events.append(ResearchEvent(
            scenario_id=scenario_id,
            instrument_id=self.instrument_id,
            event_type=event_type,
            event_time_ns=ts_ns if event_time_ns is None else event_time_ns,
            observed_time_ns=ts_ns,
            previous_state=previous,
            next_state=next_state,
            reason_code=reason,
            reference_price=None if price is None else f"{price:.12f}".rstrip("0").rstrip("."),
            details=details or {},
        ))
        self._states[scenario_id] = next_state

    def _atr(self) -> float | None:
        return None if len(self._true_ranges) < self.config.atr_period else sum(self._true_ranges) / len(self._true_ranges)

    def _relative_volume(self, volume: float) -> float:
        if len(self._volumes) < max(8, self.config.volume_period // 3):
            return 1.0
        base = median(self._volumes)
        return volume / base if base > 0 else 1.0

    def _finalize_five(self) -> FiveBar:
        p = self._minute_parts
        return FiveBar(p[-1].ts_ns, p[0].open, max(x.high for x in p), min(x.low for x in p), p[-1].close,
                       sum(x.volume for x in p), sum(x.taker_buy_volume for x in p))

    @staticmethod
    def _minute_of_day(ts_ns: int) -> int:
        return int(((ts_ns - 1) % NS_DAY) // NS_MINUTE) + 1

    def _roll_day(self, bar: FiveBar) -> None:
        day = (bar.ts_ns - 1) // NS_DAY
        if self._day_bucket is None:
            self._day_bucket = day
        elif day != self._day_bucket:
            if math.isfinite(self._day_high) and math.isfinite(self._day_low):
                self._prior_days.append((self._day_high, self._day_low))
            self._day_bucket = day
            self._day_high, self._day_low = -math.inf, math.inf
            self._building.clear()
            if self._auction is not None:
                self._terminate(self._auction, bar.ts_ns, "DAY_ROLLOVER")
        self._day_high = max(self._day_high, bar.high)
        self._day_low = min(self._day_low, bar.low)

    def _update_ranges(self, bar: FiveBar, atr: float) -> None:
        day = (bar.ts_ns - 1) // NS_DAY
        minute = self._minute_of_day(bar.ts_ns)
        specs = (
            ("ASIA", 0, 360, 360, 720),
            ("LONDON", 360, 600, 720, 1080),
        )
        for label, build_start, build_end, trade_start, trade_end in specs:
            key = (day, label)
            if build_start < minute <= build_end:
                high, low = self._building.get(label, (-math.inf, math.inf))
                self._building[label] = (max(high, bar.high), min(low, bar.low))
            if minute == build_end and key not in self._ranges and label in self._building:
                high, low = self._building[label]
                rng = SessionRange(label, day, high, low, bar.ts_ns)
                self._ranges[key] = rng
                self.pool_counts[f"{label}_RANGE"] += 1
                sid = f"{self.instrument_id}-{day}-{label}"
                self._emit(sid, "SESSION_RANGE_FROZEN", bar.ts_ns, "RANGE_FROZEN", "COMPLETED_SESSION_RANGE",
                           details={"label": label, "high": high, "low": low, "width_atr": rng.width / atr})
            if minute == trade_start + self.config.bar_minutes and key in self._ranges:
                if self._auction is not None:
                    self._terminate(self._auction, bar.ts_ns, "NEXT_AUCTION_WINDOW")
                source = self._ranges[key]
                self._scenario_counter += 1
                sid = f"{self.instrument_id}-SA-{self._scenario_counter:06d}"
                q = (bar.open - source.low) / source.width if source.width > 0 else 0.5
                if q < 0:
                    preferred = Side.LOW
                elif q > 1:
                    preferred = Side.HIGH
                elif q <= self.config.edge_open_fraction:
                    preferred = Side.LOW
                elif q >= 1.0 - self.config.edge_open_fraction:
                    preferred = Side.HIGH
                else:
                    preferred = Side.HIGH if q < 0.5 else Side.LOW
                self._auction = Auction(sid, source, trade_start, trade_end, bar.open, preferred_side=preferred)
                self._emit(sid, "AUCTION_WINDOW_OPENED", bar.ts_ns, "WATCH", "NEXT_ACTIVITY_WINDOW_OPEN",
                           details={"source": label, "window_open": bar.open, "open_location": q, "preferred_side": preferred.value,
                                    "range_width_atr": source.width / atr})
                self._classify_window_open(self._auction, bar, atr)

    def _recent_outside_closes(self, source: SessionRange, side: Side) -> int:
        count = 0
        for item in reversed(self._bars):
            if item.ts_ns <= source.observed_ts_ns:
                break
            outside = item.close > source.high if side is Side.HIGH else item.close < source.low
            if not outside:
                break
            count += 1
            if count >= max(self.config.acceptance_closes, 4):
                break
        return count

    def _set_accepted(self, a: Auction, bar: FiveBar, atr: float, side: Side, reason: str) -> None:
        a.side = side
        a.direction = Direction.LONG if side is Side.HIGH else Direction.SHORT
        a.extreme = bar.high if side is Side.HIGH else bar.low
        a.displacement_close = bar.close
        a.displacement_extreme = a.extreme
        midpoint = 0.5 * (bar.open + bar.close)
        if side is Side.HIGH:
            a.retest_level = max(a.source.high, midpoint)
        else:
            a.retest_level = min(a.source.low, midpoint)
        a.phase = "ACCEPTED"
        a.phase_index = self._bar_index
        self.scenario_counts[ScenarioKind.ACCEPTED_AUCTION.value] += 1
        level = a.source.high if side is Side.HIGH else a.source.low
        excursion = (bar.close - level) / atr if side is Side.HIGH else (level - bar.close) / atr
        self._emit(a.scenario_id, "RANGE_ACCEPTANCE_CONFIRMED", bar.ts_ns, "WAIT_RETEST", reason,
                   level, {"side": side.value, "direction": a.direction.value,
                           "excursion_atr": excursion, "retest_level": a.retest_level})

    def _set_failed_reentry(self, a: Auction, bar: FiveBar, atr: float, side: Side, reason: str) -> None:
        a.side = side
        a.direction = Direction.SHORT if side is Side.HIGH else Direction.LONG
        a.extreme = bar.high if side is Side.HIGH else bar.low
        prior = list(self._bars)[-self.config.mss_lookback_bars-1:-1]
        if not prior:
            return
        a.structure = min(x.low for x in prior) if a.direction is Direction.SHORT else max(x.high for x in prior)
        a.phase = "RECLAIMED"
        a.phase_index = self._bar_index
        self.scenario_counts[ScenarioKind.FAILED_AUCTION.value] += 1
        level = a.source.high if side is Side.HIGH else a.source.low
        self._emit(a.scenario_id, "FAILED_AUCTION_RECLAIMED", bar.ts_ns, "WAIT_MSS", reason,
                   level, {"side": side.value, "direction": a.direction.value,
                           "extreme": a.extreme, "structure": a.structure})

    def _classify_window_open(self, a: Auction, bar: FiveBar, atr: float) -> None:
        source = a.source
        above_open = a.window_open > source.high
        below_open = a.window_open < source.low
        if not (above_open or below_open):
            return
        side = Side.HIGH if above_open else Side.LOW
        level = source.high if side is Side.HIGH else source.low
        back_inside = bar.close <= level if side is Side.HIGH else bar.close >= level
        if back_inside:
            self._set_failed_reentry(a, bar, atr, side, "PREWINDOW_BREAK_REENTERED_RANGE")
            return
        closes = self._recent_outside_closes(source, side)
        compressed = source.width / atr <= (self.config.max_asia_range_atr if source.label == "ASIA" else self.config.max_london_range_atr)
        directional = bar.close > bar.open if side is Side.HIGH else bar.close < bar.open
        flow_ok = bar.signed_flow >= self.config.mss_flow_min if side is Side.HIGH else bar.signed_flow <= -self.config.mss_flow_min
        excursion = (bar.close - level) / atr if side is Side.HIGH else (level - bar.close) / atr
        if closes >= self.config.acceptance_closes and compressed and excursion >= self.config.acceptance_excursion_atr and (directional or flow_ok):
            self._set_accepted(a, bar, atr, side, "PREWINDOW_SUSTAINED_ACCEPTANCE")
        else:
            # The level was already crossed before this decision window.  It may
            # become a failed auction on re-entry or a sustained acceptance, but
            # it is not a fresh in-window sweep.
            if side is Side.HIGH:
                a.outside_closes_high = closes
                a.crossed_high = True
            else:
                a.outside_closes_low = closes
                a.crossed_low = True

    def _targets(self, direction: Direction, entry: float, auction: Auction, atr: float) -> list[tuple[str, float]]:
        levels: list[tuple[str, float]] = []
        source = auction.source
        levels += [(f"{source.label}_HIGH", source.high), (f"{source.label}_LOW", source.low)]
        # Measured range expansion is an auction objective, not a fitted fixed R target.
        levels += [(f"{source.label}_UP_EXPANSION", source.high + source.width),
                   (f"{source.label}_DOWN_EXPANSION", source.low - source.width)]
        for i, (high, low) in enumerate(reversed(self._prior_days), start=1):
            levels += [(f"PRIOR_DAY_{i}_HIGH", high), (f"PRIOR_DAY_{i}_LOW", low)]
        for (day, label), rng in self._ranges.items():
            if day == source.day_bucket:
                levels += [(f"{label}_HIGH", rng.high), (f"{label}_LOW", rng.low)]
        if direction is Direction.LONG:
            valid = [(n, p) for n, p in levels if p > entry and p - entry <= self.config.max_target_atr * atr]
            valid.sort(key=lambda x: x[1])
        else:
            valid = [(n, p) for n, p in levels if p < entry and entry - p <= self.config.max_target_atr * atr]
            valid.sort(key=lambda x: -x[1])
        # Remove near-duplicate prices while preserving nearest-first order.
        out: list[tuple[str, float]] = []
        for item in valid:
            if not any(abs(item[1] - existing[1]) <= self.config.price_increment for existing in out):
                out.append(item)
        return out

    def _make_plan(self, auction: Auction, bar: FiveBar, atr: float, stop_anchor: float) -> TradePlan | None:
        assert auction.direction is not None and auction.side is not None
        entry = bar.close
        if auction.direction is Direction.LONG:
            stop = min(stop_anchor, entry - self.config.min_stop_atr * atr) - self.config.stop_buffer_atr * atr
        else:
            stop = max(stop_anchor, entry + self.config.min_stop_atr * atr) + self.config.stop_buffer_atr * atr
        stop_distance = abs(entry - stop)
        if stop_distance > self.config.max_stop_atr * atr or stop <= 0:
            self.skips["STOP_TOO_WIDE"] += 1
            return None
        entry_cost = entry * self.config.effective_taker_rate
        stop_cost = stop * self.config.effective_taker_rate
        slip = self.config.tick_slippage_units * self.config.price_increment
        loss = stop_distance + entry_cost + stop_cost + slip
        for target_name, target in self._targets(auction.direction, entry, auction, atr):
            target_cost = target * self.config.effective_maker_rate
            profit = abs(target - entry) - entry_cost - target_cost - slip
            if profit <= 0:
                continue
            net_r = profit / loss
            if net_r >= self.config.min_net_r:
                return TradePlan(
                    auction.scenario_id,
                    ScenarioKind.FAILED_AUCTION if auction.phase.startswith("PULLBACK_FAIL") else ScenarioKind.ACCEPTED_AUCTION,
                    auction.direction,
                    bar.ts_ns,
                    entry,
                    stop,
                    target,
                    loss,
                    profit,
                    net_r,
                    {"source_session": auction.source.label, "source_high": auction.source.high, "source_low": auction.source.low,
                     "source_side": auction.side.value, "target_name": target_name, "atr": atr,
                     "window_open": auction.window_open, "stop_distance": stop_distance,
                     "entry_flow": bar.signed_flow, "entry_relative_volume": self._relative_volume(bar.volume)},
                )
        self.skips["NO_COSTED_STRUCTURAL_TARGET"] += 1
        return None

    def _preferred_cross_allowed(self, auction: Auction, side: Side) -> bool:
        open_ = auction.window_open
        source = auction.source
        if open_ > source.high:
            return side is Side.HIGH
        if open_ < source.low:
            return side is Side.LOW
        return side is auction.preferred_side

    def _watch(self, a: Auction, bar: FiveBar, atr: float) -> None:
        source = a.source
        high_cross = bar.high >= source.high + self.config.sweep_min_atr * atr
        low_cross = bar.low <= source.low - self.config.sweep_min_atr * atr
        opened_above = a.window_open > source.high
        opened_below = a.window_open < source.low
        a.crossed_high = a.crossed_high or high_cross
        a.crossed_low = a.crossed_low or low_cross

        # A pre-window break is classified by sustained acceptance or re-entry,
        # never as a fresh violent sweep at the first decision bar.
        if opened_above and bar.close <= source.high:
            self._set_failed_reentry(a, bar, atr, Side.HIGH, "PREWINDOW_BREAK_REENTERED_RANGE")
            return
        if opened_below and bar.close >= source.low:
            self._set_failed_reentry(a, bar, atr, Side.LOW, "PREWINDOW_BREAK_REENTERED_RANGE")
            return

        for side, crossed in ((Side.HIGH, high_cross), (Side.LOW, low_cross)):
            prewindow_same_side = (side is Side.HIGH and opened_above) or (side is Side.LOW and opened_below)
            if prewindow_same_side or not crossed or not self._preferred_cross_allowed(a, side):
                continue
            level = source.high if side is Side.HIGH else source.low
            penetration = (bar.high - level) / atr if side is Side.HIGH else (level - bar.low) / atr
            if penetration > self.config.sweep_max_atr:
                self._terminate(a, bar.ts_ns, "VIOLENT_CROSS")
                return
            inside = bar.close <= level - self.config.reclaim_buffer_atr * atr if side is Side.HIGH else bar.close >= level + self.config.reclaim_buffer_atr * atr
            close_ok = bar.close_location <= self.config.reclaim_close_location if side is Side.HIGH else bar.close_location >= 1.0 - self.config.reclaim_close_location
            if inside and close_ok:
                a.side = side
                a.direction = Direction.SHORT if side is Side.HIGH else Direction.LONG
                a.extreme = bar.high if side is Side.HIGH else bar.low
                prior = list(self._bars)[-self.config.mss_lookback_bars-1:-1]
                if not prior:
                    return
                a.structure = min(x.low for x in prior) if a.direction is Direction.SHORT else max(x.high for x in prior)
                a.phase = "RECLAIMED"
                a.phase_index = self._bar_index
                self.scenario_counts[ScenarioKind.FAILED_AUCTION.value] += 1
                self._emit(a.scenario_id, "FAILED_AUCTION_RECLAIMED", bar.ts_ns, "WAIT_MSS", "SWEEP_CLOSED_BACK_INSIDE",
                           level, {"side": side.value, "direction": a.direction.value, "extreme": a.extreme, "structure": a.structure})
                return

        if not (opened_above or opened_below) and a.crossed_high and a.crossed_low:
            self._terminate(a, bar.ts_ns, "DUAL_SIDED_RANGE_CLEAR")
            return

        # Acceptance classification uses closes, not just wicks.
        if bar.close > source.high:
            a.outside_closes_high += 1; a.outside_closes_low = 0
        elif bar.close < source.low:
            a.outside_closes_low += 1; a.outside_closes_high = 0
        else:
            a.outside_closes_high = a.outside_closes_low = 0
        compressed = source.width / atr <= (self.config.max_asia_range_atr if source.label == "ASIA" else self.config.max_london_range_atr)
        for side, closes in ((Side.HIGH, a.outside_closes_high), (Side.LOW, a.outside_closes_low)):
            if closes < self.config.acceptance_closes or not compressed:
                continue
            level = source.high if side is Side.HIGH else source.low
            excursion = (bar.close - level) / atr if side is Side.HIGH else (level - bar.close) / atr
            directional = bar.close > bar.open if side is Side.HIGH else bar.close < bar.open
            flow_ok = bar.signed_flow >= self.config.mss_flow_min if side is Side.HIGH else bar.signed_flow <= -self.config.mss_flow_min
            if excursion >= self.config.acceptance_excursion_atr and directional and bar.body >= self.config.acceptance_body_atr * atr and flow_ok:
                self._set_accepted(a, bar, atr, side, "MULTI_CLOSE_DISPLACEMENT_OUTSIDE")
                return

    def _advance_reclaimed(self, a: Auction, bar: FiveBar, atr: float) -> None:
        assert a.direction is not None and a.structure is not None and a.extreme is not None
        if a.direction is Direction.SHORT:
            a.extreme = max(a.extreme, bar.high)
            invalid = bar.close > a.source.high + self.config.reclaim_buffer_atr * atr
            confirmed = bar.close < a.structure and bar.close < bar.open and bar.body >= self.config.mss_body_atr * atr and bar.signed_flow <= -self.config.mss_flow_min
        else:
            a.extreme = min(a.extreme, bar.low)
            invalid = bar.close < a.source.low - self.config.reclaim_buffer_atr * atr
            confirmed = bar.close > a.structure and bar.close > bar.open and bar.body >= self.config.mss_body_atr * atr and bar.signed_flow >= self.config.mss_flow_min
        if invalid:
            side = Side.HIGH if a.direction is Direction.SHORT else Side.LOW
            level = a.source.high if side is Side.HIGH else a.source.low
            outside = bar.close > level if side is Side.HIGH else bar.close < level
            directional = bar.close > bar.open if side is Side.HIGH else bar.close < bar.open
            if outside and directional and bar.body >= self.config.acceptance_body_atr * atr:
                self._set_accepted(a, bar, atr, side, "FAILED_RECLAIM_BECAME_ACCEPTED_AUCTION")
            else:
                self._terminate(a, bar.ts_ns, "RECLAIM_FAILED")
            return
        if confirmed:
            a.displacement_close = bar.close
            a.displacement_extreme = bar.low if a.direction is Direction.SHORT else bar.high
            a.phase = "PULLBACK_FAIL_WAIT"
            a.phase_index = self._bar_index
            self._emit(a.scenario_id, "MSS_DISPLACEMENT_CONFIRMED", bar.ts_ns, "WAIT_PULLBACK", "INTERNAL_STRUCTURE_BROKEN_AWAY_FROM_SWEEP",
                       a.structure, {"direction": a.direction.value, "flow": bar.signed_flow, "body_atr": bar.body / atr})

    def _advance_failed_pullback(self, a: Auction, bar: FiveBar, atr: float, allow_entry: bool) -> TradePlan | None:
        assert a.direction is not None and a.displacement_close is not None and a.extreme is not None
        age = self._bar_index - a.phase_index
        if age > self.config.pullback_expiry_bars:
            self._terminate(a, bar.ts_ns, "PULLBACK_WINDOW_EXPIRED")
            return None
        if a.direction is Direction.SHORT:
            retraced = bar.high >= a.displacement_close + self.config.pullback_min_atr * atr
            invalid = bar.high >= a.extreme
            if retraced:
                a.pullback_seen = True
                a.pullback_extreme = bar.high if a.pullback_extreme is None else max(a.pullback_extreme, bar.high)
            reaccelerated = a.pullback_seen and bar.close < bar.open and bar.body >= self.config.reacceleration_body_atr * atr and bar.signed_flow <= -self.config.reacceleration_flow_min
        else:
            retraced = bar.low <= a.displacement_close - self.config.pullback_min_atr * atr
            invalid = bar.low <= a.extreme
            if retraced:
                a.pullback_seen = True
                a.pullback_extreme = bar.low if a.pullback_extreme is None else min(a.pullback_extreme, bar.low)
            reaccelerated = a.pullback_seen and bar.close > bar.open and bar.body >= self.config.reacceleration_body_atr * atr and bar.signed_flow >= self.config.reacceleration_flow_min
        if invalid:
            self._terminate(a, bar.ts_ns, "SWEEP_EXTREME_INVALIDATED")
            return None
        if reaccelerated and a.pullback_extreme is not None:
            self._emit(a.scenario_id, "PULLBACK_REACCELERATION_CONFIRMED", bar.ts_ns, "ENTRY_READY", "DISPLACEMENT_RETRACE_HELD",
                       bar.close, {"pullback_extreme": a.pullback_extreme, "direction": a.direction.value})
            plan = self._make_plan(a, bar, atr, a.pullback_extreme)
            return self._finish_plan(a, bar.ts_ns, plan, allow_entry)
        return None

    def _advance_accepted(self, a: Auction, bar: FiveBar, atr: float, allow_entry: bool) -> TradePlan | None:
        assert a.direction is not None and a.side is not None
        age = self._bar_index - a.phase_index
        if age > self.config.retest_expiry_bars:
            self._terminate(a, bar.ts_ns, "ACCEPTANCE_RETEST_EXPIRED")
            return None
        level = a.source.high if a.side is Side.HIGH else a.source.low
        retest_level = a.retest_level if a.retest_level is not None else level
        if a.direction is Direction.LONG:
            invalid = bar.close < level - self.config.reclaim_buffer_atr * atr
            touched = bar.low <= retest_level + self.config.retest_tolerance_atr * atr
            held = bar.close >= level
            if touched and held:
                a.pullback_seen = True
                a.pullback_extreme = bar.low if a.pullback_extreme is None else min(a.pullback_extreme, bar.low)
            reaccelerated = a.pullback_seen and bar.close > bar.open and bar.body >= self.config.reacceleration_body_atr * atr and bar.signed_flow >= self.config.reacceleration_flow_min
        else:
            invalid = bar.close > level + self.config.reclaim_buffer_atr * atr
            touched = bar.high >= retest_level - self.config.retest_tolerance_atr * atr
            held = bar.close <= level
            if touched and held:
                a.pullback_seen = True
                a.pullback_extreme = bar.high if a.pullback_extreme is None else max(a.pullback_extreme, bar.high)
            reaccelerated = a.pullback_seen and bar.close < bar.open and bar.body >= self.config.reacceleration_body_atr * atr and bar.signed_flow <= -self.config.reacceleration_flow_min
        if invalid:
            self._terminate(a, bar.ts_ns, "ACCEPTANCE_FAILED_BACK_INSIDE")
            return None
        if reaccelerated and a.pullback_extreme is not None:
            self._emit(a.scenario_id, "ACCEPTED_BOUNDARY_REACCELERATION", bar.ts_ns, "ENTRY_READY", "BOUNDARY_RETEST_HELD",
                       level, {"pullback_extreme": a.pullback_extreme, "direction": a.direction.value,
                               "retest_level": retest_level})
            plan = self._make_plan(a, bar, atr, a.pullback_extreme)
            return self._finish_plan(a, bar.ts_ns, plan, allow_entry)
        return None

    def _finish_plan(self, a: Auction, ts_ns: int, plan: TradePlan | None, allow_entry: bool) -> TradePlan | None:
        if plan is None:
            self._terminate(a, ts_ns, "COSTED_PLAN_REJECTED")
            return None
        if not allow_entry:
            self.skips["GLOBAL_SLOT_OCCUPIED"] += 1
            self._emit(a.scenario_id, "TRADE_PLAN_REJECTED", ts_ns, "TERMINAL", "GLOBAL_SLOT_OCCUPIED", plan.expected_entry)
            self._auction = None
            return None
        self._emit(a.scenario_id, "TRADE_PLAN_EMITTED", ts_ns, "PLAN_EMITTED", "CAUSAL_SESSION_AUCTION_VALID",
                   plan.expected_entry, {"scenario": plan.scenario.value, "direction": plan.direction.value,
                                         "entry": plan.expected_entry, "stop": plan.stop_price,
                                         "target": plan.target_price, "net_r": plan.net_r})
        self._auction = None
        return plan

    def _terminate(self, a: Auction, ts_ns: int, reason: str) -> None:
        self.skips[reason] += 1
        self._emit(a.scenario_id, "SCENARIO_TERMINAL", ts_ns, "TERMINAL", reason,
                   details={"source": a.source.label, "phase": a.phase})
        if self._auction is a:
            self._auction = None

    def _process_five(self, bar: FiveBar, allow_entry: bool) -> TradePlan | None:
        self._bar_index += 1
        self._roll_day(bar)
        if self._previous_close is not None:
            tr = max(bar.high - bar.low, abs(bar.high - self._previous_close), abs(bar.low - self._previous_close))
            self._true_ranges.append(tr)
        atr = self._atr()
        self._bars.append(bar)
        minute = self._minute_of_day(bar.ts_ns)
        if atr is not None and atr > 0:
            self._update_ranges(bar, atr)
            a = self._auction
            if a is not None:
                if minute > a.trade_end_minute:
                    self._terminate(a, bar.ts_ns, "AUCTION_WINDOW_EXPIRED")
                elif a.phase == "WATCH":
                    self._watch(a, bar, atr)
                elif a.phase == "RECLAIMED":
                    self._advance_reclaimed(a, bar, atr)
                elif a.phase == "PULLBACK_FAIL_WAIT":
                    plan = self._advance_failed_pullback(a, bar, atr, allow_entry)
                    if plan is not None:
                        self._volumes.append(bar.volume); self._previous_close = bar.close
                        return plan
                elif a.phase == "ACCEPTED":
                    plan = self._advance_accepted(a, bar, atr, allow_entry)
                    if plan is not None:
                        self._volumes.append(bar.volume); self._previous_close = bar.close
                        return plan
        self._volumes.append(bar.volume)
        self._previous_close = bar.close
        return None

    def on_bar(self, bar: BarObs, *, allow_entry: bool = True) -> TradePlan | None:
        bucket = (bar.ts_ns - 1) // (self.config.bar_minutes * NS_MINUTE)
        if self._minute_bucket is None:
            self._minute_bucket = bucket
        if bucket != self._minute_bucket:
            completed = self._finalize_five()
            self._minute_parts = [bar]
            self._minute_bucket = bucket
            return self._process_five(completed, allow_entry)
        self._minute_parts.append(bar)
        return None

    def mark_plan_rejected(self, plan: TradePlan, ts_ns: int, reason: str, details: dict[str, Any] | None = None) -> None:
        self.skips[reason] += 1
        self._emit(plan.scenario_id, "TRADE_PLAN_REJECTED", ts_ns, "TERMINAL", reason, plan.expected_entry, details)

    def mark_plan_submitted(self, plan: TradePlan, ts_ns: int, details: dict[str, Any]) -> None:
        self._emit(plan.scenario_id, "TRADE_PLAN_SUBMITTED", ts_ns, "SUBMITTED", "NAUTILUS_ORDER_LIST_SUBMITTED", plan.expected_entry, details)

    def mark_trade_terminal(self, plan: TradePlan, ts_ns: int, reason: str, details: dict[str, Any] | None = None) -> None:
        self._emit(plan.scenario_id, "TRADE_TERMINAL", ts_ns, "TERMINAL", reason, plan.expected_entry, details)


__all__ = ["BarObs", "CausalLiquidityAuctionEngine", "Direction", "LogicConfig", "RiskSizer", "TradePlan"]

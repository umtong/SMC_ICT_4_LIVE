"""Pure, causal domain logic for candidate-easychart v3.

The source makes an important distinction between a setup becoming armed and a
trade becoming executable.  An EasyChart order block is known at the engulfing
candle close, but its first target is the high/low of the impulse wave that
exists *when the first retrace reaches the zone*.  Therefore the gross RR gate
belongs at entry time, not necessarily at setup-formation time.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from enum import Enum
import math
from typing import Iterable


class Side(int, Enum):
    LONG = 1
    SHORT = -1


class TargetMode(str, Enum):
    FIXED_STRUCTURE = "FIXED_STRUCTURE"
    IMPULSE_EXTREME = "IMPULSE_EXTREME"


@dataclass(frozen=True, slots=True)
class Candle:
    ts_open_ns: int
    ts_close_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        values = (self.open, self.high, self.low, self.close, self.volume)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("candle values must be finite")
        if self.ts_close_ns < self.ts_open_ns:
            raise ValueError("close timestamp precedes open timestamp")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("invalid OHLC geometry")

    @property
    def body_low(self) -> float:
        return min(self.open, self.close)

    @property
    def body_high(self) -> float:
        return max(self.open, self.close)

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open


@dataclass(frozen=True, slots=True)
class EasyChartOrderBlock:
    side: Side
    observed_time_ns: int
    zone_low: float
    zone_high: float
    formation_low: float
    formation_high: float
    body_ratio: float
    previous_body: float
    current_body: float

    @property
    def proximal(self) -> float:
        return self.zone_high if self.side is Side.LONG else self.zone_low

    @property
    def distal(self) -> float:
        return self.zone_low if self.side is Side.LONG else self.zone_high


def detect_easychart_order_block(previous: Candle, current: Candle) -> EasyChartOrderBlock | None:
    """Detect the source-defined body-engulfing order block.

    The engulfed previous body is the zone.  It is observable only after the
    current candle closes.  Body equality is accepted; zero-body candles are
    excluded so a doji cannot manufacture an infinite ratio.
    """
    if previous.body <= 0.0 or current.body <= 0.0:
        return None
    if previous.bearish and current.bullish:
        if current.body_low <= previous.body_low and current.body_high >= previous.body_high:
            return EasyChartOrderBlock(
                side=Side.LONG,
                observed_time_ns=current.ts_close_ns,
                zone_low=previous.body_low,
                zone_high=previous.body_high,
                formation_low=min(previous.low, current.low),
                formation_high=max(previous.high, current.high),
                body_ratio=current.body / previous.body,
                previous_body=previous.body,
                current_body=current.body,
            )
    if previous.bullish and current.bearish:
        if current.body_low <= previous.body_low and current.body_high >= previous.body_high:
            return EasyChartOrderBlock(
                side=Side.SHORT,
                observed_time_ns=current.ts_close_ns,
                zone_low=previous.body_low,
                zone_high=previous.body_high,
                formation_low=min(previous.low, current.low),
                formation_high=max(previous.high, current.high),
                body_ratio=current.body / previous.body,
                previous_body=previous.body,
                current_body=current.body,
            )
    return None


@dataclass(frozen=True, slots=True)
class LiquidityPool:
    pool_id: str
    side: str
    level: float
    event_time_ns: int
    observed_time_ns: int
    timeframe_minutes: int
    strength: int = 1

    def __post_init__(self) -> None:
        if self.side not in {"HIGH", "LOW"}:
            raise ValueError("pool side must be HIGH or LOW")
        if self.observed_time_ns < self.event_time_ns:
            raise ValueError("pool observed before its event")


@dataclass(frozen=True, slots=True)
class ArmedSetup:
    setup_id: str
    causal_event_id: str
    symbol: str
    family: str
    side: Side
    observed_time_ns: int
    entry: float
    stop: float
    target_mode: TargetMode
    initial_target: float
    fixed_target_id: str
    source_pool_id: str
    zone_low: float
    zone_high: float
    formation_extreme: float
    body_ratio: float
    previous_body: float = 0.0
    current_body: float = 0.0
    context_bias: str = "UNRESOLVED"
    source_timeframe_minutes: int = 0

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) for value in (self.entry, self.stop, self.initial_target)):
            raise ValueError("setup prices must be finite")
        if self.side is Side.LONG and not self.stop < self.entry:
            raise ValueError("invalid long setup stop")
        if self.side is Side.SHORT and not self.entry < self.stop:
            raise ValueError("invalid short setup stop")
        if self.target_mode is TargetMode.FIXED_STRUCTURE:
            if self.side is Side.LONG and not self.entry < self.initial_target:
                raise ValueError("invalid long fixed target")
            if self.side is Side.SHORT and not self.initial_target < self.entry:
                raise ValueError("invalid short fixed target")

    def executable(self, target: float, *, target_id: str, min_gross_rr: float = 1.0) -> "TradePlan | None":
        if not math.isfinite(target):
            return None
        if self.side is Side.LONG and not self.entry < target:
            return None
        if self.side is Side.SHORT and not target < self.entry:
            return None
        risk = abs(self.entry - self.stop)
        if risk <= 0.0:
            return None
        rr = abs(target - self.entry) / risk
        if rr < min_gross_rr - 1e-12:
            return None
        return TradePlan(
            plan_id=self.setup_id,
            causal_event_id=self.causal_event_id,
            symbol=self.symbol,
            family=self.family,
            side=self.side,
            observed_time_ns=self.observed_time_ns,
            entry=self.entry,
            stop=self.stop,
            target=target,
            gross_rr=rr,
            source_pool_id=self.source_pool_id,
            target_pool_id=target_id,
            zone_low=self.zone_low,
            zone_high=self.zone_high,
            formation_extreme=self.formation_extreme,
            body_ratio=self.body_ratio,
            previous_body=self.previous_body,
            current_body=self.current_body,
            context_bias=self.context_bias,
            source_timeframe_minutes=self.source_timeframe_minutes,
            target_mode=self.target_mode.value,
        )


@dataclass(frozen=True, slots=True)
class TradePlan:
    plan_id: str
    causal_event_id: str
    symbol: str
    family: str
    side: Side
    observed_time_ns: int
    entry: float
    stop: float
    target: float
    gross_rr: float
    source_pool_id: str
    target_pool_id: str
    zone_low: float
    zone_high: float
    formation_extreme: float
    body_ratio: float
    previous_body: float = 0.0
    current_body: float = 0.0
    context_bias: str = "UNRESOLVED"
    source_timeframe_minutes: int = 0
    target_mode: str = TargetMode.FIXED_STRUCTURE.value

    def __post_init__(self) -> None:
        if self.side is Side.LONG and not self.stop < self.entry < self.target:
            raise ValueError("invalid long geometry")
        if self.side is Side.SHORT and not self.target < self.entry < self.stop:
            raise ValueError("invalid short geometry")
        computed = abs(self.target - self.entry) / abs(self.entry - self.stop)
        if abs(computed - self.gross_rr) > 1e-9:
            raise ValueError("gross RR does not match geometry")
        if self.gross_rr < 1.0 - 1e-12:
            raise ValueError("gross RR must be at least 1.0")


@dataclass(frozen=True, slots=True)
class CostAssumptions:
    entry_fee_bps: float = 5.0
    stop_fee_bps: float = 5.0
    target_fee_bps: float = 2.0
    entry_slippage_bps: float = 0.0
    stop_slippage_bps: float = 2.5
    target_slippage_bps: float = 0.0
    expected_funding_bps: float = 1.0

    def rate(self, bps: float) -> float:
        return bps / 10_000.0


def planned_loss_per_unit(plan: TradePlan, costs: CostAssumptions) -> float:
    price_loss = abs(plan.entry - plan.stop)
    entry_cost = plan.entry * costs.rate(costs.entry_fee_bps + costs.entry_slippage_bps)
    stop_cost = plan.stop * costs.rate(costs.stop_fee_bps + costs.stop_slippage_bps)
    funding_buffer = plan.entry * costs.rate(abs(costs.expected_funding_bps))
    return price_loss + entry_cost + stop_cost + funding_buffer


def floor_to_increment(value: float, increment: str | float) -> float:
    inc = Decimal(str(increment))
    if inc <= 0:
        raise ValueError("increment must be positive")
    units = (Decimal(str(value)) / inc).to_integral_value(rounding=ROUND_DOWN)
    return float(units * inc)


def size_for_fixed_risk(
    *,
    nav: float,
    risk_fraction: float,
    plan: TradePlan,
    costs: CostAssumptions,
    size_increment: str | float,
) -> tuple[float, float, float]:
    if not math.isfinite(nav) or nav <= 0.0:
        raise ValueError("NAV must be positive")
    if abs(risk_fraction - 0.03) > 1e-12:
        raise ValueError("candidate-easychart risk fraction is fixed at 3%")
    per_unit = planned_loss_per_unit(plan, costs)
    if not math.isfinite(per_unit) or per_unit <= 0.0:
        raise ValueError("invalid planned loss per unit")
    budget = nav * risk_fraction
    quantity = floor_to_increment(budget / per_unit, size_increment)
    planned = quantity * per_unit
    if planned > budget + max(1e-8, budget * 1e-12):
        raise AssertionError("rounded quantity exceeds risk budget")
    return quantity, per_unit, planned


def nearest_directional_pool(
    *, side: Side, entry: float, pools: Iterable[LiquidityPool]
) -> LiquidityPool | None:
    directional = [
        pool
        for pool in pools
        if (side is Side.LONG and pool.side == "HIGH" and pool.level > entry)
        or (side is Side.SHORT and pool.side == "LOW" and pool.level < entry)
    ]
    directional.sort(key=lambda pool: abs(pool.level - entry))
    return directional[0] if directional else None

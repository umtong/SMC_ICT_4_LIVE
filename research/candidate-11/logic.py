"""Candidate 11c: Session-Framed Causal Draw--Auction Model (SCDAM).

This module intentionally contains no backtest loop. It maps causal external
liquidity, resolves a draw on liquidity, and emits an executable plan only after
an auction scenario completes in the required order. NautilusTrader remains the
sole owner of clocks, orders, fills, fees, margin, positions, and NAV.
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

MINUTE_NS = 60_000_000_000

try:
    from smc_ict_4.contracts import ResearchEvent
except ImportError:  # local detector diagnostics; production image provides the contract
    @dataclass(frozen=True, slots=True)
    class ResearchEvent:  # type: ignore[no-redef]
        scenario_id: str
        instrument_id: str
        event_type: str
        event_time_ns: int
        observed_time_ns: int
        previous_state: str
        next_state: str
        reason_code: str
        reference_price: str | None = None
        details: dict[str, Any] = field(default_factory=dict)


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
    # Existing names are retained so frozen config files remain loadable.
    atr_period: int = 30
    volume_period: int = 120
    pivot_wing: int = 3  # deprecated for external pools; retained for compatibility
    pivot_expiry_bars: int = 1440
    daily_expiry_bars: int = 10080
    max_pools_per_side: int = 80
    sweep_min_atr: float = 0.05
    sweep_max_atr: float = 2.50
    min_relative_volume: float = 1.00
    event_expiry_bars: int = 240  # one completed four-hour auction episode
    retrace_expiry_bars: int = 12
    internal_lookback: int = 12
    rejection_wick_min: float = 0.20
    rejection_reclaim_atr: float = 0.05
    absorption_flow_min: float = 0.08
    displacement_body_atr: float = 0.20
    displacement_flow_min: float = 0.03
    acceptance_close_atr: float = 0.08
    acceptance_close_location: float = 0.60
    acceptance_flow_min: float = 0.06
    acceptance_hold_atr: float = 0.02
    acceptance_retest_atr: float = 0.18
    stop_buffer_atr: float = 0.08
    min_stop_atr: float = 0.08
    max_stop_atr: float = 1.35  # deprecated: risk sizing, not a width cap, controls exposure
    min_net_r: float = 1.25
    risk_fraction: float = 0.03
    effective_taker_rate: float = 0.0008
    effective_maker_rate: float = 0.0004

    # Candidate 11b structural controls.
    internal_tf_bars: int = 5
    external_tf_bars: int = 240
    internal_pivot_wing: int = 1
    acceptance_min_closes: int = 2
    acceptance_pullback_wing: int = 2
    reacceleration_body_atr: float = 0.18
    reacceleration_flow_min: float = 0.04
    range_expiry_bars: int = 2160  # 36 hours of one-minute bars
    daily_range_expiry_bars: int = 7200  # five days
    pool_merge_atr: float = 0.12
    target_obstacle_atr: float = 0.20
    context_flow_bars: int = 60
    context_momentum_bars: int = 3
    context_alignment_min: float = 0.15
    draw_dominance_min: float = 0.15

    def __post_init__(self) -> None:
        if self.atr_period < 2 or self.volume_period < 2:
            raise ValueError("invalid rolling periods")
        if self.internal_tf_bars < 2 or self.external_tf_bars <= self.internal_tf_bars:
            raise ValueError("invalid structural timeframes")
        if self.internal_pivot_wing < 1 or self.acceptance_pullback_wing < 1:
            raise ValueError("invalid causal pivot wings")
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
    range_id: str | None = None
    opposite_level: float | None = None
    strength: int = 1
    external: bool = True
    range_close_location: float = 0.5
    range_signed_flow: float = 0.0
    triggerable: bool = True
    trigger_start_ts_ns: int = 0
    trigger_end_ts_ns: int = (1 << 63) - 1


@dataclass(slots=True)
class StructuralBar:
    start_ts_ns: int
    end_ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float
    high_ts_ns: int
    low_ts_ns: int

    @property
    def signed_flow(self) -> float:
        if self.volume <= 0:
            return 0.0
        return max(-1.0, min(1.0, 2.0 * self.taker_buy_volume / self.volume - 1.0))

    @property
    def span(self) -> float:
        return max(self.high - self.low, 1e-12)


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
    state: str = "OBSERVE"
    elapsed: int = 0
    held_outside: int = 0
    scenario: Scenario | None = None
    direction: Direction | None = None
    retrace_level: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    reclaim_seen: bool = False
    outside_streak: int = 0
    pullback_candidate_index: int | None = None
    pullback_extreme: float | None = None
    pullback_known_index: int | None = None
    acceptance_impulse_extreme: float | None = None
    displacement_index: int | None = None
    zone_low: float | None = None
    zone_high: float | None = None
    draw_side: Side | None = None
    draw_score: float = 0.0
    acceptance_invalidated: bool = False
    framed_draw_side: Side | None = None
    framed_target_pool_id: str | None = None
    framed_target_level: float | None = None
    continuation_target_pool_id: str | None = None
    continuation_target_level: float | None = None
    reversal_target_pool_id: str | None = None
    reversal_target_level: float | None = None
    source_draw_side: Side | None = None
    source_draw_score: float = 0.0
    framed_draw_score: float = 0.0
    framed_draw_method: str = "UNRESOLVED"
    framed_high_hazard: float = 0.0
    framed_low_hazard: float = 0.0
    crossed_pool_ids: list[str] = field(default_factory=list)
    last_crossed_level: float | None = None
    cascade_count: int = 0


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
    expire_ts_ns: int
    entry_order_type: str = "LIMIT"
    entry_post_only: bool = True
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
    """Exact NAV-risk sizing; actual margin infeasibility rejects, never clips."""

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
        feasible, reason = True, "OK"
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


class _TimeAggregator:
    def __init__(self, period_bars: int) -> None:
        self.period_ns = period_bars * 60_000_000_000
        self.bucket: int | None = None
        self.current: StructuralBar | None = None

    def update(self, bar: BarObs) -> StructuralBar | None:
        # Close timestamps exactly on a boundary belong to the interval ending
        # at that boundary. The -1 keeps them in the completed interval.
        bucket = (bar.ts_ns - 1) // self.period_ns
        completed: StructuralBar | None = None
        if self.bucket is None:
            self.bucket = bucket
            self.current = StructuralBar(
                bar.ts_ns, bar.ts_ns, bar.open, bar.high, bar.low, bar.close,
                bar.volume, bar.taker_buy_volume, bar.ts_ns, bar.ts_ns,
            )
            return None
        if bucket != self.bucket:
            completed = self.current
            self.bucket = bucket
            self.current = StructuralBar(
                bar.ts_ns, bar.ts_ns, bar.open, bar.high, bar.low, bar.close,
                bar.volume, bar.taker_buy_volume, bar.ts_ns, bar.ts_ns,
            )
            return completed
        assert self.current is not None
        self.current.end_ts_ns = bar.ts_ns
        self.current.close = bar.close
        self.current.volume += bar.volume
        self.current.taker_buy_volume += bar.taker_buy_volume
        if bar.high > self.current.high:
            self.current.high = bar.high
            self.current.high_ts_ns = bar.ts_ns
        if bar.low < self.current.low:
            self.current.low = bar.low
            self.current.low_ts_ns = bar.ts_ns
        return None


class CausalAuctionEngine:
    """External liquidity map, draw resolver, and ordered FAR/AAC scenarios."""

    def __init__(self, config: LogicConfig, instrument_id: str) -> None:
        self.config = config
        self.instrument_id = instrument_id
        self.bars: list[BarObs] = []
        self.true_ranges: deque[float] = deque(maxlen=config.atr_period)
        self.volumes: deque[float] = deque(maxlen=config.volume_period)
        self.pools: list[Pool] = []
        self.internal_bars: list[StructuralBar] = []
        self.context_bars: list[StructuralBar] = []
        self.internal_highs: list[tuple[int, int, float]] = []
        self.internal_lows: list[tuple[int, int, float]] = []
        self.active: Auction | None = None
        self.active_trade_id: str | None = None
        self.active_trade_state: str | None = None
        self.events: list[ResearchEvent] = []
        self.skips: Counter[str] = Counter()
        self._index = -1
        self._pool_seq = 0
        self._internal_agg = _TimeAggregator(config.internal_tf_bars)
        self._context_agg = _TimeAggregator(config.external_tf_bars)
        self._day_agg = _TimeAggregator(1440)

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
                reference_price=None if reference_price is None else format(reference_price, ".10f"),
                details=details or {},
            ),
        )

    def _new_range(self, bar: StructuralBar, source: str, strength: int, expiry_bars: int) -> None:
        if bar.high <= bar.low:
            return
        self._pool_seq += 1
        range_id = f"{self.instrument_id}-{source}-R{self._pool_seq:06d}"
        high_id = f"{range_id}-HIGH"
        low_id = f"{range_id}-LOW"
        close_location = (bar.close - bar.low) / bar.span
        high = Pool(
            scenario_id=high_id,
            side=Side.HIGH,
            level=bar.high,
            source=source,
            candidate_ts_ns=bar.high_ts_ns,
            confirmed_ts_ns=bar.end_ts_ns,
            confirmed_index=self._index,
            expiry_index=self._index + expiry_bars,
            range_id=range_id,
            opposite_level=bar.low,
            strength=strength,
            external=True,
            range_close_location=close_location,
            range_signed_flow=bar.signed_flow,
        )
        low = Pool(
            scenario_id=low_id,
            side=Side.LOW,
            level=bar.low,
            source=source,
            candidate_ts_ns=bar.low_ts_ns,
            confirmed_ts_ns=bar.end_ts_ns,
            confirmed_index=self._index,
            expiry_index=self._index + expiry_bars,
            range_id=range_id,
            opposite_level=bar.high,
            strength=strength,
            external=True,
            range_close_location=close_location,
            range_signed_flow=bar.signed_flow,
        )
        self._merge_or_add(high)
        self._merge_or_add(low)

    def _merge_or_add(self, pool: Pool) -> None:
        atr = self.atr or 0.0
        merge = max(self.config.pool_merge_atr * atr, 1e-9)
        duplicate = next(
            (
                p for p in reversed(self.pools)
                if not p.consumed and p.side == pool.side and abs(p.level - pool.level) <= merge
                and p.strength >= pool.strength
            ),
            None,
        )
        if duplicate is not None:
            duplicate.expiry_index = max(duplicate.expiry_index, pool.expiry_index)
            return
        self.pools.append(pool)
        self._event(
            pool.scenario_id, "EXTERNAL_LIQUIDITY_CONFIRMED", pool.candidate_ts_ns,
            pool.confirmed_ts_ns, "MAP", "ARMED", pool.source, pool.level,
            {
                "side": pool.side.value,
                "range_id": pool.range_id,
                "opposite_level": pool.opposite_level,
                "strength": pool.strength,
                "range_close_location": pool.range_close_location,
                "range_signed_flow": pool.range_signed_flow,
            },
        )
        live = [p for p in self.pools if p.side == pool.side and not p.consumed]
        if len(live) > self.config.max_pools_per_side:
            victim = min(live, key=lambda p: (p.strength, p.confirmed_index))
            victim.consumed = True
            self._event(victim.scenario_id, "POOL_PRUNED", victim.confirmed_ts_ns, pool.confirmed_ts_ns, "ARMED", "TERMINAL", "LOWEST_STRENGTH_OLDEST", victim.level)

    def _update_structure(self, bar: BarObs) -> None:
        internal = self._internal_agg.update(bar)
        if internal is not None:
            self.internal_bars.append(internal)
            self._confirm_internal_pivots(bar.ts_ns)
        context = self._context_agg.update(bar)
        if context is not None:
            self.context_bars.append(context)
            self._new_range(context, "COMPLETED_4H_AUCTION", 2, self.config.range_expiry_bars)
        daily = self._day_agg.update(bar)
        if daily is not None:
            self._new_range(daily, "PREVIOUS_UTC_DAY", 3, self.config.daily_range_expiry_bars)

    def _confirm_internal_pivots(self, observed_ts_ns: int) -> None:
        wing = self.config.internal_pivot_wing
        if len(self.internal_bars) < 2 * wing + 1:
            return
        i = len(self.internal_bars) - 1 - wing
        window = self.internal_bars[i - wing : i + wing + 1]
        center = self.internal_bars[i]
        if center.high == max(b.high for b in window) and sum(b.high == center.high for b in window) == 1:
            self.internal_highs.append((center.high_ts_ns, observed_ts_ns, center.high))
        if center.low == min(b.low for b in window) and sum(b.low == center.low for b in window) == 1:
            self.internal_lows.append((center.low_ts_ns, observed_ts_ns, center.low))

    def _latest_internal(self, side: Side, before_ts_ns: int, after_ts_ns: int | None = None) -> float | None:
        points = self.internal_highs if side == Side.HIGH else self.internal_lows
        max_age = self.config.internal_lookback * self.config.internal_tf_bars * 60_000_000_000
        floor = before_ts_ns - max_age if after_ts_ns is None else max(after_ts_ns, before_ts_ns - max_age)
        valid = [level for _, known, level in points if floor <= known < before_ts_ns]
        return valid[-1] if valid else None

    def _expire_pools(self, ts_ns: int) -> None:
        for pool in self.pools:
            if not pool.consumed and self._index > pool.expiry_index:
                pool.consumed = True
                self._event(pool.scenario_id, "POOL_EXPIRED", pool.confirmed_ts_ns, ts_ns, "ARMED", "TERMINAL", "CAUSAL_EXPIRY", pool.level)

    def _context_alignment(self, side: Side) -> float:
        # Dimensionless sign agreement, not a PnL-fitted score.
        if len(self.context_bars) < self.config.context_momentum_bars:
            momentum = 0.0
        else:
            sample = self.context_bars[-self.config.context_momentum_bars :]
            scale = sum(b.span for b in sample) / len(sample)
            momentum = (sample[-1].close - sample[0].open) / max(scale, 1e-12)
            momentum = max(-1.0, min(1.0, momentum))
        flow_sample = self.bars[-self.config.context_flow_bars :]
        total = sum(b.volume for b in flow_sample)
        flow = sum(b.volume * b.signed_flow for b in flow_sample) / max(total, 1e-12)
        score = 0.65 * momentum + 0.35 * max(-1.0, min(1.0, flow * 4.0))
        return score if side == Side.HIGH else -score

    def _source_range_side_alignment(self, pool: Pool) -> float:
        """Return how strongly the completed source auction accepted ``pool.side``.

        Close location is the primary auction-acceptance observation; aggregate
        aggressor flow is secondary.  The transform is symmetric and
        dimensionless, so the same rule applies to high and low endpoints.
        """
        close_direction = 2.0 * max(0.0, min(1.0, pool.range_close_location)) - 1.0
        flow_direction = max(-1.0, min(1.0, pool.range_signed_flow * 4.0))
        score = 0.75 * close_direction + 0.25 * flow_direction
        return score if pool.side == Side.HIGH else -score

    @staticmethod
    def _liquidity_hazard(pool: Pool, price: float, atr: float) -> float:
        """Spatial draw proxy: stronger and nearer external pools dominate."""
        distance_atr = abs(pool.level - price) / max(atr, 1e-12)
        return float(pool.strength) / max(distance_atr, 0.20)

    def _draw_resolution(
        self,
        price: float,
        atr: float,
        excluded_ids: set[str] | None = None,
    ) -> tuple[Side | None, float, Pool | None, Pool | None, float, float]:
        """Resolve the next external draw before selecting FAR versus AAC.

        Only causally confirmed, live external liquidity is eligible.  On each
        side, the strongest spatial hazard is retained rather than summing
        correlated duplicate levels.  The normalized dominance score supplies
        a reject option when both sides are plausible.
        """
        excluded = excluded_ids or set()
        highs = [
            p for p in self.pools
            if p.scenario_id not in excluded and not p.consumed and p.external
            and p.source != "ROUND_NUMBER" and "SHELF" not in p.source
            and p.side == Side.HIGH and self._index <= p.expiry_index and p.level > price
        ]
        lows = [
            p for p in self.pools
            if p.scenario_id not in excluded and not p.consumed and p.external
            and p.source != "ROUND_NUMBER" and "SHELF" not in p.source
            and p.side == Side.LOW and self._index <= p.expiry_index and p.level < price
        ]
        high_pool = max(highs, key=lambda p: self._liquidity_hazard(p, price, atr), default=None)
        low_pool = max(lows, key=lambda p: self._liquidity_hazard(p, price, atr), default=None)
        high_hazard = 0.0 if high_pool is None else self._liquidity_hazard(high_pool, price, atr)
        low_hazard = 0.0 if low_pool is None else self._liquidity_hazard(low_pool, price, atr)
        total = high_hazard + low_hazard
        if total <= 0:
            return None, 0.0, high_pool, low_pool, high_hazard, low_hazard
        signed = (high_hazard - low_hazard) / total
        if abs(signed) < self.config.draw_dominance_min:
            return None, signed, high_pool, low_pool, high_hazard, low_hazard
        side = Side.HIGH if signed > 0 else Side.LOW
        return side, signed, high_pool, low_pool, high_hazard, low_hazard

    def _resolve_trigger_draw(
        self,
        pool: Pool,
        price: float,
        atr: float,
        excluded_ids: set[str] | None = None,
    ) -> tuple[Side | None, float, Pool | None, Pool | None, float, float, str]:
        """Resolve draw with a paired-range fallback only when the broad map abstains.

        The fallback is not a relaxed hazard threshold.  It uses information already
        fixed when the source range completed: where that auction closed and the
        aggregate aggressive-flow sign.  A low raid of a range which decisively
        accepted its high, for example, inherits the paired high as its draw.
        """
        side, score, high_pool, low_pool, high_hazard, low_hazard = self._draw_resolution(
            price, atr, excluded_ids=excluded_ids,
        )
        if side is not None:
            return side, score, high_pool, low_pool, high_hazard, low_hazard, "EXTERNAL_HAZARD_DOMINANCE"

        paired = self._paired_target_pool(pool)
        alignment = self._source_range_side_alignment(pool)
        if paired is None or abs(alignment) < self.config.context_alignment_min:
            # Final causal fallback: completed higher-timeframe momentum plus the
            # most recent hour of aggregate aggressive flow.  It is used only
            # after the spatial map and source auction both abstain.
            context_high = self._context_alignment(Side.HIGH)
            if abs(context_high) < self.config.context_alignment_min:
                return None, score, high_pool, low_pool, high_hazard, low_hazard, "REJECT_AMBIGUOUS"
            draw_side = Side.HIGH if context_high > 0 else Side.LOW
            target = self._next_pool(draw_side, price, min_strength=1)
            if target is None:
                return None, score, high_pool, low_pool, high_hazard, low_hazard, "REJECT_NO_CONTEXT_TARGET"
            if draw_side == Side.HIGH:
                high_pool = target
            else:
                low_pool = target
            return draw_side, context_high, high_pool, low_pool, high_hazard, low_hazard, "CONTEXT_FLOW_MOMENTUM"

        # Positive alignment accepts the already-consumed trigger side, so the
        # draw must advance to the next live pool on that side.  Negative alignment
        # points to the still-live paired edge.  Never relabel the paired HIGH as a
        # LOW target (or vice versa).
        if alignment > 0:
            draw_side = pool.side
            target = self._next_pool(draw_side, price, min_strength=1)
        else:
            draw_side = paired.side
            target = paired
        if target is None or target.side != draw_side:
            return None, score, high_pool, low_pool, high_hazard, low_hazard, "REJECT_NO_CAUSAL_SOURCE_TARGET"
        signed_score = abs(alignment) if draw_side == Side.HIGH else -abs(alignment)
        if draw_side == Side.HIGH:
            high_pool = target
        else:
            low_pool = target
        return draw_side, signed_score, high_pool, low_pool, high_hazard, low_hazard, "SOURCE_RANGE_ACCEPTANCE"

    def _next_pool(self, side: Side, price: float, min_strength: int = 1) -> Pool | None:
        candidates = [
            p for p in self.pools
            if not p.consumed and p.external and p.strength >= min_strength
            and p.source != "ROUND_NUMBER" and "SHELF" not in p.source
            and ((side == Side.HIGH and p.side == Side.HIGH and p.level > price)
                 or (side == Side.LOW and p.side == Side.LOW and p.level < price))
        ]
        return min(candidates, key=lambda p: abs(p.level - price)) if candidates else None

    def _paired_target_pool(self, pool: Pool) -> Pool | None:
        if pool.range_id is None:
            return None
        opposite = Side.LOW if pool.side == Side.HIGH else Side.HIGH
        counterpart = next(
            (p for p in self.pools if p.range_id == pool.range_id and p.side == opposite),
            None,
        )
        if counterpart is None or counterpart.consumed or self._index > counterpart.expiry_index:
            return None
        return counterpart

    def _far_target_pool(self, pool: Pool, price: float) -> Pool | None:
        """Use the first valid external liquidity on the path to the paired edge."""
        counterpart = self._paired_target_pool(pool)
        if counterpart is None:
            return None
        # Weaker sub-session liquidity is an execution obstacle, not the final
        # draw of a scenario framed by a stronger source range.  Only an equal
        # or stronger pool may replace the paired source edge as the target.
        candidate = self._next_pool(counterpart.side, price, min_strength=pool.strength)
        if candidate is None:
            return counterpart
        if counterpart.side == Side.HIGH:
            return candidate if candidate.level <= counterpart.level else counterpart
        return candidate if candidate.level >= counterpart.level else counterpart

    def _consume_crossed_target_only(self, bar: BarObs, prev: BarObs) -> tuple[list[Pool], list[Pool]]:
        """Retire untriggerable target liquidity on its causal first passage.

        Target-only 4H/day/round levels must not remain magically live after price
        has already traded through them.  During an active auction the cascade
        updater owns this transition; this method is used only before a new trigger.
        """
        highs = [
            p for p in self.pools
            if not p.consumed and p.external and not p.triggerable
            and p.confirmed_index < self._index and p.side == Side.HIGH
            and prev.close <= p.level < bar.high
        ]
        lows = [
            p for p in self.pools
            if not p.consumed and p.external and not p.triggerable
            and p.confirmed_index < self._index and p.side == Side.LOW
            and prev.close >= p.level > bar.low
        ]
        for pool in [*highs, *lows]:
            pool.consumed = True
            self._event(
                pool.scenario_id, "TARGET_LIQUIDITY_CONSUMED", bar.ts_ns, bar.ts_ns,
                "ARMED", "TERMINAL", "CAUSAL_FIRST_PASSAGE", pool.level,
                {"source": pool.source, "side": pool.side.value},
            )
        return highs, lows

    def _detect_sweep(
        self,
        bar: BarObs,
        prev: BarObs,
        atr: float,
        rel_volume: float,
        auxiliary_high: list[Pool] | None = None,
        auxiliary_low: list[Pool] | None = None,
    ) -> None:
        if self.active is not None or self.active_trade_id is not None:
            return
        crossed_high = [
            p for p in self.pools if not p.consumed and p.external and p.triggerable
            and p.confirmed_index < self._index and bar.ts_ns <= p.trigger_end_ts_ns
            and p.side == Side.HIGH and prev.close <= p.level < bar.high
        ]
        crossed_low = [
            p for p in self.pools if not p.consumed and p.external and p.triggerable
            and p.confirmed_index < self._index and bar.ts_ns <= p.trigger_end_ts_ns
            and p.side == Side.LOW and prev.close >= p.level > bar.low
        ]
        auxiliary_high = auxiliary_high or []
        auxiliary_low = auxiliary_low or []
        if (crossed_high and (crossed_low or auxiliary_low)) or (crossed_low and auxiliary_high):
            for p in [*crossed_high, *crossed_low]:
                p.consumed = True
            self.skips["AMBIGUOUS_BOTH_SIDES_SWEPT"] += 1
            self._event(
                "AMBIGUOUS", "AMBIGUOUS_SWEEP", bar.ts_ns, bar.ts_ns,
                "ARMED", "TERMINAL", "BAR_PATH_UNRESOLVABLE", bar.close,
                {"high_pool_count": len(crossed_high) + len(auxiliary_high), "low_pool_count": len(crossed_low) + len(auxiliary_low)},
            )
            return
        crossed = crossed_high or crossed_low
        if not crossed:
            return
        side = Side.HIGH if crossed_high else Side.LOW
        auxiliary = auxiliary_high if side == Side.HIGH else auxiliary_low
        all_crossed = [*crossed, *auxiliary]
        for p in crossed:
            p.consumed = True
        # Stronger levels dominate; deepest level breaks ties.
        if side == Side.HIGH:
            pool = max(crossed, key=lambda p: (p.strength, p.level))
            extreme = bar.high
            internal = self._latest_internal(Side.LOW, bar.ts_ns)
            penetration = (bar.high - pool.level) / atr
        else:
            pool = max(crossed, key=lambda p: (p.strength, -p.level))
            extreme = bar.low
            internal = self._latest_internal(Side.HIGH, bar.ts_ns)
            penetration = (pool.level - bar.low) / atr
        if internal is None:
            self.skips["NO_CAUSAL_INTERNAL_STRUCTURE"] += 1
            return
        if rel_volume < self.config.min_relative_volume or not self.config.sweep_min_atr <= penetration <= self.config.sweep_max_atr:
            self.skips["SWEEP_ACTIVITY_OR_PENETRATION"] += 1
            return
        sweep_flow_ok = bar.signed_flow >= self.config.absorption_flow_min if side == Side.HIGH else bar.signed_flow <= -self.config.absorption_flow_min
        if not sweep_flow_ok:
            self.skips["NO_AGGRESSOR_FLOW_AT_SWEEP"] += 1
            return
        draw_side, draw_score, high_pool, low_pool, high_hazard, low_hazard, draw_method = self._resolve_trigger_draw(
            pool,
            pool.level,
            atr,
            excluded_ids={p.scenario_id for p in all_crossed},
        )
        if draw_side is None:
            self.skips["AMBIGUOUS_EXTERNAL_DRAW"] += 1
            self._event(
                pool.scenario_id, "SWEEP_UNFRAMED", bar.ts_ns, bar.ts_ns,
                "ARMED", "TERMINAL", "AMBIGUOUS_EXTERNAL_DRAW", pool.level,
                {"high_hazard": high_hazard, "low_hazard": low_hazard, "draw_score": draw_score, "draw_method": draw_method},
            )
            return
        target_pool = high_pool if draw_side == Side.HIGH else low_pool
        assert target_pool is not None

        # The external map and the completed source auction are independent
        # priors.  A strong disagreement keeps FAR and AAC alive together; it
        # is not resolved by silently overriding one with the other.
        source_score = self._source_range_side_alignment(pool)
        if abs(source_score) >= self.config.context_alignment_min:
            source_draw_side = pool.side if source_score > 0 else (Side.LOW if pool.side == Side.HIGH else Side.HIGH)
        else:
            source_draw_side = None
        source_target = None
        if source_draw_side is not None:
            if source_draw_side != side:
                source_target = self._far_target_pool(pool, pool.level)
            else:
                source_target = self._next_pool(source_draw_side, pool.level, min_strength=1)

        rejection_seed = draw_side != side or (source_draw_side is not None and source_draw_side != side)
        acceptance_seed = draw_side == side or (source_draw_side is not None and source_draw_side == side)
        reversal_target = source_target if source_draw_side is not None and source_draw_side != side else (target_pool if draw_side != side else None)
        continuation_target = source_target if source_draw_side == side and source_target is not None else (target_pool if draw_side == side else None)

        self.active = Auction(
            pool=pool,
            sweep=bar,
            sweep_index=self._index,
            atr=atr,
            internal_level=internal,
            sweep_extreme=extreme,
            rejection_seed=rejection_seed,
            acceptance_seed=acceptance_seed,
            state="OBSERVE",
            framed_draw_side=draw_side,
            framed_target_pool_id=target_pool.scenario_id,
            framed_target_level=target_pool.level,
            continuation_target_pool_id=(None if continuation_target is None else continuation_target.scenario_id),
            continuation_target_level=(None if continuation_target is None else continuation_target.level),
            reversal_target_pool_id=(None if reversal_target is None else reversal_target.scenario_id),
            reversal_target_level=(None if reversal_target is None else reversal_target.level),
            source_draw_side=source_draw_side,
            source_draw_score=source_score,
            framed_draw_score=draw_score,
            framed_draw_method=draw_method,
            framed_high_hazard=high_hazard,
            framed_low_hazard=low_hazard,
            crossed_pool_ids=[p.scenario_id for p in all_crossed],
            last_crossed_level=(max(p.level for p in all_crossed) if side == Side.HIGH else min(p.level for p in all_crossed)),
            cascade_count=sum(1 for p in all_crossed if p.source != "ROUND_NUMBER"),
        )
        self._event(
            pool.scenario_id, "LIQUIDITY_SWEEP", bar.ts_ns, bar.ts_ns,
            "ARMED", "OBSERVE", "EXTERNAL_RANGE_TRADE_THROUGH", pool.level,
            {
                "side": side.value,
                "penetration_atr": penetration,
                "relative_volume": rel_volume,
                "aggregate_aggressor_flow": bar.signed_flow,
                "crossed_pool_count": len(all_crossed),
                "framed_draw_side": draw_side.value,
                "framed_draw_score": draw_score,
                "draw_method": draw_method,
                "framed_target_pool": target_pool.scenario_id,
                "framed_target_level": target_pool.level,
                "source_draw_side": None if source_draw_side is None else source_draw_side.value,
                "source_draw_score": source_score,
                "rejection_seed": rejection_seed,
                "acceptance_seed": acceptance_seed,
                "reversal_target": None if reversal_target is None else reversal_target.level,
                "continuation_target": None if continuation_target is None else continuation_target.level,
                "high_hazard": high_hazard,
                "low_hazard": low_hazard,
            },
        )

    def _update_cascade_map(self, a: Auction, bar: BarObs) -> None:
        """Consume additional same-side liquidity during one accepted-auction impulse."""
        if a.state != "OBSERVE":
            return
        if a.pool.side == Side.HIGH:
            newly = [
                p for p in self.pools
                if not p.consumed and p.external and p.side == Side.HIGH
                and p.confirmed_index < self._index and a.sweep_extreme < p.level < bar.high
            ]
            newly.sort(key=lambda p: p.level)
        else:
            newly = [
                p for p in self.pools
                if not p.consumed and p.external and p.side == Side.LOW
                and p.confirmed_index < self._index and bar.low < p.level < a.sweep_extreme
            ]
            newly.sort(key=lambda p: p.level, reverse=True)
        for pool in newly:
            pool.consumed = True
            a.crossed_pool_ids.append(pool.scenario_id)
            a.last_crossed_level = pool.level
            if pool.source != "ROUND_NUMBER":
                a.cascade_count += 1
            self._event(
                a.pool.scenario_id, "CASCADE_LIQUIDITY_CONSUMED", a.sweep.ts_ns, bar.ts_ns,
                "OBSERVE", "OBSERVE", "SAME_SIDE_EXTERNAL_LEVEL_CONSUMED", pool.level,
                {"consumed_pool": pool.scenario_id, "cascade_count": a.cascade_count},
            )
        if newly:
            # Reframe the draw after the newly discovered liquidity has actually
            # been consumed.  This is essential for nested pools: taking a nearby
            # premarket low can complete the sell-side draw and expose the paired
            # high as the next auction objective.
            draw_side, draw_score, high_pool, low_pool, high_hazard, low_hazard, draw_method = self._resolve_trigger_draw(
                a.pool,
                a.sweep_extreme,
                a.atr,
                excluded_ids=set(a.crossed_pool_ids),
            )
            if draw_side is not None:
                target = high_pool if draw_side == Side.HIGH else low_pool
                if target is not None and not target.consumed:
                    a.framed_draw_side = draw_side
                    a.framed_draw_score = draw_score
                    a.framed_draw_method = draw_method
                    a.framed_high_hazard = high_hazard
                    a.framed_low_hazard = low_hazard
                    if draw_side == a.pool.side:
                        a.acceptance_seed = True
                        a.continuation_target_pool_id = target.scenario_id
                        a.continuation_target_level = target.level
                    else:
                        # A later nearby-hazard reframe cannot negate a source
                        # auction which strongly accepted the swept side.  When
                        # the source was ambiguous or already opposed the sweep,
                        # the reframe may activate/update FAR.
                        source_same_side = (
                            a.source_draw_side == a.pool.side
                            and abs(a.source_draw_score) >= self.config.context_alignment_min
                        )
                        if not source_same_side:
                            a.rejection_seed = True
                            if a.reversal_target_pool_id is None:
                                a.reversal_target_pool_id = target.scenario_id
                                a.reversal_target_level = target.level
                        a.framed_target_pool_id = target.scenario_id
                        a.framed_target_level = target.level
                    self._event(
                        a.pool.scenario_id, "DRAW_REFRAMED", a.sweep.ts_ns, bar.ts_ns,
                        "OBSERVE", "OBSERVE", draw_method, target.level,
                        {
                            "draw_side": draw_side.value,
                            "draw_score": draw_score,
                            "target_pool": target.scenario_id,
                            "cascade_count": a.cascade_count,
                        },
                    )

    def _update_auction_episode(self, a: Auction, bar: BarObs) -> None:
        """Maintain separate extremes for the competing FAR and AAC hypotheses.

        FAR needs the last raid extreme until a reclaim begins.  AAC freezes the
        impulse extreme only when its causal pullback becomes known.  Extending one
        hypothesis must not overwrite or erase the state of the other.
        """
        if a.state != "OBSERVE":
            return
        self._update_cascade_map(a, bar)
        if a.framed_target_level is not None:
            target_reached = (
                bar.high >= a.framed_target_level
                if a.framed_draw_side == Side.HIGH
                else bar.low <= a.framed_target_level
            )
            if target_reached and a.framed_draw_side != a.pool.side:
                self._terminal(a, bar, "FRAMED_TARGET_REACHED_BEFORE_CONFIRMATION")
                return
        if a.pool.side == Side.HIGH and bar.high > a.sweep_extreme:
            a.sweep_extreme = bar.high
            a.sweep = bar
            a.reclaim_seen = False
            a.acceptance_invalidated = False
            if a.pullback_known_index is None:
                a.outside_streak = 0
                a.pullback_candidate_index = None
                a.pullback_extreme = None
                a.acceptance_impulse_extreme = None
            internal = self._latest_internal(Side.LOW, bar.ts_ns, after_ts_ns=a.pool.confirmed_ts_ns)
            if internal is not None:
                a.internal_level = internal
        elif a.pool.side == Side.LOW and bar.low < a.sweep_extreme:
            a.sweep_extreme = bar.low
            a.sweep = bar
            a.reclaim_seen = False
            a.acceptance_invalidated = False
            if a.pullback_known_index is None:
                a.outside_streak = 0
                a.pullback_candidate_index = None
                a.pullback_extreme = None
                a.acceptance_impulse_extreme = None
            internal = self._latest_internal(Side.HIGH, bar.ts_ns, after_ts_ns=a.pool.confirmed_ts_ns)
            if internal is not None:
                a.internal_level = internal

    @staticmethod
    def _zone_from_displacement(bars: list[BarObs], index: int, direction: Direction) -> tuple[float, float]:
        bar = bars[index]
        if index >= 2:
            two_back = bars[index - 2]
            if direction == Direction.LONG and bar.low > two_back.high:
                return two_back.high, bar.low
            if direction == Direction.SHORT and bar.high < two_back.low:
                return bar.high, two_back.low
        # Low-overlap displacement fallback: the body midpoint is an execution
        # void proxy, not a geometrical FVG label.
        midpoint = (bar.open + bar.close) / 2.0
        half = max(bar.body * 0.10, bar.span * 0.03)
        return midpoint - half, midpoint + half

    def _confirm_far(self, a: Auction, bar: BarObs) -> TradePlan | None:
        if not a.rejection_seed:
            return None
        if bar.ts_ns < a.pool.trigger_start_ts_ns:
            return None
        if bar.ts_ns > a.pool.trigger_end_ts_ns:
            self._terminal(a, bar, "SESSION_DECISION_WINDOW_EXPIRED")
            return None
        side = a.pool.side
        reclaimed = bar.close < a.pool.level if side == Side.HIGH else bar.close > a.pool.level
        if reclaimed and not a.reclaim_seen:
            a.reclaim_seen = True
            post_sweep_side = Side.LOW if side == Side.HIGH else Side.HIGH
            internal = self._latest_internal(post_sweep_side, bar.ts_ns, after_ts_ns=a.sweep.ts_ns)
            if internal is not None:
                a.internal_level = internal
        if not a.reclaim_seen:
            return None
        if side == Side.HIGH:
            mss = bar.close < a.internal_level
            flow = bar.signed_flow <= -self.config.displacement_flow_min
            direction = Direction.SHORT
            draw_side = Side.LOW
            stop = a.sweep_extreme + self.config.stop_buffer_atr * a.atr
        else:
            mss = bar.close > a.internal_level
            flow = bar.signed_flow >= self.config.displacement_flow_min
            direction = Direction.LONG
            draw_side = Side.HIGH
            stop = a.sweep_extreme - self.config.stop_buffer_atr * a.atr
        body = bar.body >= self.config.displacement_body_atr * a.atr
        if not (mss and flow and body):
            return None
        target_pool = next(
            (p for p in self.pools if p.scenario_id == a.reversal_target_pool_id and not p.consumed),
            None,
        )
        if target_pool is None or a.reversal_target_level is None:
            self._terminal(a, bar, "REVERSAL_TARGET_NO_LONGER_LIVE")
            return None
        target = a.reversal_target_level
        if (direction == Direction.LONG and target <= bar.close) or (direction == Direction.SHORT and target >= bar.close):
            self._terminal(a, bar, "PAIRED_TARGET_WRONG_SIDE")
            return None
        a.state = "FAR_CONFIRMED"
        a.scenario = Scenario.FAR
        a.direction = direction
        a.stop_price = stop
        a.target_price = target
        a.draw_side = draw_side
        a.draw_score = 1.0
        a.displacement_index = self._index
        a.zone_low, a.zone_high = self._zone_from_displacement(self.bars, self._index, direction)
        a.elapsed = 0
        self._event(
            a.pool.scenario_id, "FAR_CONFIRMED", a.sweep.ts_ns, bar.ts_ns,
            "OBSERVE", "FAR_CONFIRMED", "RECLAIM_MSS_DISPLACEMENT_TO_PAIRED_DRAW", a.pool.level,
            {
                "internal_level": a.internal_level,
                "draw_side": draw_side.value,
                "target_pool": target_pool.scenario_id,
                "target": target,
                "zone_low": a.zone_low,
                "zone_high": a.zone_high,
                "stop": stop,
                "framed_draw_score": a.framed_draw_score,
                "source_draw_side": None if a.source_draw_side is None else a.source_draw_side.value,
                "source_draw_score": a.source_draw_score,
                "framed_high_hazard": a.framed_high_hazard,
                "framed_low_hazard": a.framed_low_hazard,
            },
        )
        return self._costed_limit_plan(a, bar, "FAR_FIRST_EXECUTION_VOID_LIMIT")

    def _track_aac_pullback(self, a: Auction, bar: BarObs) -> None:
        side = a.pool.side
        boundary = a.last_crossed_level if a.last_crossed_level is not None else a.pool.level
        deep_reentry = (
            bar.close < boundary - self.config.acceptance_retest_atr * a.atr
            if side == Side.HIGH
            else bar.close > boundary + self.config.acceptance_retest_atr * a.atr
        )
        if deep_reentry:
            a.acceptance_invalidated = True
            a.outside_streak = 0
            return
        outside = bar.close >= boundary + self.config.acceptance_hold_atr * a.atr if side == Side.HIGH else bar.close <= boundary - self.config.acceptance_hold_atr * a.atr
        if outside:
            a.outside_streak += 1
        else:
            a.outside_streak = 0
        if a.outside_streak < self.config.acceptance_min_closes:
            return
        wing = self.config.acceptance_pullback_wing
        if len(self.bars) < 2 * wing + 1:
            return
        center_index = self._index - wing
        if center_index <= a.sweep_index:
            return
        window = self.bars[center_index - wing : center_index + wing + 1]
        center = self.bars[center_index]
        if side == Side.HIGH:
            pivot = center.low == min(b.low for b in window) and sum(b.low == center.low for b in window) == 1
            defended = center.low >= boundary - self.config.acceptance_retest_atr * a.atr
            expansion = max(a.sweep_extreme - boundary, 1e-12)
            retention = (center.low - boundary) / expansion
            if pivot and defended and retention >= 0.50:
                a.pullback_candidate_index = center_index
                a.pullback_extreme = center.low
                a.pullback_known_index = self._index
                a.acceptance_impulse_extreme = a.sweep_extreme
        else:
            pivot = center.high == max(b.high for b in window) and sum(b.high == center.high for b in window) == 1
            defended = center.high <= boundary + self.config.acceptance_retest_atr * a.atr
            expansion = max(boundary - a.sweep_extreme, 1e-12)
            retention = (boundary - center.high) / expansion
            if pivot and defended and retention >= 0.50:
                a.pullback_candidate_index = center_index
                a.pullback_extreme = center.high
                a.pullback_known_index = self._index
                a.acceptance_impulse_extreme = a.sweep_extreme

    def _confirm_aac(self, a: Auction, bar: BarObs) -> TradePlan | None:
        if not a.acceptance_seed:
            return None
        if a.cascade_count < 2 and (a.framed_draw_side is None or a.framed_draw_side != a.pool.side):
            return None
        self._track_aac_pullback(a, bar)
        if bar.ts_ns < a.pool.trigger_start_ts_ns:
            return None
        if bar.ts_ns > a.pool.trigger_end_ts_ns:
            self._terminal(a, bar, "SESSION_DECISION_WINDOW_EXPIRED")
            return None
        if a.acceptance_invalidated:
            return None
        if a.pullback_known_index is None or a.acceptance_impulse_extreme is None or self._index <= a.pullback_known_index:
            return None
        side = a.pool.side
        context = self._context_alignment(side)
        if side == Side.HIGH:
            reaccelerated = bar.close > a.acceptance_impulse_extreme
            flow = bar.signed_flow >= self.config.reacceleration_flow_min
            location = bar.close_location >= 0.60
            direction = Direction.LONG
            boundary = a.last_crossed_level if a.last_crossed_level is not None else a.pool.level
            stop = min(float(a.pullback_extreme), boundary) - self.config.stop_buffer_atr * a.atr
            target_id = a.continuation_target_pool_id or a.framed_target_pool_id
            target_level = a.continuation_target_level if a.continuation_target_level is not None else a.framed_target_level
            target_pool = next((p for p in self.pools if p.scenario_id == target_id and not p.consumed), None)
        else:
            reaccelerated = bar.close < a.acceptance_impulse_extreme
            flow = bar.signed_flow <= -self.config.reacceleration_flow_min
            location = bar.close_location <= 0.40
            direction = Direction.SHORT
            boundary = a.last_crossed_level if a.last_crossed_level is not None else a.pool.level
            stop = max(float(a.pullback_extreme), boundary) + self.config.stop_buffer_atr * a.atr
            target_id = a.continuation_target_pool_id or a.framed_target_pool_id
            target_level = a.continuation_target_level if a.continuation_target_level is not None else a.framed_target_level
            target_pool = next((p for p in self.pools if p.scenario_id == target_id and not p.consumed), None)
        body = bar.body >= self.config.reacceleration_body_atr * a.atr
        if not (reaccelerated and flow and location and body):
            return None
        if target_pool is None or target_level is None:
            self._terminal(a, bar, "CONTINUATION_TARGET_NO_LONGER_LIVE")
            return None
        # A source range may define the boundary, but using that same range's
        # close/flow both to create and confirm continuation is circular.
        if a.framed_draw_method != "EXTERNAL_HAZARD_DOMINANCE":
            self._terminal(a, bar, "AAC_REQUIRES_INDEPENDENT_EXTERNAL_DRAW")
            return None
        target_hazard = a.framed_high_hazard if side == Side.HIGH else a.framed_low_hazard
        counter_hazard = a.framed_low_hazard if side == Side.HIGH else a.framed_high_hazard
        a.state = "AAC_CONFIRMED"
        a.scenario = Scenario.AAC
        a.direction = direction
        a.stop_price = stop
        a.target_price = target_level
        a.draw_side = side
        a.draw_score = a.framed_draw_score
        a.displacement_index = self._index
        a.zone_low, a.zone_high = self._zone_from_displacement(self.bars, self._index, direction)
        a.elapsed = 0
        self._event(
            a.pool.scenario_id, "AAC_CONFIRMED", a.sweep.ts_ns, bar.ts_ns,
            "OBSERVE", "AAC_CONFIRMED", "OUTSIDE_HOLD_CAUSAL_PULLBACK_REACCELERATION", a.pool.level,
            {
                "draw_side": side.value,
                "draw_score": context,
                "draw_method": a.framed_draw_method,
                "target_pool": target_pool.scenario_id,
                "target": target_level,
                "target_hazard": target_hazard,
                "counter_hazard": counter_hazard,
                "pullback_extreme": a.pullback_extreme,
                "zone_low": a.zone_low,
                "zone_high": a.zone_high,
                "stop": stop,
            },
        )
        return self._costed_limit_plan(a, bar, "AAC_FIRST_EXECUTION_VOID_LIMIT")

    def _costed_limit_plan(self, a: Auction, confirmation_bar: BarObs, reason: str) -> TradePlan | None:
        """Emit a passive GTD limit plan at confirmation; never infer its fill.

        LONG orders rest at the high edge of the causal execution void; SHORT
        orders rest at the low edge.  These are the nearest favorable prices to
        the displacement close, preserving confirmation while recovering price.
        NautilusTrader alone decides whether the parent fills before expiry.
        """
        assert a.direction is not None and a.scenario is not None
        assert a.stop_price is not None and a.target_price is not None
        assert a.zone_low is not None and a.zone_high is not None
        entry = a.zone_high if a.direction == Direction.LONG else a.zone_low
        stop, target = a.stop_price, a.target_price
        if a.direction == Direction.LONG:
            risk = entry - stop
            gain = target - entry
            passive = entry < confirmation_bar.close
        else:
            risk = stop - entry
            gain = entry - target
            passive = entry > confirmation_bar.close
        if not passive:
            self._terminal(a, confirmation_bar, "LIMIT_NOT_PASSIVE_AT_CONFIRMATION")
            return None
        if risk <= 0 or gain <= 0:
            self._terminal(a, confirmation_bar, "NON_CAUSAL_PRICE_ORDER")
            return None
        if risk / a.atr < self.config.min_stop_atr:
            self._terminal(a, confirmation_bar, "STOP_DISTANCE_BELOW_EXECUTION_FLOOR")
            return None
        # Entry and target are explicitly passive limits; stop is a marketable
        # protective order.  Actual fees remain Nautilus account events.
        loss = risk + entry * self.config.effective_maker_rate + stop * self.config.effective_taker_rate
        net_gain = gain - entry * self.config.effective_maker_rate - target * self.config.effective_maker_rate
        net_r = net_gain / loss
        if net_gain <= 0 or net_r < self.config.min_net_r:
            self._terminal(a, confirmation_bar, "INSUFFICIENT_COSTED_STRUCTURAL_R")
            return None
        expire_ts_ns = confirmation_bar.ts_ns + self.config.retrace_expiry_bars * MINUTE_NS
        plan = TradePlan(
            scenario_id=a.pool.scenario_id,
            scenario=a.scenario,
            direction=a.direction,
            observed_ts_ns=confirmation_bar.ts_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target,
            atr=a.atr,
            loss_per_unit=loss,
            gain_per_unit=net_gain,
            net_r=net_r,
            reason_code=reason,
            expire_ts_ns=expire_ts_ns,
            entry_order_type="LIMIT",
            entry_post_only=True,
            details={
                "pool_level": a.pool.level,
                "pool_source": a.pool.source,
                "range_id": a.pool.range_id,
                "sweep_ts_ns": a.sweep.ts_ns,
                "sweep_extreme": a.sweep_extreme,
                "draw_side": None if a.draw_side is None else a.draw_side.value,
                "draw_score": a.draw_score,
                "draw_method": a.framed_draw_method,
                "zone_low": a.zone_low,
                "zone_high": a.zone_high,
                "confirmation_close": confirmation_bar.close,
                "entry_cost_assumption": "MAKER",
                "entry_expiry_bars": self.config.retrace_expiry_bars,
            },
        )
        self._event(
            a.pool.scenario_id, "TRADE_PLAN_CONFIRMED", a.sweep.ts_ns, confirmation_bar.ts_ns,
            a.state, "PENDING_ENTRY", reason, entry,
            {
                "scenario": a.scenario.value,
                "direction": a.direction.value,
                "entry_order_type": plan.entry_order_type,
                "entry_post_only": plan.entry_post_only,
                "expire_ts_ns": expire_ts_ns,
                "target": target,
                "stop": stop,
                "net_r": net_r,
            },
        )
        a.state = "PENDING_ENTRY"
        return plan

    def _terminal(self, a: Auction, bar: BarObs, reason: str) -> None:
        self._event(a.pool.scenario_id, "AUCTION_TERMINAL", a.sweep.ts_ns, bar.ts_ns, a.state, "TERMINAL", reason, a.pool.level)
        self.skips[reason] += 1
        self.active = None

    def mark_submitted(self, plan: TradePlan, quantity: Decimal, details: dict[str, Any] | None = None) -> None:
        if self.active_trade_id is not None:
            raise RuntimeError("global candidate slot already occupied")
        if self.active is None or self.active.pool.scenario_id != plan.scenario_id:
            raise RuntimeError("submitted plan does not match active auction")
        self._event(
            plan.scenario_id, "ENTRY_ORDER_LIST_SUBMITTED", plan.observed_ts_ns,
            plan.observed_ts_ns, "PENDING_ENTRY", "PENDING_ENTRY", plan.reason_code,
            plan.expected_entry,
            {"scenario": plan.scenario.value, "direction": plan.direction.value, "quantity": str(quantity), "net_r": plan.net_r, **(details or {})},
        )
        self.active_trade_id, self.active_trade_state = plan.scenario_id, "PENDING_ENTRY"
        self.active = None

    def mark_entry_filled(self, ts_ns: int, details: dict[str, Any] | None = None) -> None:
        """Advance the global slot only from a real Nautilus parent fill."""
        if self.active_trade_id is None or self.active_trade_state != "PENDING_ENTRY":
            return
        self._event(
            self.active_trade_id,
            "ENTRY_FILLED",
            ts_ns,
            ts_ns,
            "PENDING_ENTRY",
            "POSITION",
            "NAUTILUS_ORDER_FILLED",
            details=details or {},
        )
        self.active_trade_state = "POSITION"

    def mark_rejected(self, plan: TradePlan, ts_ns: int, reason: str, details: dict[str, Any] | None = None) -> None:
        if self.active is None or self.active.pool.scenario_id != plan.scenario_id:
            return
        previous_state = self.active.state
        self._event(
            plan.scenario_id,
            "ENTRY_PLAN_REJECTED",
            plan.observed_ts_ns,
            ts_ns,
            previous_state,
            "TERMINAL",
            reason,
            plan.expected_entry,
            details or {},
        )
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
        self._update_structure(bar)
        atr, med_volume = self.atr, self.median_volume
        if atr is None or med_volume is None or atr <= 0:
            return None
        self._expire_pools(bar.ts_ns)
        if self.active_trade_id is not None or prev is None:
            return None
        if self.active is None:
            auxiliary_high, auxiliary_low = self._consume_crossed_target_only(bar, prev)
            self._detect_sweep(
                bar, prev, atr, bar.volume / max(med_volume, 1e-12),
                auxiliary_high=auxiliary_high, auxiliary_low=auxiliary_low,
            )
            return None
        a = self.active
        a.elapsed += 1
        self._update_auction_episode(a, bar)
        if self.active is None:
            return None
        if bar.ts_ns > a.pool.trigger_end_ts_ns:
            self._terminal(a, bar, "SESSION_DECISION_WINDOW_EXPIRED")
            return None
        if a.elapsed > self.config.event_expiry_bars and bar.ts_ns >= a.pool.trigger_start_ts_ns:
            self._terminal(a, bar, "COMPETING_HYPOTHESES_UNRESOLVED")
            return None
        plan = self._confirm_far(a, bar)
        if plan is None and self.active is not None:
            plan = self._confirm_aac(a, bar)
        if plan is not None and not allow_entry:
            self.mark_rejected(plan, bar.ts_ns, "OUTSIDE_EVALUATION_WINDOW")
            return None
        return plan

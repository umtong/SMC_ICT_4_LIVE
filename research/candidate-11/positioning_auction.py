"""Positioning-driven local failed-auction scenario for Candidate 11.

This module is a trading-scenario state machine, not a backtest engine.  It
uses completed one-minute bars plus conservatively timestamped Binance USD-M
positioning observations and emits an executable plan only after the following
causal sequence completes:

1. a completed local swing range exposes a confirmed liquidity endpoint;
2. price and open interest build toward that endpoint;
3. aggressive flow actually trades through the endpoint;
4. the endpoint is reclaimed while open interest contracts; and
5. opposite internal structure breaks with displacement.

The local swing detector is deliberately separated from the scenario.  A
pivot/range is never a trade by itself.  NautilusTrader remains the sole owner
of orders, fills, fees, margin, positions, and NAV.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite, log
from statistics import median
from typing import Any

try:
    from .logic import (
        BarObs,
        Direction,
        MINUTE_NS,
        ResearchEvent,
        Scenario,
        Side,
        StructuralBar,
        TradePlan,
    )
except ImportError:  # direct unit-test execution from this directory
    from logic import (
        BarObs,
        Direction,
        MINUTE_NS,
        ResearchEvent,
        Scenario,
        Side,
        StructuralBar,
        TradePlan,
    )


@dataclass(frozen=True, slots=True)
class PositioningObs:
    """One completed, causally visible positioning snapshot."""

    ts_ns: int
    open_interest: float
    open_interest_value: float
    taker_ratio: float | None
    account_ratio: float | None
    top_position_ratio: float | None
    premium_close: float | None

    def __post_init__(self) -> None:
        required = (self.open_interest, self.open_interest_value)
        optional = (
            self.taker_ratio,
            self.account_ratio,
            self.top_position_ratio,
            self.premium_close,
        )
        if self.ts_ns < 0 or not all(isfinite(value) and value >= 0 for value in required):
            raise ValueError("invalid positioning observation")
        if any(value is not None and not isfinite(value) for value in optional):
            raise ValueError("invalid optional positioning observation")
        for ratio in (self.taker_ratio, self.account_ratio, self.top_position_ratio):
            if ratio is not None and ratio <= 0:
                raise ValueError("positioning ratios must be positive when present")


@dataclass(frozen=True, slots=True)
class PositioningAuctionConfig:
    """Structural controls inherited from the existing SCDAM where possible."""

    atr_period: int = 30
    volume_period: int = 120
    structure_tf_bars: int = 5
    pivot_wing: int = 1
    pool_expiry_bars: int = 360
    max_pools_per_side: int = 40
    pool_merge_atr: float = 0.10
    sweep_min_atr: float = 0.05
    sweep_max_atr: float = 2.50
    min_relative_volume: float = 0.85
    sweep_flow_min: float = 0.05
    event_expiry_bars: int = 60
    retrace_expiry_bars: int = 12
    displacement_body_atr: float = 0.20
    displacement_flow_min: float = 0.03
    stop_buffer_atr: float = 0.08
    min_stop_atr: float = 0.08
    min_net_r: float = 1.25
    risk_fraction: float = 0.03
    effective_taker_rate: float = 0.0008
    effective_maker_rate: float = 0.0004
    oi_build_lookback_minutes: int = 30
    positioning_stale_minutes: int = 10

    def __post_init__(self) -> None:
        if self.atr_period < 2 or self.volume_period < 2:
            raise ValueError("invalid rolling periods")
        if self.structure_tf_bars < 2 or self.pivot_wing < 1:
            raise ValueError("invalid structural detector")
        if self.pool_expiry_bars < self.structure_tf_bars:
            raise ValueError("pool expiry is too short")
        if self.event_expiry_bars < 2 or self.retrace_expiry_bars < 1:
            raise ValueError("invalid scenario horizons")
        if not 0 < self.risk_fraction <= 0.03:
            raise ValueError("risk_fraction must be in (0, 0.03]")
        if self.min_net_r <= 0:
            raise ValueError("min_net_r must be positive")
        if self.oi_build_lookback_minutes < 5 or self.positioning_stale_minutes < 5:
            raise ValueError("invalid positioning horizons")


@dataclass(frozen=True, slots=True)
class LocalPivot:
    side: Side
    event_ts_ns: int
    known_ts_ns: int
    level: float


@dataclass(slots=True)
class LocalPool:
    scenario_id: str
    range_id: str
    side: Side
    level: float
    paired_level: float
    candidate_ts_ns: int
    confirmed_ts_ns: int
    confirmed_index: int
    expiry_index: int
    consumed: bool = False


@dataclass(slots=True)
class PositioningAuction:
    pool: LocalPool
    sweep: BarObs
    sweep_index: int
    initial_sweep_ts_ns: int
    atr: float
    internal_level: float
    target_level: float
    sweep_extreme: float
    sweep_open_interest: float
    prior_open_interest: float
    positioning_ts_ns: int
    oi_peak: float
    elapsed: int = 0
    reclaim_seen: bool = False
    state: str = "OBSERVE"
    direction: Direction | None = None
    stop_price: float | None = None
    zone_low: float | None = None
    zone_high: float | None = None


class _TimeAggregator:
    """Completed fixed-time structural bars with close timestamps."""

    def __init__(self, period_bars: int) -> None:
        self.period_ns = period_bars * MINUTE_NS
        self.bucket: int | None = None
        self.current: StructuralBar | None = None

    def update(self, bar: BarObs) -> StructuralBar | None:
        bucket = (bar.ts_ns - 1) // self.period_ns
        if self.bucket is None:
            self.bucket = bucket
            self.current = StructuralBar(
                bar.ts_ns,
                bar.ts_ns,
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
                bar.taker_buy_volume,
                bar.ts_ns,
                bar.ts_ns,
            )
            return None
        if bucket != self.bucket:
            completed = self.current
            self.bucket = bucket
            self.current = StructuralBar(
                bar.ts_ns,
                bar.ts_ns,
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
                bar.taker_buy_volume,
                bar.ts_ns,
                bar.ts_ns,
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


class PositioningUnwindAuctionEngine:
    """Local liquidity raid followed by causal positioning unwind."""

    def __init__(
        self,
        config: PositioningAuctionConfig,
        instrument_id: str,
    ) -> None:
        self.config = config
        self.instrument_id = instrument_id
        self.bars: list[BarObs] = []
        self.true_ranges: deque[float] = deque(maxlen=config.atr_period)
        self.volumes: deque[float] = deque(maxlen=config.volume_period)
        self.positioning: deque[PositioningObs] = deque(maxlen=7 * 24 * 12)
        self.structural_bars: list[StructuralBar] = []
        self.high_pivots: list[LocalPivot] = []
        self.low_pivots: list[LocalPivot] = []
        self.pools: list[LocalPool] = []
        self.active: PositioningAuction | None = None
        self.active_trade_id: str | None = None
        self.active_trade_state: str | None = None
        self.events: list[ResearchEvent] = []
        self.skips: Counter[str] = Counter()
        self._index = -1
        self._range_seq = 0
        self._aggregate = _TimeAggregator(config.structure_tf_bars)

    @property
    def atr(self) -> float | None:
        if len(self.true_ranges) != self.config.atr_period:
            return None
        return sum(self.true_ranges) / len(self.true_ranges)

    @property
    def median_volume(self) -> float | None:
        if len(self.volumes) != self.config.volume_period:
            return None
        return median(self.volumes)

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
                reference_price=(
                    None if reference_price is None else format(reference_price, ".10f")
                ),
                details=details or {},
            ),
        )

    def _observe_positioning(self, observation: PositioningObs | None, bar_ts_ns: int) -> None:
        if observation is None:
            return
        if observation.ts_ns > bar_ts_ns:
            raise ValueError("future positioning observation")
        if self.positioning and observation.ts_ns < self.positioning[-1].ts_ns:
            raise ValueError("positioning observations must be monotonic")
        if not self.positioning or observation.ts_ns > self.positioning[-1].ts_ns:
            self.positioning.append(observation)
        elif observation != self.positioning[-1]:
            raise ValueError("conflicting positioning snapshot timestamp")

    def _latest_positioning(self, ts_ns: int) -> PositioningObs | None:
        stale_ns = self.config.positioning_stale_minutes * MINUTE_NS
        for item in reversed(self.positioning):
            if item.ts_ns <= ts_ns:
                return item if ts_ns - item.ts_ns <= stale_ns else None
        return None

    def _positioning_at_or_before(self, ts_ns: int) -> PositioningObs | None:
        for item in reversed(self.positioning):
            if item.ts_ns <= ts_ns:
                return item
        return None

    def _bar_close_at_or_before(self, ts_ns: int) -> float | None:
        for bar in reversed(self.bars):
            if bar.ts_ns <= ts_ns:
                return bar.close
        return None

    def _confirm_pivots(self, observed_ts_ns: int) -> None:
        wing = self.config.pivot_wing
        if len(self.structural_bars) < 2 * wing + 1:
            return
        center_index = len(self.structural_bars) - 1 - wing
        window = self.structural_bars[center_index - wing : center_index + wing + 1]
        center = self.structural_bars[center_index]
        candidates: list[LocalPivot] = []
        if center.high == max(item.high for item in window) and sum(
            item.high == center.high for item in window
        ) == 1:
            candidates.append(
                LocalPivot(Side.HIGH, center.high_ts_ns, observed_ts_ns, center.high),
            )
        if center.low == min(item.low for item in window) and sum(
            item.low == center.low for item in window
        ) == 1:
            candidates.append(
                LocalPivot(Side.LOW, center.low_ts_ns, observed_ts_ns, center.low),
            )
        for pivot in candidates:
            target_list = self.high_pivots if pivot.side == Side.HIGH else self.low_pivots
            if target_list and target_list[-1].event_ts_ns == pivot.event_ts_ns:
                continue
            target_list.append(pivot)
            opposite_list = self.low_pivots if pivot.side == Side.HIGH else self.high_pivots
            opposite = next(
                (
                    item
                    for item in reversed(opposite_list)
                    if item.known_ts_ns < observed_ts_ns
                    and item.level != pivot.level
                ),
                None,
            )
            if opposite is not None:
                self._new_local_range(pivot, opposite, observed_ts_ns)

    def _new_local_range(
        self,
        pivot: LocalPivot,
        opposite: LocalPivot,
        observed_ts_ns: int,
    ) -> None:
        high_pivot = pivot if pivot.side == Side.HIGH else opposite
        low_pivot = pivot if pivot.side == Side.LOW else opposite
        if high_pivot.level <= low_pivot.level:
            self.skips["NON_CAUSAL_LOCAL_RANGE_ORDER"] += 1
            return
        self._range_seq += 1
        range_id = f"{self.instrument_id}-POSITIONING-R{self._range_seq:06d}"
        high = LocalPool(
            scenario_id=f"{range_id}-HIGH",
            range_id=range_id,
            side=Side.HIGH,
            level=high_pivot.level,
            paired_level=low_pivot.level,
            candidate_ts_ns=high_pivot.event_ts_ns,
            confirmed_ts_ns=observed_ts_ns,
            confirmed_index=self._index,
            expiry_index=self._index + self.config.pool_expiry_bars,
        )
        low = LocalPool(
            scenario_id=f"{range_id}-LOW",
            range_id=range_id,
            side=Side.LOW,
            level=low_pivot.level,
            paired_level=high_pivot.level,
            candidate_ts_ns=low_pivot.event_ts_ns,
            confirmed_ts_ns=observed_ts_ns,
            confirmed_index=self._index,
            expiry_index=self._index + self.config.pool_expiry_bars,
        )
        self._merge_or_add(high)
        self._merge_or_add(low)

    def _merge_or_add(self, pool: LocalPool) -> None:
        atr = self.atr or 0.0
        merge_distance = max(self.config.pool_merge_atr * atr, 1e-12)
        duplicate = next(
            (
                existing
                for existing in reversed(self.pools)
                if not existing.consumed
                and existing.side == pool.side
                and abs(existing.level - pool.level) <= merge_distance
            ),
            None,
        )
        if duplicate is not None:
            duplicate.expiry_index = max(duplicate.expiry_index, pool.expiry_index)
            return
        self.pools.append(pool)
        self._event(
            pool.scenario_id,
            "LOCAL_LIQUIDITY_RANGE_CONFIRMED",
            pool.candidate_ts_ns,
            pool.confirmed_ts_ns,
            "MAP",
            "ARMED",
            "CAUSAL_5M_SWING_RANGE",
            pool.level,
            {
                "side": pool.side.value,
                "range_id": pool.range_id,
                "paired_level": pool.paired_level,
                "expiry_index": pool.expiry_index,
            },
        )
        live = [
            item for item in self.pools if item.side == pool.side and not item.consumed
        ]
        if len(live) > self.config.max_pools_per_side:
            victim = min(live, key=lambda item: (item.expiry_index, item.confirmed_index))
            victim.consumed = True
            self._event(
                victim.scenario_id,
                "LOCAL_POOL_PRUNED",
                victim.candidate_ts_ns,
                pool.confirmed_ts_ns,
                "ARMED",
                "TERMINAL",
                "OLDEST_LOCAL_LIQUIDITY",
                victim.level,
            )

    def _update_structure(self, bar: BarObs) -> None:
        completed = self._aggregate.update(bar)
        if completed is None:
            return
        self.structural_bars.append(completed)
        self._confirm_pivots(bar.ts_ns)

    def _latest_internal(
        self,
        side: Side,
        before_ts_ns: int,
        after_ts_ns: int | None = None,
    ) -> float | None:
        points = self.high_pivots if side == Side.HIGH else self.low_pivots
        max_age = 12 * self.config.structure_tf_bars * MINUTE_NS
        floor = before_ts_ns - max_age
        if after_ts_ns is not None:
            floor = max(floor, after_ts_ns)
        valid = [
            point.level
            for point in points
            if floor <= point.known_ts_ns < before_ts_ns
        ]
        return valid[-1] if valid else None

    def _expire_pools(self, ts_ns: int) -> None:
        for pool in self.pools:
            if not pool.consumed and self._index > pool.expiry_index:
                pool.consumed = True
                self._event(
                    pool.scenario_id,
                    "LOCAL_POOL_EXPIRED",
                    pool.candidate_ts_ns,
                    ts_ns,
                    "ARMED",
                    "TERMINAL",
                    "CAUSAL_LOCAL_EXPIRY",
                    pool.level,
                )

    @staticmethod
    def _signed_ratio(value: float | None, side_sign: float) -> float | None:
        if value is None or value <= 0:
            return None
        return side_sign * log(value)

    def _crowd_alignment(
        self,
        positioning: PositioningObs,
        side: Side,
    ) -> tuple[bool, dict[str, float | None]]:
        side_sign = 1.0 if side == Side.HIGH else -1.0
        taker = self._signed_ratio(positioning.taker_ratio, side_sign)
        account = self._signed_ratio(positioning.account_ratio, side_sign)
        top = self._signed_ratio(positioning.top_position_ratio, side_sign)
        premium = (
            None
            if positioning.premium_close is None
            else side_sign * positioning.premium_close
        )
        aligned = any(
            value is not None and value > 0.0
            for value in (taker, account, top, premium)
        )
        return aligned, {
            "sweep_direction_taker_log": taker,
            "sweep_direction_account_log": account,
            "sweep_direction_top_position_log": top,
            "sweep_direction_premium": premium,
        }

    def _positioning_build(
        self,
        bar: BarObs,
        side: Side,
    ) -> tuple[bool, PositioningObs | None, PositioningObs | None, dict[str, Any]]:
        current = self._latest_positioning(bar.ts_ns)
        if current is None:
            return False, None, None, {"reason": "MISSING_OR_STALE_POSITIONING"}
        prior_ts = bar.ts_ns - self.config.oi_build_lookback_minutes * MINUTE_NS
        prior = self._positioning_at_or_before(prior_ts)
        prior_close = self._bar_close_at_or_before(prior_ts)
        if prior is None or prior_close is None:
            return False, current, prior, {"reason": "INSUFFICIENT_POSITIONING_BUILD_HISTORY"}
        if current.open_interest <= 0 or prior.open_interest <= 0 or bar.close <= 0 or prior_close <= 0:
            return False, current, prior, {"reason": "INVALID_POSITIONING_BUILD_STATE"}
        side_sign = 1.0 if side == Side.HIGH else -1.0
        oi_change = log(current.open_interest / prior.open_interest)
        price_change = side_sign * log(bar.close / prior_close)
        crowd_aligned, crowd = self._crowd_alignment(current, side)
        passed = oi_change > 0.0 and price_change > 0.0 and crowd_aligned
        return passed, current, prior, {
            "reason": "POSITION_BUILD_CONFIRMED" if passed else "POSITION_BUILD_NOT_CONFIRMED",
            "oi_build_log_change": oi_change,
            "price_build_directional_log_change": price_change,
            "crowd_aligned": crowd_aligned,
            **crowd,
        }

    def _detect_sweep(
        self,
        bar: BarObs,
        prev: BarObs,
        atr: float,
        relative_volume: float,
    ) -> None:
        crossed_high = [
            pool
            for pool in self.pools
            if not pool.consumed
            and pool.side == Side.HIGH
            and pool.confirmed_index < self._index
            and prev.close <= pool.level < bar.high
        ]
        crossed_low = [
            pool
            for pool in self.pools
            if not pool.consumed
            and pool.side == Side.LOW
            and pool.confirmed_index < self._index
            and prev.close >= pool.level > bar.low
        ]
        if crossed_high and crossed_low:
            for pool in [*crossed_high, *crossed_low]:
                pool.consumed = True
            self.skips["AMBIGUOUS_LOCAL_BOTH_SIDES_SWEPT"] += 1
            return
        crossed = crossed_high or crossed_low
        if not crossed:
            return
        side = Side.HIGH if crossed_high else Side.LOW
        pool = (
            max(crossed, key=lambda item: item.level)
            if side == Side.HIGH
            else min(crossed, key=lambda item: item.level)
        )
        for item in crossed:
            item.consumed = True
        penetration = (
            (bar.high - pool.level) / atr
            if side == Side.HIGH
            else (pool.level - bar.low) / atr
        )
        internal_side = Side.LOW if side == Side.HIGH else Side.HIGH
        internal = self._latest_internal(internal_side, bar.ts_ns)
        if internal is None:
            self.skips["POSITIONING_NO_CAUSAL_INTERNAL_STRUCTURE"] += 1
            return
        target = pool.paired_level
        if (side == Side.HIGH and target >= pool.level) or (
            side == Side.LOW and target <= pool.level
        ):
            self.skips["POSITIONING_PAIRED_TARGET_WRONG_SIDE"] += 1
            return
        if relative_volume < self.config.min_relative_volume or not (
            self.config.sweep_min_atr <= penetration <= self.config.sweep_max_atr
        ):
            self.skips["POSITIONING_SWEEP_ACTIVITY_OR_PENETRATION"] += 1
            return
        sweep_direction_flow = bar.signed_flow if side == Side.HIGH else -bar.signed_flow
        if sweep_direction_flow < self.config.sweep_flow_min:
            self.skips["POSITIONING_NO_SWEEP_DIRECTION_AGGRESSION"] += 1
            return
        build, current, prior, build_details = self._positioning_build(bar, side)
        if not build or current is None or prior is None:
            reason = str(build_details.get("reason", "POSITION_BUILD_NOT_CONFIRMED"))
            self.skips[reason] += 1
            self._event(
                pool.scenario_id,
                "POSITIONING_SWEEP_REJECTED",
                bar.ts_ns,
                bar.ts_ns,
                "ARMED",
                "TERMINAL",
                reason,
                pool.level,
                build_details,
            )
            return
        extreme = bar.high if side == Side.HIGH else bar.low
        self.active = PositioningAuction(
            pool=pool,
            sweep=bar,
            sweep_index=self._index,
            initial_sweep_ts_ns=bar.ts_ns,
            atr=atr,
            internal_level=internal,
            target_level=target,
            sweep_extreme=extreme,
            sweep_open_interest=current.open_interest,
            prior_open_interest=prior.open_interest,
            positioning_ts_ns=current.ts_ns,
            oi_peak=current.open_interest,
        )
        self._event(
            pool.scenario_id,
            "POSITIONING_LIQUIDITY_SWEEP",
            bar.ts_ns,
            bar.ts_ns,
            "ARMED",
            "OBSERVE",
            "LOCAL_RANGE_SWEEP_AFTER_POSITION_BUILD",
            pool.level,
            {
                "side": side.value,
                "range_id": pool.range_id,
                "paired_target": target,
                "penetration_atr": penetration,
                "relative_volume": relative_volume,
                "sweep_direction_flow": sweep_direction_flow,
                "sweep_open_interest": current.open_interest,
                "prior_open_interest": prior.open_interest,
                "positioning_ts_ns": current.ts_ns,
                **build_details,
            },
        )

    @staticmethod
    def _zone_from_displacement(
        bars: list[BarObs],
        index: int,
        direction: Direction,
    ) -> tuple[float, float]:
        bar = bars[index]
        if index >= 2:
            two_back = bars[index - 2]
            if direction == Direction.LONG and bar.low > two_back.high:
                return two_back.high, bar.low
            if direction == Direction.SHORT and bar.high < two_back.low:
                return bar.high, two_back.low
        midpoint = (bar.open + bar.close) / 2.0
        half = max(bar.body * 0.10, bar.span * 0.03)
        return midpoint - half, midpoint + half

    def _terminal(self, auction: PositioningAuction, bar: BarObs, reason: str) -> None:
        self._event(
            auction.pool.scenario_id,
            "POSITIONING_AUCTION_TERMINAL",
            auction.initial_sweep_ts_ns,
            bar.ts_ns,
            auction.state,
            "TERMINAL",
            reason,
            auction.pool.level,
        )
        self.skips[reason] += 1
        self.active = None

    def _confirm_far(
        self,
        auction: PositioningAuction,
        bar: BarObs,
    ) -> TradePlan | None:
        side = auction.pool.side
        current_positioning = self._latest_positioning(bar.ts_ns)
        if current_positioning is None:
            return None
        auction.oi_peak = max(auction.oi_peak, current_positioning.open_interest)
        reclaimed = bar.close < auction.pool.level if side == Side.HIGH else bar.close > auction.pool.level
        if reclaimed and not auction.reclaim_seen:
            auction.reclaim_seen = True
            internal_side = Side.LOW if side == Side.HIGH else Side.HIGH
            post_sweep_internal = self._latest_internal(
                internal_side,
                bar.ts_ns,
                after_ts_ns=auction.initial_sweep_ts_ns,
            )
            if post_sweep_internal is not None:
                auction.internal_level = post_sweep_internal
        if not auction.reclaim_seen:
            return None
        if side == Side.HIGH:
            direction = Direction.SHORT
            structure_broken = bar.close < auction.internal_level
            flow_confirmed = bar.signed_flow <= -self.config.displacement_flow_min
            stop = auction.sweep_extreme + self.config.stop_buffer_atr * auction.atr
        else:
            direction = Direction.LONG
            structure_broken = bar.close > auction.internal_level
            flow_confirmed = bar.signed_flow >= self.config.displacement_flow_min
            stop = auction.sweep_extreme - self.config.stop_buffer_atr * auction.atr
        oi_unwind = (
            current_positioning.open_interest < auction.sweep_open_interest
            and current_positioning.open_interest < auction.oi_peak
        )
        body = bar.body >= self.config.displacement_body_atr * auction.atr
        if not (structure_broken and flow_confirmed and oi_unwind and body):
            return None
        target = auction.target_level
        if (direction == Direction.LONG and target <= bar.close) or (
            direction == Direction.SHORT and target >= bar.close
        ):
            self._terminal(auction, bar, "POSITIONING_TARGET_REACHED_OR_WRONG_SIDE")
            return None
        auction.state = "POSITIONING_FAR_CONFIRMED"
        auction.direction = direction
        auction.stop_price = stop
        auction.zone_low, auction.zone_high = self._zone_from_displacement(
            self.bars,
            self._index,
            direction,
        )
        self._event(
            auction.pool.scenario_id,
            "POSITIONING_FAR_CONFIRMED",
            auction.initial_sweep_ts_ns,
            bar.ts_ns,
            "OBSERVE",
            "POSITIONING_FAR_CONFIRMED",
            "RECLAIM_OI_UNWIND_MSS_DISPLACEMENT",
            auction.pool.level,
            {
                "direction": direction.value,
                "internal_level": auction.internal_level,
                "sweep_open_interest": auction.sweep_open_interest,
                "confirmation_open_interest": current_positioning.open_interest,
                "oi_peak": auction.oi_peak,
                "target": target,
                "stop": stop,
                "zone_low": auction.zone_low,
                "zone_high": auction.zone_high,
            },
        )
        return self._costed_limit_plan(auction, bar)

    def _costed_limit_plan(
        self,
        auction: PositioningAuction,
        confirmation_bar: BarObs,
    ) -> TradePlan | None:
        assert auction.direction is not None
        assert auction.stop_price is not None
        assert auction.zone_low is not None and auction.zone_high is not None
        direction = auction.direction
        entry = auction.zone_high if direction == Direction.LONG else auction.zone_low
        stop = auction.stop_price
        target = auction.target_level
        if direction == Direction.LONG:
            risk = entry - stop
            gain = target - entry
            passive = entry < confirmation_bar.close
        else:
            risk = stop - entry
            gain = entry - target
            passive = entry > confirmation_bar.close
        if not passive:
            self._terminal(auction, confirmation_bar, "POSITIONING_LIMIT_NOT_PASSIVE")
            return None
        if risk <= 0 or gain <= 0:
            self._terminal(auction, confirmation_bar, "POSITIONING_NON_CAUSAL_PRICE_ORDER")
            return None
        if risk / auction.atr < self.config.min_stop_atr:
            self._terminal(auction, confirmation_bar, "POSITIONING_STOP_DISTANCE_BELOW_FLOOR")
            return None
        loss = (
            risk
            + entry * self.config.effective_maker_rate
            + stop * self.config.effective_taker_rate
        )
        net_gain = (
            gain
            - entry * self.config.effective_maker_rate
            - target * self.config.effective_maker_rate
        )
        net_r = net_gain / loss
        if net_gain <= 0 or net_r < self.config.min_net_r:
            self._terminal(auction, confirmation_bar, "POSITIONING_INSUFFICIENT_COSTED_R")
            return None
        expire_ts_ns = confirmation_bar.ts_ns + self.config.retrace_expiry_bars * MINUTE_NS
        plan = TradePlan(
            scenario_id=auction.pool.scenario_id,
            scenario=Scenario.FAR,
            direction=direction,
            observed_ts_ns=confirmation_bar.ts_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target,
            atr=auction.atr,
            loss_per_unit=loss,
            gain_per_unit=net_gain,
            net_r=net_r,
            reason_code="POSITIONING_UNWIND_FIRST_EXECUTION_VOID_LIMIT",
            expire_ts_ns=expire_ts_ns,
            entry_order_type="LIMIT",
            entry_post_only=True,
            details={
                "pool_level": auction.pool.level,
                "pool_source": "LOCAL_5M_POSITIONING_RANGE",
                "range_id": auction.pool.range_id,
                "sweep_ts_ns": auction.initial_sweep_ts_ns,
                "sweep_extreme": auction.sweep_extreme,
                "draw_side": (
                    Side.HIGH.value if direction == Direction.LONG else Side.LOW.value
                ),
                "draw_score": 1.0,
                "draw_method": "POSITION_BUILD_AND_UNWIND",
                "zone_low": auction.zone_low,
                "zone_high": auction.zone_high,
                "confirmation_close": confirmation_bar.close,
                "entry_cost_assumption": "MAKER",
                "entry_expiry_bars": self.config.retrace_expiry_bars,
                "sweep_open_interest": auction.sweep_open_interest,
                "prior_open_interest": auction.prior_open_interest,
                "oi_peak": auction.oi_peak,
            },
        )
        self._event(
            auction.pool.scenario_id,
            "TRADE_PLAN_CONFIRMED",
            auction.initial_sweep_ts_ns,
            confirmation_bar.ts_ns,
            auction.state,
            "PENDING_ENTRY",
            plan.reason_code,
            entry,
            {
                "scenario": Scenario.FAR.value,
                "direction": direction.value,
                "entry_order_type": plan.entry_order_type,
                "entry_post_only": plan.entry_post_only,
                "expire_ts_ns": expire_ts_ns,
                "target": target,
                "stop": stop,
                "net_r": net_r,
            },
        )
        auction.state = "PENDING_ENTRY"
        return plan

    def on_bar(
        self,
        bar: BarObs,
        positioning: PositioningObs | None,
        *,
        allow_entry: bool = True,
    ) -> TradePlan | None:
        self._observe_positioning(positioning, bar.ts_ns)
        self._index += 1
        prev = self.bars[-1] if self.bars else None
        true_range = (
            bar.high - bar.low
            if prev is None
            else max(
                bar.high - bar.low,
                abs(bar.high - prev.close),
                abs(bar.low - prev.close),
            )
        )
        self.true_ranges.append(true_range)
        self.volumes.append(bar.volume)
        self.bars.append(bar)
        self._update_structure(bar)
        atr = self.atr
        med_volume = self.median_volume
        if atr is None or med_volume is None or atr <= 0:
            return None
        self._expire_pools(bar.ts_ns)
        if self.active_trade_id is not None or prev is None:
            return None
        if self.active is None:
            self._detect_sweep(
                bar,
                prev,
                atr,
                bar.volume / max(med_volume, 1e-12),
            )
            return None
        auction = self.active
        auction.elapsed += 1
        if auction.pool.side == Side.HIGH and bar.high > auction.sweep_extreme:
            auction.sweep_extreme = bar.high
            auction.sweep = bar
            auction.reclaim_seen = False
        elif auction.pool.side == Side.LOW and bar.low < auction.sweep_extreme:
            auction.sweep_extreme = bar.low
            auction.sweep = bar
            auction.reclaim_seen = False
        if auction.pool.side == Side.HIGH and bar.low <= auction.target_level:
            self._terminal(auction, bar, "POSITIONING_TARGET_REACHED_BEFORE_CONFIRMATION")
            return None
        if auction.pool.side == Side.LOW and bar.high >= auction.target_level:
            self._terminal(auction, bar, "POSITIONING_TARGET_REACHED_BEFORE_CONFIRMATION")
            return None
        if auction.elapsed > self.config.event_expiry_bars:
            self._terminal(auction, bar, "POSITIONING_UNWIND_NOT_CONFIRMED")
            return None
        plan = self._confirm_far(auction, bar)
        if plan is not None and not allow_entry:
            self.mark_rejected(plan, bar.ts_ns, "OUTSIDE_EVALUATION_WINDOW")
            return None
        return plan

    def mark_submitted(
        self,
        plan: TradePlan,
        quantity: Decimal,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self.active_trade_id is not None:
            raise RuntimeError("global candidate slot already occupied")
        if self.active is None or self.active.pool.scenario_id != plan.scenario_id:
            raise RuntimeError("submitted plan does not match active positioning auction")
        self._event(
            plan.scenario_id,
            "ENTRY_ORDER_LIST_SUBMITTED",
            plan.observed_ts_ns,
            plan.observed_ts_ns,
            "PENDING_ENTRY",
            "PENDING_ENTRY",
            plan.reason_code,
            plan.expected_entry,
            {
                "scenario": plan.scenario.value,
                "direction": plan.direction.value,
                "quantity": str(quantity),
                "net_r": plan.net_r,
                **(details or {}),
            },
        )
        self.active_trade_id = plan.scenario_id
        self.active_trade_state = "PENDING_ENTRY"
        self.active = None

    def mark_entry_filled(
        self,
        ts_ns: int,
        details: dict[str, Any] | None = None,
    ) -> None:
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

    def mark_rejected(
        self,
        plan: TradePlan,
        ts_ns: int,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self.active is None or self.active.pool.scenario_id != plan.scenario_id:
            return
        previous = self.active.state
        self._event(
            plan.scenario_id,
            "ENTRY_PLAN_REJECTED",
            plan.observed_ts_ns,
            ts_ns,
            previous,
            "TERMINAL",
            reason,
            plan.expected_entry,
            details or {},
        )
        self.skips[reason] += 1
        self.active = None

    def mark_trade_terminal(
        self,
        ts_ns: int,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self.active_trade_id is None:
            return
        self._event(
            self.active_trade_id,
            "POSITION_TERMINAL",
            ts_ns,
            ts_ns,
            self.active_trade_state or "POSITION",
            "TERMINAL",
            reason,
            details=details or {},
        )
        self.active_trade_id = None
        self.active_trade_state = None

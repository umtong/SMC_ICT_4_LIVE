"""Candidate 09 v2: anchored-auction liquidity transfer state engine.

This module deliberately replaces the v1 local-pivot detector.  Completed fixed
auction blocks create observable dealing ranges.  Their extremes are neutral
external-liquidity locations, not directional signals.  A breach can resolve via:

* aggressive-flow absorption -> range reclaim -> opposite micro-structure shift,
  producing a reversal toward the auction's internal equilibrium; or
* directional pressure -> displacement -> outside acceptance -> defended retest,
  producing continuation toward the next observable/derived range objective.

Only completed one-minute observations are consumed.  The module contains no
execution simulator; orders and account state remain entirely in NautilusTrader.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR
from hashlib import sha256
from math import isfinite
from statistics import median
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
    def signed_flow(self) -> float:
        return 2.0 * self.taker_buy_volume - self.volume

    @property
    def flow_imbalance(self) -> float:
        if self.volume <= 0.0:
            return 0.0
        return max(-1.0, min(1.0, self.signed_flow / self.volume))


@dataclass(frozen=True, slots=True)
class EngineConfig:
    auction_block_minutes: int = 240
    atr_period: int = 20
    volume_period: int = 60
    pressure_period: int = 15
    mss_lookback_bars: int = 6
    maximum_active_ranges: int = 12
    maximum_range_age_blocks: int = 6
    minimum_breach_atr: float = 0.08
    reclaim_buffer_atr: float = 0.03
    acceptance_buffer_atr: float = 0.12
    acceptance_closes: int = 2
    resolution_timeout_bars: int = 12
    retest_timeout_bars: int = 10
    retest_tolerance_atr: float = 0.20
    stop_buffer_atr: float = 0.12
    directional_imbalance: float = 0.10
    cumulative_flow_imbalance: float = 0.04
    minimum_volume_ratio: float = 1.00
    minimum_displacement_atr: float = 0.40
    absorption_max_progress_atr: float = 0.18
    absorption_min_wick_atr: float = 0.25
    minimum_approach_efficiency: float = 0.20
    minimum_mss_displacement_atr: float = 0.35
    minimum_net_reward_to_risk: float = 1.15
    composite_cost_per_fill: float = 0.00075
    cooldown_bars: int = 8
    use_flow_confirmation: bool = True
    require_mss_confirmation: bool = True
    require_acceptance_confirmation: bool = True

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
        *,
        ablation: str = "baseline",
    ) -> "EngineConfig":
        if ablation not in {
            "baseline",
            "no-flow",
            "no-mss-confirmation",
            "no-acceptance-confirmation",
        }:
            raise ValueError(f"unknown ablation: {ablation}")
        structure = payload["structure"]
        breach = payload["breach"]
        flow = payload["flow"]
        trade = payload["trade"]
        risk = payload["risk"]
        return cls(
            auction_block_minutes=int(structure["auction_block_minutes"]),
            atr_period=int(structure["atr_period"]),
            volume_period=int(structure["volume_period"]),
            pressure_period=int(structure["pressure_period"]),
            mss_lookback_bars=int(structure["mss_lookback_bars"]),
            maximum_active_ranges=int(structure["maximum_active_ranges"]),
            maximum_range_age_blocks=int(structure["maximum_range_age_blocks"]),
            minimum_breach_atr=float(breach["minimum_breach_atr"]),
            reclaim_buffer_atr=float(breach["reclaim_buffer_atr"]),
            acceptance_buffer_atr=float(breach["acceptance_buffer_atr"]),
            acceptance_closes=int(breach["acceptance_closes"]),
            resolution_timeout_bars=int(breach["resolution_timeout_bars"]),
            retest_timeout_bars=int(breach["retest_timeout_bars"]),
            retest_tolerance_atr=float(breach["retest_tolerance_atr"]),
            stop_buffer_atr=float(breach["stop_buffer_atr"]),
            directional_imbalance=float(flow["directional_imbalance"]),
            cumulative_flow_imbalance=float(flow["cumulative_flow_imbalance"]),
            minimum_volume_ratio=float(flow["minimum_volume_ratio"]),
            minimum_displacement_atr=float(flow["minimum_displacement_atr"]),
            absorption_max_progress_atr=float(flow["absorption_max_progress_atr"]),
            absorption_min_wick_atr=float(flow["absorption_min_wick_atr"]),
            minimum_approach_efficiency=float(flow["minimum_approach_efficiency"]),
            minimum_mss_displacement_atr=float(flow["minimum_mss_displacement_atr"]),
            minimum_net_reward_to_risk=float(trade["minimum_net_reward_to_risk"]),
            composite_cost_per_fill=float(risk["composite_taker_cost_per_fill"]),
            cooldown_bars=int(trade["cooldown_bars"]),
            use_flow_confirmation=ablation != "no-flow",
            require_mss_confirmation=ablation != "no-mss-confirmation",
            require_acceptance_confirmation=ablation != "no-acceptance-confirmation",
        )


@dataclass(slots=True)
class AuctionRange:
    range_id: str
    block_key: int
    start_ns: int
    end_ns: int
    high: float
    low: float
    midpoint: float
    width: float
    observed_index: int
    high_consumed: bool = False
    low_consumed: bool = False


@dataclass(slots=True)
class _BlockBuilder:
    block_key: int
    start_ns: int
    end_ns: int
    high: float
    low: float
    close: float
    bars: int = 1


@dataclass(slots=True)
class PendingBreach:
    scenario_id: str
    auction: AuctionRange
    direction: str
    state: str
    start_index: int
    pool_price: float
    extreme: float
    micro_break_level: float
    approach_efficiency: float
    approach_flow: float
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
    """Size from current full NAV and complete expected entry-to-stop loss."""
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
    increments = ((budget / per_unit) / quantity_increment).to_integral_value(rounding=ROUND_FLOOR)
    quantity = increments * quantity_increment
    planned = quantity * per_unit
    if quantity <= 0:
        raise ValueError("risk budget is below one exchange quantity increment")
    if planned > budget:
        raise AssertionError("floored sizing exceeded the loss budget")
    return RiskSizing(quantity, budget, per_unit, planned)


class LiquidityStateEngine:
    """Streaming causal state engine for one instrument."""

    def __init__(self, config: EngineConfig):
        if config.auction_block_minutes <= 0:
            raise ValueError("auction_block_minutes must be positive")
        self.config = config
        history_size = max(512, config.volume_period + 8, config.pressure_period + 8)
        self._bars: deque[FlowBar] = deque(maxlen=history_size)
        self._true_ranges: deque[float] = deque(maxlen=config.atr_period)
        self._volumes: deque[float] = deque(maxlen=config.volume_period)
        self._ranges: list[AuctionRange] = []
        self._block: _BlockBuilder | None = None
        self._pending: PendingBreach | None = None
        self._index = -1
        self._cooldown = 0
        self._atr = 0.0
        self._volume_median = 0.0
        self._last_timestamp = -1

    @property
    def active_pools(self) -> tuple[AuctionRange, ...]:
        return tuple(self._ranges)

    @property
    def atr(self) -> float:
        return self._atr

    @property
    def _block_ns(self) -> int:
        return self.config.auction_block_minutes * MINUTE_NS

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
        self._update_auction_block(bar, events)
        self._prune_ranges()

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
        return (
            len(self._bars) >= max(self.config.volume_period, self.config.pressure_period + 1)
            and self._atr > 0.0
            and bool(self._ranges)
        )

    def _update_auction_block(self, bar: FlowBar, events: list[DiagnosticEvent]) -> None:
        key = (bar.ts_ns - 1) // self._block_ns
        start_ns = key * self._block_ns
        end_ns = (key + 1) * self._block_ns
        if self._block is None:
            self._block = _BlockBuilder(key, start_ns, end_ns, bar.high, bar.low, bar.close)
            return
        if key == self._block.block_key:
            self._block.high = max(self._block.high, bar.high)
            self._block.low = min(self._block.low, bar.low)
            self._block.close = bar.close
            self._block.bars += 1
            return
        if key < self._block.block_key:
            raise ValueError("auction block key moved backward")
        completed = self._block
        if completed.high > completed.low and completed.bars >= max(2, self.config.auction_block_minutes // 2):
            identity = sha256(
                f"{completed.block_key}|{completed.high:.10f}|{completed.low:.10f}".encode(),
            ).hexdigest()[:16]
            auction = AuctionRange(
                range_id=identity,
                block_key=completed.block_key,
                start_ns=completed.start_ns,
                end_ns=completed.end_ns,
                high=completed.high,
                low=completed.low,
                midpoint=(completed.high + completed.low) / 2.0,
                width=completed.high - completed.low,
                observed_index=self._index,
            )
            self._ranges.append(auction)
            self._ranges = self._ranges[-self.config.maximum_active_ranges :]
            events.append(
                DiagnosticEvent(
                    scenario_id=f"range-{identity}",
                    event_type="DEALING_RANGE_CONFIRMED",
                    event_time_ns=completed.end_ns,
                    observed_time_ns=bar.ts_ns,
                    previous_state="FORMING",
                    next_state="ARMED",
                    reason_code="COMPLETED_ANCHORED_AUCTION_RANGE",
                    reference_price=auction.midpoint,
                    details={
                        "range_high": auction.high,
                        "range_low": auction.low,
                        "range_width": auction.width,
                        "bars": completed.bars,
                        "auction_block_minutes": self.config.auction_block_minutes,
                    },
                ),
            )
        self._block = _BlockBuilder(key, start_ns, end_ns, bar.high, bar.low, bar.close)

    def _prune_ranges(self) -> None:
        if not self._ranges:
            return
        newest_key = self._block.block_key if self._block is not None else self._ranges[-1].block_key
        minimum_key = newest_key - self.config.maximum_range_age_blocks
        self._ranges = [auction for auction in self._ranges if auction.block_key >= minimum_key]

    def _detect_breach(self, bar: FlowBar, events: list[DiagnosticEvent]) -> None:
        if len(self._bars) < self.config.pressure_period + 1:
            return
        previous = list(self._bars)[-2]
        buffer = self.config.minimum_breach_atr * self._atr
        high_candidates = [
            auction
            for auction in self._ranges
            if not auction.high_consumed
            and previous.close <= auction.high
            and bar.high >= auction.high + buffer
        ]
        low_candidates = [
            auction
            for auction in self._ranges
            if not auction.low_consumed
            and previous.close >= auction.low
            and bar.low <= auction.low - buffer
        ]
        if high_candidates and low_candidates:
            events.append(
                DiagnosticEvent(
                    scenario_id=f"ambiguous-{bar.ts_ns}",
                    event_type="AMBIGUOUS_TWO_SIDED_BREACH",
                    event_time_ns=bar.ts_ns,
                    observed_time_ns=bar.ts_ns,
                    previous_state="IDLE",
                    next_state="NO_TRADE",
                    reason_code="BOTH_EXTERNAL_SIDES_TOUCHED_IN_ONE_OBSERVATION",
                    reference_price=bar.close,
                    details={"high_ranges": len(high_candidates), "low_ranges": len(low_candidates)},
                ),
            )
            return
        if not high_candidates and not low_candidates:
            return

        if high_candidates:
            auction = max(high_candidates, key=lambda item: item.high)
            auction.high_consumed = True
            direction = "UP"
            pool_price = auction.high
            extreme = bar.high
            recent = list(self._bars)[:-1][-self.config.mss_lookback_bars :]
            micro_break = min(item.low for item in recent)
        else:
            auction = min(low_candidates, key=lambda item: item.low)
            auction.low_consumed = True
            direction = "DOWN"
            pool_price = auction.low
            extreme = bar.low
            recent = list(self._bars)[:-1][-self.config.mss_lookback_bars :]
            micro_break = max(item.high for item in recent)

        efficiency, pressure_flow = self._approach_pressure(direction)
        scenario_id = f"auction-{auction.range_id}-{direction.lower()}-{bar.ts_ns}"
        pending = PendingBreach(
            scenario_id=scenario_id,
            auction=auction,
            direction=direction,
            state="BREACHED",
            start_index=self._index,
            pool_price=pool_price,
            extreme=extreme,
            micro_break_level=micro_break,
            approach_efficiency=efficiency,
            approach_flow=pressure_flow,
            absorption_seen=self._is_absorption(bar, direction, pool_price),
            directional_flow_seen=self._directional_flow(bar, direction),
            displacement_seen=self._displacement(bar, direction),
        )
        pending.outside_closes = 1 if self._outside(bar, direction, pool_price) else 0
        self._pending = pending
        events.append(
            self._transition(
                pending,
                event_type="NEUTRAL_LIQUIDITY_BREACH",
                next_state="BREACHED",
                reason_code="ANCHORED_AUCTION_EXTREME_TAKEN",
                bar=bar,
                previous_override="ARMED",
                details=self._details(bar, pending),
            ),
        )

    def _advance_pending(self, bar: FlowBar, events: list[DiagnosticEvent]) -> Signal | None:
        pending = self._pending
        assert pending is not None
        age = self._index - pending.start_index
        pending.extreme = max(pending.extreme, bar.high) if pending.direction == "UP" else min(pending.extreme, bar.low)
        pending.absorption_seen = pending.absorption_seen or self._is_absorption(
            bar,
            pending.direction,
            pending.pool_price,
        )
        pending.directional_flow_seen = pending.directional_flow_seen or self._directional_flow(bar, pending.direction)
        pending.displacement_seen = pending.displacement_seen or self._displacement(bar, pending.direction)

        if self._outside(bar, pending.direction, pending.pool_price):
            pending.outside_closes += 1
        else:
            pending.outside_closes = 0

        if pending.state == "BREACHED" and self._reclaimed(bar, pending.direction, pending.pool_price):
            if pending.absorption_seen and pending.approach_efficiency >= 0.0:
                previous = pending.state
                pending.state = "RECLAIMED"
                pending.reclaim_index = self._index
                events.append(
                    self._transition(
                        pending,
                        event_type="RANGE_RECLAIMED",
                        next_state="RECLAIMED",
                        reason_code="AGGRESSIVE_FLOW_ABSORBED_AND_AUCTION_REACCEPTED",
                        bar=bar,
                        previous_override=previous,
                        details=self._details(bar, pending),
                    ),
                )

        if pending.state == "BREACHED" and self._acceptance_ready(pending, bar):
            previous = pending.state
            pending.state = "ACCEPTED"
            pending.acceptance_index = self._index
            events.append(
                self._transition(
                    pending,
                    event_type="OUTSIDE_ACCEPTANCE",
                    next_state="ACCEPTED",
                    reason_code="DIRECTIONAL_PRESSURE_AND_DISPLACEMENT_ACCEPTED_OUTSIDE_RANGE",
                    bar=bar,
                    previous_override=previous,
                    details=self._details(bar, pending),
                ),
            )

        if pending.state == "RECLAIMED" and pending.reclaim_index is not None:
            if self._index > pending.reclaim_index and self._mss_confirmed(pending, bar):
                return self._finish_signal_or_reject(
                    pending,
                    bar,
                    self._build_signal(pending, bar, branch="REVERSAL"),
                    events,
                )

        if pending.state == "ACCEPTED" and pending.acceptance_index is not None:
            if self._index > pending.acceptance_index and self._continuation_retest(pending, bar):
                return self._finish_signal_or_reject(
                    pending,
                    bar,
                    self._build_signal(pending, bar, branch="CONTINUATION"),
                    events,
                )
            if self._index - pending.acceptance_index > self.config.retest_timeout_bars:
                self._expire(pending, bar, "ACCEPTED_BREAK_DID_NOT_RETEST", events)
                return None

        if age > self.config.resolution_timeout_bars and pending.state != "ACCEPTED":
            self._expire(pending, bar, "BREACH_DID_NOT_RESOLVE_CAUSALLY", events)
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
                    reason_code="COST_OR_OBJECTIVE_MADE_SCENARIO_UNTRADEABLE",
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
        atr = max(self._atr, 1e-12)
        if branch == "REVERSAL" and pending.direction == "UP":
            side = "SELL"
            stop = pending.extreme + self.config.stop_buffer_atr * atr
            candidates = [pending.auction.midpoint, pending.auction.low]
            targets = [value for value in candidates if value < entry]
            target = max(targets) if targets else None
        elif branch == "REVERSAL":
            side = "BUY"
            stop = pending.extreme - self.config.stop_buffer_atr * atr
            candidates = [pending.auction.midpoint, pending.auction.high]
            targets = [value for value in candidates if value > entry]
            target = min(targets) if targets else None
        elif pending.direction == "UP":
            side = "BUY"
            stop = min(pending.pool_price - self.config.stop_buffer_atr * atr, bar.low - self.config.stop_buffer_atr * atr)
            extension = pending.pool_price + pending.auction.width
            observed = [item.high for item in self._ranges if not item.high_consumed and item.high > entry]
            target = min([extension, *observed]) if observed else extension
        else:
            side = "SELL"
            stop = max(pending.pool_price + self.config.stop_buffer_atr * atr, bar.high + self.config.stop_buffer_atr * atr)
            extension = pending.pool_price - pending.auction.width
            observed = [item.low for item in self._ranges if not item.low_consumed and item.low < entry]
            target = max([extension, *observed]) if observed else extension

        if target is None:
            return None
        if side == "BUY" and not stop < entry < target:
            return None
        if side == "SELL" and not target < entry < stop:
            return None

        cost = self.config.composite_cost_per_fill
        price_risk = abs(entry - stop)
        two_fill_cost_at_entry = 2.0 * cost * entry
        # Reject scenarios where realistic transaction cost dominates the actual
        # invalidation distance.  This is a market-structure/economic condition,
        # not a nominal or leverage cap.
        if price_risk < two_fill_cost_at_entry:
            return None
        risk = price_risk + cost * entry + cost * stop
        reward = abs(target - entry) - cost * entry - cost * target
        if risk <= 0.0 or reward <= 0.0:
            return None
        net_rr = reward / risk
        if net_rr < self.config.minimum_net_reward_to_risk:
            return None
        reason = (
            "ABSORPTION_RECLAIM_AND_MICRO_STRUCTURE_SHIFT"
            if branch == "REVERSAL"
            else "OUTSIDE_ACCEPTANCE_AND_DEFENDED_RETEST"
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
                "auction_range_id": pending.auction.range_id,
                "auction_high": pending.auction.high,
                "auction_low": pending.auction.low,
                "auction_midpoint": pending.auction.midpoint,
                "approach_efficiency": pending.approach_efficiency,
                "approach_flow": pending.approach_flow,
                "atr": atr,
            },
        )

    def _acceptance_ready(self, pending: PendingBreach, bar: FlowBar) -> bool:
        required = self.config.acceptance_closes if self.config.require_acceptance_confirmation else 1
        closes_ok = pending.outside_closes >= required
        if not closes_ok:
            return False
        if not self.config.require_acceptance_confirmation:
            return True
        pressure_ok = (
            pending.approach_efficiency >= self.config.minimum_approach_efficiency
            and (
                pending.approach_flow >= self.config.cumulative_flow_imbalance
                if pending.direction == "UP"
                else pending.approach_flow <= -self.config.cumulative_flow_imbalance
            )
        )
        flow_ok = pending.directional_flow_seen if self.config.use_flow_confirmation else True
        return (
            pressure_ok
            and flow_ok
            and pending.displacement_seen
            and self._volume_ratio(bar) >= self.config.minimum_volume_ratio
        )

    def _mss_confirmed(self, pending: PendingBreach, bar: FlowBar) -> bool:
        body = abs(bar.close - bar.open) / max(self._atr, 1e-12)
        if pending.direction == "UP":
            structural = bar.close < pending.micro_break_level and bar.close < bar.open
            flow_ok = bar.flow_imbalance <= -self.config.directional_imbalance / 2.0
        else:
            structural = bar.close > pending.micro_break_level and bar.close > bar.open
            flow_ok = bar.flow_imbalance >= self.config.directional_imbalance / 2.0
        if not self.config.require_mss_confirmation:
            structural = self._reclaimed(bar, pending.direction, pending.pool_price) and (
                bar.close < bar.open if pending.direction == "UP" else bar.close > bar.open
            )
            return structural and (flow_ok or not self.config.use_flow_confirmation)
        return (
            structural
            and body >= self.config.minimum_mss_displacement_atr
            and (flow_ok or not self.config.use_flow_confirmation)
        )

    def _continuation_retest(self, pending: PendingBreach, bar: FlowBar) -> bool:
        tolerance = self.config.retest_tolerance_atr * self._atr
        if pending.direction == "UP":
            location = bar.low <= pending.pool_price + tolerance and bar.close > pending.pool_price
            body_direction = bar.close >= bar.open
            flow_ok = bar.flow_imbalance >= -self.config.directional_imbalance / 2.0
        else:
            location = bar.high >= pending.pool_price - tolerance and bar.close < pending.pool_price
            body_direction = bar.close <= bar.open
            flow_ok = bar.flow_imbalance <= self.config.directional_imbalance / 2.0
        return location and body_direction and (flow_ok or not self.config.use_flow_confirmation)

    def _approach_pressure(self, direction: str) -> tuple[float, float]:
        window = list(self._bars)[:-1][-self.config.pressure_period :]
        if len(window) < 2:
            return 0.0, 0.0
        signed_move = window[-1].close - window[0].close
        path = sum(abs(right.close - left.close) for left, right in zip(window, window[1:]))
        efficiency = signed_move / max(path, 1e-12)
        signed_flow = sum(item.signed_flow for item in window)
        total_volume = sum(item.volume for item in window)
        flow = signed_flow / max(total_volume, 1e-12)
        if direction == "DOWN":
            efficiency = -efficiency
        return efficiency, flow

    def _is_absorption(self, bar: FlowBar, direction: str, pool_price: float) -> bool:
        atr = max(self._atr, 1e-12)
        if direction == "UP":
            progress = max(0.0, bar.close - pool_price) / atr
            wick = (bar.high - max(bar.open, bar.close)) / atr
            flow_ok = bar.flow_imbalance >= self.config.directional_imbalance
        else:
            progress = max(0.0, pool_price - bar.close) / atr
            wick = (min(bar.open, bar.close) - bar.low) / atr
            flow_ok = bar.flow_imbalance <= -self.config.directional_imbalance
        stalled = progress <= self.config.absorption_max_progress_atr or wick >= self.config.absorption_min_wick_atr
        return (
            self._volume_ratio(bar) >= self.config.minimum_volume_ratio
            and stalled
            and (flow_ok or not self.config.use_flow_confirmation)
        )

    def _directional_flow(self, bar: FlowBar, direction: str) -> bool:
        if not self.config.use_flow_confirmation:
            return True
        return (
            bar.flow_imbalance >= self.config.directional_imbalance
            if direction == "UP"
            else bar.flow_imbalance <= -self.config.directional_imbalance
        )

    def _displacement(self, bar: FlowBar, direction: str) -> bool:
        body = abs(bar.close - bar.open) / max(self._atr, 1e-12)
        aligned = bar.close > bar.open if direction == "UP" else bar.close < bar.open
        return aligned and body >= self.config.minimum_displacement_atr

    def _outside(self, bar: FlowBar, direction: str, pool_price: float) -> bool:
        buffer = self.config.acceptance_buffer_atr * self._atr
        return bar.close > pool_price + buffer if direction == "UP" else bar.close < pool_price - buffer

    def _reclaimed(self, bar: FlowBar, direction: str, pool_price: float) -> bool:
        buffer = self.config.reclaim_buffer_atr * self._atr
        return bar.close < pool_price - buffer if direction == "UP" else bar.close > pool_price + buffer

    def _volume_ratio(self, bar: FlowBar) -> float:
        return bar.volume / max(self._volume_median, 1e-12)

    def _details(self, bar: FlowBar, pending: PendingBreach) -> dict[str, float | int | str]:
        return {
            "atr": self._atr,
            "flow_imbalance": bar.flow_imbalance,
            "volume_ratio": self._volume_ratio(bar),
            "pool_price": pending.pool_price,
            "auction_high": pending.auction.high,
            "auction_low": pending.auction.low,
            "auction_midpoint": pending.auction.midpoint,
            "approach_efficiency": pending.approach_efficiency,
            "approach_flow": pending.approach_flow,
            "micro_break_level": pending.micro_break_level,
            "outside_closes": pending.outside_closes,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
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

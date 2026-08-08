"""Candidate 09 v29: causal depth-state router.

A completed-auction breach starts neutral. Continuation requires outside acceptance
while aligned taker aggression meets depletion of opposing one-percent notional depth.
Reversal requires a range reclaim while that aggression persists and opposing depth
replenishes. Depth observations are supplied only after their source minute completes.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN
from math import log
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
    bid_depth: float | None = None
    ask_depth: float | None = None
    bid_notional: float | None = None
    ask_notional: float | None = None
    depth_observed_ns: int | None = None

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
    loss_per_unit = abs(entry_price - stop_price) + (entry_price + stop_price) * cost_rate_per_fill
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
    depth_scale_period: int
    auction_minutes: int
    minimum_breach_atr: float
    acceptance_closes: int
    resolution_bars: int
    minimum_flow: float
    minimum_displacement_atr: float
    minimum_depth_z: float
    stop_buffer_atr: float
    minimum_net_reward_to_risk: float
    cost_per_fill: float
    cooldown_bars: int
    use_depth: bool = True
    use_flow: bool = True
    require_replenishment: bool = True

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
        *,
        ablation: str = "baseline",
    ) -> "EngineConfig":
        allowed = {"baseline", "no-depth", "no-flow", "no-replenishment"}
        if ablation not in allowed:
            raise ValueError(f"unknown ablation: {ablation}")
        logic = payload["logic"]
        return cls(
            atr_period=int(logic["atr_period"]),
            depth_scale_period=int(logic["depth_scale_period"]),
            auction_minutes=int(logic["auction_minutes"]),
            minimum_breach_atr=float(logic["minimum_breach_atr"]),
            acceptance_closes=int(logic["acceptance_closes"]),
            resolution_bars=int(logic["resolution_bars"]),
            minimum_flow=float(logic["minimum_flow"]),
            minimum_displacement_atr=float(logic["minimum_displacement_atr"]),
            minimum_depth_z=float(logic["minimum_depth_z"]),
            stop_buffer_atr=float(logic["stop_buffer_atr"]),
            minimum_net_reward_to_risk=float(
                payload["trade"]["minimum_net_reward_to_risk"]
            ),
            cost_per_fill=float(payload["risk"]["composite_taker_cost_per_fill"]),
            cooldown_bars=int(payload["trade"]["cooldown_bars"]),
            use_depth=ablation != "no-depth",
            use_flow=ablation != "no-flow",
            require_replenishment=ablation != "no-replenishment",
        )


@dataclass(slots=True)
class Auction:
    bucket: int
    high: float
    low: float
    close: float


@dataclass(slots=True)
class Pending:
    scenario_id: str
    direction: int
    boundary: float
    source_high: float
    source_low: float
    extreme: float
    pre_opposing_notional: float | None
    frozen_depth_scale: float | None
    bars: int = 0
    outside_closes: int = 0


class LiquidityStateEngine:
    def __init__(self, config: EngineConfig):
        self.config = config
        self.previous: FlowBar | None = None
        self.true_ranges: deque[float] = deque(maxlen=config.atr_period)
        self.bid_log_changes: deque[float] = deque(maxlen=config.depth_scale_period)
        self.ask_log_changes: deque[float] = deque(maxlen=config.depth_scale_period)
        self.current_auction: Auction | None = None
        self.completed_auctions: deque[Auction] = deque(maxlen=32)
        self.pending: Pending | None = None
        self.scenario_counter = 0
        self.cooldown = 0

    def _event(
        self,
        sid: str,
        bar: FlowBar,
        event_type: str,
        previous: str,
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
            previous,
            next_state,
            reason,
            reference,
            details or {},
        )

    def _atr(self) -> float:
        return sum(self.true_ranges) / len(self.true_ranges) if self.true_ranges else 0.0

    @staticmethod
    def _opposing_notional(bar: FlowBar, direction: int) -> float | None:
        return bar.ask_notional if direction > 0 else bar.bid_notional

    def _depth_scale(self, direction: int) -> float | None:
        values = self.ask_log_changes if direction > 0 else self.bid_log_changes
        if len(values) < max(12, self.config.depth_scale_period // 4):
            return None
        return max(median(abs(value) for value in values), 1e-6)

    def _update_observations(self, bar: FlowBar) -> None:
        if self.previous is None:
            return
        self.true_ranges.append(
            max(
                bar.high - bar.low,
                abs(bar.high - self.previous.close),
                abs(bar.low - self.previous.close),
            )
        )
        for current, prior, target in (
            (bar.bid_notional, self.previous.bid_notional, self.bid_log_changes),
            (bar.ask_notional, self.previous.ask_notional, self.ask_log_changes),
        ):
            if current is not None and prior is not None and current > 0 and prior > 0:
                target.append(log(current / prior))

    def _update_auction(self, bar: FlowBar) -> None:
        bucket = (bar.ts_ns - MINUTE_NS) // (
            self.config.auction_minutes * MINUTE_NS
        )
        if self.current_auction is None:
            self.current_auction = Auction(bucket, bar.high, bar.low, bar.close)
            return
        if bucket != self.current_auction.bucket:
            self.completed_auctions.append(self.current_auction)
            self.current_auction = Auction(bucket, bar.high, bar.low, bar.close)
        else:
            self.current_auction.high = max(self.current_auction.high, bar.high)
            self.current_auction.low = min(self.current_auction.low, bar.low)
            self.current_auction.close = bar.close

    def _build_signal(
        self,
        pending: Pending,
        bar: FlowBar,
        *,
        side: str,
        branch: str,
        stop: float,
        target: float,
        reason: str,
    ) -> Signal | None:
        entry = bar.close
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
            pending.scenario_id,
            branch,
            side,
            bar.ts_ns,
            entry,
            stop,
            target,
            ratio,
            reason,
        )

    def on_bar(self, bar: FlowBar) -> EngineResult:
        events: list[DiagnosticEvent] = []
        self._update_observations(bar)
        self._update_auction(bar)
        atr = self._atr()
        if self.cooldown > 0:
            self.cooldown -= 1

        ready = (
            len(self.true_ranges) >= self.config.atr_period
            and bool(self.completed_auctions)
            and bar.bid_notional is not None
            and bar.ask_notional is not None
        )

        if self.pending is None and self.cooldown == 0 and ready and atr > 0:
            source = self.completed_auctions[-1]
            direction = 0
            if bar.high > source.high + self.config.minimum_breach_atr * atr:
                direction = 1
            elif bar.low < source.low - self.config.minimum_breach_atr * atr:
                direction = -1
            if direction:
                previous = self.previous or bar
                pre_depth = self._opposing_notional(previous, direction)
                depth_scale = self._depth_scale(direction)
                if not self.config.use_depth or (
                    pre_depth is not None and depth_scale is not None
                ):
                    self.scenario_counter += 1
                    sid = f"DEPTH-{bar.ts_ns}-{self.scenario_counter}"
                    boundary = source.high if direction > 0 else source.low
                    extreme = bar.high if direction > 0 else bar.low
                    self.pending = Pending(
                        sid,
                        direction,
                        boundary,
                        source.high,
                        source.low,
                        extreme,
                        pre_depth,
                        depth_scale,
                    )
                    events.append(
                        self._event(
                            sid,
                            bar,
                            "LIQUIDITY_BREACH",
                            "ARMED",
                            "BREACHED",
                            "COMPLETED_AUCTION_EDGE_BREACHED",
                            boundary,
                            {
                                "direction": direction,
                                "pre_opposing_notional": pre_depth,
                                "frozen_depth_scale": depth_scale,
                            },
                        )
                    )

        elif self.pending is not None:
            pending = self.pending
            pending.bars += 1
            pending.extreme = (
                max(pending.extreme, bar.high)
                if pending.direction > 0
                else min(pending.extreme, bar.low)
            )
            outside = (
                bar.close > pending.boundary
                if pending.direction > 0
                else bar.close < pending.boundary
            )
            inside = (
                bar.close < pending.boundary
                if pending.direction > 0
                else bar.close > pending.boundary
            )
            pending.outside_closes = pending.outside_closes + 1 if outside else 0

            aligned_flow = bar.imbalance * pending.direction
            flow_ok = (
                not self.config.use_flow
                or aligned_flow >= self.config.minimum_flow
            )
            current_depth = self._opposing_notional(bar, pending.direction)
            depth_z: float | None = None
            if (
                current_depth is not None
                and current_depth > 0
                and pending.pre_opposing_notional is not None
                and pending.pre_opposing_notional > 0
                and pending.frozen_depth_scale is not None
                and pending.frozen_depth_scale > 0
            ):
                depth_z = log(
                    current_depth / pending.pre_opposing_notional
                ) / pending.frozen_depth_scale

            depleted = (
                not self.config.use_depth
                or depth_z is not None
                and depth_z <= -self.config.minimum_depth_z
            )
            replenished = (
                not self.config.use_depth
                or depth_z is not None
                and depth_z >= self.config.minimum_depth_z
            )
            displacement = (
                abs(bar.close - pending.boundary) / atr if atr > 0 else 0.0
            )

            if inside and flow_ok and (
                replenished or not self.config.require_replenishment
            ):
                side = "SELL" if pending.direction > 0 else "BUY"
                stop = (
                    pending.extreme + self.config.stop_buffer_atr * atr
                    if pending.direction > 0
                    else pending.extreme - self.config.stop_buffer_atr * atr
                )
                target = (pending.source_high + pending.source_low) / 2.0
                signal = self._build_signal(
                    pending,
                    bar,
                    side=side,
                    branch="REVERSAL",
                    stop=stop,
                    target=target,
                    reason="AGGRESSION_ABSORBED_BY_OPPOSING_DEPTH_REPLENISHMENT",
                )
                events.append(
                    self._event(
                        pending.scenario_id,
                        bar,
                        "BREACH_RECLAIMED",
                        "BREACHED",
                        "ENTERABLE" if signal else "NO_TRADE",
                        "DEPTH_REPLENISHED_AND_RANGE_RECLAIMED",
                        pending.boundary,
                        {
                            "aligned_flow": aligned_flow,
                            "depth_z": depth_z,
                            "net_reward_to_risk": (
                                signal.net_reward_to_risk if signal else None
                            ),
                        },
                    )
                )
                self.pending = None
                self.cooldown = self.config.cooldown_bars
                self.previous = bar
                return EngineResult(signal, tuple(events))

            if (
                pending.outside_closes >= self.config.acceptance_closes
                and flow_ok
                and depleted
                and displacement >= self.config.minimum_displacement_atr
            ):
                side = "BUY" if pending.direction > 0 else "SELL"
                stop = (
                    pending.boundary - self.config.stop_buffer_atr * atr
                    if pending.direction > 0
                    else pending.boundary + self.config.stop_buffer_atr * atr
                )
                width = pending.source_high - pending.source_low
                target = pending.boundary + pending.direction * width
                signal = self._build_signal(
                    pending,
                    bar,
                    side=side,
                    branch="CONTINUATION",
                    stop=stop,
                    target=target,
                    reason="AGGRESSION_ACCEPTED_WITH_OPPOSING_DEPTH_DEPLETION",
                )
                events.append(
                    self._event(
                        pending.scenario_id,
                        bar,
                        "OUTSIDE_ACCEPTANCE",
                        "BREACHED",
                        "ENTERABLE" if signal else "NO_TRADE",
                        "DEPTH_DEPLETED_AND_PRICE_ACCEPTED",
                        pending.boundary,
                        {
                            "aligned_flow": aligned_flow,
                            "depth_z": depth_z,
                            "displacement_atr": displacement,
                            "net_reward_to_risk": (
                                signal.net_reward_to_risk if signal else None
                            ),
                        },
                    )
                )
                self.pending = None
                self.cooldown = self.config.cooldown_bars
                self.previous = bar
                return EngineResult(signal, tuple(events))

            if pending.bars >= self.config.resolution_bars:
                events.append(
                    self._event(
                        pending.scenario_id,
                        bar,
                        "SCENARIO_EXPIRED",
                        "BREACHED",
                        "NO_TRADE",
                        "NO_CAUSAL_DEPTH_RESOLUTION_IN_TIME",
                        pending.boundary,
                        {"aligned_flow": aligned_flow, "depth_z": depth_z},
                    )
                )
                self.pending = None
                self.cooldown = self.config.cooldown_bars

        self.previous = bar
        return EngineResult(None, tuple(events))

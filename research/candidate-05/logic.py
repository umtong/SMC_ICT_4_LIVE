"""Pure causal logic for Candidate 05.

This module contains no execution, accounting, fill, or PnL simulation.  It is
kept independent from NautilusTrader so that state predicates and risk algebra
can be unit tested without duplicating a backtest engine.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class Pool:
    pool_id: str
    kind: str  # HIGH or LOW
    level: float
    event_time_ns: int
    observed_time_ns: int
    source: str
    strength: int = 1
    created_index: int = 0


@dataclass(frozen=True, slots=True)
class SweepEvidence:
    kind: str  # HIGH or LOW
    pool_level: float
    high: float
    low: float
    close: float
    open: float
    atr: float
    flow_15s: float
    flow_60s: float
    notional_burst: float
    efficiency_60s: float
    depth_imbalance_1: float
    bid_depth_change_1m: float
    ask_depth_change_1m: float

    @property
    def sweep_direction(self) -> int:
        return 1 if self.kind == "HIGH" else -1

    @property
    def penetration_atr(self) -> float:
        if self.atr <= 0.0:
            return 0.0
        if self.kind == "HIGH":
            return (self.high - self.pool_level) / self.atr
        return (self.pool_level - self.low) / self.atr

    @property
    def close_location(self) -> float:
        span = max(self.high - self.low, 1e-12)
        if self.kind == "HIGH":
            return (self.close - self.low) / span
        return (self.high - self.close) / span


@dataclass(frozen=True, slots=True)
class ClassificationThresholds:
    min_penetration_atr: float
    min_notional_burst: float
    rejection_flow_min: float
    rejection_efficiency_max: float
    rejection_depth_refill_min: float
    acceptance_flow_min: float
    acceptance_efficiency_min: float
    acceptance_depth_withdrawal_min: float
    acceptance_close_atr: float
    acceptance_close_location: float


def finite_or(value: float, default: float = 0.0) -> float:
    return value if math.isfinite(value) else default


def auction_efficiency(closes: Sequence[float]) -> float:
    """Net displacement divided by travelled path, in [0, 1]."""
    if len(closes) < 2:
        return 0.0
    path = sum(abs(right - left) for left, right in zip(closes, closes[1:]))
    if path <= 0.0:
        return 0.0
    return min(1.0, abs(closes[-1] - closes[0]) / path)


def cost_aware_target(
    entry: float,
    side: int,
    planned_loss_per_unit: float,
    target_net_r: float,
    cost_rate: float,
) -> float:
    """Price whose post-cost PnL equals ``target_net_r`` planned-loss units."""
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if entry <= 0.0 or planned_loss_per_unit <= 0.0 or target_net_r <= 0.0:
        raise ValueError("entry, planned loss, and target R must be positive")
    entry_cost = entry * cost_rate
    if side > 0:
        return (entry + target_net_r * planned_loss_per_unit + entry_cost) / (1.0 - cost_rate)
    return (entry - target_net_r * planned_loss_per_unit - entry_cost) / (1.0 + cost_rate)


def net_r_at_price(
    entry: float,
    exit_price: float,
    side: int,
    planned_loss_per_unit: float,
    cost_rate: float,
) -> float:
    if side not in (-1, 1) or planned_loss_per_unit <= 0.0:
        return -math.inf
    net = side * (exit_price - entry) - cost_rate * (entry + exit_price)
    return net / planned_loss_per_unit


def planned_loss_per_unit(
    entry: float,
    stop: float,
    side: int,
    cost_rate: float,
    adverse_slippage_rate: float,
) -> float:
    """Conservative loss per unit including entry/stop costs and adverse fills."""
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if entry <= 0.0 or stop <= 0.0:
        raise ValueError("prices must be positive")
    expected_entry = entry * (1.0 + side * adverse_slippage_rate)
    expected_stop = stop * (1.0 - side * adverse_slippage_rate)
    price_loss = side * (expected_entry - expected_stop)
    if price_loss <= 0.0:
        return -math.inf
    return price_loss + cost_rate * (expected_entry + expected_stop)


def floor_quantity(raw_quantity: float, precision: int) -> float:
    if raw_quantity <= 0.0 or not math.isfinite(raw_quantity):
        return 0.0
    scale = 10**precision
    return math.floor(raw_quantity * scale) / scale


def classify_sweep(
    evidence: SweepEvidence,
    thresholds: ClassificationThresholds,
) -> str | None:
    """Classify a pool violation as rejection, acceptance, or unresolved.

    The classification is deliberately causal and symmetric.  ``kind=HIGH``
    means aggressive buying is the sweep direction; ``kind=LOW`` mirrors every
    directional predicate.  A branch must explain both the price response and
    the observable liquidity response; a large candle by itself is insufficient.
    """
    if evidence.atr <= 0.0 or evidence.penetration_atr < thresholds.min_penetration_atr:
        return None
    if finite_or(evidence.notional_burst) < thresholds.min_notional_burst:
        return None

    direction = evidence.sweep_direction
    flow_15 = direction * finite_or(evidence.flow_15s)
    flow_60 = direction * finite_or(evidence.flow_60s)
    efficiency = finite_or(evidence.efficiency_60s)

    reclaimed = (
        evidence.close < evidence.pool_level
        if evidence.kind == "HIGH"
        else evidence.close > evidence.pool_level
    )
    accepted_distance = direction * (evidence.close - evidence.pool_level) / evidence.atr

    # A high sweep consumes asks; ask replenishment is rejection evidence.
    # A low sweep consumes bids; bid replenishment is rejection evidence.
    same_side_refill = (
        finite_or(evidence.ask_depth_change_1m)
        if evidence.kind == "HIGH"
        else finite_or(evidence.bid_depth_change_1m)
    )
    threatened_withdrawal = -same_side_refill

    rejection_flow = max(flow_15, flow_60) >= thresholds.rejection_flow_min
    rejection_response = efficiency <= thresholds.rejection_efficiency_max
    rejection_depth = same_side_refill >= thresholds.rejection_depth_refill_min
    if reclaimed and rejection_flow and rejection_response and rejection_depth:
        return "REJECTION"

    accepted = accepted_distance >= thresholds.acceptance_close_atr
    acceptance_flow = min(flow_15, flow_60) >= thresholds.acceptance_flow_min
    acceptance_response = efficiency >= thresholds.acceptance_efficiency_min
    acceptance_depth = threatened_withdrawal >= thresholds.acceptance_depth_withdrawal_min
    close_location = evidence.close_location >= thresholds.acceptance_close_location
    if accepted and acceptance_flow and acceptance_response and acceptance_depth and close_location:
        return "ACCEPTANCE"
    return None


def confirmation_passes(
    *,
    side: int,
    open_price: float,
    close_price: float,
    high: float,
    low: float,
    structure: float,
    atr: float,
    flow_60s: float,
    efficiency_60s: float,
    min_body_atr: float,
    min_flow: float,
    min_efficiency: float,
    min_close_location: float,
) -> bool:
    if side not in (-1, 1) or atr <= 0.0:
        return False
    broken = close_price > structure if side > 0 else close_price < structure
    body = side * (close_price - open_price) / atr
    directional_flow = side * finite_or(flow_60s)
    span = max(high - low, 1e-12)
    close_location = (
        (close_price - low) / span if side > 0 else (high - close_price) / span
    )
    return (
        broken
        and body >= min_body_atr
        and directional_flow >= min_flow
        and finite_or(efficiency_60s) >= min_efficiency
        and close_location >= min_close_location
    )


def choose_liquidity_target(
    *,
    entry: float,
    side: int,
    pools: Sequence[Pool],
    planned_loss: float,
    cost_rate: float,
    min_net_r: float,
    max_net_r: float,
    fallback_net_r: float,
) -> tuple[float, str, float]:
    """Choose the nearest still-active opposing pool, with causal R bounds."""
    if side > 0:
        candidates = sorted((p for p in pools if p.kind == "HIGH" and p.level > entry), key=lambda p: p.level)
    else:
        candidates = sorted((p for p in pools if p.kind == "LOW" and p.level < entry), key=lambda p: p.level, reverse=True)

    for pool in candidates:
        target_r = net_r_at_price(entry, pool.level, side, planned_loss, cost_rate)
        if target_r < min_net_r:
            continue
        capped_r = min(target_r, max_net_r)
        target = cost_aware_target(entry, side, planned_loss, capped_r, cost_rate)
        if target_r <= max_net_r:
            target = pool.level
        return target, f"POOL:{pool.pool_id}", min(target_r, max_net_r)

    target = cost_aware_target(entry, side, planned_loss, fallback_net_r, cost_rate)
    return target, "FALLBACK_CAUSAL_EXPANSION", fallback_net_r


def is_confirmed_pivot(
    values: Sequence[float],
    *,
    span: int,
    kind: str,
) -> bool:
    """Whether the center of a completed 2*span+1 window is a pivot."""
    if span < 1 or len(values) != 2 * span + 1:
        return False
    center = values[span]
    if kind == "HIGH":
        return center == max(values) and values.count(center) == 1
    if kind == "LOW":
        return center == min(values) and values.count(center) == 1
    raise ValueError("kind must be HIGH or LOW")


def mirrored_evidence(evidence: SweepEvidence) -> SweepEvidence:
    """Mirror prices/flows for symmetry tests around an arbitrary anchor."""
    anchor = evidence.pool_level
    return SweepEvidence(
        kind="LOW" if evidence.kind == "HIGH" else "HIGH",
        pool_level=anchor,
        high=2 * anchor - evidence.low,
        low=2 * anchor - evidence.high,
        close=2 * anchor - evidence.close,
        open=2 * anchor - evidence.open,
        atr=evidence.atr,
        flow_15s=-evidence.flow_15s,
        flow_60s=-evidence.flow_60s,
        notional_burst=evidence.notional_burst,
        efficiency_60s=evidence.efficiency_60s,
        depth_imbalance_1=-evidence.depth_imbalance_1,
        bid_depth_change_1m=evidence.ask_depth_change_1m,
        ask_depth_change_1m=evidence.bid_depth_change_1m,
    )


def config_thresholds(config: Mapping[str, float]) -> ClassificationThresholds:
    return ClassificationThresholds(
        min_penetration_atr=float(config["sweep_min_penetration_atr"]),
        min_notional_burst=float(config["sweep_min_notional_burst"]),
        rejection_flow_min=float(config["rejection_flow_min"]),
        rejection_efficiency_max=float(config["rejection_efficiency_max"]),
        rejection_depth_refill_min=float(config["rejection_depth_refill_min"]),
        acceptance_flow_min=float(config["acceptance_flow_min"]),
        acceptance_efficiency_min=float(config["acceptance_efficiency_min"]),
        acceptance_depth_withdrawal_min=float(config["acceptance_depth_withdrawal_min"]),
        acceptance_close_atr=float(config["acceptance_close_atr"]),
        acceptance_close_location=float(config["acceptance_close_location"]),
    )

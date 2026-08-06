"""Pure predicates for handing a consumed target pool into a new rejection scenario.

This module contains no execution, fill, position, accounting, or PnL logic.
It only decides whether a liquidity pool which closed the previous trade was
subsequently reclaimed with the same causal evidence required by Candidate 05.
"""
from __future__ import annotations

import math


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(value)) for value in values)


def target_exit_matches(
    *,
    average_exit: float,
    target: float,
    price_increment: float,
    realized_pnl: float,
) -> bool:
    """Whether a profitable close is consistent with the stored target order.

    The fixed backtest applies adverse exit slippage, so a target fill may be
    reported one instrument tick away from the submitted trigger. No wider
    tolerance is admitted.
    """
    if not _finite(average_exit, target, price_increment, realized_pnl):
        return False
    if average_exit <= 0.0 or target <= 0.0 or price_increment <= 0.0:
        return False
    return realized_pnl > 0.0 and abs(average_exit - target) <= price_increment + 1e-12


def target_sweep_bar_sponsored(
    *,
    kind: str,
    flow_15s: float,
    flow_60s: float,
    notional_burst: float,
    efficiency_60s: float,
    minimum_directional_flow: float,
    minimum_notional_burst: float,
    maximum_efficiency: float,
) -> bool:
    """Whether a completed bar supplies genuine flow behind a target-pool raid."""
    if kind not in {"HIGH", "LOW"}:
        raise ValueError("kind must be HIGH or LOW")
    if not _finite(
        flow_15s,
        flow_60s,
        notional_burst,
        efficiency_60s,
        minimum_directional_flow,
        minimum_notional_burst,
        maximum_efficiency,
    ):
        return False
    direction = 1 if kind == "HIGH" else -1
    directional_flow = max(direction * flow_15s, direction * flow_60s)
    return (
        directional_flow >= minimum_directional_flow
        and notional_burst >= minimum_notional_burst
        and efficiency_60s <= maximum_efficiency
    )


def delayed_target_reclaim_ready(
    *,
    kind: str,
    pool_level: float,
    accumulated_high: float,
    accumulated_low: float,
    current_close: float,
    atr: float,
    sweep_sponsored: bool,
    current_efficiency_60s: float,
    current_bid_depth_change_1m: float,
    current_ask_depth_change_1m: float,
    minimum_penetration_atr: float,
    maximum_efficiency: float,
    minimum_same_side_refill: float,
) -> bool:
    """Whether a target raid has become a delayed failed auction.

    Sponsorship and penetration may occur on the target-fill bar, while the
    reclaim and replenishment can become observable on a later completed bar.
    This explicitly models that sequence rather than pretending both happened
    in the same one-minute candle.
    """
    if kind not in {"HIGH", "LOW"}:
        raise ValueError("kind must be HIGH or LOW")
    if not sweep_sponsored:
        return False
    if not _finite(
        pool_level,
        accumulated_high,
        accumulated_low,
        current_close,
        atr,
        current_efficiency_60s,
        current_bid_depth_change_1m,
        current_ask_depth_change_1m,
        minimum_penetration_atr,
        maximum_efficiency,
        minimum_same_side_refill,
    ):
        return False
    if pool_level <= 0.0 or atr <= 0.0:
        return False

    if kind == "HIGH":
        penetration_atr = (accumulated_high - pool_level) / atr
        reclaimed = current_close < pool_level
        same_side_refill = current_ask_depth_change_1m
    else:
        penetration_atr = (pool_level - accumulated_low) / atr
        reclaimed = current_close > pool_level
        same_side_refill = current_bid_depth_change_1m

    return (
        penetration_atr >= minimum_penetration_atr
        and reclaimed
        and current_efficiency_60s <= maximum_efficiency
        and same_side_refill >= minimum_same_side_refill
    )


__all__ = [
    "delayed_target_reclaim_ready",
    "target_exit_matches",
    "target_sweep_bar_sponsored",
]

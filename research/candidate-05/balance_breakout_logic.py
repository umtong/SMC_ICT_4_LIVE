"""Pure causal predicates for Candidate 05 balance-expansion auctions."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

from logic import auction_efficiency


BALANCE_BARS = 20
BALANCE_MAX_RANGE_ATR = 4.0
BALANCE_MAX_EFFICIENCY = 0.30
BREAKOUT_MIN_BODY_ATR = 0.50
BREAKOUT_MIN_CLOSE_LOCATION = 0.65
BREAKOUT_MIN_FLOW_15S = 0.10
BREAKOUT_MIN_FLOW_60S = 0.15
BREAKOUT_MIN_EFFICIENCY_60S = 0.15
BREAKOUT_MIN_NOTIONAL_BURST = 1.0


@dataclass(frozen=True, slots=True)
class BalanceBreakoutEvidence:
    open_price: float
    high: float
    low: float
    close: float
    atr: float
    balance_high: float
    balance_low: float
    balance_closes: tuple[float, ...]
    flow_15s: float
    flow_60s: float
    flow_3m: float
    efficiency_60s: float
    notional_burst: float
    depth_imbalance_1: float
    bid_depth_change_5m: float
    ask_depth_change_5m: float
    oi_change_15m: float
    metrics_ready: bool


def breakout_side(evidence: BalanceBreakoutEvidence) -> int:
    """Return +1/-1 only for a coherent balance-to-expansion transition.

    A valid event begins with a low-efficiency twenty-minute balance whose total
    range fits inside four current ATRs.  Price must then close outside the
    balance with displacement, persistent aggressive flow at 15s/60s/3m,
    positive price response efficiency, above-median notional, expanding open
    interest, withdrawal of resting liquidity in front of the move, and a book
    imbalance which supports the move.  Every directional predicate is mirror
    symmetric.
    """
    values = (
        evidence.open_price,
        evidence.high,
        evidence.low,
        evidence.close,
        evidence.atr,
        evidence.balance_high,
        evidence.balance_low,
        evidence.flow_15s,
        evidence.flow_60s,
        evidence.flow_3m,
        evidence.efficiency_60s,
        evidence.notional_burst,
        evidence.depth_imbalance_1,
        evidence.bid_depth_change_5m,
        evidence.ask_depth_change_5m,
        evidence.oi_change_15m,
    )
    if not evidence.metrics_ready or not all(math.isfinite(value) for value in values):
        return 0
    if evidence.atr <= 0.0 or evidence.balance_high <= evidence.balance_low:
        return 0
    if len(evidence.balance_closes) != BALANCE_BARS:
        return 0

    balance_range_atr = (evidence.balance_high - evidence.balance_low) / evidence.atr
    if balance_range_atr > BALANCE_MAX_RANGE_ATR:
        return 0
    if auction_efficiency(evidence.balance_closes) > BALANCE_MAX_EFFICIENCY:
        return 0

    above = evidence.close > evidence.balance_high
    below = evidence.close < evidence.balance_low
    if above == below:
        return 0
    side = 1 if above else -1

    span = max(evidence.high - evidence.low, 1e-12)
    close_location = (
        (evidence.close - evidence.low) / span
        if side > 0
        else (evidence.high - evidence.close) / span
    )
    body_atr = side * (evidence.close - evidence.open_price) / evidence.atr
    directional_flow_15s = side * evidence.flow_15s
    directional_flow_60s = side * evidence.flow_60s
    directional_flow_3m = side * evidence.flow_3m
    directional_depth = side * evidence.depth_imbalance_1
    opposing_depth_withdrawal = (
        -evidence.ask_depth_change_5m
        if side > 0
        else -evidence.bid_depth_change_5m
    )

    if body_atr < BREAKOUT_MIN_BODY_ATR:
        return 0
    if close_location < BREAKOUT_MIN_CLOSE_LOCATION:
        return 0
    if directional_flow_15s < BREAKOUT_MIN_FLOW_15S:
        return 0
    if directional_flow_60s < BREAKOUT_MIN_FLOW_60S:
        return 0
    if directional_flow_3m <= 0.0:
        return 0
    if evidence.efficiency_60s < BREAKOUT_MIN_EFFICIENCY_60S:
        return 0
    if evidence.notional_burst < BREAKOUT_MIN_NOTIONAL_BURST:
        return 0
    if evidence.oi_change_15m <= 0.0:
        return 0
    if opposing_depth_withdrawal <= 0.0:
        return 0
    if directional_depth <= 0.0:
        return 0
    return side


def balance_metrics(
    *,
    balance_high: float,
    balance_low: float,
    atr: float,
    closes: Sequence[float],
) -> dict[str, float]:
    if atr <= 0.0:
        return {"range_atr": math.inf, "efficiency": math.inf}
    return {
        "range_atr": (balance_high - balance_low) / atr,
        "efficiency": auction_efficiency(tuple(float(value) for value in closes)),
    }

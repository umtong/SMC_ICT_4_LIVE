"""Pure causal predicates for Candidate 05 tail-flow liquidity reversals."""
from __future__ import annotations

import math


SWEEP_TAIL_IMPROVEMENT_MIN = 0.10
CHOCH_ACTIVE_TAIL_MIN = -0.10
CHOCH_PASSIVE_TAIL_MIN = -0.25
CHOCH_MATURE_FLOW_MAX = 0.50
CHOCH_PASSIVE_DEPTH_MIN = 0.10
MIN_LIQUIDITY_TARGET_NET_R = 0.40
MAX_LIQUIDITY_TARGET_NET_R = 1.50
FALLBACK_TARGET_NET_R = 0.75
BREAKAWAY_EXTENSION_ATR_MIN = 1.0
BREAKAWAY_DIRECTIONAL_DEPTH_MIN = 0.10
BREAKAWAY_DIRECTIONAL_FLOW_3M_MIN = 0.0
BREAKAWAY_DIRECTIONAL_IMBALANCE_MIN = 1.0 / 3.0  # favorable depth ratio >= 2:1


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(value)) for value in values)


def directional_tail_improvement(*, side: int, flow_15s: float, flow_60s: float) -> float:
    """Final-15-second aggressor-flow improvement versus the full minute."""
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not _finite(flow_15s, flow_60s):
        return -math.inf
    return side * (float(flow_15s) - float(flow_60s))


def sweep_tail_recovers(
    *,
    side: int,
    flow_15s: float,
    flow_60s: float,
    minimum: float = SWEEP_TAIL_IMPROVEMENT_MIN,
) -> bool:
    """Whether aggressor flow materially turns toward the proposed reversal."""
    return directional_tail_improvement(
        side=side,
        flow_15s=flow_15s,
        flow_60s=flow_60s,
    ) >= minimum


def choch_flow_state(
    *,
    side: int,
    flow_15s: float,
    flow_3m: float,
    depth_imbalance: float,
) -> str | None:
    """Classify an early CHoCH as actively or passively sponsored."""
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not _finite(flow_15s, flow_3m, depth_imbalance):
        return None
    directional_15s = side * float(flow_15s)
    directional_3m = side * float(flow_3m)
    directional_depth = side * float(depth_imbalance)
    if directional_3m >= CHOCH_MATURE_FLOW_MAX:
        return None
    if directional_15s >= CHOCH_ACTIVE_TAIL_MIN:
        return "ACTIVE_CONFIRMATION"
    if (
        directional_15s >= CHOCH_PASSIVE_TAIL_MIN
        and directional_depth >= CHOCH_PASSIVE_DEPTH_MIN
    ):
        return "PASSIVE_ROTATION"
    return None


def breakaway_follow_through(
    *,
    side: int,
    choch_close: float,
    current_close: float,
    atr: float,
    sweep_depth_imbalance: float,
    current_depth_imbalance: float,
    current_flow_3m: float,
) -> bool:
    """Whether CHoCH became a no-retrace, liquidity-sponsored breakaway.

    The sweep must already have at least a 2:1 favorable resting-depth ratio.
    The next completed minute must extend by at least one ATR, retain a
    ten-percentage-point book edge, and keep three-minute aggressor flow aligned.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not _finite(
        choch_close,
        current_close,
        atr,
        sweep_depth_imbalance,
        current_depth_imbalance,
        current_flow_3m,
    ) or atr <= 0.0:
        return False
    return (
        side * (current_close - choch_close) / atr >= BREAKAWAY_EXTENSION_ATR_MIN
        and side * sweep_depth_imbalance >= BREAKAWAY_DIRECTIONAL_IMBALANCE_MIN
        and side * current_depth_imbalance >= BREAKAWAY_DIRECTIONAL_DEPTH_MIN
        and side * current_flow_3m > BREAKAWAY_DIRECTIONAL_FLOW_3M_MIN
    )


def worst_entry_preserving_net_r(
    *,
    stop: float,
    target: float,
    side: int,
    minimum_net_r: float,
    cost_rate: float,
    adverse_slippage_rate: float,
) -> float:
    """Worst executable entry price which still preserves the minimum net R.

    For a long this is the highest acceptable price; for a short it is the
    lowest acceptable price. Planned loss includes the same adverse entry/stop
    slippage and round-trip cost assumptions used for sizing.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not _finite(stop, target, minimum_net_r, cost_rate, adverse_slippage_rate):
        return math.nan
    if stop <= 0.0 or target <= 0.0 or minimum_net_r <= 0.0:
        return math.nan
    if (side > 0 and not stop < target) or (side < 0 and not target < stop):
        return math.nan

    def planned_loss(entry: float) -> float:
        expected_entry = entry * (1.0 + side * adverse_slippage_rate)
        expected_stop = stop * (1.0 - side * adverse_slippage_rate)
        price_loss = side * (expected_entry - expected_stop)
        if price_loss <= 0.0:
            return math.nan
        return price_loss + cost_rate * (expected_entry + expected_stop)

    def net_r(entry: float) -> float:
        loss = planned_loss(entry)
        if not math.isfinite(loss) or loss <= 0.0:
            return -math.inf
        net = side * (target - entry) - cost_rate * (entry + target)
        return net / loss

    epsilon = max(abs(stop), abs(target), 1.0) * 1e-12
    if side > 0:
        good = stop + epsilon
        bad = target - epsilon
        if net_r(good) < minimum_net_r:
            return math.nan
        for _ in range(96):
            midpoint = (good + bad) / 2.0
            if net_r(midpoint) >= minimum_net_r:
                good = midpoint
            else:
                bad = midpoint
        return good

    bad = target + epsilon
    good = stop - epsilon
    if net_r(good) < minimum_net_r:
        return math.nan
    for _ in range(96):
        midpoint = (bad + good) / 2.0
        if net_r(midpoint) >= minimum_net_r:
            good = midpoint
        else:
            bad = midpoint
    return good


def has_adverse_slippage_room(
    *,
    observed_price: float,
    limit_price: float,
    side: int,
    adverse_slippage_rate: float,
) -> bool:
    """Whether a marketable limit still covers the configured adverse slippage."""
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not _finite(observed_price, limit_price, adverse_slippage_rate):
        return False
    required = observed_price * (1.0 + side * adverse_slippage_rate)
    return limit_price >= required if side > 0 else limit_price <= required


def choose_protectable_liquidity_milestone(
    *,
    entry: float,
    target: float,
    side: int,
    pools: list[object],
    atr: float,
    stop_buffer_atr: float,
    cost_rate: float,
    adverse_slippage_rate: float,
) -> tuple[str, float, float, float] | None:
    """Choose the nearest intermediate pool which can defend positive net PnL.

    The final target remains unchanged. This function only identifies an opposing
    liquidity pool strictly between entry and target. Once consumed, a stop one
    structural ATR buffer behind that pool must still yield positive expected PnL
    after round-trip fees and adverse stop slippage.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not _finite(
        entry,
        target,
        atr,
        stop_buffer_atr,
        cost_rate,
        adverse_slippage_rate,
    ) or entry <= 0.0 or target <= 0.0 or atr <= 0.0 or stop_buffer_atr < 0.0:
        return None
    if (side > 0 and target <= entry) or (side < 0 and target >= entry):
        return None

    opposing_kind = "HIGH" if side > 0 else "LOW"
    candidates: list[tuple[str, float, float, float]] = []
    for pool in pools:
        if str(getattr(pool, "kind", "")) != opposing_kind:
            continue
        level = float(getattr(pool, "level", math.nan))
        if not math.isfinite(level):
            continue
        between = entry < level < target if side > 0 else target < level < entry
        if not between:
            continue
        protected_stop = level - side * stop_buffer_atr * atr
        expected_exit = protected_stop * (1.0 - side * adverse_slippage_rate)
        expected_net = side * (expected_exit - entry) - cost_rate * (entry + expected_exit)
        if expected_net <= 0.0:
            continue
        candidates.append(
            (
                str(getattr(pool, "pool_id", "unknown-pool")),
                level,
                protected_stop,
                expected_net,
            ),
        )

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[1], reverse=side < 0)
    return candidates[0]


__all__ = [
    "BREAKAWAY_DIRECTIONAL_DEPTH_MIN",
    "BREAKAWAY_DIRECTIONAL_FLOW_3M_MIN",
    "BREAKAWAY_DIRECTIONAL_IMBALANCE_MIN",
    "BREAKAWAY_EXTENSION_ATR_MIN",
    "CHOCH_ACTIVE_TAIL_MIN",
    "CHOCH_MATURE_FLOW_MAX",
    "CHOCH_PASSIVE_DEPTH_MIN",
    "CHOCH_PASSIVE_TAIL_MIN",
    "FALLBACK_TARGET_NET_R",
    "MAX_LIQUIDITY_TARGET_NET_R",
    "MIN_LIQUIDITY_TARGET_NET_R",
    "SWEEP_TAIL_IMPROVEMENT_MIN",
    "breakaway_follow_through",
    "choch_flow_state",
    "choose_protectable_liquidity_milestone",
    "directional_tail_improvement",
    "has_adverse_slippage_room",
    "sweep_tail_recovers",
    "worst_entry_preserving_net_r",
]

#!/usr/bin/env python3
"""Validate compiler-declared pre-existing liquidity targets at execution time.

The pattern/scenario compiler may know the exact opposite pivot or completed
parent-session boundary that defines the trade's destination. The execution
strategy must not replace that semantic target with a measured move, but it must
still reject non-causal, wrong-direction or uneconomic declarations.
"""
from __future__ import annotations

import math
from typing import Any

from nt_liquidity_strategy import net_r_at_price
from nt_low_impact_external_strategy import LiquidityTarget


ALLOWED_TARGET_PREFIXES = (
    "causal_pivot_pool_",
    "completed_parent_session_",
    "completed_previous_day_",
    "completed_previous_week_",
)


def choose_declared_causal_target(
    signal: dict[str, Any],
    *,
    entry: float,
    stop: float,
    side: int,
    cost_rate: float,
    minimum_net_r: float,
) -> tuple[LiquidityTarget | None, str | None]:
    """Return a validated compiler target, absence, or an explicit error.

    ``(None, None)`` means the compiler did not declare a target and the
    execution strategy may consult its own causal registry. ``(None, reason)``
    means a declaration existed but violated the contract and must not fall back
    silently to another destination.
    """

    details = signal.get("details") or {}
    raw_price = details.get("causal_target_reference")
    raw_source = details.get("causal_target_source")
    raw_observed = details.get("causal_target_observed_index")
    if raw_price is None and raw_source is None and raw_observed is None:
        return None, None
    if raw_price is None or raw_source is None or raw_observed is None:
        return None, "incomplete_compiler_target_declaration"
    try:
        price = float(raw_price)
        source = str(raw_source)
        observed_index = int(raw_observed)
        signal_index = int(signal["signal_index"])
    except (KeyError, TypeError, ValueError):
        return None, "malformed_compiler_target_declaration"
    if not source.startswith(ALLOWED_TARGET_PREFIXES):
        return None, "unapproved_compiler_target_source"
    if observed_index >= signal_index:
        return None, "compiler_target_not_observed_before_signal"
    if side not in (-1, 1):
        return None, "invalid_trade_side"
    values = (price, entry, stop, cost_rate, minimum_net_r)
    if not all(math.isfinite(value) for value in values):
        return None, "nonfinite_compiler_target_geometry"
    if side * (price - entry) <= 0.0:
        return None, "compiler_target_wrong_direction"
    price_loss = side * (entry - stop)
    planned_loss = price_loss + cost_rate * (entry + stop)
    if price_loss <= 0.0 or planned_loss <= 0.0:
        return None, "invalid_compiler_stop_geometry"
    net_r = net_r_at_price(entry, price, side, planned_loss, cost_rate)
    if not math.isfinite(net_r) or net_r < minimum_net_r:
        return None, "compiler_target_below_minimum_net_r"
    return LiquidityTarget(price=price, source=source, net_r=net_r), None


__all__ = ["ALLOWED_TARGET_PREFIXES", "choose_declared_causal_target"]

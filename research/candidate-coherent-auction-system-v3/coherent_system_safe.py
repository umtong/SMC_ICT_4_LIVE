"""Runtime-safe entry point for the coherent auction system."""
from __future__ import annotations

import coherent_system as system
import semantic_liquidity_safe as safe

system.PoolMeta = safe.PoolMeta
system.build_semantic_liquidity = safe.build_semantic_liquidity
system.direction_sources = safe.direction_sources
system.route_levels = safe.route_levels

POLICY = system.POLICY
MAX_HOLD_MINUTES = system.MAX_HOLD_MINUTES
run_research = system.run_research
generate_symbol = system.generate_symbol
label_market_action = system.label_market_action

__all__ = [
    "POLICY",
    "MAX_HOLD_MINUTES",
    "run_research",
    "generate_symbol",
    "label_market_action",
]

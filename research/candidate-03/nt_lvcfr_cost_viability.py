"""Cost-feasibility helpers for structural liquidity objectives."""
from __future__ import annotations

import math


def expected_structural_target_net_per_unit(
    *,
    entry_price: float,
    target_price: float,
    direction: int,
    fee_fraction: float,
    adverse_funding_per_unit: float = 0.0,
) -> float:
    """Return conservative net PnL per unit if an executable target is reached.

    Entry and target are executable-side prices. Both taker fees and the full
    adverse funding budget are deducted. Positive output is therefore the
    minimum requirement for a structural objective to be economically tradable.
    """
    values = (entry_price, target_price, fee_fraction, adverse_funding_per_unit)
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    if not all(math.isfinite(value) for value in values):
        raise ValueError("cost-feasibility inputs must be finite")
    if entry_price <= 0.0 or target_price <= 0.0:
        raise ValueError("prices must be positive")
    if not 0.0 <= fee_fraction < 1.0:
        raise ValueError("fee_fraction must be in [0, 1)")
    if adverse_funding_per_unit < 0.0:
        raise ValueError("adverse funding must be non-negative")
    if direction > 0:
        return (
            target_price * (1.0 - fee_fraction)
            - entry_price * (1.0 + fee_fraction)
            - adverse_funding_per_unit
        )
    return (
        entry_price * (1.0 - fee_fraction)
        - target_price * (1.0 + fee_fraction)
        - adverse_funding_per_unit
    )

"""Pure branch-identity predicate for Candidate 05 external inventory raids.

This module classifies an already detected setup only. It has no market replay,
orders, fills, positions, PnL, NAV or risk-sizing behavior.
"""
from __future__ import annotations

from typing import Any


INTERNAL_HYBRID_STATE = "INTERNAL_INVENTORY_TRAP"
EXTERNAL_POOL_SOURCE = "CONFIRMED_5M_SWING"


def external_setup_from_hybrid(details: dict[str, Any]) -> bool:
    """Return whether the hybrid detector produced an external 5m setup.

    The hybrid strategy evaluates its untouched five-minute external pool store
    first. Only when that path produces no setup does it temporarily substitute
    the independent one-/three-minute pool store, whose setups are explicitly
    tagged ``INTERNAL_INVENTORY_TRAP``. The frozen five-minute pool source and
    absence of that internal tag therefore identify the external branch.
    """
    return (
        details.get("hybrid_state") != INTERNAL_HYBRID_STATE
        and str(details.get("pool_source", "")) == EXTERNAL_POOL_SOURCE
    )


__all__ = [
    "EXTERNAL_POOL_SOURCE",
    "INTERNAL_HYBRID_STATE",
    "external_setup_from_hybrid",
]

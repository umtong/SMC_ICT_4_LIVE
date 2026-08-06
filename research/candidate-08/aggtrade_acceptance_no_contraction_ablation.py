"""Single-variable ablation: remove only retest activity/imbalance contraction.

The original structural touch and accepted-side hold logic are preserved by calling the
frozen retest predicate with effectively unbounded displacement activity. All other
signal, stop, target, cost, risk, synchronization, and execution contracts remain fixed.
"""

from __future__ import annotations

from typing import Any

import aggtrade_acceptance_causal_v1 as causal
from aggtrade_acceptance_probe import acceptance_retest_holds as frozen_retest_holds


HUGE_ACTIVITY = 1.0e300


def retest_holds_without_contraction(
    row: Any,
    *,
    boundary_level: float,
    outward: int,
    atr: float,
    displacement_volume: float,
    displacement_trade_count: float,
    displacement_imbalance: float,
) -> bool:
    """Preserve structural retest rules while making contraction tests non-binding."""

    del displacement_volume, displacement_trade_count, displacement_imbalance
    return frozen_retest_holds(
        row,
        boundary_level=boundary_level,
        outward=outward,
        atr=atr,
        displacement_volume=HUGE_ACTIVITY,
        displacement_trade_count=HUGE_ACTIVITY,
        displacement_imbalance=HUGE_ACTIVITY * (1 if outward > 0 else -1),
    )


def build_acceptance_signals_no_contraction(**kwargs: Any) -> Any:
    """Invoke the frozen causal builder with one predicate temporarily ablated."""

    original = causal.acceptance_retest_holds
    causal.acceptance_retest_holds = retest_holds_without_contraction
    try:
        return causal.build_acceptance_signals(**kwargs)
    finally:
        causal.acceptance_retest_holds = original

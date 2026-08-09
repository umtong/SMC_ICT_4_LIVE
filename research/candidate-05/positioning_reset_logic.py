"""Pure state predicates for v39 positioning-reset CHoCH participation."""
from __future__ import annotations

import math
from collections.abc import Sequence


MIN_PATH_EFFICIENCY_30M = 0.25
MAX_NONMATERIAL_OI_EXPANSION_15M = 0.001


def completed_path_efficiency(closes: Sequence[float]) -> float:
    """Net displacement divided by total completed-close path length."""
    values = [float(value) for value in closes]
    if len(values) < 2 or not all(math.isfinite(value) for value in values):
        return float("nan")
    path = sum(abs(current - previous) for previous, current in zip(values, values[1:]))
    if path <= 0.0:
        return 0.0
    return abs(values[-1] - values[0]) / path


def positioning_reset_supports_early_reversal(
    *,
    side: int,
    sweep_premium_change_5m: float,
    sweep_path_efficiency_30m: float,
    choch_oi_change_15m: float,
) -> bool:
    """Allow immediate CHoCH participation only after a one-way deleveraging reset.

    The premium index must already normalize in the proposed reversal direction,
    at least one quarter of the prior 30-minute travelled path must remain as
    net displacement, and open interest may expand only immaterially at CHoCH.
    """
    values = (
        sweep_premium_change_5m,
        sweep_path_efficiency_30m,
        choch_oi_change_15m,
    )
    if side not in (-1, 1) or not all(math.isfinite(value) for value in values):
        return False
    return (
        side * sweep_premium_change_5m > 0.0
        and sweep_path_efficiency_30m >= MIN_PATH_EFFICIENCY_30M
        and choch_oi_change_15m <= MAX_NONMATERIAL_OI_EXPANSION_15M
    )

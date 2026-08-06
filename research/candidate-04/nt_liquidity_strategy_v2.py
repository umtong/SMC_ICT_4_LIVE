#!/usr/bin/env python3
"""State-transition correction for the NautilusTrader candidate-04 strategy."""
from __future__ import annotations

from typing import Any

from nt_liquidity_strategy import LiquidityTransitionConfig
from nt_liquidity_strategy import LiquidityTransitionStrategy


class LiquidityTransitionStrategyV2(LiquidityTransitionStrategy):
    """Treat causal setup invalidation as a handled bar transition.

    The base detector deliberately clears ``pending`` when the first structural
    break is weak, when opposing liquidity is insufficient, or when bracket
    submission is rejected by a causal precondition. Returning ``True`` in that
    case prevents the caller from dereferencing a setup which was just consumed.
    """

    def _try_confirm_pending(self, row: dict[str, float | int]) -> bool:
        had_pending = self.pending is not None
        submitted = super()._try_confirm_pending(row)
        consumed = had_pending and self.pending is None
        return submitted or consumed


__all__ = ["LiquidityTransitionConfig", "LiquidityTransitionStrategyV2"]

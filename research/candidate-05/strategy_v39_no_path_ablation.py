#!/usr/bin/env python3
"""One-variable v39 ablation: remove only 30-minute path efficiency."""
from __future__ import annotations

from positioning_reset_logic import positioning_reset_supports_early_reversal
from strategy_base import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_v17 import EarlySponsoredChochStrategy
from strategy_v39_positioning_reset import PositioningResetReversalStrategy


class PositioningResetNoPathAblationStrategy(PositioningResetReversalStrategy):
    """Keep premium normalization and OI state; remove only path efficiency."""

    def _early_sponsored_participation_allowed(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
        flow_3m: float,
    ) -> bool:
        if not EarlySponsoredChochStrategy._early_sponsored_participation_allowed(
            self,
            setup,
            row,
            flow_3m,
        ):
            return False
        allowed = positioning_reset_supports_early_reversal(
            side=setup.side,
            sweep_premium_change_5m=float(
                setup.details.get("sweep_premium_change_5m", float("nan")),
            ),
            sweep_path_efficiency_30m=1.0,
            choch_oi_change_15m=self._feature("oi_change_15m"),
        )
        key = (
            "positioning_reset_early_participation_pass"
            if allowed
            else "positioning_reset_early_participation_deferred"
        )
        self.diagnostics[key] += 1
        return allowed


__all__ = ["PositioningResetNoPathAblationStrategy"]

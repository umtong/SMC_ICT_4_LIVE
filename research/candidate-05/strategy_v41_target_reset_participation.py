#!/usr/bin/env python3
"""Candidate 05 v41: target-consumption reset may participate at early CHoCH.

A generic sweep still requires v39's premium/OI positioning reset before using
an immediate, price-capped sponsored-CHoCH order. A target handoff is different:
the prior frozen destination has already traded, the target raid has been
sponsored, reclaimed, replenished, and supported by reversal-side depth. For
that branch only, those target-consumption observations replace the redundant
pre-sweep positioning-reset gate. The existing early-flow phase test, CHoCH,
active target, cost geometry, stop, 3% sizing, and Nautilus lifecycle remain
unchanged.
"""
from __future__ import annotations

from strategy import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_v17 import EarlySponsoredChochStrategy
from strategy_v40_unfilled_target_handoff import UnfilledTargetHandoffStrategy


class TargetResetParticipationStrategy(UnfilledTargetHandoffStrategy):
    """Bypass only the generic positioning gate for a qualified target handoff."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "target_reset_early_participation_pass": 0,
                "target_reset_early_participation_phase_rejected": 0,
            },
        )

    def _early_sponsored_participation_allowed(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
        flow_3m: float,
    ) -> bool:
        if not bool(setup.details.get("target_handoff", False)):
            return super()._early_sponsored_participation_allowed(
                setup,
                row,
                flow_3m,
            )

        allowed = EarlySponsoredChochStrategy._early_sponsored_participation_allowed(
            self,
            setup,
            row,
            flow_3m,
        )
        key = (
            "target_reset_early_participation_pass"
            if allowed
            else "target_reset_early_participation_phase_rejected"
        )
        self.diagnostics[key] += 1
        return allowed


LiquidityResponseStrategy = TargetResetParticipationStrategy

__all__ = [
    "LiquidityResponseConfig",
    "LiquidityResponseStrategy",
    "TargetResetParticipationStrategy",
]

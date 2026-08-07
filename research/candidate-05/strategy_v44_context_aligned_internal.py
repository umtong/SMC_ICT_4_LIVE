#!/usr/bin/env python3
"""Candidate 05 v44: internal raids require accepted quarter-hour context.

The v43 lower-timeframe branch was designed as a pullback continuation inside a
prior information-repricing state. Its implementation treated absence of that
state as neutral, which allowed stand-alone 1m/3m reversals. In the frozen BTC
30-day evaluation those absence-neutral entries were 0/6 and lost 11.98k USDT.

v44 changes only that causal prerequisite. External 5m liquidity, target-reset
handoff, PBA, entry/stop/target geometry, fees, slippage, 3% NAV risk, order
lifecycle and NautilusTrader execution remain unchanged.
"""
from __future__ import annotations

from strategy import LiquidityResponseConfig
from strategy_v41_target_reset_participation import TargetResetParticipationStrategy


ALIGNED_CONTEXT = "ALIGNED_ACCEPTED_QUARTER_REPRICING"


class ContextAlignedInternalStrategy(TargetResetParticipationStrategy):
    """Reject an internal trap unless it is an aligned repricing pullback."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "internal_context_required_pass": 0,
                "internal_context_required_rejections": 0,
            },
        )

    def _detect_sweep(
        self,
        row: dict[str, float | int],
        previous_close: float,
    ) -> None:
        prior_scenario = None if self.pending is None else self.pending.scenario_id
        super()._detect_sweep(row, previous_close)
        setup = self.pending
        if setup is None or setup.scenario_id == prior_scenario:
            return
        if setup.details.get("hybrid_state") != "INTERNAL_INVENTORY_TRAP":
            return
        if setup.details.get("quarter_context_state") == ALIGNED_CONTEXT:
            self.diagnostics["internal_context_required_pass"] += 1
            return
        self.diagnostics["internal_context_required_rejections"] += 1
        self._expire_pending(
            row,
            "INTERNAL_TRAP_REQUIRES_ACCEPTED_ALIGNED_QUARTER_REPRICING",
        )


LiquidityResponseStrategy = ContextAlignedInternalStrategy

__all__ = [
    "ALIGNED_CONTEXT",
    "ContextAlignedInternalStrategy",
    "LiquidityResponseConfig",
    "LiquidityResponseStrategy",
]

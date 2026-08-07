#!/usr/bin/env python3
"""Candidate 05 v40: hand an unfilled, completed target to a new auction.

The parent reversal is resolved when its frozen live-liquidity target trades
before the resting entry fills. That is not permission to chase the completed
move. Instead, the consumed target becomes a new liquidity event and is
observed through the existing target-handoff rejection state machine. The
single entry order remains under cancellation until NautilusTrader confirms it
is gone; the handoff is observational during that interval.
"""
from __future__ import annotations

from strategy import LiquidityResponseConfig
from strategy import PositioningResetInventoryHybridStrategy
from target_handoff_models import PendingTargetExit


TARGET_COMPLETED_REASON = "SCENARIO_TARGET_REACHED_WHILE_ENTRY_RESTING"


class UnfilledTargetHandoffStrategy(PositioningResetInventoryHybridStrategy):
    """Promote only a newly rejected target after the original parent resolves."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "unfilled_target_handoff_watches_armed": 0,
                "unfilled_target_handoff_watch_conflicts": 0,
            },
        )

    def _request_scenario_entry_cancel(
        self,
        row: dict[str, float | int],
        reason: str,
    ) -> None:
        if reason == TARGET_COMPLETED_REASON:
            self._arm_unfilled_target_handoff(row)
        super()._request_scenario_entry_cancel(row, reason)

    def _arm_unfilled_target_handoff(
        self,
        row: dict[str, float | int],
    ) -> None:
        target = self.current_liquidity_target
        if target is None or not target.target_source.startswith("POOL:"):
            return
        if self.target_sweep_watch is not None:
            self.diagnostics["unfilled_target_handoff_watch_conflicts"] += 1
            return

        # No synthetic fill or PnL is created. These values only transport the
        # already frozen target metadata into the observational handoff helper.
        pending = PendingTargetExit(
            target=target,
            event_ts=int(row["ts"]),
            average_exit=float(target.target),
            realized_pnl=0.0,
        )
        self._arm_target_watch(pending, row)
        if self.target_sweep_watch is None:
            return
        # Mirror the already validated filled-target lifecycle: a completed
        # target bar may itself contain sponsorship, penetration, reclaim,
        # replenishment, tail inflection and directional resting depth. The new
        # pending setup is observational while the old order cancel confirms.
        self._observe_target_watch(row)
        self._promote_target_watch(row)
        self.diagnostics["unfilled_target_handoff_watches_armed"] += 1


LiquidityResponseStrategy = UnfilledTargetHandoffStrategy

__all__ = [
    "LiquidityResponseConfig",
    "LiquidityResponseStrategy",
    "TARGET_COMPLETED_REASON",
    "UnfilledTargetHandoffStrategy",
]

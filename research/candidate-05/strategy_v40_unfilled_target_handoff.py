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

import math

from logic import Pool
from strategy import LiquidityResponseConfig
from strategy import PositioningResetInventoryHybridStrategy
from target_handoff_models import CurrentLiquidityTarget
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
                "unfilled_target_handoff_metadata_missing": 0,
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
        if self.target_sweep_watch is not None:
            self.diagnostics["unfilled_target_handoff_watch_conflicts"] += 1
            return

        # Filled-position handoff metadata lives in current_liquidity_target.
        # A still-resting entry instead owns the same frozen destination through
        # v26's pending-scenario fields. Read that authoritative state directly
        # before the cancel lifecycle clears it.
        target_value = float(self.pending_scenario_target)
        pool_id = self.pending_scenario_target_pool_id
        side = int(self.entry_side)
        scenario_id = self.current_scenario_id
        if (
            not math.isfinite(target_value)
            or target_value <= 0.0
            or pool_id is None
            or side not in (-1, 1)
            or scenario_id is None
        ):
            self.diagnostics["unfilled_target_handoff_metadata_missing"] += 1
            return

        pool = self.active_pools.get(pool_id)
        if pool is None:
            pool = Pool(
                pool_id=pool_id,
                kind="HIGH" if side > 0 else "LOW",
                level=target_value,
                event_time_ns=int(row["ts"]),
                observed_time_ns=int(row["ts"]),
                source="UNFILLED_FROZEN_TARGET_SNAPSHOT",
                strength=1,
                created_index=self.bar_index,
            )
        target = CurrentLiquidityTarget(
            pool=pool,
            target=target_value,
            target_source=f"POOL:{pool_id}",
            entry_side=side,
            source_scenario_id=scenario_id,
        )
        pending = PendingTargetExit(
            target=target,
            event_ts=int(row["ts"]),
            average_exit=target_value,
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

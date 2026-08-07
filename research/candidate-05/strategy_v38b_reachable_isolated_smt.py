#!/usr/bin/env python3
"""Candidate 05 v38b: isolated SMT reversal with a reachable liquidity draw."""
from __future__ import annotations

import math

from strategy_v38_isolated_smt_reversal import IsolatedSmtReversalStrategy
from strategy_v9 import ArmedEntryPath


class ReachableIsolatedSmtReversalStrategy(IsolatedSmtReversalStrategy):
    """Require the confirmed reclaim to establish a credible target path.

    This is the only core-variable ablation permitted after the v38 logic
    failure. The selected opposing-liquidity target is not changed. At CHoCH,
    the response must have already travelled at least one third of the path from
    the swept session boundary to that frozen target:

    ``remaining target distance <= 2 * demonstrated boundary reclaim``.

    This is a measured-move validity contract, not a target optimisation. The
    multiplier is fixed and is not searched. Every detector, peer-state rule,
    stop, target identity, cost, 3% current-NAV quantity, NautilusTrader order
    lifecycle and global executable slot remains inherited unchanged.
    """

    BRANCH = "SMT_REACHABLE_ISOLATED_REVERSAL"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "smt_reachable_target_evaluations": 0,
                "smt_reachable_target_confirmations": 0,
                "smt_reachable_target_rejections": 0,
                "smt_reachable_invalid_geometry": 0,
            },
        )

    def _submit_isolated_price_cap(
        self,
        armed: ArmedEntryPath,
        row: dict[str, float | int],
    ) -> bool:
        self.diagnostics["smt_reachable_target_evaluations"] += 1
        side = int(armed.setup.side)
        target = float(self._frozen_target_price(armed))
        boundary = float(armed.details.get("session_boundary", math.nan))
        confirmation = float(armed.details.get("confirmation_close", math.nan))
        values = (target, boundary, confirmation)
        if not all(math.isfinite(value) for value in values):
            self.diagnostics["smt_reachable_invalid_geometry"] += 1
            self._expire_armed_entry(
                row,
                "REACHABILITY_GEOMETRY_IS_NOT_FINITE",
            )
            return False

        demonstrated_reclaim = side * (confirmation - boundary)
        remaining_target_distance = side * (target - confirmation)
        total_boundary_to_target = side * (target - boundary)
        completion_fraction = (
            demonstrated_reclaim / total_boundary_to_target
            if total_boundary_to_target > 0.0
            else math.nan
        )
        armed.details.update(
            {
                "target_reachability_policy": (
                    "CHOCH_COMPLETED_AT_LEAST_ONE_THIRD_OF_BOUNDARY_TO_TARGET_PATH"
                ),
                "demonstrated_boundary_reclaim": demonstrated_reclaim,
                "remaining_target_distance": remaining_target_distance,
                "boundary_to_target_distance": total_boundary_to_target,
                "boundary_to_target_completion_fraction": completion_fraction,
                "maximum_remaining_to_reclaim_multiple": 2.0,
            },
        )
        if (
            demonstrated_reclaim <= 0.0
            or remaining_target_distance <= 0.0
            or total_boundary_to_target <= 0.0
        ):
            self.diagnostics["smt_reachable_invalid_geometry"] += 1
            self._expire_armed_entry(
                row,
                "REACHABILITY_DIRECTIONAL_GEOMETRY_INVALID",
            )
            return False
        if remaining_target_distance > 2.0 * demonstrated_reclaim + 1e-12:
            self.diagnostics["smt_reachable_target_rejections"] += 1
            self._expire_armed_entry(
                row,
                "OPPOSING_LIQUIDITY_TARGET_NOT_REACHABLE_FROM_CONFIRMED_RECLAIM",
            )
            return False

        self.diagnostics["smt_reachable_target_confirmations"] += 1
        return bool(super()._submit_isolated_price_cap(armed, row))


__all__ = ["ReachableIsolatedSmtReversalStrategy"]

#!/usr/bin/env python3
"""Candidate 05 v38b: isolated SMT reversal with a reachable liquidity draw."""
from __future__ import annotations

from strategy_v38_isolated_smt_reversal import IsolatedSmtReversalStrategy
from strategy_v9 import ArmedEntryPath
from target_reachability_logic import MAX_REMAINING_TO_RECLAIM_MULTIPLE
from target_reachability_logic import measured_move_target_reachability


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
        decision = measured_move_target_reachability(
            side=int(armed.setup.side),
            session_boundary=float(armed.details.get("session_boundary", float("nan"))),
            confirmation_close=float(armed.details.get("confirmation_close", float("nan"))),
            target=float(self._frozen_target_price(armed)),
        )
        armed.details.update(
            {
                "target_reachability_policy": (
                    "CHOCH_COMPLETED_AT_LEAST_ONE_THIRD_OF_BOUNDARY_TO_TARGET_PATH"
                ),
                "target_reachability_reason_code": decision.reason_code,
                "demonstrated_boundary_reclaim": decision.demonstrated_reclaim,
                "remaining_target_distance": decision.remaining_target_distance,
                "boundary_to_target_distance": decision.boundary_to_target_distance,
                "boundary_to_target_completion_fraction": decision.completion_fraction,
                "maximum_remaining_to_reclaim_multiple": (
                    MAX_REMAINING_TO_RECLAIM_MULTIPLE
                ),
            },
        )
        if not decision.reachable:
            if "GEOMETRY" in decision.reason_code:
                self.diagnostics["smt_reachable_invalid_geometry"] += 1
            else:
                self.diagnostics["smt_reachable_target_rejections"] += 1
            self._expire_armed_entry(row, decision.reason_code)
            return False

        self.diagnostics["smt_reachable_target_confirmations"] += 1
        return bool(super()._submit_isolated_price_cap(armed, row))


__all__ = ["ReachableIsolatedSmtReversalStrategy"]

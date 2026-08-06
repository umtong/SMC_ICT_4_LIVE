"""Actual-entry order geometry for candidate-07.

The state machine emits a structural target at the confirmation close, while
NautilusTrader submits the order on the next completed one-minute bar. This
module preserves the structural target but reapplies the configured R bound at
the actual causal submission reference. It has no order, fill, account, PnL,
or portfolio logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from model import Direction, ScenarioKind


@dataclass(frozen=True, slots=True)
class AdjustedGeometry:
    target: Decimal
    risk_distance: Decimal
    reward_distance: Decimal
    expected_rr: Decimal
    target_was_clamped: bool


def adjust_target_for_submission(
    *,
    kind: ScenarioKind,
    direction: Direction,
    entry_reference: Decimal,
    stop: Decimal,
    structural_target: Decimal,
    maximum_reversal_rr: Decimal,
    continuation_rr: Decimal,
) -> AdjustedGeometry:
    """Reapply target geometry at the real next-bar submission reference."""
    if entry_reference <= 0 or stop <= 0 or structural_target <= 0:
        raise ValueError("prices must be positive")
    if maximum_reversal_rr <= 0 or continuation_rr <= 0:
        raise ValueError("R bounds must be positive")

    if direction is Direction.LONG:
        risk = entry_reference - stop
        structural_reward = structural_target - entry_reference
    else:
        risk = stop - entry_reference
        structural_reward = entry_reference - structural_target
    if risk <= 0 or structural_reward <= 0:
        raise ValueError("entry, stop, and target geometry is not tradeable")

    if kind is ScenarioKind.ACCEPTANCE_CONTINUATION:
        applied_rr = continuation_rr
        target = (
            entry_reference + risk * applied_rr
            if direction is Direction.LONG
            else entry_reference - risk * applied_rr
        )
        target_was_clamped = target != structural_target
    else:
        cap_target = (
            entry_reference + risk * maximum_reversal_rr
            if direction is Direction.LONG
            else entry_reference - risk * maximum_reversal_rr
        )
        if direction is Direction.LONG:
            target = min(structural_target, cap_target)
        else:
            target = max(structural_target, cap_target)
        target_was_clamped = target != structural_target

    reward = target - entry_reference if direction is Direction.LONG else entry_reference - target
    if reward <= 0:
        raise ValueError("adjusted target must remain favorable")
    return AdjustedGeometry(
        target=target,
        risk_distance=risk,
        reward_distance=reward,
        expected_rr=reward / risk,
        target_was_clamped=target_was_clamped,
    )

"""V30 accepted-auction pullback-leg invalidation and expansion objective.

V29 proved that outside initiatives and accepted counterflow pullbacks occur
frequently, but its stop and target still belonged to the original initiative.
V30 treats a break of the completed pullback as a new auction leg:

* invalidate beyond the completed pullback swing, not the complete impulse;
* primary target the second structure-width expansion node, because the first
  node is already partly traversed before the conditional entry exists;
* ablation target the first node while keeping every other rule identical.
"""
from __future__ import annotations

from dataclasses import replace

from core import Side
from impact_elasticity_resumption_v28_nautilus_week import (
    InitiativeSetup,
    SIDE_COST_BUFFER_FRACTION,
)
from impact_regime_probe import EventFeature
from impact_resumption_stop_state_v29 import (
    PullbackResumptionEntryStateMachine,
)


PRIMARY_EXPANSION_MULTIPLE = 2.0
CONTROL_EXPANSION_MULTIPLE = 1.0


class AcceptedPullbackExpansionStateMachine(
    PullbackResumptionEntryStateMachine,
):
    """Conditional pullback break with pullback-swing invalidation."""

    def __init__(self, *, target_multiple: float) -> None:
        if target_multiple not in (
            PRIMARY_EXPANSION_MULTIPLE,
            CONTROL_EXPANSION_MULTIPLE,
        ):
            raise ValueError("v30 target_multiple must be the frozen 1x or 2x node")
        super().__init__()
        self.target_multiple = float(target_multiple)

    def _expansion_target(self, setup: InitiativeSetup) -> float:
        return (
            setup.boundary + self.target_multiple * setup.structure_width
            if setup.side is Side.LONG
            else setup.boundary - self.target_multiple * setup.structure_width
        )

    def _target_touched(
        self,
        setup: InitiativeSetup,
        feature: EventFeature,
    ) -> bool:
        target = self._expansion_target(setup)
        return (
            float(feature.bar.high) >= target
            if setup.side is Side.LONG
            else float(feature.bar.low) <= target
        )

    def _stop_and_target(
        self,
        setup: InitiativeSetup,
    ) -> tuple[float, float]:
        if setup.pullback_low is None or setup.pullback_high is None:
            raise RuntimeError("v30 stop requires the completed pullback swing")
        target = self._expansion_target(setup)
        if setup.side is Side.LONG:
            stop = float(setup.pullback_low) * (
                1.0 - SIDE_COST_BUFFER_FRACTION
            )
        else:
            stop = float(setup.pullback_high) * (
                1.0 + SIDE_COST_BUFFER_FRACTION
            )
        return float(stop), float(target)

    def _arm_conditional_entry(self, **kwargs: object) -> None:
        instruction_start = len(self.stop_instructions)
        transition_start = len(self.transitions)
        super()._arm_conditional_entry(**kwargs)
        if len(self.stop_instructions) == instruction_start:
            return

        reason = (
            "ACCEPTED_PULLBACK_BREAK_TO_SECOND_EXPANSION_NODE"
            if self.target_multiple == PRIMARY_EXPANSION_MULTIPLE
            else "ACCEPTED_PULLBACK_BREAK_TO_FIRST_EXPANSION_NODE_CONTROL"
        )
        plan = replace(self.market_plans[-1], reason_code=reason)
        self.market_plans[-1] = plan
        self.stop_instructions[-1] = replace(
            self.stop_instructions[-1],
            plan=plan,
            entry_reason=(
                "BREAK_PULLBACK_SWING_TOWARD_SECOND_EXPANSION"
                if self.target_multiple == PRIMARY_EXPANSION_MULTIPLE
                else "BREAK_PULLBACK_SWING_TOWARD_FIRST_EXPANSION_CONTROL"
            ),
        )
        self.entry_decisions[-1] = replace(
            self.entry_decisions[-1],
            stop_price=float(plan.stop_price),
            target_price=float(plan.target_price),
            reason_code=reason,
        )
        for position in range(transition_start, len(self.transitions)):
            row = self.transitions[position]
            if row.event_type == "STOP_LIMIT_INSTRUCTION_ARMED":
                self.transitions[position] = replace(row, reason_code=reason)
        self.counts[
            "second_expansion_instructions"
            if self.target_multiple == PRIMARY_EXPANSION_MULTIPLE
            else "first_expansion_control_instructions"
        ] += 1

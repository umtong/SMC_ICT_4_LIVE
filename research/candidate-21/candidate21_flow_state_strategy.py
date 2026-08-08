"""State-resolved synchronized flow sweep.

This strategy keeps the full-size GTC market sweep, one-hour/three-hour context,
structural stop, 3% continuous-NAV risk and four-hour/funding exit unchanged.
Before entry it additionally separates persistent sponsorship and delayed price
discovery from flow/efficiency decay using only the already frozen parent and
immediate response observations.
"""
from __future__ import annotations

from candidate21_flow_sweep_strategy import Candidate21FlowSweepConfig
from candidate21_flow_sweep_strategy import Candidate21FlowSweepStrategy
from flow_transition_logic import FlowTransitionRoute
from flow_transition_logic import classify_flow_transition
from flow_transition_logic import evidence_from_router_details
from strategy_base import PendingSetup


class Candidate21FlowStateConfig(Candidate21FlowSweepConfig, frozen=True):
    """No new numeric parameters."""


class Candidate21FlowStateStrategy(Candidate21FlowSweepStrategy):
    """Trade only sponsored or newly released price-discovery transitions."""

    def __init__(self, config: Candidate21FlowStateConfig) -> None:
        super().__init__(config=config)
        self.diagnostics.update(
            {
                "candidate21_flow_state_persistent": 0,
                "candidate21_flow_state_delayed": 0,
                "candidate21_flow_state_decayed": 0,
                "candidate21_flow_state_incomplete": 0,
            },
        )

    def _submit_event_entry(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
    ) -> bool:
        if setup.branch != "ACCEPTANCE":
            return super()._submit_event_entry(setup, row)
        raw = setup.details.get("event_time_response")
        if not isinstance(raw, dict):
            self.diagnostics["candidate21_flow_state_incomplete"] = int(
                self.diagnostics["candidate21_flow_state_incomplete"],
            ) + 1
            return self._close_without_trade(
                setup,
                row,
                "MISSING_FROZEN_PARENT_RESPONSE_FOR_FLOW_STATE",
                {},
            )
        try:
            decision = classify_flow_transition(
                evidence_from_router_details(raw),
            )
        except ValueError as exc:
            self.diagnostics["candidate21_flow_state_incomplete"] = int(
                self.diagnostics["candidate21_flow_state_incomplete"],
            ) + 1
            return self._close_without_trade(
                setup,
                row,
                "INCOMPLETE_PARENT_RESPONSE_FOR_FLOW_STATE",
                {"flow_state_error": str(exc)},
            )

        setup.details["candidate21_flow_transition"] = decision.details()
        if not decision.eligible:
            self.diagnostics["candidate21_flow_state_decayed"] = int(
                self.diagnostics["candidate21_flow_state_decayed"],
            ) + 1
            return self._close_without_trade(
                setup,
                row,
                decision.reason,
                {
                    "candidate21_flow_transition": decision.details(),
                },
            )
        if decision.route is FlowTransitionRoute.PERSISTENT_SPONSORSHIP:
            key = "candidate21_flow_state_persistent"
        else:
            key = "candidate21_flow_state_delayed"
        self.diagnostics[key] = int(self.diagnostics[key]) + 1
        return super()._submit_event_entry(setup, row)


__all__ = [
    "Candidate21FlowStateConfig",
    "Candidate21FlowStateStrategy",
]

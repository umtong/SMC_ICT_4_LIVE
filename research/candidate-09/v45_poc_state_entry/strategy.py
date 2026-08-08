"""Candidate 09 v45: enter when outside POC migration completes.

V42's state evidence removed seven of eight losing price-only trades, but 40 of
64 valid states never revisited the consumed boundary and therefore expired.
V45 changes no context or state rule.  After the exact V42 price acceptance and
consecutive outside POC migration have completed, the current state-completion
bar owns entry, invalidation and the remaining natural objective.  The exact
control leaves V42's first-retest execution unchanged.
"""
from __future__ import annotations

from effort_result_router import AuctionDecision
from strategy_v42 import Candidate16Config as _Candidate42Config
from strategy_v42 import Candidate16Strategy as _Candidate42Strategy


class Candidate16Config(_Candidate42Config, frozen=True):
    candidate45_enter_on_state_completion: bool = True


class Candidate16Strategy(_Candidate42Strategy):
    """Exact V42 state with either immediate state entry or frozen retest entry."""

    def __init__(self, config: Candidate16Config) -> None:
        super().__init__(config=config)
        self.diagnostics.update(
            {
                "candidate45_state_completion_entries_attempted": 0,
                "candidate45_state_completion_entries_submitted": 0,
                "candidate45_retest_control_paths": 0,
            }
        )

    def _complete_parent(self, row: dict[str, float | int]) -> None:
        state = self.parent_auction
        was_acceptance = (
            state is not None
            and state.decision is AuctionDecision.ACCEPTANCE_CONTINUATION
        )
        super()._complete_parent(row)
        if not was_acceptance or self.pending is None:
            return
        if self.pending.branch != "ACCEPTANCE":
            return
        if not bool(self.pending.details.get("candidate42_poc_migration_pass", False)):
            return
        if not self.config.candidate45_enter_on_state_completion:
            self.diagnostics["candidate45_retest_control_paths"] = int(
                self.diagnostics["candidate45_retest_control_paths"]
            ) + 1
            self.pending.details["candidate45_execution"] = "FIRST_BOUNDARY_RETEST"
            return

        self.pending.details["candidate45_execution"] = "POC_STATE_COMPLETION_BAR"
        self.diagnostics["candidate45_state_completion_entries_attempted"] = int(
            self.diagnostics["candidate45_state_completion_entries_attempted"]
        ) + 1
        scenario_id = self.pending.scenario_id
        self._transition(
            scenario_id,
            "POC_VALUE_ACCEPTANCE_ENTRY_EVALUATION",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_EVALUATION",
            "NEW_TRADED_VALUE_COMPLETED_OUTSIDE_CONSUMED_BOUNDARY",
            float(row["close"]),
            self.pending.details,
        )
        if self._submit_entry(self.pending, row):
            self.diagnostics["candidate45_state_completion_entries_submitted"] = int(
                self.diagnostics["candidate45_state_completion_entries_submitted"]
            ) + 1


__all__ = ["Candidate16Config", "Candidate16Strategy"]

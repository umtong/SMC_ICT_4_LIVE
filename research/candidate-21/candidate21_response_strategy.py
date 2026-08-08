"""Candidate 21 v2: same-minute auction resiliency router.

The first ten seconds define the boundary shock. The remaining fifty seconds
are a strictly later response window. Classification therefore completes at
the boundary minute close rather than waiting one to three additional bars and
consuming the target geometry.
"""
from __future__ import annotations

from dataclasses import asdict

from boundary_response_router import BoundaryResponse
from boundary_response_router import BoundaryResponseDecision
from boundary_response_router import classify_boundary_response
from candidate21_strategy import Candidate21Config
from candidate21_strategy import Candidate21Strategy
from strategy_base import PendingSetup


Candidate21ResponseConfig = Candidate21Config


class Candidate21ResponseStrategy(Candidate21Strategy):
    """Resolve the opening shock from its non-overlapping 10-60s response."""

    def __init__(self, config: Candidate21ResponseConfig) -> None:
        super().__init__(config=config)
        self.diagnostics.update(
            {
                "candidate21_response_classified": 0,
                "candidate21_response_acceptance": 0,
                "candidate21_response_failed_auction": 0,
                "candidate21_response_unresolved": 0,
            },
        )

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        armed_before = int(self.diagnostics["candidate21_clock_events_armed"])
        super()._detect_sweep(row, previous_close)
        if int(self.diagnostics["candidate21_clock_events_armed"]) == armed_before:
            return

        setup = self.pending
        state = self.clock_auction
        if (
            setup is None
            or state is None
            or setup.branch != "CLOCK_AUCTION"
            or setup.created_index != self.bar_index
        ):
            return

        response = BoundaryResponse(
            direction=state.direction,
            boundary_level=state.boundary_level,
            opening_close=self._feature("qh_open_close"),
            minute_close=float(row["close"]),
            minute_notional=self._feature("notional_60s"),
            minute_flow=self._feature("flow_60s"),
            opening_notional=self._feature("qh_open_notional"),
            opening_signed_notional=self._feature("qh_open_signed_notional"),
            depth_imbalance_1=self._feature("depth_imbalance_1"),
            liquidity_ahead_change_1m=self._feature(
                self._ahead_depth_field(state.direction),
            ),
        )
        decision, reason, response_details = classify_boundary_response(response)
        setup.details["boundary_response"] = response_details
        setup.details["boundary_response_decision"] = decision.value
        setup.details["boundary_response_reason"] = reason
        setup.details["response_event_window_end_ns"] = (
            int(row["ts"]) // 60_000_000_000 * 60_000_000_000 + 10_000_000_000
        )
        setup.details["response_observed_time_ns"] = int(row["ts"])
        self.diagnostics["candidate21_response_classified"] = int(
            self.diagnostics["candidate21_response_classified"],
        ) + 1
        self._transition(
            setup.scenario_id,
            "BOUNDARY_RESPONSE_CLASSIFIED",
            int(setup.details["response_event_window_end_ns"]),
            int(row["ts"]),
            decision.value,
            reason,
            float(row["close"]),
            setup.details,
        )

        if decision is BoundaryResponseDecision.UNRESOLVED:
            self.diagnostics["candidate21_response_unresolved"] = int(
                self.diagnostics["candidate21_response_unresolved"],
            ) + 1
            self.diagnostics["candidate21_unresolved"] = int(
                self.diagnostics["candidate21_unresolved"],
            ) + 1
            self.pending = None
            self.clock_auction = None
            return

        terminal = asdict(state)
        terminal["decision"] = decision.value
        if decision is BoundaryResponseDecision.ACCEPTANCE:
            if not self.config.enable_acceptance:
                self.pending = None
                self.clock_auction = None
                return
            branch = "ACCEPTANCE"
            side = state.direction
            natural_target = state.acceptance_target
            source = "PRIOR_BALANCE_MEASURED_EXPANSION"
            self.diagnostics["candidate21_response_acceptance"] = int(
                self.diagnostics["candidate21_response_acceptance"],
            ) + 1
            self.diagnostics["candidate21_acceptance_confirmed"] = int(
                self.diagnostics["candidate21_acceptance_confirmed"],
            ) + 1
        else:
            if not self.config.enable_rejection:
                self.pending = None
                self.clock_auction = None
                return
            branch = "REJECTION"
            side = -state.direction
            natural_target = state.rejection_target
            source = "PRIOR_BALANCE_OPPOSITE_EDGE"
            self.diagnostics["candidate21_response_failed_auction"] = int(
                self.diagnostics["candidate21_response_failed_auction"],
            ) + 1
            self.diagnostics["candidate21_failed_auction_confirmed"] = int(
                self.diagnostics["candidate21_failed_auction_confirmed"],
            ) + 1

        completed = PendingSetup(
            scenario_id=setup.scenario_id,
            branch=branch,
            side=side,
            swept_kind=setup.swept_kind,
            pool_id=setup.pool_id,
            pool_level=setup.pool_level,
            created_index=self.bar_index,
            expires_index=self.bar_index,
            sweep_extreme=setup.sweep_extreme,
            structure=setup.structure,
            atr=setup.atr,
            hold_count=0,
            retrace_armed=False,
            details={
                **setup.details,
                "candidate21_branch": decision.value,
                "natural_target": natural_target,
                "natural_target_source": source,
                "same_minute_non_overlapping_response": True,
                "terminal_clock_router_state": terminal,
            },
        )
        self.pending = completed
        self.clock_auction = None
        self._transition(
            completed.scenario_id,
            "CLOCK_AUCTION_COMPLETED",
            int(setup.details["response_event_window_end_ns"]),
            int(row["ts"]),
            "ENTRY_EVALUATION",
            reason,
            float(row["close"]),
            completed.details,
        )
        self._submit_entry(completed, row)


__all__ = ["Candidate21ResponseConfig", "Candidate21ResponseStrategy"]

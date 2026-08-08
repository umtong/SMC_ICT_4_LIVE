"""Candidate 09 v47: apparent acceptance whose traded value fails to migrate.

This is not the inherited immediate failed-auction reversal.  Price must first
complete the sequential true-acceptance state outside a completed-auction
boundary.  If consecutive outside footprint POC migration does not complete,
the apparent acceptance becomes a latent failed-value-migration state.  It owns
no order.  A later bar must reaccept the old boundary with opposite trade flow,
opposite footprint delta, inside POC and directional close location; that new
reentry leg owns reversal execution.

The exact control removes only the prior POC-migration-failure requirement.  It
still waits for the same later boundary reacceptance and uses identical entry,
invalidation, natural objective, costs, risk and NautilusTrader execution.
"""
from __future__ import annotations

import math
from typing import Any

from effort_result_router import AuctionDecision
from strategy_base import PendingSetup
from strategy_v42 import Candidate16Config as _Candidate42Config
from strategy_v42 import Candidate16Strategy as _Candidate42Strategy


class Candidate16Config(_Candidate42Config, frozen=True):
    candidate47_require_poc_migration_failure: bool = True
    candidate47_reentry_timeout_bars: int = 3


class Candidate16Strategy(_Candidate42Strategy):
    """Delayed reversal after accepted price fails to establish outside value."""

    def __init__(self, config: Candidate16Config) -> None:
        super().__init__(config=config)
        self.diagnostics.update(
            {
                "candidate47_acceptances_evaluated": 0,
                "candidate47_failed_value_states_armed": 0,
                "candidate47_migrated_value_no_trades": 0,
                "candidate47_control_states_armed": 0,
                "candidate47_boundary_reentries": 0,
                "candidate47_reentry_timeouts": 0,
                "candidate47_acceptance_extensions": 0,
                "candidate47_reversal_entries": 0,
            }
        )

    def _candidate47_migration_details(self, state: Any) -> tuple[bool, dict[str, Any]]:
        terminal_outside = (
            math.isfinite(self._candidate42_terminal_poc)
            and state.direction
            * (self._candidate42_terminal_poc - state.pool_level)
            > 0.0
        )
        directional_migration = (
            math.isfinite(self._candidate42_first_outside_poc)
            and math.isfinite(self._candidate42_latest_outside_poc)
            and state.direction
            * (
                self._candidate42_latest_outside_poc
                - self._candidate42_first_outside_poc
            )
            >= 0.0
        )
        passed = (
            self._candidate42_max_outside_poc_streak
            >= self.config.candidate42_min_consecutive_outside_poc_bars
            and terminal_outside
            and directional_migration
        )
        return passed, {
            "candidate47_poc_observations": self._candidate42_poc_observations,
            "candidate47_max_outside_poc_streak": (
                self._candidate42_max_outside_poc_streak
            ),
            "candidate47_first_outside_poc": self._candidate42_first_outside_poc,
            "candidate47_latest_outside_poc": self._candidate42_latest_outside_poc,
            "candidate47_terminal_poc": self._candidate42_terminal_poc,
            "candidate47_terminal_poc_outside": terminal_outside,
            "candidate47_directional_poc_migration": directional_migration,
            "candidate47_poc_migration_pass": passed,
        }

    def _candidate47_close_parent(
        self,
        row: dict[str, float | int],
        reason: str,
        details: dict[str, Any],
    ) -> None:
        self._close_parent_without_trade(row, reason, details)
        self._candidate42_reset_poc_state()

    def _complete_parent(self, row: dict[str, float | int]) -> None:
        state = self.parent_auction
        setup = self.pending
        if state is None or setup is None:
            return
        if state.decision is not AuctionDecision.ACCEPTANCE_CONTINUATION:
            super()._complete_parent(row)
            self._candidate42_reset_poc_state()
            return

        self.diagnostics["candidate47_acceptances_evaluated"] = int(
            self.diagnostics["candidate47_acceptances_evaluated"]
        ) + 1
        migration_pass, migration = self._candidate47_migration_details(state)
        details = {
            **setup.details,
            **migration,
            "candidate47_require_poc_migration_failure": (
                self.config.candidate47_require_poc_migration_failure
            ),
            "candidate47_terminal_router_state": self._state_details(state),
            "candidate47_acceptance_direction": state.direction,
        }
        if self.config.candidate47_require_poc_migration_failure and migration_pass:
            self.diagnostics["candidate47_migrated_value_no_trades"] = int(
                self.diagnostics["candidate47_migrated_value_no_trades"]
            ) + 1
            self._candidate47_close_parent(
                row,
                "ACCEPTED_PRICE_AND_TRADED_VALUE_MIGRATED_TOGETHER",
                details,
            )
            return

        acceptance_direction = state.direction
        side = -acceptance_direction
        self.pending = PendingSetup(
            scenario_id=setup.scenario_id,
            branch="FAILED_VALUE_MIGRATION_REENTRY",
            side=side,
            swept_kind=setup.swept_kind,
            pool_id=setup.pool_id,
            pool_level=setup.pool_level,
            created_index=self.bar_index,
            expires_index=self.bar_index + self.config.candidate47_reentry_timeout_bars,
            sweep_extreme=setup.sweep_extreme,
            structure=setup.pool_level,
            atr=setup.atr,
            hold_count=0,
            retrace_armed=False,
            details={
                **details,
                "candidate47_state_completed_ts_ns": int(row["ts"]),
            },
        )
        self.parent_auction = None
        if migration_pass:
            self.diagnostics["candidate47_control_states_armed"] = int(
                self.diagnostics["candidate47_control_states_armed"]
            ) + 1
        else:
            self.diagnostics["candidate47_failed_value_states_armed"] = int(
                self.diagnostics["candidate47_failed_value_states_armed"]
            ) + 1
        self._transition(
            setup.scenario_id,
            "FAILED_VALUE_MIGRATION_STATE_FROZEN",
            int(row["ts"]),
            int(row["ts"]),
            "AWAITING_OLD_BOUNDARY_REACCEPTANCE",
            "APPARENT_PRICE_ACCEPTANCE_OWNS_NO_REVERSAL_ORDER",
            float(row["close"]),
            self.pending.details,
        )
        self._candidate42_reset_poc_state()

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        if setup is None or setup.branch != "FAILED_VALUE_MIGRATION_REENTRY":
            return super()._process_pending(row)
        if self.bar_index > setup.expires_index:
            self.diagnostics["candidate47_reentry_timeouts"] = int(
                self.diagnostics["candidate47_reentry_timeouts"]
            ) + 1
            self._expire_pending(row, "OLD_BOUNDARY_REACCEPTANCE_EXPIRED")
            return False
        if self.bar_index <= setup.created_index:
            return True

        acceptance_direction = -setup.side
        close = float(row["close"])
        extended = (
            close > setup.sweep_extreme
            if acceptance_direction > 0
            else close < setup.sweep_extreme
        )
        if extended:
            self.diagnostics["candidate47_acceptance_extensions"] = int(
                self.diagnostics["candidate47_acceptance_extensions"]
            ) + 1
            self._expire_pending(row, "APPARENT_ACCEPTANCE_EXTENDED_BEYOND_PARENT_EXTREME")
            return False

        reentered = (
            close < setup.pool_level
            if acceptance_direction > 0
            else close > setup.pool_level
        )
        if not reentered:
            return True
        side = setup.side
        flow = side * self._feature("flow_60s")
        footprint_delta = side * self._feature("footprint_delta_60s")
        poc = self._feature("footprint_poc_price")
        poc_inside_old_value = (
            math.isfinite(poc)
            and acceptance_direction * (poc - setup.pool_level) <= 0.0
        )
        span = max(float(row["high"]) - float(row["low"]), 1e-12)
        close_location = (
            (close - float(row["low"])) / span
            if side > 0
            else (float(row["high"]) - close) / span
        )
        passed = (
            flow >= self.config.acceptance_flow_min
            and footprint_delta > 0.0
            and poc_inside_old_value
            and close_location >= self.config.acceptance_close_location
        )
        setup.details["candidate47_latest_reentry_evidence"] = {
            "directional_flow": flow,
            "directional_footprint_delta": footprint_delta,
            "footprint_poc_price": poc,
            "poc_inside_old_value": poc_inside_old_value,
            "close_location": close_location,
            "passed": passed,
        }
        if not passed:
            return True

        self.diagnostics["candidate47_boundary_reentries"] = int(
            self.diagnostics["candidate47_boundary_reentries"]
        ) + 1
        setup.branch = "REJECTION"
        self._transition(
            setup.scenario_id,
            "OLD_BOUNDARY_REACCEPTED_WITH_VALUE_RETURN",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_EVALUATION",
            "NEW_REVERSAL_LEG_OWNS_EXECUTION",
            close,
            setup.details,
        )
        self.diagnostics["candidate47_reversal_entries"] = int(
            self.diagnostics["candidate47_reversal_entries"]
        ) + 1
        return self._submit_entry(setup, row)


__all__ = ["Candidate16Config", "Candidate16Strategy"]

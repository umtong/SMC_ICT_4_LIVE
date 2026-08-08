"""Candidate 09 v46: first retest of migrated POC, not the consumed boundary.

V42 defines a new accepted auction only after price holds outside a completed-
auction boundary and footprint POC remains outside on consecutive completed
observations.  Once that new traded-value center exists, V46 arms the first
retest of the migrated POC.  The old consumed boundary still owns invalidation
and the next unconsumed completed-auction pool still owns the target.

The exact control leaves V42's first old-boundary retest unchanged.  Context,
state, invalidation, target, costs, risk and NautilusTrader execution are fixed.
"""
from __future__ import annotations

import math

from strategy_v42 import Candidate16Config as _Candidate42Config
from strategy_v42 import Candidate16Strategy as _Candidate42Strategy


class Candidate16Config(_Candidate42Config, frozen=True):
    candidate46_retest_migrated_poc: bool = True


class Candidate16Strategy(_Candidate42Strategy):
    """Exact V42 state with a new-value-center retest execution."""

    def __init__(self, config: Candidate16Config) -> None:
        super().__init__(config=config)
        self.diagnostics.update(
            {
                "candidate46_poc_retests_armed": 0,
                "candidate46_poc_retest_entries": 0,
                "candidate46_poc_retest_timeouts": 0,
                "candidate46_old_boundary_invalidations": 0,
                "candidate46_boundary_retest_control_paths": 0,
            }
        )

    def _complete_parent(self, row: dict[str, float | int]) -> None:
        super()._complete_parent(row)
        if self.pending is None or self.pending.branch != "ACCEPTANCE":
            return
        if not bool(self.pending.details.get("candidate42_poc_migration_pass", False)):
            return
        if not self.config.candidate46_retest_migrated_poc:
            self.diagnostics["candidate46_boundary_retest_control_paths"] = int(
                self.diagnostics["candidate46_boundary_retest_control_paths"]
            ) + 1
            self.pending.details["candidate46_execution"] = "OLD_BOUNDARY_RETEST"
            return

        migrated_poc = float(self.pending.details["candidate42_terminal_poc"])
        if not math.isfinite(migrated_poc):
            self._expire_pending(row, "MIGRATED_POC_NOT_FINITE")
            return
        self.pending.branch = "ACCEPTANCE_POC_RETEST"
        self.pending.details.update(
            {
                "candidate46_execution": "MIGRATED_POC_RETEST",
                "candidate46_old_boundary": self.pending.pool_level,
                "candidate46_migrated_poc": migrated_poc,
            }
        )
        self.diagnostics["candidate46_poc_retests_armed"] = int(
            self.diagnostics["candidate46_poc_retests_armed"]
        ) + 1
        self._transition(
            self.pending.scenario_id,
            "MIGRATED_POC_RETEST_ARMED",
            int(row["ts"]),
            int(row["ts"]),
            "NEW_VALUE_RETEST_ARMED",
            "CONSECUTIVE_OUTSIDE_POC_NOW_OWNS_PULLBACK_LOCATION",
            migrated_poc,
            self.pending.details,
        )

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        if setup is None or setup.branch != "ACCEPTANCE_POC_RETEST":
            return super()._process_pending(row)
        if self.bar_index > setup.expires_index:
            self.diagnostics["candidate46_poc_retest_timeouts"] = int(
                self.diagnostics["candidate46_poc_retest_timeouts"]
            ) + 1
            self._expire_pending(row, "MIGRATED_POC_RETEST_EXPIRED")
            return False
        if self.bar_index <= setup.created_index:
            return True

        side = setup.side
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return True
        close = float(row["close"])
        old_boundary = float(setup.details["candidate46_old_boundary"])
        migrated_poc = float(setup.details["candidate46_migrated_poc"])
        old_boundary_distance = side * (close - old_boundary) / atr
        if old_boundary_distance < -self.config.acceptance_hold_tolerance_atr:
            self.diagnostics["candidate46_old_boundary_invalidations"] = int(
                self.diagnostics["candidate46_old_boundary_invalidations"]
            ) + 1
            self._expire_pending(row, "OLD_AUCTION_BOUNDARY_REACCEPTED")
            return False

        touched = (
            float(row["low"])
            <= migrated_poc + self.config.acceptance_retrace_tolerance_atr * atr
            if side > 0
            else float(row["high"])
            >= migrated_poc - self.config.acceptance_retrace_tolerance_atr * atr
        )
        held_new_value = side * (close - migrated_poc) >= 0.0
        tail_flow = side * self._feature("flow_15s")
        span = max(float(row["high"]) - float(row["low"]), 1e-12)
        close_location = (
            (close - float(row["low"])) / span
            if side > 0
            else (float(row["high"]) - close) / span
        )
        if not (
            touched
            and held_new_value
            and tail_flow >= -self.config.acceptance_max_counterflow
            and close_location >= self.config.acceptance_retest_close_location
        ):
            return True

        setup.branch = "ACCEPTANCE"
        setup.pool_level = old_boundary
        setup.details.update(
            {
                "candidate46_poc_retest_tail_flow": tail_flow,
                "candidate46_poc_retest_close_location": close_location,
                "candidate46_poc_retest_ts_ns": int(row["ts"]),
            }
        )
        self.diagnostics["candidate46_poc_retest_entries"] = int(
            self.diagnostics["candidate46_poc_retest_entries"]
        ) + 1
        return self._submit_entry(setup, row)


__all__ = ["Candidate16Config", "Candidate16Strategy"]

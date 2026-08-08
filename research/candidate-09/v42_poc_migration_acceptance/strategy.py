"""Candidate 09 v42: completed-auction acceptance with migrating traded value.

V35 showed that completed-auction boundaries were materially better contexts
than micro pivots, but its three-level stacked-imbalance ownership was too rare.
V42 does not loosen that detector.  It replaces the market-state evidence with
a distinct auction-market mechanism: after a completed-auction boundary is
consumed, the minute footprint POC must migrate outside the old boundary and
remain there on consecutive completed observations.  Price acceptance defines
the state, POC migration proves that new traded value formed outside, and the
first later defended boundary retest owns execution.

Failed and unresolved parent auctions are no-trades.  The exact control removes
only POC-migration ownership; context, price state, retest, invalidation,
natural objective, costs, risk and NautilusTrader execution remain unchanged.
"""
from __future__ import annotations

import math
from typing import Any

from effort_result_router import AuctionDecision
from strategy_v1 import Candidate16Strategy as _Candidate16V1Strategy
from strategy_v35 import Candidate16Config as _Candidate35Config
from strategy_v35 import Candidate16Strategy as _Candidate35Strategy


class Candidate16Config(_Candidate35Config, frozen=True):
    candidate42_require_poc_migration: bool = True
    candidate42_min_consecutive_outside_poc_bars: int = 2


class Candidate16Strategy(_Candidate35Strategy):
    """True acceptance whose new value area is observed through footprint POC."""

    def __init__(self, config: Candidate16Config) -> None:
        super().__init__(config=config)
        if config.candidate42_min_consecutive_outside_poc_bars < 2:
            raise ValueError("POC migration requires at least two completed observations")
        self._candidate42_reset_poc_state()
        self.diagnostics.update(
            {
                "candidate42_acceptances_evaluated": 0,
                "candidate42_poc_migration_passes": 0,
                "candidate42_poc_migration_blocks": 0,
                "candidate42_price_only_control_paths": 0,
                "candidate42_max_outside_poc_streak_observed": 0,
            }
        )

    def _candidate42_reset_poc_state(self) -> None:
        self._candidate42_outside_poc_streak = 0
        self._candidate42_max_outside_poc_streak = 0
        self._candidate42_first_outside_poc = math.nan
        self._candidate42_latest_outside_poc = math.nan
        self._candidate42_terminal_poc = math.nan
        self._candidate42_poc_observations = 0

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._candidate42_reset_poc_state()

    def _expire_pending(self, row: dict[str, float | int], reason: str) -> None:
        super()._expire_pending(row, reason)
        if self.parent_auction is None:
            self._candidate42_reset_poc_state()

    def _router_observation(
        self,
        row: dict[str, float | int],
        direction: int,
    ):
        if self.parent_auction is not None and self.parent_auction.observations == 0:
            self._candidate42_reset_poc_state()
        observation = super()._router_observation(row, direction)
        state = self.parent_auction
        if state is None:
            return observation

        poc = self._feature("footprint_poc_price")
        self._candidate42_terminal_poc = poc
        if not math.isfinite(poc):
            self._candidate42_outside_poc_streak = 0
            return observation

        self._candidate42_poc_observations += 1
        outside = direction * (poc - state.pool_level) > 0.0
        if outside:
            self._candidate42_outside_poc_streak += 1
            self._candidate42_max_outside_poc_streak = max(
                self._candidate42_max_outside_poc_streak,
                self._candidate42_outside_poc_streak,
            )
            if not math.isfinite(self._candidate42_first_outside_poc):
                self._candidate42_first_outside_poc = poc
            self._candidate42_latest_outside_poc = poc
        else:
            self._candidate42_outside_poc_streak = 0
        self.diagnostics["candidate42_max_outside_poc_streak_observed"] = max(
            int(self.diagnostics["candidate42_max_outside_poc_streak_observed"]),
            self._candidate42_max_outside_poc_streak,
        )
        return observation

    def _candidate42_close_without_trade(
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

        self.diagnostics["candidate42_acceptances_evaluated"] = int(
            self.diagnostics["candidate42_acceptances_evaluated"]
        ) + 1
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
        migration_pass = (
            self._candidate42_max_outside_poc_streak
            >= self.config.candidate42_min_consecutive_outside_poc_bars
            and terminal_outside
            and directional_migration
        )
        details = {
            **setup.details,
            "candidate42_require_poc_migration": (
                self.config.candidate42_require_poc_migration
            ),
            "candidate42_min_consecutive_outside_poc_bars": (
                self.config.candidate42_min_consecutive_outside_poc_bars
            ),
            "candidate42_poc_observations": self._candidate42_poc_observations,
            "candidate42_max_outside_poc_streak": (
                self._candidate42_max_outside_poc_streak
            ),
            "candidate42_first_outside_poc": self._candidate42_first_outside_poc,
            "candidate42_latest_outside_poc": self._candidate42_latest_outside_poc,
            "candidate42_terminal_poc": self._candidate42_terminal_poc,
            "candidate42_terminal_poc_outside": terminal_outside,
            "candidate42_directional_poc_migration": directional_migration,
            "candidate42_poc_migration_pass": migration_pass,
            "candidate42_terminal_router_state": self._state_details(state),
        }

        if self.config.candidate42_require_poc_migration and not migration_pass:
            self.diagnostics["candidate42_poc_migration_blocks"] = int(
                self.diagnostics["candidate42_poc_migration_blocks"]
            ) + 1
            self._candidate42_close_without_trade(
                row,
                "TRUE_ACCEPTANCE_WITHOUT_CONSECUTIVE_OUTSIDE_POC_MIGRATION",
                details,
            )
            return

        if migration_pass:
            self.diagnostics["candidate42_poc_migration_passes"] = int(
                self.diagnostics["candidate42_poc_migration_passes"]
            ) + 1
        else:
            self.diagnostics["candidate42_price_only_control_paths"] = int(
                self.diagnostics["candidate42_price_only_control_paths"]
            ) + 1

        # V33's stack ownership is intentionally bypassed only after the V42
        # state has independently completed.  The frozen V1 acceptance method
        # still owns first-retest execution, invalidation and target geometry.
        setup.details.update(details)
        _Candidate16V1Strategy._complete_parent(self, row)
        if self.pending is not None:
            self.pending.details.update(details)
        self._reset_stack()
        self._candidate42_reset_poc_state()


__all__ = ["Candidate16Config", "Candidate16Strategy"]

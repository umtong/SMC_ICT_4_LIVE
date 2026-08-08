"""Candidate 09 v33: price-level stacked-imbalance acceptance continuation.

The parent liquidity interaction, costs, natural targets, risk sizing and
NautilusTrader lifecycle are reused from Candidate 16 v1 / Candidate 05. Failed
auctions are explicit no-trades. A continuation is eligible only after true
outside acceptance and a directional price-level footprint stack crossing the
consumed boundary; entry remains the first later defended retest.
"""
from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any

from effort_result_router import AuctionDecision
from strategy_v1 import Candidate16Config as _Candidate16V1Config
from strategy_v1 import Candidate16Strategy as _Candidate16V1Strategy


class Candidate16Config(_Candidate16V1Config, frozen=True):
    candidate33_require_stacked_imbalance: bool = True
    candidate33_min_stacked_levels: int = 3
    candidate33_stack_boundary_tolerance_atr: float = 0.25
    candidate33_trade_failed_auction: bool = False


class Candidate16Strategy(_Candidate16V1Strategy):
    """True-acceptance continuation with a distinct price-level state channel."""

    def __init__(self, config: Candidate16Config) -> None:
        super().__init__(config=config)
        self._candidate33_stack_levels = 0
        self._candidate33_stack_low = math.nan
        self._candidate33_stack_high = math.nan
        self._candidate33_stack_delta = math.nan
        self._candidate33_stack_poc = math.nan
        self.diagnostics.update(
            {
                "candidate33_accepted_without_stack": 0,
                "candidate33_accepted_with_stack": 0,
                "candidate33_failed_auction_no_trades": 0,
                "candidate33_stack_crossed_boundary": 0,
                "candidate33_late_flat_order_rejections": 0,
                "candidate33_protective_rejection_fail_closes": 0,
            },
        )

    def _reset_stack(self) -> None:
        self._candidate33_stack_levels = 0
        self._candidate33_stack_low = math.nan
        self._candidate33_stack_high = math.nan
        self._candidate33_stack_delta = math.nan
        self._candidate33_stack_poc = math.nan

    def _router_observation(
        self,
        row: dict[str, float | int],
        direction: int,
    ):
        observation = super()._router_observation(row, direction)
        if self.parent_auction is not None and self.parent_auction.observations == 0:
            self._reset_stack()
        if direction > 0:
            levels = int(max(0.0, self._feature("stacked_buy_imbalance_levels")))
            low = self._feature("stacked_buy_low")
            high = self._feature("stacked_buy_high")
        else:
            levels = int(max(0.0, self._feature("stacked_sell_imbalance_levels")))
            low = self._feature("stacked_sell_low")
            high = self._feature("stacked_sell_high")
        if levels > self._candidate33_stack_levels:
            self._candidate33_stack_levels = levels
            self._candidate33_stack_low = low
            self._candidate33_stack_high = high
            self._candidate33_stack_delta = self._feature("footprint_delta_60s")
            self._candidate33_stack_poc = self._feature("footprint_poc_price")
        return observation

    def _stack_crossed_boundary(self, *, direction: int, level: float, atr: float) -> bool:
        if not (
            math.isfinite(self._candidate33_stack_low)
            and math.isfinite(self._candidate33_stack_high)
            and math.isfinite(atr)
            and atr > 0.0
        ):
            return False
        tolerance = self.config.candidate33_stack_boundary_tolerance_atr * atr
        if direction > 0:
            return (
                self._candidate33_stack_high >= level
                and self._candidate33_stack_low <= level + tolerance
            )
        return (
            self._candidate33_stack_low <= level
            and self._candidate33_stack_high >= level - tolerance
        )

    def _close_parent_without_trade(
        self,
        row: dict[str, float | int],
        reason: str,
        details: dict[str, Any],
    ) -> None:
        setup = self.pending
        if setup is not None:
            self._transition(
                setup.scenario_id,
                "PARENT_AUCTION_COMPLETED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                reason,
                float(row["close"]),
                details,
            )
        self.pending = None
        self.parent_auction = None
        self._reset_stack()

    def _complete_parent(self, row: dict[str, float | int]) -> None:
        state = self.parent_auction
        setup = self.pending
        if state is None or setup is None:
            return
        stack_crossed = self._stack_crossed_boundary(
            direction=state.direction,
            level=state.pool_level,
            atr=state.atr,
        )
        stack_details = {
            "candidate33_require_stacked_imbalance": (
                self.config.candidate33_require_stacked_imbalance
            ),
            "candidate33_stack_levels": self._candidate33_stack_levels,
            "candidate33_stack_low": self._candidate33_stack_low,
            "candidate33_stack_high": self._candidate33_stack_high,
            "candidate33_stack_delta": self._candidate33_stack_delta,
            "candidate33_stack_poc": self._candidate33_stack_poc,
            "candidate33_stack_crossed_boundary": stack_crossed,
            "candidate33_terminal_router_state": asdict(state),
        }
        stack_details["candidate33_terminal_router_state"]["decision"] = (
            state.decision.value
        )

        if state.decision is AuctionDecision.UNRESOLVED:
            self.diagnostics["candidate16_unresolved"] = int(
                self.diagnostics["candidate16_unresolved"],
            ) + 1
            self._close_parent_without_trade(row, state.reason, stack_details)
            return

        if state.decision is AuctionDecision.FAILED_AUCTION:
            if self.config.candidate33_trade_failed_auction:
                super()._complete_parent(row)
                self._reset_stack()
            else:
                self.diagnostics["candidate33_failed_auction_no_trades"] = int(
                    self.diagnostics["candidate33_failed_auction_no_trades"],
                ) + 1
                self._close_parent_without_trade(
                    row,
                    "FAILED_AUCTION_NOT_PART_OF_V33_CONTINUATION_FAMILY",
                    stack_details,
                )
            return

        stack_pass = (
            self._candidate33_stack_levels
            >= self.config.candidate33_min_stacked_levels
            and stack_crossed
            and (
                not math.isfinite(self._candidate33_stack_delta)
                or state.direction * self._candidate33_stack_delta > 0.0
            )
        )
        if self.config.candidate33_require_stacked_imbalance and not stack_pass:
            self.diagnostics["candidate33_accepted_without_stack"] = int(
                self.diagnostics["candidate33_accepted_without_stack"],
            ) + 1
            self._close_parent_without_trade(
                row,
                "TRUE_ACCEPTANCE_WITHOUT_BOUNDARY_CROSSING_FOOTPRINT_STACK",
                stack_details,
            )
            return

        self.diagnostics["candidate33_accepted_with_stack"] = int(
            self.diagnostics["candidate33_accepted_with_stack"],
        ) + 1
        self.diagnostics["candidate33_stack_crossed_boundary"] = int(
            self.diagnostics["candidate33_stack_crossed_boundary"],
        ) + int(stack_crossed)
        super()._complete_parent(row)
        if self.pending is not None:
            self.pending.details.update(stack_details)
        self._reset_stack()

    def on_order_rejected(self, event: Any) -> None:
        late_flat_child = (
            self.portfolio.is_flat(self.config.instrument_id)
            and not self.entry_pending
            and self.current_scenario_id is None
        )
        if late_flat_child:
            self.diagnostics["candidate33_late_flat_order_rejections"] = int(
                self.diagnostics["candidate33_late_flat_order_rejections"],
            ) + 1
            return
        super().on_order_rejected(event)
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.diagnostics["candidate33_protective_rejection_fail_closes"] = int(
                self.diagnostics["candidate33_protective_rejection_fail_closes"],
            ) + 1
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)


__all__ = ["Candidate16Config", "Candidate16Strategy"]

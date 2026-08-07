#!/usr/bin/env python3
"""Candidate 05 v18: execution-confirmed entry cancellation."""
from __future__ import annotations

from typing import Any

from cancel_race_exit_logic import SUBMIT_MARKET_FLATTEN
from cancel_race_exit_logic import WAIT_FOR_CONTINGENT_RESOLUTION
from cancel_race_exit_logic import cancel_race_exit_action
from entry_cancel_race_logic import entry_cancel_resolution
from retrace_logic import pending_limit_invalidated
from strategy_base import LiquidityResponseConfig
from strategy_v17 import EarlySponsoredChochStrategy


class ExecutionConfirmedCancelStrategy(EarlySponsoredChochStrategy):
    """Keep scenario and exit state until Nautilus execution is authoritative.

    A completed one-minute bar can cross a resting entry and its structural stop.
    The entry may fill after cancellation was requested. Existing bracket exits
    can then race an explicit market flatten. Submitting both produced a real
    reduce-only rejection when the structural stop closed first.

    This version changes only order lifecycle. A market flatten is submitted
    only after no reduce-only contingent remains and the position is still open.
    Signal, entry, target, stop, size, fees, slippage, three-percent NAV risk and
    all market interpretation are unchanged.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.entry_cancel_requested = False
        self.entry_cancel_confirmed = False
        self.entry_cancel_reason: str | None = None
        self.entry_cancel_requested_index = -1
        self.entry_cancel_requested_ts = 0
        self.cancel_race_exit_armed = False
        self.cancel_race_flatten_submitted = False
        self.diagnostics.update(
            {
                "entry_cancel_requests": 0,
                "entry_cancel_confirmations": 0,
                "entry_cancel_retries": 0,
                "entry_fills_during_cancel_pending": 0,
                "entry_cancel_unfilled_finalizations": 0,
                "cancel_race_contingent_waits": 0,
                "cancel_race_contingent_cancel_retries": 0,
                "cancel_race_market_flatten_submissions": 0,
            },
        )

    def _manage_pending_entry(self, row: dict[str, float | int]) -> None:
        if self.entry_cancel_requested:
            resolution = entry_cancel_resolution(
                cancel_requested=True,
                cancel_confirmed=self.entry_cancel_confirmed,
                position_open=not self.portfolio.is_flat(self.config.instrument_id),
                bar_index=self.bar_index,
                requested_index=self.entry_cancel_requested_index,
            )
            if resolution == "CLOSE_UNFILLED":
                self._finalize_confirmed_unfilled_cancel(row)
                return
            if (
                self.bar_index > self.entry_cancel_requested_index
                and self.portfolio.is_flat(self.config.instrument_id)
                and not self._has_open_entry_order()
            ):
                self.entry_cancel_confirmed = True
                self._finalize_confirmed_unfilled_cancel(row)
                return
            if self.bar_index > self.entry_cancel_requested_index + 1:
                self.cancel_all_orders(self.config.instrument_id)
                self.diagnostics["entry_cancel_retries"] += 1
            return

        reason: str | None = None
        if pending_limit_invalidated(
            side=self.entry_side,
            stop=self.entry_stop,
            high=float(row["high"]),
            low=float(row["low"]),
        ):
            reason = "STRUCTURAL_STOP_REACHED_WHILE_ENTRY_RESTING"
            self.diagnostics["entry_invalidated_unfilled"] += 1
        elif self.bar_index > self.entry_expires_index:
            reason = "ENTRY_VALIDITY_WINDOW_EXPIRED"
            self.diagnostics["entry_expired_unfilled"] += 1
        elif self._funding_blackout(int(row["ts"])):
            reason = "FUNDING_BLACKOUT_WHILE_ENTRY_RESTING"
        elif not self._in_evaluation(int(row["ts"])):
            reason = "EVALUATION_ENDED_WHILE_ENTRY_RESTING"
        if reason is None:
            return

        self.entry_cancel_requested = True
        self.entry_cancel_confirmed = False
        self.entry_cancel_reason = reason
        self.entry_cancel_requested_index = self.bar_index
        self.entry_cancel_requested_ts = int(row["ts"])
        self.diagnostics["entry_cancel_requests"] += 1
        self.cancel_all_orders(self.config.instrument_id)
        if self.current_scenario_id is not None:
            self._transition(
                self.current_scenario_id,
                "ENTRY_CANCEL_REQUESTED",
                int(row["ts"]),
                int(row["ts"]),
                "ENTRY_CANCEL_PENDING",
                reason,
                float(row["close"]),
                {
                    "entry_limit": self.entry_limit,
                    "stop": self.entry_stop,
                    "bars_resting": self.bar_index - self.entry_pending_index,
                },
            )

    def on_order_canceled(self, event: Any) -> None:
        if self.entry_cancel_requested:
            if not self.entry_cancel_confirmed:
                self.entry_cancel_confirmed = True
                self.diagnostics["entry_cancel_confirmations"] += 1
            ts = int(getattr(event, "ts_event", self.bars[-1]["ts"]))
            if (
                self.current_scenario_id is not None
                and self.scenario_states.get(self.current_scenario_id) != "CLOSED"
            ):
                self._transition(
                    self.current_scenario_id,
                    "ENTRY_CANCEL_EVENT_OBSERVED",
                    ts,
                    ts,
                    "ENTRY_CANCEL_PENDING",
                    "NAUTILUS_ORDER_CANCEL_EVENT_RECEIVED",
                    float(self.bars[-1]["close"]),
                    {"event": str(event), "requested_reason": self.entry_cancel_reason},
                )
        if self.cancel_race_exit_armed:
            self._continue_cancel_race_exit(
                ts=int(getattr(event, "ts_event", self.bars[-1]["ts"])),
                reference_price=float(self.bars[-1]["close"]),
                source="ORDER_CANCEL_EVENT",
                retry_cancel=False,
            )

    def on_position_opened(self, event: Any) -> None:
        super().on_position_opened(event)
        if not self.entry_cancel_requested:
            return

        self.diagnostics["entry_fills_during_cancel_pending"] += 1
        ts = int(getattr(event, "ts_event", self.bars[-1]["ts"]))
        if (
            self.current_scenario_id is not None
            and self.scenario_states.get(self.current_scenario_id) != "CLOSED"
        ):
            self._transition(
                self.current_scenario_id,
                "ENTRY_FILLED_DURING_CANCEL_PENDING",
                ts,
                ts,
                "EXIT_PENDING",
                "INTRABAR_ENTRY_AND_INVALIDATION_ORDER_AMBIGUITY_CONSERVATIVE_FLATTEN",
                float(self.bars[-1]["close"]),
                {
                    "event": str(event),
                    "cancel_reason": self.entry_cancel_reason,
                    "entry_limit": self.entry_limit,
                    "structural_stop": self.entry_stop,
                    "cancel_requested_index": self.entry_cancel_requested_index,
                    "exit_resolution_policy": "CONTINGENT_FIRST_THEN_SINGLE_MARKET_FLATTEN",
                },
            )
        self.entry_pending = False
        self.exit_pending = True
        self.cancel_race_exit_armed = True
        self.cancel_race_flatten_submitted = False
        self.cancel_all_orders(self.config.instrument_id)

    def _manage_open_position(self, row: dict[str, float | int]) -> None:
        if self.cancel_race_exit_armed:
            self._continue_cancel_race_exit(
                ts=int(row["ts"]),
                reference_price=float(row["close"]),
                source="COMPLETED_BAR",
                retry_cancel=True,
            )
            return
        super()._manage_open_position(row)

    def _continue_cancel_race_exit(
        self,
        *,
        ts: int,
        reference_price: float,
        source: str,
        retry_cancel: bool,
    ) -> None:
        action = cancel_race_exit_action(
            position_open=not self.portfolio.is_flat(self.config.instrument_id),
            open_reduce_only_orders=self._has_open_reduce_only_order(),
            flatten_submitted=self.cancel_race_flatten_submitted,
        )
        if action == WAIT_FOR_CONTINGENT_RESOLUTION:
            self.diagnostics["cancel_race_contingent_waits"] += 1
            if retry_cancel:
                self.cancel_all_orders(self.config.instrument_id)
                self.diagnostics["cancel_race_contingent_cancel_retries"] += 1
            return
        if action != SUBMIT_MARKET_FLATTEN:
            return

        self.cancel_race_flatten_submitted = True
        self.diagnostics["cancel_race_market_flatten_submissions"] += 1
        if self.current_scenario_id is not None:
            self._transition(
                self.current_scenario_id,
                "CANCEL_RACE_MARKET_FLATTEN_SUBMITTED",
                ts,
                ts,
                "EXIT_PENDING",
                "NO_REDUCE_ONLY_CONTINGENT_REMAINS_WHILE_POSITION_OPEN",
                reference_price,
                {
                    "source": source,
                    "cancel_reason": self.entry_cancel_reason,
                    "entry_limit": self.entry_limit,
                    "structural_stop": self.entry_stop,
                },
            )
        self.close_all_positions(self.config.instrument_id)

    def _finalize_confirmed_unfilled_cancel(
        self,
        row: dict[str, float | int],
    ) -> None:
        if self.current_scenario_id is not None:
            self._transition(
                self.current_scenario_id,
                "UNFILLED_ENTRY_CANCEL_CONFIRMED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                self.entry_cancel_reason or "ENTRY_CANCEL_CONFIRMED",
                float(row["close"]),
                {
                    "entry_limit": self.entry_limit,
                    "stop": self.entry_stop,
                    "cancel_requested_index": self.entry_cancel_requested_index,
                    "cancel_confirmed": self.entry_cancel_confirmed,
                },
            )
        self.diagnostics["entry_cancel_unfilled_finalizations"] += 1
        self._clear_trade_state()

    def _has_open_entry_order(self) -> bool:
        for order in self.cache.orders(instrument_id=self.config.instrument_id):
            if order.is_open and not bool(getattr(order, "is_reduce_only", False)):
                return True
        return False

    def _has_open_reduce_only_order(self) -> bool:
        for order in self.cache.orders(instrument_id=self.config.instrument_id):
            if order.is_open and bool(getattr(order, "is_reduce_only", False)):
                return True
        return False

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self.entry_cancel_requested = False
        self.entry_cancel_confirmed = False
        self.entry_cancel_reason = None
        self.entry_cancel_requested_index = -1
        self.entry_cancel_requested_ts = 0
        self.cancel_race_exit_armed = False
        self.cancel_race_flatten_submitted = False


__all__ = ["ExecutionConfirmedCancelStrategy"]

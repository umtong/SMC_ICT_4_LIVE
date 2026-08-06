#!/usr/bin/env python3
"""Candidate 05 v18: execution-confirmed entry cancellation."""
from __future__ import annotations

from typing import Any

from entry_cancel_race_logic import entry_cancel_resolution
from retrace_logic import pending_limit_invalidated
from strategy_base import LiquidityResponseConfig
from strategy_v17 import EarlySponsoredChochStrategy


class ExecutionConfirmedCancelStrategy(EarlySponsoredChochStrategy):
    """Keep scenario state until a pending entry cancel is execution-confirmed.

    A completed one-minute bar can cross both a resting limit and its structural
    stop. NautilusTrader may match the entry before the strategy receives the
    bar, while position/cancel events are delivered afterward. The earlier code
    labeled the order unfilled, cleared its scenario, and could then receive a
    real fill which remained open as an unmatched position.

    v18 changes only that lifecycle:

    * an invalidation/expiry/funding/evaluation condition requests cancellation
      and enters ENTRY_CANCEL_PENDING without clearing the scenario;
    * a confirmed cancel is finalized as unfilled only on a later completed bar;
    * if a position opens before that finalization, all contingents are canceled
      and the filled position is immediately flattened through NautilusTrader;
    * no signal, order price, target, stop, size, fee, slippage, risk or market
      interpretation is changed.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.entry_cancel_requested = False
        self.entry_cancel_confirmed = False
        self.entry_cancel_reason: str | None = None
        self.entry_cancel_requested_index = -1
        self.entry_cancel_requested_ts = 0
        self.diagnostics.update(
            {
                "entry_cancel_requests": 0,
                "entry_cancel_confirmations": 0,
                "entry_cancel_retries": 0,
                "entry_fills_during_cancel_pending": 0,
                "entry_cancel_unfilled_finalizations": 0,
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

            # Some adapters may emit multiple child-order cancellation events or
            # omit an entry-specific event from this callback. On a later bar,
            # the cache is authoritative: no open non-reduce entry order and a
            # flat portfolio is equivalent to cancel confirmation.
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
        if not self.entry_cancel_requested:
            return
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
                },
            )
        self.entry_pending = False
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.exit_pending = True
            self.cancel_all_orders(self.config.instrument_id)
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

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self.entry_cancel_requested = False
        self.entry_cancel_confirmed = False
        self.entry_cancel_reason = None
        self.entry_cancel_requested_index = -1
        self.entry_cancel_requested_ts = 0


__all__ = ["ExecutionConfirmedCancelStrategy"]

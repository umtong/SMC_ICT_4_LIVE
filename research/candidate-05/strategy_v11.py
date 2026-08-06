#!/usr/bin/env python3
"""Candidate 05 v11: confirm contingent-stop updates or exit the failed protection path."""
from __future__ import annotations

import math
from typing import Any

from nautilus_trader.model.enums import OrderType

from flow_inflection_logic import choose_protectable_liquidity_milestone
from strategy_base import LiquidityResponseConfig
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v9 import ObservedEntryPathStrategy


class ConfirmedLiquidityProtectionStrategy(ObservedEntryPathStrategy):
    """Promote consumed intermediate liquidity only after execution confirms the stop.

    A nearer opposing liquidity pool is selected only when a stop one structural
    ATR buffer behind it still leaves positive expected PnL after fees and
    adverse stop slippage. Touching that pool requests a contingent stop update.
    The strategy does not assume the request succeeded: the protection state is
    armed only by ``OrderUpdated``.

    If the venue rejects the update, the protected level is reaccepted while the
    update is pending, or no confirmation arrives by the next completed minute,
    the auction path is no longer safely executable. Existing orders are canceled
    and the position is flattened. This keeps execution state and scenario state
    identical instead of silently retaining the original full-risk stop.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.protection_pool_id: str | None = None
        self.protection_milestone = float("nan")
        self.protection_stop = float("nan")
        self.protection_expected_net = float("nan")
        self.protection_armed = False
        self.protection_update_pending = False
        self.protection_order_id: Any | None = None
        self.protection_requested_index = -1
        self.diagnostics.update(
            {
                "protectable_milestones": 0,
                "milestone_protection_requests": 0,
                "milestone_protection_confirmations": 0,
                "milestone_modify_rejections": 0,
                "milestone_pending_level_reacceptance_exits": 0,
                "milestone_pending_timeout_exits": 0,
                "milestone_same_bar_failures": 0,
                "milestone_stop_not_found": 0,
            },
        )

    def _submit_price_capped_bracket(
        self,
        *,
        armed: ArmedEntryPath,
        row: dict[str, float | int],
        entry_price: Any,
        stop_price: Any,
        target_price: Any,
        sizing_entry: float,
        planned_loss: float,
        target_source: str,
        target_r: float,
        branch: str,
        event_type: str,
        reason: str,
        expires_index: int,
        entry_tag: str,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        entry = _as_float(entry_price)
        target = _as_float(target_price)
        milestone = choose_protectable_liquidity_milestone(
            entry=entry,
            target=target,
            side=armed.setup.side,
            pools=list(self.active_pools.values()),
            atr=armed.atr,
            stop_buffer_atr=self.config.stop_buffer_atr,
            cost_rate=self.config.all_in_cost_bps_each_side / 10_000.0,
            adverse_slippage_rate=self.config.adverse_slippage_bps_each_side / 10_000.0,
        )
        submitted = super()._submit_price_capped_bracket(
            armed=armed,
            row=row,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            sizing_entry=sizing_entry,
            planned_loss=planned_loss,
            target_source=target_source,
            target_r=target_r,
            branch=branch,
            event_type=event_type,
            reason=reason,
            expires_index=expires_index,
            entry_tag=entry_tag,
            extra={
                **(extra or {}),
                "protection_milestone": None if milestone is None else milestone[1],
                "protection_stop": None if milestone is None else milestone[2],
                "protection_pool_id": None if milestone is None else milestone[0],
                "protection_expected_net_per_unit": None if milestone is None else milestone[3],
            },
        )
        if not submitted:
            return False
        if milestone is None:
            self._clear_protection_state()
            return True

        pool_id, level, protected_stop, _expected_net = milestone
        rounded_stop = _as_float(self.instrument.make_price(protected_stop))
        expected_exit = rounded_stop * (
            1.0 - armed.setup.side * self.config.adverse_slippage_bps_each_side / 10_000.0
        )
        rounded_net = armed.setup.side * (expected_exit - entry) - (
            self.config.all_in_cost_bps_each_side / 10_000.0
        ) * (entry + expected_exit)
        if rounded_net <= 0.0:
            self._clear_protection_state()
            return True

        self.protection_pool_id = pool_id
        self.protection_milestone = level
        self.protection_stop = rounded_stop
        self.protection_expected_net = rounded_net
        self.protection_armed = False
        self.protection_update_pending = False
        self.protection_order_id = None
        self.protection_requested_index = -1
        self.diagnostics["protectable_milestones"] += 1
        return True

    def _manage_open_position(self, row: dict[str, float | int]) -> None:
        if self.exit_pending:
            return

        if self.protection_update_pending:
            side = self.entry_side
            crossed = (
                float(row["low"]) <= self.protection_stop
                if side > 0
                else float(row["high"]) >= self.protection_stop
            )
            if crossed:
                self.diagnostics["milestone_pending_level_reacceptance_exits"] += 1
                self._flatten_for_failed_protection(
                    row,
                    event_type="PENDING_PROTECTION_LEVEL_REACCEPTED",
                    reason="CONSUMED_LIQUIDITY_REACCEPTED_BEFORE_STOP_UPDATE_CONFIRMATION",
                )
                return
            if self.bar_index > self.protection_requested_index + 1:
                self.diagnostics["milestone_pending_timeout_exits"] += 1
                self._flatten_for_failed_protection(
                    row,
                    event_type="PROTECTION_UPDATE_CONFIRMATION_TIMEOUT",
                    reason="STOP_UPDATE_NOT_CONFIRMED_BY_NEXT_COMPLETED_MINUTE",
                )
                return

        if (
            not self.protection_armed
            and not self.protection_update_pending
            and self.protection_pool_id is not None
            and math.isfinite(self.protection_milestone)
            and math.isfinite(self.protection_stop)
        ):
            side = self.entry_side
            touched = (
                float(row["high"]) >= self.protection_milestone
                if side > 0
                else float(row["low"]) <= self.protection_milestone
            )
            if touched:
                close = float(row["close"])
                stop_is_live = close > self.protection_stop if side > 0 else close < self.protection_stop
                if not stop_is_live:
                    self.diagnostics["milestone_same_bar_failures"] += 1
                    self._flatten_for_failed_protection(
                        row,
                        event_type="MILESTONE_FAILED_IN_SAME_BAR",
                        reason="CONSUMED_LIQUIDITY_WAS_IMMEDIATELY_REACCEPTED",
                    )
                    return
                stop_order = self._open_structural_stop()
                if stop_order is None:
                    self.diagnostics["milestone_stop_not_found"] += 1
                    self._flatten_for_failed_protection(
                        row,
                        event_type="STRUCTURAL_STOP_NOT_FOUND_AT_MILESTONE",
                        reason="CANNOT_PROTECT_CONSUMED_LIQUIDITY_WITHOUT_ACTIVE_STOP",
                    )
                    return

                self.protection_order_id = stop_order.client_order_id
                self.protection_update_pending = True
                self.protection_requested_index = self.bar_index
                self.diagnostics["milestone_protection_requests"] += 1
                if self.current_scenario_id is not None:
                    self._transition(
                        self.current_scenario_id,
                        "INTERMEDIATE_LIQUIDITY_PROTECTION_REQUESTED",
                        int(row["ts"]),
                        int(row["ts"]),
                        "POSITION_OPEN",
                        "REQUEST_STOP_BEHIND_FIRST_MONETIZABLE_CONSUMED_POOL",
                        self.protection_stop,
                        self._protection_details(),
                    )
                self.modify_order(
                    stop_order,
                    trigger_price=self.instrument.make_price(self.protection_stop),
                )

        super()._manage_open_position(row)

    def on_order_updated(self, event: Any) -> None:
        if not self._matches_protection_order(event) or not self.protection_update_pending:
            return
        event_trigger = getattr(event, "trigger_price", None)
        if event_trigger is not None:
            event_trigger_value = _as_float(event_trigger)
            tolerance = _as_float(self.instrument.price_increment) + 1e-12
            if abs(event_trigger_value - self.protection_stop) > tolerance:
                return
        self.protection_update_pending = False
        self.protection_armed = True
        self.entry_stop = self.protection_stop
        self.diagnostics["milestone_protection_confirmations"] += 1
        ts = int(getattr(event, "ts_event", self.bars[-1]["ts"]))
        if self.current_scenario_id is not None:
            self._transition(
                self.current_scenario_id,
                "INTERMEDIATE_LIQUIDITY_DEFENDED",
                ts,
                ts,
                "POSITION_OPEN",
                "FIRST_MONETIZABLE_POOL_CONSUMED_AND_STOP_UPDATE_CONFIRMED",
                self.protection_stop,
                {
                    **self._protection_details(),
                    "order_event": str(event),
                },
            )

    def on_order_modify_rejected(self, event: Any) -> None:
        if not self._matches_protection_order(event) or not self.protection_update_pending:
            return
        self.protection_update_pending = False
        self.diagnostics["milestone_modify_rejections"] += 1
        self.diagnostics["order_rejections"] += 1
        row = self.bars[-1]
        self._flatten_for_failed_protection(
            row,
            event_type="MILESTONE_PROTECTION_MODIFY_REJECTED",
            reason="VENUE_REJECTED_STRUCTURAL_STOP_PROMOTION",
            extra={"order_event": str(event)},
        )

    def _matches_protection_order(self, event: Any) -> bool:
        if self.protection_order_id is None:
            return False
        event_id = getattr(event, "client_order_id", None)
        return event_id is not None and str(event_id) == str(self.protection_order_id)

    def _flatten_for_failed_protection(
        self,
        row: dict[str, float | int],
        *,
        event_type: str,
        reason: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if self.exit_pending:
            return
        self.exit_pending = True
        self.cancel_all_orders(self.config.instrument_id)
        self.close_all_positions(self.config.instrument_id)
        if self.current_scenario_id is not None:
            self._transition(
                self.current_scenario_id,
                event_type,
                int(row["ts"]),
                int(row["ts"]),
                "EXIT_PENDING",
                reason,
                float(row["close"]),
                {
                    **self._protection_details(),
                    **(extra or {}),
                },
            )

    def _protection_details(self) -> dict[str, Any]:
        return {
            "milestone_pool_id": self.protection_pool_id,
            "milestone": self.protection_milestone,
            "protected_stop": self.protection_stop,
            "expected_net_per_unit_after_cost_and_slippage": self.protection_expected_net,
            "protection_order_id": (
                None if self.protection_order_id is None else str(self.protection_order_id)
            ),
            "requested_index": self.protection_requested_index,
        }

    def _open_structural_stop(self) -> Any | None:
        for order in self.cache.orders(instrument_id=self.config.instrument_id):
            if order.is_open and order.order_type == OrderType.STOP_MARKET:
                return order
        return None

    def _clear_protection_state(self) -> None:
        self.protection_pool_id = None
        self.protection_milestone = float("nan")
        self.protection_stop = float("nan")
        self.protection_expected_net = float("nan")
        self.protection_armed = False
        self.protection_update_pending = False
        self.protection_order_id = None
        self.protection_requested_index = -1

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._clear_protection_state()


__all__ = ["ConfirmedLiquidityProtectionStrategy"]

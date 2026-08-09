#!/usr/bin/env python3
"""Candidate 05 v10: defend a monetizable liquidity pool after it is consumed."""
from __future__ import annotations

import math
from typing import Any

from nautilus_trader.model.enums import OrderType

from flow_inflection_logic import choose_protectable_liquidity_milestone
from strategy_base import LiquidityResponseConfig
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v9 import ObservedEntryPathStrategy


class ProtectedLiquidityPathStrategy(ObservedEntryPathStrategy):
    """Promote consumed intermediate liquidity into the scenario invalidation.

    The final opposing pool remains the profit objective. Some paths contain a
    nearer opposing pool which is too small to justify ending the trade, but is
    large enough that a stop placed just behind it would still leave positive
    post-cost PnL. Once price consumes that pool, the auction has made measurable
    progress. A return through the consumed pool means the expected rotation has
    failed, so the structural stop is modified to the near side of that pool.

    The milestone is not selected by an R threshold. It is the nearest live
    opposing pool between entry and final target for which the same ATR buffer,
    fees and adverse stop slippage still leave strictly positive expected PnL.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.protection_pool_id: str | None = None
        self.protection_milestone = float("nan")
        self.protection_stop = float("nan")
        self.protection_expected_net = float("nan")
        self.protection_armed = False
        self.diagnostics.update(
            {
                "protectable_milestones": 0,
                "milestone_protection_updates": 0,
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
        pool_id, level, protected_stop, expected_net = milestone
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
        self.diagnostics["protectable_milestones"] += 1
        return True

    def _manage_open_position(self, row: dict[str, float | int]) -> None:
        if self.exit_pending:
            return
        if (
            not self.protection_armed
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
                    self.exit_pending = True
                    self.cancel_all_orders(self.config.instrument_id)
                    self.close_all_positions(self.config.instrument_id)
                    if self.current_scenario_id is not None:
                        self._transition(
                            self.current_scenario_id,
                            "MILESTONE_FAILED_IN_SAME_BAR",
                            int(row["ts"]),
                            int(row["ts"]),
                            "EXIT_PENDING",
                            "CONSUMED_LIQUIDITY_WAS_IMMEDIATELY_REACCEPTED",
                            close,
                            {
                                "milestone_pool_id": self.protection_pool_id,
                                "milestone": self.protection_milestone,
                                "protected_stop": self.protection_stop,
                            },
                        )
                    return
                stop_order = self._open_structural_stop()
                if stop_order is None:
                    self.diagnostics["milestone_stop_not_found"] += 1
                else:
                    self.modify_order(
                        stop_order,
                        trigger_price=self.instrument.make_price(self.protection_stop),
                    )
                    self.entry_stop = self.protection_stop
                    self.protection_armed = True
                    self.diagnostics["milestone_protection_updates"] += 1
                    if self.current_scenario_id is not None:
                        self._transition(
                            self.current_scenario_id,
                            "INTERMEDIATE_LIQUIDITY_DEFENDED",
                            int(row["ts"]),
                            int(row["ts"]),
                            "POSITION_OPEN",
                            "FIRST_MONETIZABLE_POOL_CONSUMED_AND_PROMOTED_TO_INVALIDATION",
                            self.protection_stop,
                            {
                                "milestone_pool_id": self.protection_pool_id,
                                "milestone": self.protection_milestone,
                                "protected_stop": self.protection_stop,
                                "expected_net_per_unit_after_cost_and_slippage": self.protection_expected_net,
                            },
                        )
        super()._manage_open_position(row)

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

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._clear_protection_state()


__all__ = ["ProtectedLiquidityPathStrategy"]

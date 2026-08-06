#!/usr/bin/env python3
"""Candidate 05 v12: software-enforced invalidation behind consumed liquidity."""
from __future__ import annotations

import math
from typing import Any

from flow_inflection_logic import choose_protectable_liquidity_milestone
from strategy_base import LiquidityResponseConfig
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v9 import ObservedEntryPathStrategy


class SoftwareLiquidityProtectionStrategy(ObservedEntryPathStrategy):
    """Defend a consumed intermediate pool without racing a contingent modification.

    The original structural stop remains live as a catastrophic exchange-side
    fallback.  A nearer opposing liquidity pool is eligible for software
    protection only when a stop one ATR buffer behind the pool would still leave
    positive expected PnL after round-trip fees and adverse exit slippage.

    Once a completed minute consumes that pool without simultaneously trading
    back through the protected level, the protected level becomes the scenario
    invalidation.  A later completed bar which reaccepts it causes NautilusTrader
    to cancel the bracket and flatten the position.  No stop modification is
    submitted, so execution cannot enter the modify/reject/race state observed in
    v11.  Order matching, cancellation, market close, fees, positions and NAV all
    remain owned by NautilusTrader.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.protection_pool_id: str | None = None
        self.protection_milestone = float("nan")
        self.protection_stop = float("nan")
        self.protection_expected_net = float("nan")
        self.protection_armed = False
        self.protection_armed_index = -1
        self.diagnostics.update(
            {
                "protectable_milestones": 0,
                "software_protection_arms": 0,
                "software_protection_exits": 0,
                "software_same_bar_failures": 0,
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
                "protection_mode": "SOFTWARE_COMPLETED_BAR_INVALIDATION",
                "protection_milestone": None if milestone is None else milestone[1],
                "protection_stop": None if milestone is None else milestone[2],
                "protection_pool_id": None if milestone is None else milestone[0],
                "protection_expected_net_per_unit": None if milestone is None else milestone[3],
            },
        )
        if not submitted:
            return False
        self._clear_protection_state()
        if milestone is None:
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
            return True

        self.protection_pool_id = pool_id
        self.protection_milestone = level
        self.protection_stop = rounded_stop
        self.protection_expected_net = rounded_net
        self.diagnostics["protectable_milestones"] += 1
        return True

    def _manage_open_position(self, row: dict[str, float | int]) -> None:
        if self.exit_pending:
            return

        side = self.entry_side
        if self.protection_armed:
            invalidated = (
                float(row["low"]) <= self.protection_stop
                if side > 0
                else float(row["high"]) >= self.protection_stop
            )
            if invalidated:
                self.diagnostics["software_protection_exits"] += 1
                self._flatten_at_software_invalidation(
                    row,
                    event_type="INTERMEDIATE_LIQUIDITY_REACCEPTED",
                    reason="CONSUMED_MONETIZABLE_POOL_REACCEPTED_AFTER_CONFIRMATION",
                )
                return

        if (
            not self.protection_armed
            and self.protection_pool_id is not None
            and math.isfinite(self.protection_milestone)
            and math.isfinite(self.protection_stop)
        ):
            touched = (
                float(row["high"]) >= self.protection_milestone
                if side > 0
                else float(row["low"]) <= self.protection_milestone
            )
            if touched:
                also_invalidated = (
                    float(row["low"]) <= self.protection_stop
                    if side > 0
                    else float(row["high"]) >= self.protection_stop
                )
                if also_invalidated:
                    self.diagnostics["software_same_bar_failures"] += 1
                    self._flatten_at_software_invalidation(
                        row,
                        event_type="MILESTONE_FAILED_IN_SAME_COMPLETED_BAR",
                        reason="CONSUMED_LIQUIDITY_AND_REACCEPTANCE_ORDER_WAS_AMBIGUOUS",
                    )
                    return

                self.protection_armed = True
                self.protection_armed_index = self.bar_index
                self.diagnostics["software_protection_arms"] += 1
                if self.current_scenario_id is not None:
                    self._transition(
                        self.current_scenario_id,
                        "INTERMEDIATE_LIQUIDITY_SOFTWARE_DEFENDED",
                        int(row["ts"]),
                        int(row["ts"]),
                        "POSITION_OPEN",
                        "FIRST_MONETIZABLE_POOL_CONSUMED_WITH_PROTECTED_LEVEL_INTACT",
                        self.protection_stop,
                        self._protection_details(),
                    )

        super()._manage_open_position(row)

    def _flatten_at_software_invalidation(
        self,
        row: dict[str, float | int],
        *,
        event_type: str,
        reason: str,
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
                self._protection_details(),
            )

    def _protection_details(self) -> dict[str, Any]:
        return {
            "protection_mode": "SOFTWARE_COMPLETED_BAR_INVALIDATION",
            "milestone_pool_id": self.protection_pool_id,
            "milestone": self.protection_milestone,
            "protected_stop": self.protection_stop,
            "expected_net_per_unit_after_cost_and_slippage": self.protection_expected_net,
            "armed_index": self.protection_armed_index,
        }

    def _clear_protection_state(self) -> None:
        self.protection_pool_id = None
        self.protection_milestone = float("nan")
        self.protection_stop = float("nan")
        self.protection_expected_net = float("nan")
        self.protection_armed = False
        self.protection_armed_index = -1

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._clear_protection_state()


__all__ = ["SoftwareLiquidityProtectionStrategy"]

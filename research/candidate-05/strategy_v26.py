#!/usr/bin/env python3
"""Candidate 05 v26: scenario-state validity for every resting entry."""
from __future__ import annotations

import math
from typing import Any

from entry_cancel_race_logic import entry_cancel_resolution
from entry_scenario_validity_logic import DAYTRADE_HORIZON_EXPIRED
from entry_scenario_validity_logic import EVALUATION_BOUNDARY
from entry_scenario_validity_logic import FUNDING_BOUNDARY
from entry_scenario_validity_logic import STRUCTURAL_STOP
from entry_scenario_validity_logic import TARGET_COMPLETED
from entry_scenario_validity_logic import TARGET_SOURCE_EXPIRED
from entry_scenario_validity_logic import scenario_entry_cancel_reason
from strategy_base import LiquidityResponseConfig
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v25 import ConfirmedSecondTouchStrategy


class ScenarioValidEntryStrategy(ConfirmedSecondTouchStrategy):
    """Keep a resting intent only while its original auction remains unresolved.

    Earlier versions assigned a fixed two- or eight-completed-bar lifetime after
    order submission.  Those counts were not an invalidation condition from the
    market hypothesis. In the frozen BTC weeks, coherent price-protected entries
    were canceled while their structural stop and live opposing-liquidity target
    both remained intact.

    v26 changes only pending-entry lifecycle. Every submitted bracket stores its
    original target, target-pool identity and the existing 180-minute daytrade
    horizon measured from the scenario's CHoCH/path-arm event. The entry remains
    active until one of these causal boundaries occurs:

    * original structural stop is reached;
    * original target is reached before entry;
    * original target pool expires before entry;
    * existing daytrade horizon, funding boundary or evaluation boundary occurs.

    Fixed order-expiry indices are recorded for ablation diagnostics but ignored.
    The v18 execution-confirmed cancel race remains authoritative, including the
    conservative immediate flatten when a fill arrives during cancellation.
    Signal logic, price, target, stop, sizing, 3% risk, costs, slippage, execution
    engine and one-global-executable-intent rule are unchanged.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.pending_scenario_target = float("nan")
        self.pending_scenario_target_pool_id: str | None = None
        self.pending_scenario_horizon_index = -1
        self.pending_original_expiry_index = -1
        self.pending_original_expiry_crossed = False
        self.diagnostics.update(
            {
                "scenario_valid_pending_entries": 0,
                "fixed_entry_expiry_boundaries_ignored": 0,
                "scenario_target_reached_while_entry_resting": 0,
                "scenario_target_source_expired_while_entry_resting": 0,
                "scenario_daytrade_horizon_expired_while_entry_resting": 0,
                "scenario_structural_stop_reached_while_entry_resting": 0,
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
                "entry_validity_policy": "SCENARIO_STATE_NOT_FIXED_BAR_COUNT",
                "original_fixed_expiry_index": expires_index,
                "scenario_horizon_index": armed.created_index + self.config.max_hold_bars,
            },
        )
        if not submitted:
            self._clear_pending_scenario_validity()
            return False

        self.pending_scenario_target = _as_float(target_price)
        self.pending_scenario_target_pool_id = (
            target_source.split(":", 1)[1]
            if target_source.startswith("POOL:")
            else None
        )
        self.pending_scenario_horizon_index = (
            armed.created_index + self.config.max_hold_bars
        )
        self.pending_original_expiry_index = expires_index
        self.pending_original_expiry_crossed = False
        self.diagnostics["scenario_valid_pending_entries"] += 1
        return True

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

        if (
            not self.pending_original_expiry_crossed
            and self.pending_original_expiry_index >= 0
            and self.bar_index > self.pending_original_expiry_index
        ):
            self.pending_original_expiry_crossed = True
            self.diagnostics["fixed_entry_expiry_boundaries_ignored"] += 1
            if self.current_scenario_id is not None:
                self._transition(
                    self.current_scenario_id,
                    "FIXED_ENTRY_EXPIRY_IGNORED",
                    int(row["ts"]),
                    int(row["ts"]),
                    "ENTRY_PENDING",
                    "ORIGINAL_SCENARIO_STOP_AND_TARGET_REMAIN_ACTIVE",
                    float(row["close"]),
                    {
                        "original_fixed_expiry_index": self.pending_original_expiry_index,
                        "scenario_horizon_index": self.pending_scenario_horizon_index,
                        "entry_limit": self.entry_limit,
                        "stop": self.entry_stop,
                        "target": self.pending_scenario_target,
                    },
                )

        target_source_active = (
            self.pending_scenario_target_pool_id is None
            or self.pending_scenario_target_pool_id in self.active_pools
        )
        reason = scenario_entry_cancel_reason(
            side=self.entry_side,
            high=float(row["high"]),
            low=float(row["low"]),
            structural_stop=self.entry_stop,
            target=self.pending_scenario_target,
            target_source_active=target_source_active,
            bar_index=self.bar_index,
            horizon_index=self.pending_scenario_horizon_index,
            funding_blackout=self._funding_blackout(int(row["ts"])),
            in_evaluation=self._in_evaluation(int(row["ts"])),
        )
        if reason is None:
            return

        if reason == STRUCTURAL_STOP:
            self.diagnostics["entry_invalidated_unfilled"] += 1
            self.diagnostics["scenario_structural_stop_reached_while_entry_resting"] += 1
        elif reason == TARGET_COMPLETED:
            self.diagnostics["scenario_target_reached_while_entry_resting"] += 1
            if self.current_branch == self.SECOND_TOUCH_BRANCH:
                self.diagnostics["confirmed_second_touch_targets_reached_unfilled"] += 1
        elif reason == TARGET_SOURCE_EXPIRED:
            self.diagnostics["scenario_target_source_expired_while_entry_resting"] += 1
            if self.current_branch == self.SECOND_TOUCH_BRANCH:
                self.diagnostics["confirmed_second_touch_sources_expired_unfilled"] += 1
        elif reason == DAYTRADE_HORIZON_EXPIRED:
            self.diagnostics["scenario_daytrade_horizon_expired_while_entry_resting"] += 1
        elif reason not in (FUNDING_BOUNDARY, EVALUATION_BOUNDARY):
            raise RuntimeError(f"unhandled pending-entry resolution: {reason}")

        self._request_scenario_entry_cancel(row, reason)

    def _request_scenario_entry_cancel(
        self,
        row: dict[str, float | int],
        reason: str,
    ) -> None:
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
                    "target": self.pending_scenario_target,
                    "target_pool_id": self.pending_scenario_target_pool_id,
                    "bars_resting": self.bar_index - self.entry_pending_index,
                    "original_fixed_expiry_index": self.pending_original_expiry_index,
                    "scenario_horizon_index": self.pending_scenario_horizon_index,
                },
            )

    def _clear_pending_scenario_validity(self) -> None:
        self.pending_scenario_target = float("nan")
        self.pending_scenario_target_pool_id = None
        self.pending_scenario_horizon_index = -1
        self.pending_original_expiry_index = -1
        self.pending_original_expiry_crossed = False

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._clear_pending_scenario_validity()


__all__ = ["ScenarioValidEntryStrategy"]

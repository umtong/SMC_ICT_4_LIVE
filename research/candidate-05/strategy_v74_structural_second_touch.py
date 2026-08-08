#!/usr/bin/env python3
"""Candidate 05 v74: confirmation is evidence; CHoCH second touch is entry.

The 91-day v46 attribution showed that marketable confirmed-retrace entries were
negative while confirmed second-touch entries were positive.  v74 changes only
that execution transition.  Once the real retest response is confirmed, the
strategy never chases the response close.  It rests one passive order at the
original CHoCH structural reference, with the same frozen target, sweep-based
stop, costs, 3% current-NAV risk and scenario-state cancellation contract.

Sponsored no-retrace participation and every detector remain unchanged.
"""
from __future__ import annotations

import math

from confirmed_second_touch_logic import confirmed_second_touch_geometry
from flow_inflection_logic import MIN_LIQUIDITY_TARGET_NET_R
from strategy import LiquidityResponseConfig
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy


class StructuralSecondTouchStrategy(NoPostRetraceBreakawayStrategy):
    """Use the response to arm, never to chase, a structural second touch."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "structural_second_touch_candidates": 0,
                "structural_second_touch_submissions": 0,
                "structural_second_touch_invalid_geometry": 0,
                "structural_second_touch_horizon_exhausted": 0,
            },
        )

    def _submit_retest_response(
        self,
        armed: ArmedEntryPath,
        row: dict[str, float | int],
        *,
        flow_15s: float,
        depth_imbalance: float,
    ) -> bool:
        side = armed.setup.side
        response_price = _as_float(self.instrument.make_price(float(row["close"])))
        entry_price = self.instrument.make_price(armed.choch_close)
        entry = _as_float(entry_price)
        stop_price = self.instrument.make_price(armed.stop)
        stop = _as_float(stop_price)
        target_price = self.instrument.make_price(self._frozen_target_price(armed))
        target = _as_float(target_price)
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0

        geometry = confirmed_second_touch_geometry(
            side=side,
            choch_reference=entry,
            confirmed_response_price=response_price,
            stop=stop,
            target=target,
            cost_rate=cost_rate,
            adverse_slippage_rate=slippage_rate,
            minimum_net_r=MIN_LIQUIDITY_TARGET_NET_R,
        )
        if geometry is None:
            self.diagnostics["structural_second_touch_invalid_geometry"] += 1
            self._expire_armed_entry(
                row,
                "CONFIRMED_RESPONSE_DID_NOT_LEAVE_EXECUTABLE_CHOCH_SECOND_TOUCH",
            )
            return False

        expires_index = min(
            armed.created_index + self.config.max_hold_bars,
            self.bar_index + self.config.acceptance_retrace_bars,
        )
        if expires_index <= self.bar_index:
            self.diagnostics["structural_second_touch_horizon_exhausted"] += 1
            self._expire_armed_entry(
                row,
                "STRUCTURAL_SECOND_TOUCH_HORIZON_ALREADY_EXHAUSTED",
            )
            return False

        target_source = str(armed.details.get("frozen_target_source", ""))
        target_pool_id = armed.details.get("frozen_target_pool_id")
        armed.details.update(
            {
                "retest_response_close": response_price,
                "retest_response_flow_15s": flow_15s,
                "retest_response_depth_imbalance_1": depth_imbalance,
                "retest_response_directional_flow_15s": side * flow_15s,
                "retest_response_directional_depth": side * depth_imbalance,
                "structural_second_touch_entry": geometry.entry,
                "structural_second_touch_target_net_r": geometry.target_net_r,
                "structural_second_touch_expires_index": expires_index,
                "entry_timing_policy": "CONFIRMATION_IS_EVIDENCE_NOT_ENTRY",
            },
        )
        self.diagnostics["structural_second_touch_candidates"] += 1
        self._transition(
            armed.setup.scenario_id,
            "CHOCH_RETEST_RESPONSE_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "STRUCTURAL_SECOND_TOUCH_ENTRY_PENDING",
            "CONFIRMATION_IS_EVIDENCE;_WAIT_FOR_FIRST_PASSIVE_CHOCH_RETURN",
            response_price,
            {
                **armed.details,
                "entry_limit": geometry.entry,
                "stop": stop,
                "target": target,
                "target_source": target_source,
            },
        )
        submitted = self._submit_price_capped_bracket(
            armed=armed,
            row=row,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            sizing_entry=geometry.entry,
            planned_loss=geometry.planned_loss_per_unit,
            target_source=target_source,
            target_r=geometry.target_net_r,
            branch=self.SECOND_TOUCH_BRANCH,
            event_type="STRUCTURAL_SECOND_TOUCH_LIMIT_SUBMITTED",
            reason="FIRST_PASSIVE_CHOCH_RETURN_AFTER_CONFIRMED_RESPONSE",
            expires_index=expires_index,
            entry_tag="STRUCTURAL_SECOND_TOUCH_ENTRY",
            extra={
                "confirmed_response_price": response_price,
                "second_touch_entry": geometry.entry,
                "second_touch_target_net_r": geometry.target_net_r,
                "second_touch_horizon_bars": expires_index - self.bar_index,
            },
        )
        if submitted:
            self.second_touch_target = target
            self.second_touch_target_pool_id = (
                None if target_pool_id is None else str(target_pool_id)
            )
            self.second_touch_response_index = self.bar_index
            self.diagnostics["structural_second_touch_submissions"] += 1
            self.diagnostics["confirmed_second_touch_submissions"] += 1
        return submitted


LiquidityResponseStrategy = StructuralSecondTouchStrategy

__all__ = [
    "LiquidityResponseConfig",
    "LiquidityResponseStrategy",
    "StructuralSecondTouchStrategy",
]

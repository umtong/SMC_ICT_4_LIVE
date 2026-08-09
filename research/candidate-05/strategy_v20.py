#!/usr/bin/env python3
"""Candidate 05 v20: execution-time confirmation of a CHoCH retest."""
from __future__ import annotations

import math
from typing import Any

from depth_logic import DIRECTIONAL_DEPTH_MIN
from flow_inflection_logic import FALLBACK_TARGET_NET_R
from flow_inflection_logic import MAX_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import MIN_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import breakaway_follow_through
from flow_inflection_logic import has_adverse_slippage_room
from logic import choose_liquidity_target
from logic import net_r_at_price
from logic import planned_loss_per_unit
from retrace_logic import pending_limit_invalidated
from retest_response_logic import retest_response_ready
from retest_response_logic import retest_touched
from sponsored_choch_logic import slippage_protected_marketable_limit
from strategy_base import LiquidityResponseConfig
from strategy_base import _as_float
from strategy_v18 import ExecutionConfirmedCancelStrategy
from strategy_v9 import ArmedEntryPath


class ConfirmedRetestResponseStrategy(ExecutionConfirmedCancelStrategy):
    """Trade the retest only after its current flow and book reject the level.

    The older path placed a limit at the CHoCH close as soon as the next minute
    was not a breakaway. That exposed the order before the actual retrace showed
    whether liquidity would support or run through it.

    v20 keeps the scenario alive as one auction episode. It waits for an actual
    touch of the CHoCH reference and then requires a completed close back on the
    scenario side, aligned final-15-second aggressor flow, and the already-used
    0.10 directional-depth minimum. A bounded marketable limit is submitted from
    the response close, with sizing based on its configured adverse-slippage cap.
    Until response, structural invalidation, a valid breakaway, funding/evaluation
    boundary, or the existing daytrade horizon resolves the episode, no second
    overlapping sweep scenario is opened.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "retest_response_observation_bars": 0,
                "retest_response_touches": 0,
                "retest_response_confirmations": 0,
                "retest_response_submissions": 0,
                "retest_response_reclaim_rejections": 0,
                "retest_response_flow_rejections": 0,
                "retest_response_depth_rejections": 0,
                "retest_response_stop_invalidations": 0,
                "retest_response_horizon_expiries": 0,
                "retest_response_no_live_target": 0,
                "retest_response_cost_geometry_rejections": 0,
            },
        )

    def _resolve_entry_path(self, row: dict[str, float | int]) -> None:
        armed = self.armed_entry_path
        if armed is None or self.bar_index <= armed.created_index:
            return

        self.diagnostics["retest_response_observation_bars"] += 1
        side = armed.setup.side
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])

        if pending_limit_invalidated(
            side=side,
            stop=armed.stop,
            high=high,
            low=low,
        ):
            self.diagnostics["retest_response_stop_invalidations"] += 1
            self._expire_armed_entry(
                row,
                "SWEEP_EXTREME_INVALIDATED_BEFORE_RETEST_RESPONSE",
            )
            return

        touched = retest_touched(
            side=side,
            reference_price=armed.choch_close,
            high=high,
            low=low,
        )
        if touched:
            touch_count = int(armed.details.get("retest_touch_count", 0)) + 1
            armed.details["retest_touch_count"] = touch_count
            armed.details["latest_retest_touch_index"] = self.bar_index
            armed.details["latest_retest_touch_ts"] = int(row["ts"])
            self.diagnostics["retest_response_touches"] += 1
            if touch_count == 1:
                self.diagnostics["entry_path_retrace"] += 1
                self._transition(
                    armed.setup.scenario_id,
                    "CHOCH_RETEST_TOUCHED",
                    int(row["ts"]),
                    int(row["ts"]),
                    "RETEST_RESPONSE_OBSERVATION",
                    "PRICE_RETURNED_TO_CONFIRMED_CHOCH_REFERENCE",
                    armed.choch_close,
                    {
                        **armed.details,
                        "touch_high": high,
                        "touch_low": low,
                        "touch_close": close,
                    },
                )

            flow_15s = self._feature("flow_15s")
            depth = self._feature("depth_imbalance_1")
            ready = retest_response_ready(
                side=side,
                reference_price=armed.choch_close,
                high=high,
                low=low,
                close=close,
                flow_15s=flow_15s,
                depth_imbalance=depth,
                minimum_directional_depth=DIRECTIONAL_DEPTH_MIN,
            )
            if ready:
                self.diagnostics["retest_response_confirmations"] += 1
                self._submit_retest_response(
                    armed,
                    row,
                    flow_15s=flow_15s,
                    depth_imbalance=depth,
                )
                return

            if side * (close - armed.choch_close) <= 0.0:
                self.diagnostics["retest_response_reclaim_rejections"] += 1
            if not math.isfinite(flow_15s) or side * flow_15s <= 0.0:
                self.diagnostics["retest_response_flow_rejections"] += 1
            if not math.isfinite(depth) or side * depth < DIRECTIONAL_DEPTH_MIN:
                self.diagnostics["retest_response_depth_rejections"] += 1
            return

        is_breakaway = breakaway_follow_through(
            side=side,
            choch_close=armed.choch_close,
            current_close=close,
            atr=armed.atr,
            sweep_depth_imbalance=float(
                armed.setup.details.get("depth_imbalance_1", float("nan")),
            ),
            current_depth_imbalance=self._feature("depth_imbalance_1"),
            current_flow_3m=self._feature("flow_3m"),
        )
        if is_breakaway and self._submit_breakaway_limit(armed, row):
            self.diagnostics["entry_path_breakaway"] += 1
            return

        if self.bar_index - armed.created_index >= self.config.max_hold_bars:
            self.diagnostics["retest_response_horizon_expiries"] += 1
            self._expire_armed_entry(
                row,
                "RETEST_RESPONSE_DAYTRADE_HORIZON_EXPIRED",
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
        observed_price = self.instrument.make_price(float(row["close"]))
        observed = _as_float(observed_price)
        stop_price = self.instrument.make_price(armed.stop)
        stop = _as_float(stop_price)
        if (side > 0 and not stop < observed) or (side < 0 and not observed < stop):
            self.diagnostics["retest_response_cost_geometry_rejections"] += 1
            return False

        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        raw_limit = slippage_protected_marketable_limit(
            observed_price=observed,
            side=side,
            adverse_slippage_rate=slippage_rate,
            price_increment=_as_float(self.instrument.price_increment),
        )
        if not math.isfinite(raw_limit):
            self.diagnostics["retest_response_cost_geometry_rejections"] += 1
            return False

        entry_price = self.instrument.make_price(raw_limit)
        entry = _as_float(entry_price)
        if not has_adverse_slippage_room(
            observed_price=observed,
            limit_price=entry,
            side=side,
            adverse_slippage_rate=slippage_rate,
        ):
            self.diagnostics["retest_response_cost_geometry_rejections"] += 1
            return False

        planned_loss = planned_loss_per_unit(
            entry,
            stop,
            side,
            cost_rate,
            slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["retest_response_cost_geometry_rejections"] += 1
            return False

        target, target_source, target_r = choose_liquidity_target(
            entry=entry,
            side=side,
            pools=list(self.active_pools.values()),
            planned_loss=planned_loss,
            cost_rate=cost_rate,
            min_net_r=MIN_LIQUIDITY_TARGET_NET_R,
            max_net_r=MAX_LIQUIDITY_TARGET_NET_R,
            fallback_net_r=FALLBACK_TARGET_NET_R,
        )
        if not target_source.startswith("POOL:"):
            self.diagnostics["retest_response_no_live_target"] += 1
            return False

        target_price = self.instrument.make_price(target)
        target = _as_float(target_price)
        rounded_r = net_r_at_price(entry, target, side, planned_loss, cost_rate)
        if (
            rounded_r + 1e-9 < MIN_LIQUIDITY_TARGET_NET_R
            or (side > 0 and not stop < entry < target)
            or (side < 0 and not target < entry < stop)
        ):
            self.diagnostics["retest_response_cost_geometry_rejections"] += 1
            return False

        armed.details.update(
            {
                "retest_response_close": observed,
                "retest_response_flow_15s": flow_15s,
                "retest_response_depth_imbalance_1": depth_imbalance,
                "retest_response_directional_flow_15s": side * flow_15s,
                "retest_response_directional_depth": side * depth_imbalance,
                "retest_response_minimum_directional_depth": DIRECTIONAL_DEPTH_MIN,
            },
        )
        self._transition(
            armed.setup.scenario_id,
            "CHOCH_RETEST_RESPONSE_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "RETEST_RESPONSE_EXECUTION",
            "RETEST_RECLAIMED_WITH_CURRENT_FLOW_AND_DEPTH",
            observed,
            {
                **armed.details,
                "raw_slippage_protected_limit": raw_limit,
                "rounded_slippage_protected_limit": entry,
                "target": target,
                "target_source": target_source,
                "rounded_target_net_r": rounded_r,
            },
        )
        submitted = self._submit_price_capped_bracket(
            armed=armed,
            row=row,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            sizing_entry=entry,
            planned_loss=planned_loss,
            target_source=target_source,
            target_r=target_r,
            branch="TAIL_FLOW_CONFIRMED_RETRACE",
            event_type="RETEST_RESPONSE_MARKETABLE_LIMIT_SUBMITTED",
            reason="CURRENT_RETEST_RESPONSE_WITH_COST_AWARE_PRICE_CAP",
            expires_index=self.bar_index + 2,
            entry_tag="CONFIRMED_CHOCH_RETEST_RESPONSE_ENTRY",
            extra={
                "observed_retest_response_price": observed,
                "raw_slippage_protected_limit": raw_limit,
                "rounded_slippage_protected_limit": entry,
                "rounded_target_net_r": rounded_r,
            },
        )
        if submitted:
            self.diagnostics["retest_response_submissions"] += 1
        return submitted


__all__ = ["ConfirmedRetestResponseStrategy"]

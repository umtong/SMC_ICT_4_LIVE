#!/usr/bin/env python3
"""Candidate 05 v25: first passive CHoCH touch after confirmed response."""
from __future__ import annotations

import math
from typing import Any

from confirmed_second_touch_logic import confirmed_second_touch_geometry
from flow_inflection_logic import MIN_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import has_adverse_slippage_room
from logic import net_r_at_price
from logic import planned_loss_per_unit
from scenario_target_logic import frozen_target_reached_before_entry
from sponsored_choch_logic import slippage_protected_marketable_limit
from strategy_base import LiquidityResponseConfig
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v24 import ResetReacceleratedBalanceAcceptanceStrategy


class ConfirmedSecondTouchStrategy(ResetReacceleratedBalanceAcceptanceStrategy):
    """Avoid chasing a valid response when its frozen target is already too near.

    v24 correctly confirms the actual CHoCH retest with a completed reclaim,
    current final-15-second flow and current directional depth. Three otherwise
    coherent paths were then discarded because a marketable entry at the
    response close no longer preserved the existing 0.40 post-cost R to the
    CHoCH-time frozen target.

    v25 does not restore the discarded blind retrace order. Before confirmation
    there is still no executable order. Only after the actual retest response is
    confirmed, if the marketable cap is too late but the original CHoCH close is
    still a valid cost-aware entry, the strategy rests one passive limit at that
    structural reference. It is canceled on the original structural stop,
    frozen-target completion, target-source expiry, funding/evaluation boundary,
    or the earlier of the existing retest and daytrade horizons.

    Signal detectors, frozen target, stop, 3% current-NAV risk sizing, costs,
    slippage, execution model and one-global-intent rule are unchanged.
    """

    SECOND_TOUCH_BRANCH = "TAIL_FLOW_CONFIRMED_SECOND_TOUCH"
    _FROZEN_BRANCHES = (
        ResetReacceleratedBalanceAcceptanceStrategy._FROZEN_BRANCHES
        | {SECOND_TOUCH_BRANCH}
    )

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.second_touch_target = float("nan")
        self.second_touch_target_pool_id: str | None = None
        self.second_touch_response_index = -1
        self.diagnostics.update(
            {
                "confirmed_second_touch_candidates": 0,
                "confirmed_second_touch_submissions": 0,
                "confirmed_second_touch_invalid_geometry": 0,
                "confirmed_second_touch_horizon_exhausted": 0,
                "confirmed_second_touch_targets_reached_unfilled": 0,
                "confirmed_second_touch_sources_expired_unfilled": 0,
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
        """Use the existing marketable response when valid, else one second touch."""
        side = armed.setup.side
        observed_price = self.instrument.make_price(float(row["close"]))
        observed = _as_float(observed_price)
        stop_price = self.instrument.make_price(armed.stop)
        stop = _as_float(stop_price)
        target_price = self.instrument.make_price(self._frozen_target_price(armed))
        target = _as_float(target_price)
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0

        raw_marketable = slippage_protected_marketable_limit(
            observed_price=observed,
            side=side,
            adverse_slippage_rate=slippage_rate,
            price_increment=_as_float(self.instrument.price_increment),
        )
        marketable = _as_float(self.instrument.make_price(raw_marketable)) if math.isfinite(raw_marketable) else float("nan")
        marketable_loss = planned_loss_per_unit(
            marketable,
            stop,
            side,
            cost_rate,
            slippage_rate,
        ) if math.isfinite(marketable) else float("nan")
        marketable_r = net_r_at_price(
            marketable,
            target,
            side,
            marketable_loss,
            cost_rate,
        ) if math.isfinite(marketable_loss) and marketable_loss > 0.0 else float("nan")
        marketable_valid = (
            math.isfinite(marketable)
            and has_adverse_slippage_room(
                observed_price=observed,
                limit_price=marketable,
                side=side,
                adverse_slippage_rate=slippage_rate,
            )
            and math.isfinite(marketable_loss)
            and marketable_loss > 0.0
            and marketable_r + 1e-9 >= MIN_LIQUIDITY_TARGET_NET_R
            and (
                stop < marketable < target
                if side > 0
                else target < marketable < stop
            )
        )
        if marketable_valid:
            return super()._submit_retest_response(
                armed,
                row,
                flow_15s=flow_15s,
                depth_imbalance=depth_imbalance,
            )

        entry_price = self.instrument.make_price(armed.choch_close)
        entry = _as_float(entry_price)
        geometry = confirmed_second_touch_geometry(
            side=side,
            choch_reference=entry,
            confirmed_response_price=observed,
            stop=stop,
            target=target,
            cost_rate=cost_rate,
            adverse_slippage_rate=slippage_rate,
            minimum_net_r=MIN_LIQUIDITY_TARGET_NET_R,
        )
        if geometry is None:
            self.diagnostics["confirmed_second_touch_invalid_geometry"] += 1
            return super()._submit_retest_response(
                armed,
                row,
                flow_15s=flow_15s,
                depth_imbalance=depth_imbalance,
            )

        expires_index = min(
            armed.created_index + self.config.max_hold_bars,
            self.bar_index + self.config.acceptance_retrace_bars,
        )
        if expires_index <= self.bar_index:
            self.diagnostics["confirmed_second_touch_horizon_exhausted"] += 1
            self._expire_armed_entry(
                row,
                "CONFIRMED_SECOND_TOUCH_HORIZON_ALREADY_EXHAUSTED",
            )
            return False

        target_source = str(armed.details.get("frozen_target_source", ""))
        target_pool_id = armed.details.get("frozen_target_pool_id")
        armed.details.update(
            {
                "retest_response_close": observed,
                "retest_response_flow_15s": flow_15s,
                "retest_response_depth_imbalance_1": depth_imbalance,
                "retest_response_directional_flow_15s": side * flow_15s,
                "retest_response_directional_depth": side * depth_imbalance,
                "marketable_response_entry": marketable,
                "marketable_response_target_net_r": marketable_r,
                "confirmed_second_touch_entry": geometry.entry,
                "confirmed_second_touch_target_net_r": geometry.target_net_r,
                "confirmed_second_touch_expires_index": expires_index,
            },
        )
        self.diagnostics["confirmed_second_touch_candidates"] += 1
        self._transition(
            armed.setup.scenario_id,
            "CHOCH_RETEST_RESPONSE_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "CONFIRMED_SECOND_TOUCH_ENTRY_PENDING",
            "RETEST_RESPONSE_CONFIRMED_BUT_MARKETABLE_ENTRY_TOO_CLOSE_TO_FROZEN_TARGET",
            observed,
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
            event_type="CONFIRMED_SECOND_TOUCH_LIMIT_SUBMITTED",
            reason="FIRST_PASSIVE_RETURN_TO_CHOCH_AFTER_CONFIRMED_RETEST_RESPONSE",
            expires_index=expires_index,
            entry_tag="CONFIRMED_CHOCH_SECOND_TOUCH_ENTRY",
            extra={
                "confirmed_response_price": observed,
                "discarded_marketable_entry": marketable,
                "discarded_marketable_target_net_r": marketable_r,
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
            self.diagnostics["confirmed_second_touch_submissions"] += 1
        return submitted

    def _manage_pending_entry(self, row: dict[str, float | int]) -> None:
        if (
            self.current_branch == self.SECOND_TOUCH_BRANCH
            and self.entry_pending
            and not self.entry_cancel_requested
        ):
            if frozen_target_reached_before_entry(
                side=self.entry_side,
                target=self.second_touch_target,
                high=float(row["high"]),
                low=float(row["low"]),
            ):
                self.diagnostics["confirmed_second_touch_targets_reached_unfilled"] += 1
                self._request_second_touch_cancel(
                    row,
                    "FROZEN_TARGET_REACHED_BEFORE_CONFIRMED_SECOND_TOUCH_FILL",
                )
                return
            if (
                self.second_touch_target_pool_id is not None
                and self.second_touch_target_pool_id not in self.active_pools
            ):
                self.diagnostics["confirmed_second_touch_sources_expired_unfilled"] += 1
                self._request_second_touch_cancel(
                    row,
                    "FROZEN_TARGET_SOURCE_EXPIRED_BEFORE_CONFIRMED_SECOND_TOUCH_FILL",
                )
                return
        super()._manage_pending_entry(row)

    def _request_second_touch_cancel(
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
                    "target": self.second_touch_target,
                    "bars_resting": self.bar_index - self.entry_pending_index,
                },
            )

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self.second_touch_target = float("nan")
        self.second_touch_target_pool_id = None
        self.second_touch_response_index = -1


__all__ = ["ConfirmedSecondTouchStrategy"]

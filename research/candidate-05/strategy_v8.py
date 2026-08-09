#!/usr/bin/env python3
"""Candidate 05 v8: tail-flow inflection into the next live liquidity pool."""
from __future__ import annotations

import math

from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce

from flow_inflection_logic import FALLBACK_TARGET_NET_R
from flow_inflection_logic import MAX_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import MIN_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import choch_flow_state
from flow_inflection_logic import directional_tail_improvement
from flow_inflection_logic import sweep_tail_recovers
from logic import choose_liquidity_target
from logic import floor_quantity
from logic import net_r_at_price
from logic import planned_loss_per_unit
from retrace_logic import structural_stop
from strategy_base import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v4 import LiquidityResponseDepthStrategy


class TailFlowLiquidityStrategy(LiquidityResponseDepthStrategy):
    """Trade an early reversal only toward an observable opposing liquidity draw.

    The scenario begins with a completed five-minute liquidity pool raid which
    reclaims the range while aggressive flow is absorbed and the threatened side
    of the book refills. The final fifteen seconds must improve materially toward
    the reversal, resting depth must support that direction, and the subsequent
    CHoCH must occur before three-minute flow becomes a mature chase.

    Entry rests at the completed CHoCH close. The structural invalidation remains
    beyond the original sweep extreme. The target is the nearest still-live
    opposing five-minute pool whose post-cost reward is at least 0.40 planned-loss
    units, capped at 1.50 units. Thus entry, invalidation, and exit all correspond
    to explicit auction-state transitions rather than a candle pattern.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "sweep_tail_inflection_pass": 0,
                "sweep_tail_inflection_fail": 0,
                "choch_active_confirmation": 0,
                "choch_passive_rotation": 0,
                "choch_flow_rejected": 0,
                "choch_close_limit_submissions": 0,
                "liquidity_pool_targets": 0,
                "fallback_targets": 0,
            },
        )

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        if setup is not None and not bool(setup.details.get("sweep_tail_checked", False)):
            flow_15s = float(setup.details.get("flow_15s", float("nan")))
            flow_60s = float(setup.details.get("flow_60s", float("nan")))
            improvement = directional_tail_improvement(
                side=setup.side,
                flow_15s=flow_15s,
                flow_60s=flow_60s,
            )
            setup.details["sweep_tail_checked"] = True
            setup.details["directional_tail_improvement"] = improvement
            if not sweep_tail_recovers(
                side=setup.side,
                flow_15s=flow_15s,
                flow_60s=flow_60s,
            ):
                self.diagnostics["sweep_tail_inflection_fail"] += 1
                self._expire_pending(row, "SWEEP_TAIL_DID_NOT_INFLECT_TOWARD_REVERSAL")
                return False
            self.diagnostics["sweep_tail_inflection_pass"] += 1
            self._transition(
                setup.scenario_id,
                "SWEEP_TAIL_INFLECTION_CONFIRMED",
                int(row["ts"]),
                int(row["ts"]),
                "CHOCH_ARMED",
                "FINAL_FIFTEEN_SECONDS_IMPROVED_TOWARD_REVERSAL",
                float(row["close"]),
                setup.details,
            )
        return super()._process_pending(row)

    def _submit_entry(self, setup: PendingSetup, row: dict[str, float | int]) -> bool:
        side = setup.side
        flow_state = choch_flow_state(
            side=side,
            flow_15s=self._feature("flow_15s"),
            flow_3m=self._feature("flow_3m"),
            depth_imbalance=self._feature("depth_imbalance_1"),
        )
        if flow_state is None:
            self.diagnostics["choch_flow_rejected"] += 1
            self._expire_pending(row, "CHOCH_FLOW_WAS_COUNTER_DIRECTIONAL_OR_ALREADY_MATURE")
            return False
        if flow_state == "ACTIVE_CONFIRMATION":
            self.diagnostics["choch_active_confirmation"] += 1
        else:
            self.diagnostics["choch_passive_rotation"] += 1

        atr = self._atr()
        entry_price = self.instrument.make_price(float(row["close"]))
        stop_price = self.instrument.make_price(
            structural_stop(setup.sweep_extreme, side, atr, self.config.stop_buffer_atr),
        )
        entry, stop = _as_float(entry_price), _as_float(stop_price)
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(
            entry,
            stop,
            side,
            cost_rate,
            self.config.adverse_slippage_bps_each_side / 10_000.0,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self._expire_pending(row, "INVALID_CHOCH_CLOSE_STOP_GEOMETRY")
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
        target_price = self.instrument.make_price(target)
        target = _as_float(target_price)
        if (side > 0 and not stop < entry < target) or (side < 0 and not target < entry < stop):
            self._expire_pending(row, "INVALID_LIQUIDITY_TARGET_BRACKET")
            return False

        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        quantity_value = floor_quantity(
            risk_budget / planned_loss,
            int(self.instrument.size_precision),
        )
        if quantity_value <= 0.0 or quantity_value * entry < 10.0:
            self._expire_pending(row, "QUANTITY_BELOW_INSTRUMENT_MINIMUM")
            return False

        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY if side > 0 else OrderSide.SELL,
            quantity=self.instrument.make_qty(quantity_value),
            entry_order_type=OrderType.LIMIT,
            entry_price=entry_price,
            time_in_force=TimeInForce.GTC,
            entry_post_only=False,
            tp_price=target_price,
            tp_post_only=False,
            sl_trigger_price=stop_price,
            entry_tags=["TAIL_FLOW_REVERSAL_ENTRY"],
            tp_tags=["OPPOSING_LIQUIDITY_TARGET"],
            sl_tags=["SWEEP_EXTREME_INVALIDATION"],
        )
        self.submit_order_list(order_list)
        self.entry_pending = True
        self.entry_pending_index = self.bar_index
        self.entry_expires_index = self.bar_index + 2
        self.entry_side = side
        self.entry_stop = stop
        self.entry_limit = entry
        self.last_entry_index = self.bar_index
        self.current_scenario_id = setup.scenario_id
        self.current_branch = "TAIL_FLOW_LIQUIDITY_REVERSAL"
        self.current_pool_level = setup.pool_level
        self.pending = None
        self.diagnostics["entry_submissions"] += 1
        self.diagnostics["choch_close_limit_submissions"] += 1
        self.diagnostics["max_simultaneous_entry_intents"] = 1
        if target_source.startswith("POOL:"):
            self.diagnostics["liquidity_pool_targets"] += 1
        else:
            self.diagnostics["fallback_targets"] += 1

        self._transition(
            setup.scenario_id,
            "CHOCH_CLOSE_LIMIT_SUBMITTED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_PENDING",
            "EARLY_FLOW_REVERSAL_TOWARD_LIVE_OPPOSING_LIQUIDITY",
            entry,
            {
                **setup.details,
                "flow_state": flow_state,
                "choch_flow_15s": self._feature("flow_15s"),
                "choch_flow_60s": self._feature("flow_60s"),
                "choch_flow_3m": self._feature("flow_3m"),
                "choch_depth_imbalance_1": self._feature("depth_imbalance_1"),
                "side": side,
                "sweep_extreme": setup.sweep_extreme,
                "confirmation_close": float(row["close"]),
                "entry_limit": entry,
                "stop": stop,
                "target": target,
                "target_source": target_source,
                "target_net_r_before_rounding": target_r,
                "target_net_r_after_rounding": net_r_at_price(
                    entry,
                    target,
                    side,
                    planned_loss,
                    cost_rate,
                ),
                "quantity": quantity_value,
                "equity": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit": planned_loss,
                "planned_account_loss": quantity_value * planned_loss,
            },
        )
        return True


__all__ = ["TailFlowLiquidityStrategy"]

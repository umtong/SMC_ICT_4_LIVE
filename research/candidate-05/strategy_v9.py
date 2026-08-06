#!/usr/bin/env python3
"""Candidate 05 v9: observe one minute, then retrace or price-protected breakaway."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce

from flow_inflection_logic import FALLBACK_TARGET_NET_R
from flow_inflection_logic import MAX_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import MIN_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import breakaway_follow_through
from flow_inflection_logic import choch_flow_state
from flow_inflection_logic import has_adverse_slippage_room
from flow_inflection_logic import worst_entry_preserving_net_r
from logic import choose_liquidity_target
from logic import floor_quantity
from logic import net_r_at_price
from logic import planned_loss_per_unit
from retrace_logic import pending_limit_invalidated
from retrace_logic import structural_stop
from strategy_base import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v8 import TailFlowLiquidityStrategy


@dataclass(slots=True)
class ArmedEntryPath:
    setup: PendingSetup
    flow_state: str
    choch_close: float
    stop: float
    atr: float
    created_index: int
    created_ts: int
    details: dict[str, Any]


class ObservedEntryPathStrategy(TailFlowLiquidityStrategy):
    """Separate a first retrace from a true no-retrace breakaway.

    CHoCH only arms the entry path. The next completed minute determines whether
    the auction is rotating back to the CHoCH close or extending without a
    tradable retrace. Ordinary rotations rest one limit at the CHoCH close for
    the original eight-bar rejection horizon. A no-retrace extension requires a
    one-ATR follow-through, a two-to-one favorable book at the sweep, persistent
    directional depth and aligned three-minute flow.

    Breakaways never use an unbounded market order. The strategy finds the next
    live opposing liquidity target, solves the worst entry price which preserves
    at least 0.40 post-cost R, and submits one marketable-but-price-capped limit.
    Quantity is sized from that worst price, so a fill at the limit still respects
    the three-percent NAV loss budget.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.armed_entry_path: ArmedEntryPath | None = None
        self.diagnostics.update(
            {
                "entry_path_armed": 0,
                "entry_path_retrace": 0,
                "entry_path_breakaway": 0,
                "breakaway_geometry_rejected": 0,
                "breakaway_price_protection_rejected": 0,
                "breakaway_limit_submissions": 0,
                "retrace_limit_submissions": 0,
            },
        )

    def on_bar(self, bar: Bar) -> None:
        self.bar_index += 1
        row = {
            "ts": int(bar.ts_event),
            "open": _as_float(bar.open),
            "high": _as_float(bar.high),
            "low": _as_float(bar.low),
            "close": _as_float(bar.close),
            "volume": _as_float(bar.volume),
        }
        previous_close = float(self.bars[-1]["close"]) if self.bars else float(row["open"])
        self.bars.append(row)
        self._advance_features(int(row["ts"]))
        self._record_equity(int(row["ts"]))
        self._update_five_minute(row)
        self._prune_pools(row)

        if not self.portfolio.is_flat(self.config.instrument_id):
            self.diagnostics["max_open_positions_observed"] = 1
            self._manage_open_position(row)
            return
        if self.entry_pending:
            self._manage_pending_entry(row)
            return
        if not self._in_evaluation(int(row["ts"])):
            self.pending = None
            self._expire_armed_entry(row, "EVALUATION_ENDED_BEFORE_ENTRY_PATH_RESOLUTION")
            return
        if self._funding_blackout(int(row["ts"])):
            if self.armed_entry_path is not None:
                self._expire_armed_entry(row, "FUNDING_BLACKOUT_BEFORE_ENTRY_PATH_RESOLUTION")
            else:
                self._expire_pending(row, "FUNDING_BLACKOUT")
            return
        if not self._features_ready(int(row["ts"])) or len(self.bars) < self.config.atr_period + 2:
            return
        if self.armed_entry_path is not None:
            self._resolve_entry_path(row)
            return
        if self.pending is not None and self._process_pending(row):
            return
        if self.pending is None and self.bar_index - self.last_entry_index >= self.config.cooldown_bars:
            self._detect_sweep(row, previous_close)

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
        stop = _as_float(
            self.instrument.make_price(
                structural_stop(setup.sweep_extreme, side, atr, self.config.stop_buffer_atr),
            ),
        )
        choch_close = _as_float(self.instrument.make_price(float(row["close"])))
        if (side > 0 and not stop < choch_close) or (side < 0 and not choch_close < stop):
            self._expire_pending(row, "INVALID_ARMED_ENTRY_STOP_GEOMETRY")
            return False

        details = {
            **setup.details,
            "flow_state": flow_state,
            "choch_flow_15s": self._feature("flow_15s"),
            "choch_flow_60s": self._feature("flow_60s"),
            "choch_flow_3m": self._feature("flow_3m"),
            "choch_depth_imbalance_1": self._feature("depth_imbalance_1"),
            "side": side,
            "sweep_extreme": setup.sweep_extreme,
            "confirmation_close": choch_close,
            "stop": stop,
        }
        self.armed_entry_path = ArmedEntryPath(
            setup=setup,
            flow_state=flow_state,
            choch_close=choch_close,
            stop=stop,
            atr=atr,
            created_index=self.bar_index,
            created_ts=int(row["ts"]),
            details=details,
        )
        self.pending = None
        self.diagnostics["entry_path_armed"] += 1
        self._transition(
            setup.scenario_id,
            "ENTRY_PATH_ARMED",
            int(row["ts"]),
            int(row["ts"]),
            "ONE_MINUTE_PATH_OBSERVATION",
            "WAIT_FOR_RETRACE_OR_LIQUIDITY_SPONSORED_BREAKAWAY",
            choch_close,
            details,
        )
        return True

    def _resolve_entry_path(self, row: dict[str, float | int]) -> None:
        armed = self.armed_entry_path
        if armed is None or self.bar_index <= armed.created_index:
            return
        side = armed.setup.side
        if pending_limit_invalidated(
            side=side,
            stop=armed.stop,
            high=float(row["high"]),
            low=float(row["low"]),
        ):
            self._expire_armed_entry(row, "SWEEP_EXTREME_INVALIDATED_DURING_PATH_OBSERVATION")
            return

        is_breakaway = breakaway_follow_through(
            side=side,
            choch_close=armed.choch_close,
            current_close=float(row["close"]),
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
        self.diagnostics["entry_path_retrace"] += 1
        self._submit_retrace_limit(armed, row)

    def _submit_retrace_limit(
        self,
        armed: ArmedEntryPath,
        row: dict[str, float | int],
    ) -> bool:
        side = armed.setup.side
        entry = armed.choch_close
        stop = armed.stop
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(entry, stop, side, cost_rate, slippage_rate)
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self._expire_armed_entry(row, "INVALID_RETRACE_STOP_GEOMETRY")
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
        entry_price = self.instrument.make_price(entry)
        stop_price = self.instrument.make_price(stop)
        if (side > 0 and not stop < entry < target) or (side < 0 and not target < entry < stop):
            self._expire_armed_entry(row, "INVALID_RETRACE_LIQUIDITY_BRACKET")
            return False
        return self._submit_price_capped_bracket(
            armed=armed,
            row=row,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            sizing_entry=entry,
            planned_loss=planned_loss,
            target_source=target_source,
            target_r=target_r,
            branch="TAIL_FLOW_LIQUIDITY_RETRACE",
            event_type="CHOCH_RETRACE_LIMIT_SUBMITTED",
            reason="FIRST_RETRACE_TO_CONFIRMED_CHOCH_CLOSE",
            expires_index=armed.created_index + self.config.rejection_confirmation_bars,
            entry_tag="CHOCH_RETRACE_ENTRY",
        )

    def _submit_breakaway_limit(
        self,
        armed: ArmedEntryPath,
        row: dict[str, float | int],
    ) -> bool:
        side = armed.setup.side
        observed_entry = _as_float(self.instrument.make_price(float(row["close"])))
        stop = armed.stop
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        observed_loss = planned_loss_per_unit(observed_entry, stop, side, cost_rate, slippage_rate)
        if not math.isfinite(observed_loss) or observed_loss <= 0.0:
            self.diagnostics["breakaway_geometry_rejected"] += 1
            return False
        target, target_source, target_r = choose_liquidity_target(
            entry=observed_entry,
            side=side,
            pools=list(self.active_pools.values()),
            planned_loss=observed_loss,
            cost_rate=cost_rate,
            min_net_r=MIN_LIQUIDITY_TARGET_NET_R,
            max_net_r=MAX_LIQUIDITY_TARGET_NET_R,
            fallback_net_r=FALLBACK_TARGET_NET_R,
        )
        target_price = self.instrument.make_price(target)
        target = _as_float(target_price)
        raw_bound = worst_entry_preserving_net_r(
            stop=stop,
            target=target,
            side=side,
            minimum_net_r=MIN_LIQUIDITY_TARGET_NET_R,
            cost_rate=cost_rate,
            adverse_slippage_rate=slippage_rate,
        )
        if not math.isfinite(raw_bound):
            self.diagnostics["breakaway_geometry_rejected"] += 1
            return False
        price_increment = _as_float(self.instrument.price_increment)
        entry_price = self.instrument.make_price(raw_bound)
        entry = _as_float(entry_price)
        if side > 0 and entry > raw_bound:
            entry_price = self.instrument.make_price(raw_bound - price_increment)
            entry = _as_float(entry_price)
        elif side < 0 and entry < raw_bound:
            entry_price = self.instrument.make_price(raw_bound + price_increment)
            entry = _as_float(entry_price)
        if not has_adverse_slippage_room(
            observed_price=observed_entry,
            limit_price=entry,
            side=side,
            adverse_slippage_rate=slippage_rate,
        ):
            self.diagnostics["breakaway_price_protection_rejected"] += 1
            return False
        planned_loss = planned_loss_per_unit(entry, stop, side, cost_rate, slippage_rate)
        rounded_target_r = net_r_at_price(entry, target, side, planned_loss, cost_rate)
        if rounded_target_r + 1e-9 < MIN_LIQUIDITY_TARGET_NET_R:
            self.diagnostics["breakaway_geometry_rejected"] += 1
            return False
        stop_price = self.instrument.make_price(stop)
        self.diagnostics["breakaway_limit_submissions"] += 1
        return self._submit_price_capped_bracket(
            armed=armed,
            row=row,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            sizing_entry=entry,
            planned_loss=planned_loss,
            target_source=target_source,
            target_r=target_r,
            branch="TAIL_FLOW_LIQUIDITY_BREAKAWAY",
            event_type="PRICE_PROTECTED_BREAKAWAY_LIMIT_SUBMITTED",
            reason="NO_RETRACE_EXTENSION_WITH_TARGET_DERIVED_ENTRY_CAP",
            expires_index=self.bar_index + 2,
            entry_tag="PRICE_PROTECTED_BREAKAWAY_ENTRY",
            extra={
                "observed_breakaway_price": observed_entry,
                "raw_maximum_acceptable_entry": raw_bound,
                "rounded_maximum_acceptable_entry": entry,
                "breakaway_extension_atr": side * (float(row["close"]) - armed.choch_close) / armed.atr,
                "breakaway_flow_3m": self._feature("flow_3m"),
                "breakaway_depth_imbalance_1": self._feature("depth_imbalance_1"),
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
        side = armed.setup.side
        entry = _as_float(entry_price)
        stop = _as_float(stop_price)
        target = _as_float(target_price)
        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        quantity_value = floor_quantity(risk_budget / planned_loss, int(self.instrument.size_precision))
        if quantity_value <= 0.0 or quantity_value * sizing_entry < 10.0:
            self._expire_armed_entry(row, "QUANTITY_BELOW_INSTRUMENT_MINIMUM")
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
            entry_tags=[entry_tag],
            tp_tags=["OPPOSING_LIQUIDITY_TARGET"],
            sl_tags=["SWEEP_EXTREME_INVALIDATION"],
        )
        self.submit_order_list(order_list)
        self.entry_pending = True
        self.entry_pending_index = self.bar_index
        self.entry_expires_index = expires_index
        self.entry_side = side
        self.entry_stop = stop
        self.entry_limit = entry
        self.last_entry_index = self.bar_index
        self.current_scenario_id = armed.setup.scenario_id
        self.current_branch = branch
        self.current_pool_level = armed.setup.pool_level
        self.armed_entry_path = None
        self.diagnostics["entry_submissions"] += 1
        self.diagnostics["max_simultaneous_entry_intents"] = 1
        if branch.endswith("RETRACE"):
            self.diagnostics["retrace_limit_submissions"] += 1
        if target_source.startswith("POOL:"):
            self.diagnostics["liquidity_pool_targets"] += 1
        else:
            self.diagnostics["fallback_targets"] += 1
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        details = {
            **armed.details,
            "branch": branch,
            "entry_limit": entry,
            "stop": stop,
            "target": target,
            "target_source": target_source,
            "target_net_r_before_rounding": target_r,
            "target_net_r_after_rounding": net_r_at_price(entry, target, side, planned_loss, cost_rate),
            "quantity": quantity_value,
            "equity": equity,
            "risk_budget": risk_budget,
            "planned_loss_per_unit": planned_loss,
            "planned_account_loss": quantity_value * planned_loss,
            "entry_expires_index": expires_index,
            **(extra or {}),
        }
        self._transition(
            armed.setup.scenario_id,
            event_type,
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_PENDING",
            reason,
            entry,
            details,
        )
        return True

    def _expire_armed_entry(self, row: dict[str, float | int], reason: str) -> None:
        armed = self.armed_entry_path
        if armed is None:
            return
        self.diagnostics["expired_setups"] += 1
        self._transition(
            armed.setup.scenario_id,
            "ARMED_ENTRY_PATH_INVALIDATED",
            int(row["ts"]),
            int(row["ts"]),
            "CLOSED",
            reason,
            float(row["close"]),
            armed.details,
        )
        self.armed_entry_path = None

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self.armed_entry_path = None


__all__ = ["ObservedEntryPathStrategy"]

"""New-leg confirmation, cost geometry, and Nautilus bracket for V50."""
from __future__ import annotations

import math
from typing import Any

from nautilus_trader.model.enums import OrderSide, TimeInForce

from logic import confirmation_passes, floor_quantity, net_r_at_price, planned_loss_per_unit
from v50_types import DeleveragingWatch


class Candidate50ExecutionMixin:
    def _candidate50_close_watch(
        self,
        row: dict[str, float | int],
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        watch = self._candidate50_watch
        if watch is None:
            return
        self._transition(
            watch.scenario_id,
            "DELEVERAGING_EXHAUSTION_NO_TRADE",
            int(row["ts"]),
            int(row["ts"]),
            "CLOSED",
            reason,
            float(row["close"]),
            {**watch.details, **(details or {})},
        )
        self._candidate50_watch = None

    def _candidate50_process_watch(self, row: dict[str, float | int]) -> None:
        watch = self._candidate50_watch
        if watch is None or self.bar_index <= watch.created_index:
            return
        if self.bar_index > watch.expires_index:
            self.diagnostics["candidate50_watches_expired"] += 1
            self._candidate50_close_watch(row, "OPPOSITE_INITIATIVE_WINDOW_EXPIRED")
            return
        ts_event = int(row["ts"])
        if self._funding_blackout(ts_event) or not self._in_evaluation(ts_event):
            self._candidate50_close_watch(row, "FUNDING_OR_EVALUATION_BOUNDARY")
            return
        close = float(row["close"])
        if watch.shock_direction * (close - watch.shock_extreme) > 0.0:
            self.diagnostics["candidate50_new_discovery_invalidations"] += 1
            self._candidate50_close_watch(row, "SHOCK_DIRECTION_PRICE_DISCOVERY_RESUMED")
            return
        if watch.reversal_side * (watch.boundary - close) <= 0.0:
            self.diagnostics["candidate50_target_consumed_before_trigger"] += 1
            self._candidate50_close_watch(row, "OLD_BALANCE_BOUNDARY_REACHED_BEFORE_ENTRY")
            return
        if not self._features_ready(ts_event):
            return

        side = watch.reversal_side
        initiative = confirmation_passes(
            side=side,
            open_price=float(row["open"]),
            close_price=close,
            high=float(row["high"]),
            low=float(row["low"]),
            structure=watch.reversal_structure,
            atr=watch.atr,
            flow_60s=self._feature("flow_60s"),
            efficiency_60s=self._feature("efficiency_60s"),
            min_body_atr=self.config.rejection_confirm_body_atr,
            min_flow=self.config.rejection_confirm_flow_min,
            min_efficiency=self.config.rejection_confirm_efficiency_min,
            min_close_location=self.config.rejection_confirm_close_location,
        )
        if not initiative:
            return
        self.diagnostics["candidate50_reversal_initiatives"] += 1
        poc = self._feature("footprint_poc_price")
        delta = self._feature("footprint_delta_60s")
        poc_migrated = (
            math.isfinite(poc)
            and math.isfinite(delta)
            and side * (poc - watch.event_poc) > 0.0
            and side * delta > 0.0
        )
        if not poc_migrated:
            self.diagnostics["candidate50_poc_migration_blocks"] += 1
            return
        self._candidate50_submit(watch, row, poc=poc, delta=delta)

    def _candidate50_submit(
        self,
        watch: DeleveragingWatch,
        row: dict[str, float | int],
        *,
        poc: float,
        delta: float,
    ) -> None:
        side = watch.reversal_side
        entry = float(row["close"])
        stop = watch.shock_extreme - side * self.config.stop_buffer_atr * watch.atr
        target = watch.boundary
        valid = stop < entry < target if side > 0 else target < entry < stop
        if not valid:
            self.diagnostics["candidate50_geometry_rejections"] += 1
            self._candidate50_close_watch(row, "INVALID_REVERSAL_GEOMETRY")
            return

        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(
            entry, stop, side, cost_rate, slippage_rate,
        )
        target_r = net_r_at_price(entry, target, side, planned_loss, cost_rate)
        if (
            not math.isfinite(planned_loss)
            or planned_loss <= 0.0
            or not math.isfinite(target_r)
            or target_r < self.config.min_target_net_r
        ):
            self.diagnostics["candidate50_geometry_rejections"] += 1
            self._candidate50_close_watch(
                row,
                "OLD_BALANCE_TARGET_HAS_INSUFFICIENT_COST_AFTER_GEOMETRY",
                {"candidate50_target_net_r": target_r},
            )
            return

        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        quantity = floor_quantity(
            risk_budget / planned_loss,
            int(self.instrument.size_precision),
        )
        if quantity <= 0.0 or quantity * entry < 10.0:
            self.diagnostics["candidate50_geometry_rejections"] += 1
            self._candidate50_close_watch(row, "QUANTITY_BELOW_INSTRUMENT_MINIMUM")
            return

        details = {
            **watch.details,
            "candidate50_trigger_ts_ns": int(row["ts"]),
            "candidate50_trigger_poc": poc,
            "candidate50_trigger_delta": delta,
            "candidate50_entry_estimate": entry,
            "candidate50_stop": stop,
            "candidate50_target": target,
            "candidate50_target_net_r": target_r,
            "candidate50_equity": equity,
            "candidate50_risk_budget": risk_budget,
            "candidate50_planned_loss_per_unit": planned_loss,
            "candidate50_quantity": quantity,
            "candidate50_planned_account_loss": quantity * planned_loss,
        }
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY if side > 0 else OrderSide.SELL,
            quantity=self.instrument.make_qty(quantity),
            time_in_force=TimeInForce.GTC,
            tp_price=self.instrument.make_price(target),
            sl_trigger_price=self.instrument.make_price(stop),
        )
        self.submit_order_list(order_list)
        self.entry_pending = True
        self.entry_pending_index = self.bar_index
        self.last_entry_index = self.bar_index
        self.current_scenario_id = watch.scenario_id
        self.current_branch = "REJECTION"
        self.current_pool_level = watch.boundary
        self._candidate50_watch = None
        self.diagnostics["entry_submissions"] += 1
        self.diagnostics["candidate50_entries_submitted"] += 1
        self.diagnostics["max_simultaneous_entry_intents"] = max(
            int(self.diagnostics["max_simultaneous_entry_intents"]), 1,
        )
        self._transition(
            watch.scenario_id,
            "DELEVERAGING_EXHAUSTION_REVERSAL_ENTRY_SUBMITTED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_PENDING",
            "NEW_OPPOSITE_AUCTION_LEG_WITH_VALUE_MIGRATION",
            entry,
            details,
        )

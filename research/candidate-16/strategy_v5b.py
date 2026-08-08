"""Candidate 16 v5b: all-or-none capped execution for completed v5 states.

The v5 untouched run proved that a STOP_LIMIT trigger submitted after a
completed bar can already be behind the next replayed bar and is then rejected
as "in the market".  This module changes no state, direction, confirmation,
stop, target, cost, risk, or date.  It reuses the repository's proven
Candidate-18 execution repair: a LIMIT parent at the identical worst-fill cap
with FOK time-in-force.  The entry fills in full at or better than the cap on
the first executable event, or opens no position.
"""
from __future__ import annotations

import math

from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce

from logic import floor_quantity
from logic import planned_loss_per_unit
from strategy_base import PendingSetup
from strategy_v5 import Candidate16V5Config
from strategy_v5 import Candidate16V5Strategy as _Candidate16V5Strategy


class Candidate16V5BStrategy(_Candidate16V5Strategy):
    """Execute a completed crowded-failure state with a capped FOK bracket."""

    def __init__(self, config: Candidate16V5Config) -> None:
        super().__init__(config=config)
        self.diagnostics["candidate16_v5b_fok_limit_entries"] = 0

    def _submit_entry(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
    ) -> bool:
        atr = self._atr()
        side = setup.side
        signal_close = float(row["close"])
        if not math.isfinite(atr) or atr <= 0.0 or side not in (-1, 1):
            self.diagnostics["candidate16_v5_geometry_rejected"] = int(
                self.diagnostics["candidate16_v5_geometry_rejected"],
            ) + 1
            self._expire_pending(row, "INVALID_CROWDED_FAILURE_GEOMETRY")
            return False

        shock_high = float(setup.details["shock_high"])
        shock_low = float(setup.details["shock_low"])
        shock_extreme = shock_low if side > 0 else shock_high
        stop = shock_extreme - side * self.config.stop_buffer_atr * atr

        # Preserve v5's exact worst permissible price. Only the trigger race is
        # removed; the fill cap, stop geometry and 3% worst-fill risk are fixed.
        entry_trigger = (
            signal_close + side * self.config.crowded_entry_rearm_atr * atr
        )
        structural_risk = abs(entry_trigger - stop)
        if not math.isfinite(structural_risk) or structural_risk <= 0.0:
            self.diagnostics["candidate16_v5_geometry_rejected"] = int(
                self.diagnostics["candidate16_v5_geometry_rejected"],
            ) + 1
            self._expire_pending(row, "INVALID_CROWDED_FAILURE_STOP")
            return False
        entry_limit = entry_trigger + (
            side
            * self.config.crowded_entry_limit_risk_expansion
            * structural_risk
        )

        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        adverse_slippage_rate = (
            self.config.adverse_slippage_bps_each_side / 10_000.0
        )
        planned_loss = planned_loss_per_unit(
            entry_limit,
            stop,
            side,
            cost_rate,
            adverse_slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["candidate16_v5_geometry_rejected"] = int(
                self.diagnostics["candidate16_v5_geometry_rejected"],
            ) + 1
            self._expire_pending(row, "INVALID_CROWDED_FOK_LIMIT_RISK")
            return False

        target_values = self._natural_target(
            entry=entry_limit,
            side=side,
            planned_loss=planned_loss,
            cost_rate=cost_rate,
            setup=setup,
        )
        if target_values is None:
            self.diagnostics["candidate16_v5_no_natural_target"] = int(
                self.diagnostics["candidate16_v5_no_natural_target"],
            ) + 1
            self._expire_pending(
                row,
                "NO_COST_AWARE_NATURAL_OBJECTIVE_FOR_CROWDED_FAILURE",
            )
            return False
        target, target_source, target_r = target_values

        if side > 0 and not (stop < signal_close < entry_limit < target):
            self.diagnostics["candidate16_v5_geometry_rejected"] = int(
                self.diagnostics["candidate16_v5_geometry_rejected"],
            ) + 1
            self._expire_pending(row, "INVALID_LONG_CROWDED_FOK_LIMIT_BRACKET")
            return False
        if side < 0 and not (target < entry_limit < signal_close < stop):
            self.diagnostics["candidate16_v5_geometry_rejected"] = int(
                self.diagnostics["candidate16_v5_geometry_rejected"],
            ) + 1
            self._expire_pending(row, "INVALID_SHORT_CROWDED_FOK_LIMIT_BRACKET")
            return False

        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        raw_quantity = risk_budget / planned_loss
        quantity_value = floor_quantity(
            raw_quantity,
            int(self.instrument.size_precision),
        )
        if quantity_value <= 0.0 or quantity_value * entry_limit < 10.0:
            self._expire_pending(row, "QUANTITY_BELOW_INSTRUMENT_MINIMUM")
            return False

        order_side = OrderSide.BUY if side > 0 else OrderSide.SELL
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=order_side,
            quantity=self.instrument.make_qty(quantity_value),
            entry_order_type=OrderType.LIMIT,
            entry_price=self.instrument.make_price(entry_limit),
            time_in_force=TimeInForce.FOK,
            entry_post_only=False,
            entry_tags=["ENTRY", "CANDIDATE16_V5B_FOK_PRICE_CAP"],
            tp_price=self.instrument.make_price(target),
            sl_trigger_price=self.instrument.make_price(stop),
        )
        self.submit_order_list(order_list)
        self.entry_pending = True
        self.entry_pending_index = self.bar_index
        self.last_entry_index = self.bar_index
        self.current_scenario_id = setup.scenario_id
        self.current_branch = setup.branch
        self.current_pool_level = setup.pool_level
        self.pending = None
        self.diagnostics["entry_submissions"] = int(
            self.diagnostics["entry_submissions"],
        ) + 1
        self.diagnostics["candidate16_v5b_fok_limit_entries"] = int(
            self.diagnostics["candidate16_v5b_fok_limit_entries"],
        ) + 1
        self.diagnostics["max_simultaneous_entry_intents"] = max(
            int(self.diagnostics["max_simultaneous_entry_intents"]),
            1,
        )
        self._transition(
            setup.scenario_id,
            "ENTRY_SUBMITTED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_PENDING",
            "COMPLETED_CROWDED_FAILURE_WITH_ALL_OR_NONE_PRICE_CAP",
            entry_limit,
            {
                **setup.details,
                "candidate16_version": "v5b-fok-execution-repair",
                "branch": setup.branch,
                "side": side,
                "signal_close": signal_close,
                "preserved_v5_entry_trigger": entry_trigger,
                "entry_limit_worst_fill": entry_limit,
                "entry_time_in_force": "FOK",
                "entry_all_or_none": True,
                "stop": stop,
                "target": target,
                "target_source": target_source,
                "target_net_r": target_r,
                "quantity": quantity_value,
                "equity": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit_at_worst_fill": planned_loss,
                "planned_account_loss_at_worst_fill": (
                    quantity_value * planned_loss
                ),
            },
        )
        return True


__all__ = ["Candidate16V5Config", "Candidate16V5BStrategy"]

#!/usr/bin/env python3
"""Candidate 05 v5: depth-confirmed retrace plus two-to-one breakaway entry."""
from __future__ import annotations

import math
from typing import Any

from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce

from breakaway_logic import BREAKAWAY_FAVORABLE_DEPTH_RATIO
from breakaway_logic import breakaway_depth_state
from breakaway_logic import favorable_depth_ratio
from logic import cost_aware_target
from logic import floor_quantity
from logic import net_r_at_price
from logic import planned_loss_per_unit
from retrace_logic import structural_stop
from strategy_base import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v4 import LiquidityResponseDepthStrategy


class LiquidityResponseBreakawayStrategy(LiquidityResponseDepthStrategy):
    """Use market entry only when favorable resting depth is at least two-to-one.

    Ordinary depth-confirmed rejection scenarios retain the v2 first 50% retrace
    limit. A separate breakaway state is recognized when favorable resting depth
    is at least twice adverse depth. In that state a deep rebalance is no longer
    the expected path, so the confirmed CHoCH submits a market bracket instead.
    Risk remains the same 3% NAV loss budget with the same structural stop,
    explicit costs and adverse-slippage allowance.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "breakaway_favorable_depth_ratio": BREAKAWAY_FAVORABLE_DEPTH_RATIO,
                "breakaway_market_submissions": 0,
            },
        )

    def _submit_entry(self, setup: PendingSetup, row: dict[str, float | int]) -> bool:
        imbalance = float(setup.details.get("depth_imbalance_1", float("nan")))
        if breakaway_depth_state(side=setup.side, depth_imbalance=imbalance):
            return self._submit_breakaway_entry(setup, row, imbalance)
        return super()._submit_entry(setup, row)

    def _submit_breakaway_entry(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
        depth_imbalance: float,
    ) -> bool:
        side = setup.side
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
            self._expire_pending(row, "INVALID_BREAKAWAY_STOP_GEOMETRY")
            return False

        target_price = self.instrument.make_price(
            cost_aware_target(
                entry,
                side,
                planned_loss,
                self.config.rejection_target_net_r,
                cost_rate,
            ),
        )
        target = _as_float(target_price)
        if (side > 0 and not stop < entry < target) or (side < 0 and not target < entry < stop):
            self._expire_pending(row, "INVALID_BREAKAWAY_BRACKET")
            return False

        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        quantity_value = floor_quantity(
            risk_budget / planned_loss,
            int(self.instrument.size_precision),
        )
        if quantity_value <= 0.0 or quantity_value * entry < 10.0:
            self._expire_pending(row, "BREAKAWAY_QUANTITY_BELOW_INSTRUMENT_MINIMUM")
            return False

        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY if side > 0 else OrderSide.SELL,
            quantity=self.instrument.make_qty(quantity_value),
            entry_order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
            tp_price=target_price,
            tp_post_only=False,
            sl_trigger_price=stop_price,
        )
        self.submit_order_list(order_list)
        self.entry_pending = True
        self.entry_pending_index = self.bar_index
        self.entry_expires_index = self.bar_index + 2
        self.entry_side, self.entry_stop, self.entry_limit = side, stop, entry
        self.last_entry_index = self.bar_index
        self.current_scenario_id = setup.scenario_id
        self.current_branch = "REJECTION_BREAKAWAY"
        self.current_pool_level = setup.pool_level
        self.pending = None
        self.diagnostics["entry_submissions"] += 1
        self.diagnostics["breakaway_market_submissions"] += 1
        self.diagnostics["max_simultaneous_entry_intents"] = 1
        ratio = favorable_depth_ratio(side=side, depth_imbalance=depth_imbalance)
        self._transition(
            setup.scenario_id,
            "MARKET_BRACKET_SUBMITTED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_PENDING",
            "TWO_TO_ONE_RESTING_DEPTH_BREAKAWAY",
            entry,
            {
                **setup.details,
                "side": side,
                "sweep_extreme": setup.sweep_extreme,
                "confirmation_close": float(row["close"]),
                "expected_entry": entry,
                "stop": stop,
                "target": target,
                "favorable_depth_ratio": ratio,
                "minimum_favorable_depth_ratio": BREAKAWAY_FAVORABLE_DEPTH_RATIO,
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

    def on_position_opened(self, event: Any) -> None:
        if self.current_branch != "REJECTION_BREAKAWAY":
            super().on_position_opened(event)
            return
        self.entry_pending = False
        self.exit_pending = False
        self.position_open_index = self.bar_index
        if self.current_scenario_id is not None:
            ts = int(getattr(event, "ts_event", self.bars[-1]["ts"]))
            self._transition(
                self.current_scenario_id,
                "POSITION_OPENED",
                ts,
                ts,
                "POSITION_OPEN",
                "NAUTILUS_MARKET_ENTRY_FILLED",
                float(self.bars[-1]["close"]),
                {"event": str(event), "expected_entry": self.entry_limit},
            )


__all__ = ["LiquidityResponseBreakawayStrategy"]

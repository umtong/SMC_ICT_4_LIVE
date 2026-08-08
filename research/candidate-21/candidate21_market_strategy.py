"""Diagnostic market-entry variant for Candidate 21.

The same-minute response alpha, state classification, natural target, stop and
3% current-NAV risk budget are unchanged. Only the parent entry instruction is
changed from price-capped FOK limit to the inherited NautilusTrader market
bracket. This is an attribution experiment: if the signals lose after they are
actually entered, the alpha is rejected; if they work, execution can be designed
separately without confusing no-fill behavior with signal quality.
"""
from __future__ import annotations

import math

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce

from candidate21_response_strategy import Candidate21ResponseConfig
from candidate21_response_strategy import Candidate21ResponseStrategy
from logic import floor_quantity
from logic import net_r_at_price
from logic import planned_loss_per_unit
from strategy_base import PendingSetup


class Candidate21MarketDiagnosticConfig(Candidate21ResponseConfig, frozen=True):
    """No new fitted parameters."""


class Candidate21MarketDiagnosticStrategy(Candidate21ResponseStrategy):
    """Force resolved signals through a conservative market bracket."""

    def __init__(self, config: Candidate21MarketDiagnosticConfig) -> None:
        super().__init__(config=config)
        self.diagnostics["candidate21_market_diagnostic_entries"] = 0

    def _submit_entry(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
    ) -> bool:
        atr = self._atr()
        side = setup.side
        entry_estimate = float(row["close"])
        if setup.branch == "REJECTION":
            stop = setup.sweep_extreme - side * self.config.stop_buffer_atr * atr
        else:
            stop = setup.pool_level - side * self.config.stop_buffer_atr * atr

        natural_target = float(setup.details.get("natural_target", float("nan")))
        if (
            not math.isfinite(entry_estimate)
            or not math.isfinite(stop)
            or not math.isfinite(natural_target)
            or entry_estimate <= 0.0
            or stop <= 0.0
            or natural_target <= 0.0
        ):
            self._expire_pending(row, "INVALID_MARKET_DIAGNOSTIC_GEOMETRY")
            return False
        if side > 0 and not (stop < entry_estimate < natural_target):
            self.diagnostics["candidate21_natural_target_geometry_rejected"] = int(
                self.diagnostics["candidate21_natural_target_geometry_rejected"]
            ) + 1
            self._expire_pending(row, "LONG_NATURAL_TARGET_ALREADY_CONSUMED")
            return False
        if side < 0 and not (natural_target < entry_estimate < stop):
            self.diagnostics["candidate21_natural_target_geometry_rejected"] = int(
                self.diagnostics["candidate21_natural_target_geometry_rejected"]
            ) + 1
            self._expire_pending(row, "SHORT_NATURAL_TARGET_ALREADY_CONSUMED")
            return False

        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        adverse_slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(
            entry_estimate,
            stop,
            side,
            cost_rate,
            adverse_slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self._expire_pending(row, "INVALID_MARKET_DIAGNOSTIC_PLANNED_LOSS")
            return False
        target_net_r = net_r_at_price(
            entry_estimate,
            natural_target,
            side,
            planned_loss,
            cost_rate,
        )
        if target_net_r < self.config.min_target_net_r:
            self.diagnostics["candidate21_natural_target_geometry_rejected"] = int(
                self.diagnostics["candidate21_natural_target_geometry_rejected"]
            ) + 1
            self._expire_pending(row, "NATURAL_TARGET_BELOW_MINIMUM_NET_R")
            return False

        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        raw_quantity = risk_budget / planned_loss
        quantity_value = floor_quantity(
            raw_quantity,
            int(self.instrument.size_precision),
        )
        if quantity_value <= 0.0 or quantity_value * entry_estimate < 10.0:
            self._expire_pending(row, "QUANTITY_BELOW_INSTRUMENT_MINIMUM")
            return False

        order_side = OrderSide.BUY if side > 0 else OrderSide.SELL
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=order_side,
            quantity=self.instrument.make_qty(quantity_value),
            time_in_force=TimeInForce.GTC,
            tp_price=self.instrument.make_price(natural_target),
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
            self.diagnostics["entry_submissions"]
        ) + 1
        self.diagnostics["candidate21_market_diagnostic_entries"] = int(
            self.diagnostics["candidate21_market_diagnostic_entries"]
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
            "MARKET_ENTRY_ALPHA_ATTRIBUTION_DIAGNOSTIC",
            entry_estimate,
            {
                **setup.details,
                "branch": setup.branch,
                "side": side,
                "entry_estimate": entry_estimate,
                "entry_order_type": "MARKET",
                "diagnostic_only": True,
                "stop": stop,
                "target": natural_target,
                "target_net_r": target_net_r,
                "quantity": quantity_value,
                "equity": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit": planned_loss,
                "planned_account_loss": quantity_value * planned_loss,
            },
        )
        return True


__all__ = [
    "Candidate21MarketDiagnosticConfig",
    "Candidate21MarketDiagnosticStrategy",
]

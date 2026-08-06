#!/usr/bin/env python3
"""Candidate-04 v4: accepted-auction continuation entered on causal retest.

The v3 competing-risk state remains unchanged. A rejection failure no longer
triggers a market chase at the acceptance close. Instead, a GTD limit bracket
rests between the accepted sweep extreme and the acceptance-body midpoint.
NautilusTrader decides whether the retest fills, expires, stops or targets.
"""
from __future__ import annotations

import math
from datetime import timedelta
from typing import Any

from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import TimeInForce

from nt_liquidity_strategy import LiquidityTransitionConfig
from nt_liquidity_strategy import PendingSetup
from nt_liquidity_strategy import cost_aware_target
from nt_liquidity_strategy import floor_quantity
from nt_liquidity_strategy_v3 import LiquidityTransitionStrategyV3


def accepted_retest_price(
    acceptance_open: float,
    acceptance_close: float,
    sweep_extreme: float,
    side: int,
) -> float:
    """Return a favorable retest that cannot cross back beyond the accepted level."""
    midpoint = (acceptance_open + acceptance_close) / 2.0
    if side > 0:
        return max(sweep_extreme, midpoint)
    return min(sweep_extreme, midpoint)


class LiquidityTransitionStrategyV4(LiquidityTransitionStrategyV3):
    """Enter accepted continuation only if the market causally retests value."""

    RETRACE_EXPIRY_BARS = 15

    def __init__(self, config: LiquidityTransitionConfig) -> None:
        super().__init__(config)
        self.retrace_expiry_index = -1

    def on_bar(self, bar: Bar) -> None:
        # The base strategy's two-bar safeguard protects market brackets. A
        # deliberate GTD retest order instead remains valid for 15 completed bars.
        if self.entry_pending and self.portfolio.is_flat(self.config.instrument_id):
            next_index = self.bar_index + 1
            if self.retrace_expiry_index >= 0 and next_index > self.retrace_expiry_index:
                self.cancel_all_orders(self.config.instrument_id)
                self.entry_pending = False
                self.current_scenario = None
                self.retrace_expiry_index = -1
            else:
                self.entry_pending_index = next_index + self.RETRACE_EXPIRY_BARS
        super().on_bar(bar)

    def on_position_opened(self, event: Any) -> None:
        self.retrace_expiry_index = -1
        super().on_position_opened(event)

    def on_order_expired(self, event: Any) -> None:
        if self.entry_pending and self.portfolio.is_flat(self.config.instrument_id):
            self._clear_retest_intent("RETEST_ENTRY_EXPIRED", event)

    def on_order_canceled(self, event: Any) -> None:
        if self.entry_pending and self.portfolio.is_flat(self.config.instrument_id):
            self._clear_retest_intent("RETEST_ENTRY_CANCELED", event)

    def on_order_rejected(self, event: Any) -> None:
        if self.entry_pending and self.portfolio.is_flat(self.config.instrument_id):
            self.retrace_expiry_index = -1
        super().on_order_rejected(event)

    def _clear_retest_intent(self, event_type: str, event: Any) -> None:
        scenario = self.current_scenario or "SESSION_REJECTION_FAILURE_RETRACE"
        row = self.bars[-1]
        self._event(event_type, scenario, row, {"event": str(event)})
        self.entry_pending = False
        self.current_scenario = None
        self.retrace_expiry_index = -1

    def _process_reversal_probe(self, row: dict[str, float | int]) -> None:
        probe = self.reversal_probe
        if probe is None:
            return
        if self.bar_index > probe.expires_index:
            self._event(
                "REVERSAL_PROBE_EXPIRED",
                "SESSION_REJECTION_COMPETING_RISK",
                row,
                probe.details,
            )
            self.reversal_probe = None
            return

        reversal_target_hit = (
            float(row["high"]) >= probe.reversal_target
            if probe.reversal_side > 0
            else float(row["low"]) <= probe.reversal_target
        )
        if reversal_target_hit:
            self._event(
                "REVERSAL_SUCCEEDED_NO_TRADE",
                "SESSION_REJECTION_COMPETING_RISK",
                row,
                probe.details,
            )
            self.reversal_probe = None
            return

        continuation_side = -probe.reversal_side
        accepted = (
            float(row["close"]) > probe.sweep_extreme
            if continuation_side > 0
            else float(row["close"]) < probe.sweep_extreme
        )
        if not accepted:
            return

        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        body = continuation_side * (
            float(row["close"]) - float(row["open"])
        ) / atr
        volume_burst = self._volume_burst()
        close_location = self._close_location(row, continuation_side)
        if not (
            body >= self.config.trend_body_atr
            and volume_burst >= self.config.trend_volume_burst
            and close_location >= self.config.trend_close_location
        ):
            return

        rows = list(self.bars)
        first_global_index = self.bar_index - len(rows) + 1
        start = max(0, probe.created_index - first_global_index)
        reaction = rows[start:]
        if continuation_side > 0:
            reaction_extreme = min(float(item["low"]) for item in reaction)
        else:
            reaction_extreme = max(float(item["high"]) for item in reaction)

        setup = PendingSetup(
            scenario="SESSION_REJECTION_FAILURE_RETRACE_CONTINUATION",
            side=continuation_side,
            created_index=probe.created_index,
            expires_index=self.bar_index,
            extreme=reaction_extreme,
            structure=probe.sweep_extreme,
            atr=atr,
            target_reference=None,
            details=probe.details,
        )
        details = {
            **probe.details,
            "acceptance_body_atr": body,
            "acceptance_volume_burst": volume_burst,
            "acceptance_close_location": close_location,
            "accepted_sweep_extreme": probe.sweep_extreme,
        }
        self.reversal_probe = None
        if not self._submit_retest_bracket(setup, row, details):
            self._event(
                "RETEST_EXECUTION_REJECTED",
                setup.scenario,
                row,
                details,
            )

    def _submit_retest_bracket(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
        details: dict[str, Any],
    ) -> bool:
        side = setup.side
        atr = self._atr()
        stop = setup.extreme - side * self.config.stop_buffer_atr * atr
        entry = accepted_retest_price(
            float(row["open"]),
            float(row["close"]),
            float(details["accepted_sweep_extreme"]),
            side,
        )
        current = float(row["close"])
        favorable_limit = entry < current if side > 0 else entry > current
        price_loss = side * (entry - stop)
        if not favorable_limit or not math.isfinite(price_loss) or price_loss <= 0.0:
            return False

        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        planned_loss = price_loss + cost_rate * (entry + stop)
        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        quantity_value = floor_quantity(
            risk_budget / planned_loss,
            int(self.instrument.size_precision),
        )
        if quantity_value <= 0.0 or quantity_value * entry < 10.0:
            return False

        target = cost_aware_target(
            entry,
            side,
            planned_loss,
            self.config.trend_target_net_r,
            cost_rate,
        )
        order_side = OrderSide.BUY if side > 0 else OrderSide.SELL
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=order_side,
            quantity=self.instrument.make_qty(quantity_value),
            time_in_force=TimeInForce.GTD,
            expire_time=self.clock.utc_now() + timedelta(minutes=self.RETRACE_EXPIRY_BARS),
            entry_price=self.instrument.make_price(entry),
            entry_order_type=OrderType.LIMIT,
            tp_price=self.instrument.make_price(target),
            sl_trigger_price=self.instrument.make_price(stop),
        )
        self.submit_order_list(order_list)
        self.entry_pending = True
        self.entry_pending_index = self.bar_index + self.RETRACE_EXPIRY_BARS
        self.retrace_expiry_index = self.bar_index + self.RETRACE_EXPIRY_BARS
        self.current_scenario = setup.scenario
        self.last_entry_by_scenario[setup.scenario] = self.bar_index
        self._event(
            "RETEST_ENTRY_SUBMITTED",
            setup.scenario,
            row,
            {
                **details,
                "acceptance_close": current,
                "retest_entry": entry,
                "stop": stop,
                "target": target,
                "quantity": quantity_value,
                "equity": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit": planned_loss,
                "expiry_bars": self.RETRACE_EXPIRY_BARS,
            },
        )
        return True


__all__ = [
    "LiquidityTransitionConfig",
    "LiquidityTransitionStrategyV4",
    "accepted_retest_price",
]

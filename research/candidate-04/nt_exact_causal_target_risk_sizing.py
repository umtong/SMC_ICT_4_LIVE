#!/usr/bin/env python3
"""Current-NAV risk sizing which preserves the exact causal alpha target.

The signal strategy first selects a price level that existed before the signal:
a causal external-liquidity boundary or a pre-signal dealing-range projection.
The previous sizing adapter then replaced distant valid targets with an arbitrary
2.4R cap. A next-bar gap could consume most of that capped reward even though
the original causal target remained intact and reachable.

This adapter changes one relation only: when a causal target reference exists
and meets the existing minimum net-R check at the completed signal close, the
submitted bracket uses that exact reference. Entry/stop fill estimates, current
NAV 3% loss budget, fees, adverse-transition buffer, quantity precision and all
NautilusTrader order/account mechanics are unchanged.
"""
from __future__ import annotations

import math
from typing import Any

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce

from nt_causal_risk_sizing import GAP_QUANTILE
from nt_causal_risk_sizing import GAP_WINDOW_BARS
from nt_causal_risk_sizing import causal_gap_buffer
from nt_causal_risk_sizing import _tick_size
from nt_liquidity_strategy import PendingSetup
from nt_liquidity_strategy import cost_aware_target
from nt_liquidity_strategy import floor_quantity
from nt_liquidity_strategy import net_r_at_price


def select_exact_causal_target(
    *,
    signal_entry: float,
    stop_trigger: float,
    side: int,
    cost_rate: float,
    fallback_target_net_r: float,
    target_reference: float | None,
    minimum_reference_net_r: float,
) -> tuple[float, float | None] | None:
    """Return the exact pre-signal target or an unchanged fixed-R fallback."""

    if side not in (-1, 1):
        return None
    values = (signal_entry, stop_trigger, cost_rate, fallback_target_net_r)
    if not all(math.isfinite(float(value)) for value in values):
        return None
    signal_price_loss = side * (signal_entry - stop_trigger)
    if signal_price_loss <= 0.0:
        return None
    signal_planned_loss = signal_price_loss + cost_rate * (
        signal_entry + stop_trigger
    )
    if not math.isfinite(signal_planned_loss) or signal_planned_loss <= 0.0:
        return None

    if target_reference is None:
        target = cost_aware_target(
            signal_entry,
            side,
            signal_planned_loss,
            fallback_target_net_r,
            cost_rate,
        )
        return target, None

    reference = float(target_reference)
    if not math.isfinite(reference) or side * (reference - signal_entry) <= 0.0:
        return None
    reference_r = net_r_at_price(
        signal_entry,
        reference,
        side,
        signal_planned_loss,
        cost_rate,
    )
    if (
        not math.isfinite(reference_r)
        or reference_r < minimum_reference_net_r
    ):
        return None
    return reference, reference_r


def exact_causal_target_risk_sized_submit_bracket(
    self: Any,
    setup: PendingSetup,
    row: dict[str, float | int],
    target_net_r: float,
    details: dict[str, Any],
) -> bool:
    """Submit a Nautilus bracket without replacing the causal target by an R cap."""

    side = int(setup.side)
    atr = float(self._atr())
    if not math.isfinite(atr) or atr <= 0.0:
        return False
    stop_trigger = float(
        setup.extreme - side * self.config.stop_buffer_atr * atr
    )
    signal_entry = float(row["close"])
    cost_rate = float(self.config.all_in_cost_bps_each_side) / 10_000.0
    selected = select_exact_causal_target(
        signal_entry=signal_entry,
        stop_trigger=stop_trigger,
        side=side,
        cost_rate=cost_rate,
        fallback_target_net_r=float(target_net_r),
        target_reference=(
            float(setup.target_reference)
            if setup.target_reference is not None
            else None
        ),
        minimum_reference_net_r=float(
            self.config.session_min_opposite_target_r
        ),
    )
    if selected is None:
        return False
    target, reference_r_signal = selected

    signal_price_loss = side * (signal_entry - stop_trigger)
    signal_planned_loss = signal_price_loss + cost_rate * (
        signal_entry + stop_trigger
    )

    # Quantity remains based on conservative expected later fills only.
    excursion_buffer, gap_observations = causal_gap_buffer(
        list(self.bars),
        side,
    )
    tick = _tick_size(self.instrument)
    expected_entry_fill = signal_entry + side * excursion_buffer
    expected_stop_fill = stop_trigger - side * (excursion_buffer + tick)
    execution_price_loss = side * (expected_entry_fill - expected_stop_fill)
    if not math.isfinite(execution_price_loss) or execution_price_loss <= 0.0:
        return False
    execution_planned_loss = execution_price_loss + cost_rate * (
        expected_entry_fill + expected_stop_fill
    )
    if not math.isfinite(execution_planned_loss) or execution_planned_loss <= 0.0:
        return False

    equity = float(self._equity_value())
    risk_budget = equity * float(self.config.risk_fraction)
    raw_qty = risk_budget / execution_planned_loss
    quantity_value = floor_quantity(
        raw_qty,
        int(self.instrument.size_precision),
    )
    if quantity_value <= 0.0 or quantity_value * expected_entry_fill < 10.0:
        return False

    order_side = OrderSide.BUY if side > 0 else OrderSide.SELL
    order_list = self.order_factory.bracket(
        instrument_id=self.config.instrument_id,
        order_side=order_side,
        quantity=self.instrument.make_qty(quantity_value),
        time_in_force=TimeInForce.GTC,
        tp_price=self.instrument.make_price(target),
        sl_trigger_price=self.instrument.make_price(stop_trigger),
    )
    self.submit_order_list(order_list)
    self.entry_pending = True
    self.entry_pending_index = self.bar_index
    self.current_scenario = setup.scenario
    self.last_entry_by_scenario[setup.scenario] = self.bar_index
    self._event(
        "ENTRY_SUBMITTED",
        setup.scenario,
        row,
        {
            **details,
            "signal_entry": signal_entry,
            "stop_trigger": stop_trigger,
            "target": target,
            "target_contract": "exact_pre_signal_causal_reference",
            "arbitrary_target_r_cap_removed": setup.target_reference is not None,
            "reference_net_r_at_signal": reference_r_signal,
            "signal_planned_loss_per_unit": signal_planned_loss,
            "expected_entry_fill": expected_entry_fill,
            "expected_stop_fill": expected_stop_fill,
            "execution_planned_loss_per_unit": execution_planned_loss,
            "quantity": quantity_value,
            "equity": equity,
            "risk_budget": risk_budget,
            "adverse_excursion_buffer": excursion_buffer,
            "excursion_quantile": GAP_QUANTILE,
            "excursion_window_bars": GAP_WINDOW_BARS,
            "excursion_observations": gap_observations,
            "stop_slippage_ticks": 1,
        },
    )
    return True


__all__ = [
    "exact_causal_target_risk_sized_submit_bracket",
    "select_exact_causal_target",
]

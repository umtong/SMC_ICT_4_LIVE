#!/usr/bin/env python3
"""Isolate causal expected-fill sizing while preserving the old target cap.

This adapter changes exactly one implementation relation from the validated V31
core: the q95 whole-bar reserve is replaced by the observed bar-execution
contract's expected adverse close transition plus one entry tick and one stop
tick. The original target-selection contract, including the 2.4R cap, is
retained deliberately for controlled attribution.
"""
from __future__ import annotations

import math
from typing import Any

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce

from nt_causal_risk_sizing import GAP_WINDOW_BARS
from nt_causal_risk_sizing import _tick_size
from nt_expected_fill_risk_sizing import FILL_EXPECTATION_CONTRACT
from nt_expected_fill_risk_sizing import causal_expected_entry_deterioration
from nt_liquidity_strategy import PendingSetup
from nt_liquidity_strategy import cost_aware_target
from nt_liquidity_strategy import floor_quantity
from nt_liquidity_strategy import net_r_at_price


def capped_signal_target(
    *,
    signal_entry: float,
    stop_trigger: float,
    side: int,
    cost_rate: float,
    requested_target_net_r: float,
    target_reference: float | None,
    minimum_reference_net_r: float,
    maximum_reference_net_r: float,
) -> tuple[float, float | None, float] | None:
    """Reproduce the validated V31 target relation exactly."""

    signal_price_loss = side * (signal_entry - stop_trigger)
    if side not in (-1, 1) or signal_price_loss <= 0.0:
        return None
    signal_planned_loss = signal_price_loss + cost_rate * (
        signal_entry + stop_trigger
    )
    if not math.isfinite(signal_planned_loss) or signal_planned_loss <= 0.0:
        return None

    selected_r = float(requested_target_net_r)
    reference_r: float | None = None
    if target_reference is not None:
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
        selected_r = min(reference_r, maximum_reference_net_r)

    target = cost_aware_target(
        signal_entry,
        side,
        signal_planned_loss,
        selected_r,
        cost_rate,
    )
    return target, reference_r, signal_planned_loss


def expected_fill_only_submit_bracket(
    self: Any,
    setup: PendingSetup,
    row: dict[str, float | int],
    target_net_r: float,
    details: dict[str, Any],
) -> bool:
    """Submit the old capped target with causal expected fill prices."""

    side = int(setup.side)
    atr = float(self._atr())
    if side not in (-1, 1) or not math.isfinite(atr) or atr <= 0.0:
        return False
    stop_trigger = float(
        setup.extreme - side * self.config.stop_buffer_atr * atr
    )
    signal_entry = float(row["close"])
    cost_rate = float(self.config.all_in_cost_bps_each_side) / 10_000.0
    selected = capped_signal_target(
        signal_entry=signal_entry,
        stop_trigger=stop_trigger,
        side=side,
        cost_rate=cost_rate,
        requested_target_net_r=float(target_net_r),
        target_reference=(
            float(setup.target_reference)
            if setup.target_reference is not None
            else None
        ),
        minimum_reference_net_r=float(
            self.config.session_min_opposite_target_r
        ),
        maximum_reference_net_r=float(self.config.session_max_target_r),
    )
    if selected is None:
        return False
    target, reference_r_signal, signal_planned_loss = selected

    entry_deterioration, entry_observations = (
        causal_expected_entry_deterioration(list(self.bars), side)
    )
    if not math.isfinite(entry_deterioration):
        return False

    tick = _tick_size(self.instrument)
    expected_entry_fill = signal_entry + side * (
        entry_deterioration + tick
    )
    expected_stop_fill = stop_trigger - side * tick
    execution_price_loss = side * (
        expected_entry_fill - expected_stop_fill
    )
    execution_planned_loss = execution_price_loss + cost_rate * (
        expected_entry_fill + expected_stop_fill
    )
    if not math.isfinite(execution_planned_loss) or execution_planned_loss <= 0.0:
        return False

    equity = float(self._equity_value())
    risk_budget = equity * float(self.config.risk_fraction)
    raw_quantity = risk_budget / execution_planned_loss
    quantity_value = floor_quantity(
        raw_quantity,
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
            "target_contract": "validated_v31_capped_reference",
            "reference_net_r_at_signal": reference_r_signal,
            "signal_planned_loss_per_unit": signal_planned_loss,
            "expected_entry_fill": expected_entry_fill,
            "expected_stop_fill": expected_stop_fill,
            "execution_planned_loss_per_unit": execution_planned_loss,
            "quantity": quantity_value,
            "equity": equity,
            "risk_budget": risk_budget,
            "entry_expected_adverse_close_transition": entry_deterioration,
            "entry_adverse_tick": tick,
            "stop_expected_adverse_excursion": 0.0,
            "stop_adverse_tick": tick,
            "fill_expectation": FILL_EXPECTATION_CONTRACT,
            "fill_expectation_window_bars": GAP_WINDOW_BARS,
            "entry_expectation_observations": entry_observations,
            "stop_slippage_ticks": 1,
        },
    )
    return True


__all__ = ["capped_signal_target", "expected_fill_only_submit_bracket"]

#!/usr/bin/env python3
"""Current-NAV sizing from causal expected, direction-specific fill costs.

The former adapter used the nearest-rank q95 of an entire next-bar adverse
excursion for both the entry and the stop. That quantity is a tail reserve, not
the expected entry or stop fill requested by the project loss-budget equation.
It also reused entry-direction excursions for stop-through slippage, although a
long entry is worsened by upward movement and its stop is worsened by downward
movement (vice versa for a short).

This adapter changes the fill expectation only:

* expected market-entry deterioration is the arithmetic mean of completed
  close-to-next-bar excursions in the entry direction;
* expected stop deterioration is the arithmetic mean in the opposite direction;
* the exact pre-signal causal target, current Nautilus NAV, 3% risk budget,
  all-in fees, one stop tick, signal stop and every order/account mechanism stay
  unchanged.

The arithmetic mean includes zero excursions. It therefore estimates the
unconditional cost of the next executable bar rather than selecting a fitted
quantile. All observations are completed before the current signal.
"""
from __future__ import annotations

import math
from typing import Any

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce

from nt_causal_risk_sizing import GAP_WINDOW_BARS
from nt_causal_risk_sizing import _tick_size
from nt_liquidity_strategy import PendingSetup
from nt_liquidity_strategy import floor_quantity
from nt_exact_causal_target_risk_sizing import select_exact_causal_target


EXPECTED_GAP_MIN_OBSERVATIONS = 120


def directional_entry_excursions(
    rows: list[dict[str, float | int]],
    side: int,
    window: int = GAP_WINDOW_BARS,
) -> list[float]:
    """Completed close-to-next-bar deterioration for a market entry."""

    if side not in (-1, 1):
        return []
    selected = rows[-(window + 1) :]
    result: list[float] = []
    for previous, current in zip(selected, selected[1:]):
        previous_close = float(previous["close"])
        deterioration = (
            float(current["high"]) - previous_close
            if side > 0
            else previous_close - float(current["low"])
        )
        if math.isfinite(deterioration):
            result.append(max(deterioration, 0.0))
    return result


def causal_expected_entry_deterioration(
    rows: list[dict[str, float | int]],
    side: int,
) -> tuple[float, int]:
    """Arithmetic expected adverse entry excursion from completed bars."""

    values = directional_entry_excursions(rows, side)
    if len(values) < EXPECTED_GAP_MIN_OBSERVATIONS:
        return float("nan"), len(values)
    return sum(values) / len(values), len(values)


def expected_fill_risk_sized_submit_bracket(
    self: Any,
    setup: PendingSetup,
    row: dict[str, float | int],
    target_net_r: float,
    details: dict[str, Any],
) -> bool:
    """Submit the exact causal target with expected entry and stop fills."""

    side = int(setup.side)
    atr = float(self._atr())
    if side not in (-1, 1) or not math.isfinite(atr) or atr <= 0.0:
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
    if not math.isfinite(signal_planned_loss) or signal_planned_loss <= 0.0:
        return False

    completed = list(self.bars)
    entry_deterioration, entry_observations = (
        causal_expected_entry_deterioration(completed, side)
    )
    stop_deterioration, stop_observations = (
        causal_expected_entry_deterioration(completed, -side)
    )
    if not (
        math.isfinite(entry_deterioration)
        and math.isfinite(stop_deterioration)
    ):
        return False

    tick = _tick_size(self.instrument)
    expected_entry_fill = signal_entry + side * entry_deterioration
    expected_stop_fill = stop_trigger - side * (stop_deterioration + tick)
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
            "target_contract": "exact_pre_signal_causal_reference",
            "reference_net_r_at_signal": reference_r_signal,
            "signal_planned_loss_per_unit": signal_planned_loss,
            "expected_entry_fill": expected_entry_fill,
            "expected_stop_fill": expected_stop_fill,
            "execution_planned_loss_per_unit": execution_planned_loss,
            "quantity": quantity_value,
            "equity": equity,
            "risk_budget": risk_budget,
            "entry_expected_adverse_excursion": entry_deterioration,
            "stop_expected_adverse_excursion": stop_deterioration,
            "fill_expectation": "arithmetic_mean_completed_directional_excursion",
            "fill_expectation_window_bars": GAP_WINDOW_BARS,
            "entry_expectation_observations": entry_observations,
            "stop_expectation_observations": stop_observations,
            "stop_slippage_ticks": 1,
        },
    )
    return True


__all__ = [
    "EXPECTED_GAP_MIN_OBSERVATIONS",
    "causal_expected_entry_deterioration",
    "directional_entry_excursions",
    "expected_fill_risk_sized_submit_bracket",
]

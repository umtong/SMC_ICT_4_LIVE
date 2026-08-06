#!/usr/bin/env python3
"""Causal entry/stop fill estimates for candidate-04 NautilusTrader orders.

The strategy's causal liquidity target is selected from the signal state and is
not changed by risk sizing. Position quantity, however, must not assume that the
signal close equals later market and stop fills. The planned loss per unit uses
a direction-specific 95th percentile of completed close-to-next-bar adverse
excursions plus one exchange tick beyond the stop trigger.

This cleanly separates alpha logic from the project's expected-fill risk formula.
It is not a leverage cap, score multiplier, or separate execution simulator.
"""
from __future__ import annotations

import math
from typing import Any
from typing import Iterable

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce

from nt_liquidity_strategy import PendingSetup
from nt_liquidity_strategy import cost_aware_target
from nt_liquidity_strategy import floor_quantity
from nt_liquidity_strategy import net_r_at_price


GAP_WINDOW_BARS = 720
GAP_QUANTILE = 0.95
GAP_MIN_OBSERVATIONS = 120


def nearest_rank_quantile(values: Iterable[float], probability: float) -> float:
    finite = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not finite:
        return 0.0
    if not 0.0 <= probability <= 1.0:
        raise ValueError("probability must be in [0, 1]")
    rank = max(1, math.ceil(probability * len(finite)))
    return finite[min(rank - 1, len(finite) - 1)]


def adverse_transition_gaps(
    rows: list[dict[str, float | int]],
    side: int,
    window: int = GAP_WINDOW_BARS,
) -> list[float]:
    """Return completed close-to-next-bar excursions adverse to ``side``."""

    selected = rows[-(window + 1) :]
    gaps: list[float] = []
    for previous, current in zip(selected, selected[1:]):
        previous_close = float(previous["close"])
        adverse = (
            float(current["high"]) - previous_close
            if side > 0
            else previous_close - float(current["low"])
        )
        gaps.append(max(adverse, 0.0))
    return gaps


def causal_gap_buffer(
    rows: list[dict[str, float | int]],
    side: int,
) -> tuple[float, int]:
    gaps = adverse_transition_gaps(rows, side)
    if len(gaps) < GAP_MIN_OBSERVATIONS:
        return (max(gaps, default=0.0), len(gaps))
    return (nearest_rank_quantile(gaps, GAP_QUANTILE), len(gaps))


def _tick_size(instrument: Any) -> float:
    try:
        return float(instrument.price_increment)
    except (TypeError, ValueError):
        return 10.0 ** (-int(instrument.price_precision))


def risk_sized_submit_bracket(
    self: Any,
    setup: PendingSetup,
    row: dict[str, float | int],
    target_net_r: float,
    details: dict[str, Any],
) -> bool:
    """Submit a Nautilus bracket with conservative quantity and frozen target."""

    side = setup.side
    atr = float(self._atr())
    if not math.isfinite(atr) or atr <= 0.0:
        return False
    stop_trigger = setup.extreme - side * self.config.stop_buffer_atr * atr
    signal_entry = float(row["close"])
    cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0

    # Alpha/target contract: exactly the same signal-state prices used by V17.
    signal_price_loss = side * (signal_entry - stop_trigger)
    if not math.isfinite(signal_price_loss) or signal_price_loss <= 0.0:
        return False
    signal_planned_loss = signal_price_loss + cost_rate * (
        signal_entry + stop_trigger
    )
    target = cost_aware_target(
        signal_entry,
        side,
        signal_planned_loss,
        target_net_r,
        cost_rate,
    )
    reference_r_signal: float | None = None
    if setup.target_reference is not None:
        reference_r_signal = net_r_at_price(
            signal_entry,
            setup.target_reference,
            side,
            signal_planned_loss,
            cost_rate,
        )
        if reference_r_signal < self.config.session_min_opposite_target_r:
            return False
        cap_target = cost_aware_target(
            signal_entry,
            side,
            signal_planned_loss,
            self.config.session_max_target_r,
            cost_rate,
        )
        target = (
            min(setup.target_reference, cap_target)
            if side > 0
            else max(setup.target_reference, cap_target)
        )

    # Risk contract: expected later fills determine quantity only.
    excursion_buffer, gap_observations = causal_gap_buffer(list(self.bars), side)
    tick = _tick_size(self.instrument)
    expected_entry_fill = signal_entry + side * excursion_buffer
    expected_stop_fill = stop_trigger - side * (excursion_buffer + tick)
    execution_price_loss = side * (expected_entry_fill - expected_stop_fill)
    if not math.isfinite(execution_price_loss) or execution_price_loss <= 0.0:
        return False
    execution_planned_loss = execution_price_loss + cost_rate * (
        expected_entry_fill + expected_stop_fill
    )

    equity = float(self._equity_value())
    risk_budget = equity * self.config.risk_fraction
    raw_qty = risk_budget / execution_planned_loss
    quantity_value = floor_quantity(raw_qty, int(self.instrument.size_precision))
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
    "GAP_MIN_OBSERVATIONS",
    "GAP_QUANTILE",
    "GAP_WINDOW_BARS",
    "adverse_transition_gaps",
    "causal_gap_buffer",
    "nearest_rank_quantile",
    "risk_sized_submit_bracket",
]

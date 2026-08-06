#!/usr/bin/env python3
"""Causal entry/stop fill estimates for candidate-04 NautilusTrader orders.

The signal bar close is not assumed to equal the later market-order fill. With
minute OHLC data, a pure close-to-next-open gap understates execution uncertainty
because the matching engine can encounter the next bar path before the order is
filled. The planned loss therefore includes a direction-specific 95th percentile
of completed close-to-next-bar adverse excursions and one exchange tick beyond
the stop trigger.

This is the project's expected entry/stop fill calculation before order
submission. It is not a leverage cap, score multiplier, or separate execution
simulator.
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
    """Return a deterministic nearest-rank quantile for finite values."""

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
    """Return past close-to-next-bar excursions adverse to ``side``.

    For a long order the next bar high is the conservative adverse executable
    path from the previous close. For a short order the corresponding path is
    the next bar low. Every observation is fully completed before calibration.
    """

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
    """Submit a Nautilus bracket using causal adverse fill estimates."""

    side = setup.side
    atr = float(self._atr())
    if not math.isfinite(atr) or atr <= 0.0:
        return False
    stop_trigger = setup.extreme - side * self.config.stop_buffer_atr * atr
    signal_entry = float(row["close"])
    excursion_buffer, gap_observations = causal_gap_buffer(list(self.bars), side)
    tick = _tick_size(self.instrument)

    expected_entry_fill = signal_entry + side * excursion_buffer
    expected_stop_fill = stop_trigger - side * (excursion_buffer + tick)
    price_loss = side * (expected_entry_fill - expected_stop_fill)
    if not math.isfinite(price_loss) or price_loss <= 0.0:
        return False

    cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
    planned_loss = price_loss + cost_rate * (
        expected_entry_fill + expected_stop_fill
    )
    equity = float(self._equity_value())
    risk_budget = equity * self.config.risk_fraction
    raw_qty = risk_budget / planned_loss
    quantity_value = floor_quantity(raw_qty, int(self.instrument.size_precision))
    if quantity_value <= 0.0 or quantity_value * expected_entry_fill < 10.0:
        return False

    target = cost_aware_target(
        expected_entry_fill,
        side,
        planned_loss,
        target_net_r,
        cost_rate,
    )
    if setup.target_reference is not None:
        reference_r = net_r_at_price(
            expected_entry_fill,
            setup.target_reference,
            side,
            planned_loss,
            cost_rate,
        )
        if reference_r < self.config.session_min_opposite_target_r:
            self._event(
                "INSUFFICIENT_OPPOSING_LIQUIDITY_AFTER_EXCURSION",
                setup.scenario,
                row,
                {
                    **details,
                    "reference_net_r_after_excursion": reference_r,
                    "adverse_excursion_buffer": excursion_buffer,
                },
            )
            return False
        cap_target = cost_aware_target(
            expected_entry_fill,
            side,
            planned_loss,
            self.config.session_max_target_r,
            cost_rate,
        )
        target = (
            min(setup.target_reference, cap_target)
            if side > 0
            else max(setup.target_reference, cap_target)
        )

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
            "expected_entry_fill": expected_entry_fill,
            "stop_trigger": stop_trigger,
            "expected_stop_fill": expected_stop_fill,
            "target": target,
            "quantity": quantity_value,
            "equity": equity,
            "risk_budget": risk_budget,
            "planned_loss_per_unit": planned_loss,
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

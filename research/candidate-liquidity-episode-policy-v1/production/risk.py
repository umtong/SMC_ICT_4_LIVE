from __future__ import annotations

import math
from typing import Mapping

from .contracts import EpisodePlan, QuantityDecision


CONTRACT_STEPS: dict[str, float] = {
    "BTCUSDT": 0.001,
    "ETHUSDT": 0.001,
    "SOLUSDT": 0.01,
    "XRPUSDT": 1.0,
}


def floor_to_step(value: float, step: float) -> float:
    if step <= 0.0:
        raise ValueError("step must be positive")
    return math.floor(max(0.0, value) / step + 1e-12) * step


def size_for_plan(
    plan: EpisodePlan,
    *,
    equity: float,
    risk_fraction: float,
    maximum_leverage: float,
    minimum_notional: float,
    quantity_steps: Mapping[str, float] | None = None,
) -> QuantityDecision:
    if equity <= 0.0:
        raise ValueError("equity must be positive")
    stop_distance = abs(plan.entry - plan.stop)
    if stop_distance <= 0.0:
        raise ValueError("stop distance must be positive")
    cash_risk = equity * risk_fraction
    raw_quantity = cash_risk / stop_distance
    leverage_cap_quantity = equity * maximum_leverage / plan.entry
    capped = min(raw_quantity, leverage_cap_quantity)
    step = float((quantity_steps or CONTRACT_STEPS).get(plan.symbol, 1e-8))
    quantity = floor_to_step(capped, step)
    notional = quantity * plan.entry
    if notional < minimum_notional:
        quantity = 0.0
        notional = 0.0
    leverage = notional / equity if equity else 0.0
    return QuantityDecision(
        symbol=plan.symbol,
        equity=float(equity),
        risk_fraction=float(risk_fraction),
        cash_risk=float(cash_risk),
        raw_quantity=float(raw_quantity),
        capped_quantity=float(quantity),
        notional=float(notional),
        effective_leverage=float(leverage),
    )

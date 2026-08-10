"""Runtime correction for the causal NOTank adapter's DI calculation.

The public signal uses PLUS_DI - MINUS_DI.  ``router_picasso`` exposes the
Wilder RMA primitive but not standalone DI helpers, so this module supplies the
same causal calculation and patches the adapter before any classification.
"""
from __future__ import annotations

import math
from typing import Sequence

import router_notank_causal as _base

BarObservation = _base.BarObservation


def _finite(value: float) -> bool:
    return math.isfinite(float(value))


def _di_values(candles: Sequence[BarObservation], period: int) -> list[float]:
    size = len(candles)
    result = [math.nan] * size
    if period <= 0 or size <= period:
        return result
    tr = [0.0] * size
    plus_dm = [0.0] * size
    minus_dm = [0.0] * size
    for index in range(1, size):
        current, previous = candles[index], candles[index - 1]
        up = float(current.high) - float(previous.high)
        down = float(previous.low) - float(current.low)
        plus_dm[index] = up if up > down and up > 0.0 else 0.0
        minus_dm[index] = down if down > up and down > 0.0 else 0.0
        tr[index] = max(
            float(current.high) - float(current.low),
            abs(float(current.high) - float(previous.close)),
            abs(float(current.low) - float(previous.close)),
        )
    atr = _base._ta._rma(tr[1:], period)
    plus = _base._ta._rma(plus_dm[1:], period)
    minus = _base._ta._rma(minus_dm[1:], period)
    for offset, values in enumerate(zip(atr, plus, minus)):
        target = offset + 1
        a, p, m = values
        if not all(_finite(value) for value in (a, p, m)) or float(a) <= 1e-12:
            continue
        result[target] = 100.0 * (float(p) - float(m)) / float(a)
    return result


_base._di_values = _di_values

FeatureObservation = _base.FeatureObservation
NOTANK_STATE = _base.NOTANK_STATE
RouteConfig = _base.RouteConfig
RouteDecision = _base.RouteDecision
UNRESOLVED = _base.UNRESOLVED
classify_symbol = _base.classify_symbol
inspect_state = _base.inspect_state
route_universe = _base.route_universe

__all__ = [
    "BarObservation",
    "FeatureObservation",
    "NOTANK_STATE",
    "RouteConfig",
    "RouteDecision",
    "UNRESOLVED",
    "classify_symbol",
    "inspect_state",
    "route_universe",
]

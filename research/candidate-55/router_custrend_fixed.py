"""Exact shifted-volume correction for the public CusTrend adapter.

The first materialized adapter accidentally included the current source candle
in its rolling volume mean.  Freqtrade uses ``rolling(period).mean().shift(1)``.
This wrapper replaces that helper in the loaded module before exposing the
classifier, keeping every other source rule unchanged.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
from typing import Sequence

_BASE_PATH = Path(__file__).resolve().with_name("router_custrend.py")
_SPEC = importlib.util.spec_from_file_location("candidate55_custrend_exact_base", _BASE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load CusTrend base adapter: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)


def _rolling_shifted(values: Sequence[float], period: int) -> list[float]:
    """Causal ``rolling(period).mean().shift(1)`` without current-bar leakage."""
    output = [math.nan] * len(values)
    if period <= 0:
        return output
    running = 0.0
    for index, value in enumerate(values):
        if index >= period:
            output[index] = running / period
            running -= float(values[index - period])
        running += float(value)
    return output


# ``classify_symbol`` resolves this helper through the base module's globals.
_BASE._rolling_shifted = _rolling_shifted

BarObservation = _BASE.BarObservation
CUSTREND_STATE = _BASE.CUSTREND_STATE
FeatureObservation = _BASE.FeatureObservation
MBE2_STATE = _BASE.MBE2_STATE
PICASSO_STATE = _BASE.PICASSO_STATE
RouteConfig = _BASE.RouteConfig
RouteDecision = _BASE.RouteDecision
SMA_OFFSET_STATE = _BASE.SMA_OFFSET_STATE
UNRESOLVED = _BASE.UNRESOLVED
_aggregate_complete = _BASE._aggregate_complete
_adx_dx = _BASE._adx_dx
_decode_mode = _BASE._decode_mode
_directional_indicators = _BASE._directional_indicators
_parabolic_sar = _BASE._parabolic_sar
_talib_ema = _BASE._talib_ema
_trend_flag = _BASE._trend_flag
classify_symbol = _BASE.classify_symbol
classify_sma_offset = _BASE.classify_sma_offset
route_universe = _BASE.route_universe

__all__ = [
    "BarObservation",
    "CUSTREND_STATE",
    "FeatureObservation",
    "MBE2_STATE",
    "PICASSO_STATE",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "_aggregate_complete",
    "_adx_dx",
    "_decode_mode",
    "_directional_indicators",
    "_parabolic_sar",
    "_rolling_shifted",
    "_talib_ema",
    "_trend_flag",
    "classify_symbol",
    "route_universe",
]

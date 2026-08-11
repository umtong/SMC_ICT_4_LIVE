"""Frozen N-to-1 facade: 4h jump reversal + public ichiV2 short continuation."""
from __future__ import annotations

import router_ichi_impl as ichi
import router_jump_impl as jump

BarObservation = ichi.BarObservation
FeatureObservation = ichi.FeatureObservation
RouteConfig = ichi.RouteConfig
RouteDecision = ichi.RouteDecision
JumpRouteConfig = jump.RouteConfig
JumpRouteDecision = jump.RouteDecision
UNRESOLVED = ichi.UNRESOLVED
ICHI_STATE = ichi.ICHI_STATE
JUMP_REVERSION_STATE = jump.JUMP_REVERSION_STATE
PICASSO_STATE = ICHI_STATE
SMA_OFFSET_STATE = ICHI_STATE
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}

# Required by the reused finite-history ichi strategy and Picasso shell.
_aggregate_complete = ichi._aggregate_complete
_signal_at = ichi._signal_at
_source_arrays = ichi._source_arrays
try:
    from router_picasso import _adx, _atr, _ema, _finite, _sma  # noqa: F401
except ImportError:  # pragma: no cover
    pass


def ichi_route_universe(*args, **kwargs):
    return ichi.route_universe(*args, **kwargs)


def jump_route_universe(*args, **kwargs):
    return jump.route_universe(*args, **kwargs)


def route_universe(*args, **kwargs):
    """Default facade route remains ichi for inherited source management."""
    return ichi.route_universe(*args, **kwargs)


def route_symbol(*args, **kwargs):
    return ichi.route_symbol(*args, **kwargs)


__all__ = [
    "BarObservation",
    "FeatureObservation",
    "RouteConfig",
    "RouteDecision",
    "JumpRouteConfig",
    "JumpRouteDecision",
    "UNRESOLVED",
    "ICHI_STATE",
    "JUMP_REVERSION_STATE",
    "PICASSO_STATE",
    "SMA_OFFSET_STATE",
    "_aggregate_complete",
    "_signal_at",
    "_source_arrays",
    "ichi_route_universe",
    "jump_route_universe",
    "route_universe",
    "route_symbol",
]

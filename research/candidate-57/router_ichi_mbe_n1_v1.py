"""Frozen N-to-1 router facade: ichiV2 short trend + MBE2 collision reversal."""
from __future__ import annotations

import router_ichi_impl as ichi
import router_mbe_impl as mbe

BarObservation = ichi.BarObservation
FeatureObservation = ichi.FeatureObservation
RouteConfig = ichi.RouteConfig
RouteDecision = ichi.RouteDecision
UNRESOLVED = ichi.UNRESOLVED
ICHI_STATE = ichi.ICHI_STATE
MBE_STATE = mbe.MBE_STATE
PICASSO_STATE = ICHI_STATE
SMA_OFFSET_STATE = ICHI_STATE
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}

# Required by the reused ichi strategy and Picasso shell.
_aggregate_complete = ichi._aggregate_complete
_signal_at = ichi._signal_at
_source_arrays = ichi._source_arrays
try:
    from router_picasso import _adx, _atr, _ema, _finite, _sma  # noqa: F401
except ImportError:  # pragma: no cover
    pass


def ichi_route_universe(*args, **kwargs):
    return ichi.route_universe(*args, **kwargs)


def mbe_route_universe(*args, **kwargs):
    return mbe.route_universe(*args, **kwargs)


def route_universe(*args, **kwargs):
    """Default route remains ichi for inherited source helpers."""
    return ichi.route_universe(*args, **kwargs)


def route_symbol(*args, **kwargs):
    return ichi.route_symbol(*args, **kwargs)


__all__ = [
    "BarObservation", "FeatureObservation", "RouteConfig", "RouteDecision",
    "UNRESOLVED", "ICHI_STATE", "MBE_STATE", "PICASSO_STATE",
    "SMA_OFFSET_STATE", "_aggregate_complete", "_signal_at", "_source_arrays",
    "ichi_route_universe", "mbe_route_universe", "route_universe", "route_symbol",
]

"""Balanced plateau profile for the V15 structural short repair.

This is not a second strategy family.  It tests a neighbouring, economically
identical plateau on untouched short windows before any medium-window choice:

* DI: the local 15-minute leg may be flat or only shallowly negative (>= -20
  bps) while the four-hour auction remains negative;
* BB: the synchronized downside impulse begins at 1.2 current five-minute ATR
  rather than the core profile's 1.5 ATR, still requiring 3/4 negative peers.

Both relationships were positive throughout the three development intervals;
the purpose of this profile is to learn whether the repair is a broad state or
a narrow threshold artifact, while preserving the source opportunity and
management engines.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_CORE_PATH = Path(__file__).resolve().with_name(
    "router_zaratustra_v15_structure.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_zaratustra_v15_structure_balanced_core",
    _CORE_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load V15 structural core: {_CORE_PATH}")
_CORE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _CORE
_SPEC.loader.exec_module(_CORE)

_CORE.DI_LOCAL_PULLBACK_MIN_RETURN = -0.002
_CORE.DI_BROADER_TREND_MAX_RETURN = 0.0
_CORE.BB_MIN_IMPULSE_ATR = 1.2
_CORE.BB_MIN_NEGATIVE_BREADTH_60M = 3

BarObservation = _CORE.BarObservation
FeatureObservation = _CORE.FeatureObservation
RouteConfig = _CORE.RouteConfig
RouteDecision = _CORE.RouteDecision
UNRESOLVED = _CORE.UNRESOLVED
ZARATUSTRA_STATE = _CORE.ZARATUSTRA_STATE
PICASSO_STATE = _CORE.PICASSO_STATE
SMA_OFFSET_STATE = _CORE.SMA_OFFSET_STATE

DI_LOCAL_PULLBACK_MIN_RETURN = _CORE.DI_LOCAL_PULLBACK_MIN_RETURN
DI_BROADER_TREND_MAX_RETURN = _CORE.DI_BROADER_TREND_MAX_RETURN
BB_MIN_IMPULSE_ATR = _CORE.BB_MIN_IMPULSE_ATR
BB_MIN_NEGATIVE_BREADTH_60M = _CORE.BB_MIN_NEGATIVE_BREADTH_60M

_return_fraction = _CORE._return_fraction
di_pullback_resumption = _CORE.di_pullback_resumption
bb_clean_synchronized_expansion = _CORE.bb_clean_synchronized_expansion
classify_symbol = _CORE.classify_symbol
classify_sma_offset = _CORE.classify_sma_offset
route_universe = _CORE.route_universe

__all__ = [
    "BB_MIN_IMPULSE_ATR",
    "BB_MIN_NEGATIVE_BREADTH_60M",
    "BarObservation",
    "DI_BROADER_TREND_MAX_RETURN",
    "DI_LOCAL_PULLBACK_MIN_RETURN",
    "FeatureObservation",
    "PICASSO_STATE",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "ZARATUSTRA_STATE",
    "_return_fraction",
    "bb_clean_synchronized_expansion",
    "classify_sma_offset",
    "classify_symbol",
    "di_pullback_resumption",
    "route_universe",
]

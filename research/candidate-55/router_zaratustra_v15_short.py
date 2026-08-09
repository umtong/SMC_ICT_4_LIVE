"""Frozen short-side router for the structurally surviving V15 mechanism.

This is not a post-hoc threshold patch.  The two predeclared seven-day windows
showed the same directional decomposition: independent V15 short edges retained
positive expectancy and sufficient opportunity density while long edges caused
the regime failure.  The short family is frozen here and moved immediately to a
new 30-day interval.  No source threshold, stop, trailing rule or score changes.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
from typing import Mapping, Sequence

_BASE_PATH = Path(__file__).resolve().with_name("router_zaratustra_v15.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_zaratustra_v15_short_base", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load V15 router: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

BarObservation = _BASE.BarObservation
FeatureObservation = _BASE.FeatureObservation
RouteConfig = _BASE.RouteConfig
RouteDecision = _BASE.RouteDecision
UNRESOLVED = _BASE.UNRESOLVED
ZARATUSTRA_STATE = "PUBLIC_ZARATUSTRA_V15_INDEPENDENT_SHORT"
PICASSO_STATE = ZARATUSTRA_STATE
SMA_OFFSET_STATE = ZARATUSTRA_STATE
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}

_aggregate_complete = _BASE._aggregate_complete
_directional_indicators = _BASE._directional_indicators
_adx_dx = _BASE._adx_dx
_bollinger = _BASE._bollinger
_atr = _BASE._atr
_obv = _BASE._obv
_mfi = _BASE._mfi
source_entry_flags = _BASE.source_entry_flags


def _reject_long(decision: RouteDecision) -> RouteDecision:
    diagnostics = dict(decision.diagnostics)
    diagnostics.update(
        {
            "candidate55_frozen_family": "V15_EDGE_EXACT_SHORT",
            "long_family_structurally_rejected": 1,
            "short_family_thresholds_changed": 0,
            "selection_basis": (
                "SHORT_POSITIVE_IN_BOTH_2026-06-22_28_AND_2026-07-22_28;"
                "LONG_FAILED_2026-06-22_28"
            ),
        }
    )
    return RouteDecision(
        symbol=decision.symbol,
        state=UNRESOLVED,
        side=0,
        score=0.0,
        entry_reference=math.nan,
        stop_reference=math.nan,
        objective_reference=math.nan,
        episode_ts=int(decision.episode_ts),
        reasons=("V15_LONG_FAMILY_STRUCTURALLY_REJECTED",),
        diagnostics=diagnostics,
    )


def classify_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    decision = _BASE.classify_symbol(symbol, bars, feature, config)
    if not decision.actionable:
        return decision
    if decision.side > 0:
        return _reject_long(decision)
    diagnostics = dict(decision.diagnostics)
    diagnostics.update(
        {
            "candidate55_frozen_family": "V15_EDGE_EXACT_SHORT",
            "long_family_structurally_rejected": 1,
            "short_family_thresholds_changed": 0,
            "fresh_validation_required": 1,
        }
    )
    return RouteDecision(
        symbol=decision.symbol,
        state=ZARATUSTRA_STATE,
        side=-1,
        score=float(decision.score),
        entry_reference=float(decision.entry_reference),
        stop_reference=float(decision.stop_reference),
        objective_reference=float(decision.objective_reference),
        episode_ts=int(decision.episode_ts),
        reasons=tuple(decision.reasons) + ("FROZEN_SHORT_FAMILY",),
        diagnostics=diagnostics,
    )


classify_sma_offset = classify_symbol


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    decisions = {
        symbol: classify_symbol(
            symbol,
            bars,
            features_by_symbol.get(
                symbol,
                FeatureObservation(bars[-1].ts_event if bars else 0, ready=True),
            ),
            config,
        )
        for symbol, bars in bars_by_symbol.items()
    }
    actionable = [decision for decision in decisions.values() if decision.actionable]
    actionable.sort(
        key=lambda item: (
            -float(item.score),
            _SYMBOL_PRIORITY.get(item.symbol, 99),
            int(item.episode_ts),
        )
    )
    return (actionable[0] if actionable else None), decisions


__all__ = [
    "BarObservation",
    "FeatureObservation",
    "PICASSO_STATE",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "ZARATUSTRA_STATE",
    "_adx_dx",
    "_aggregate_complete",
    "_atr",
    "_bollinger",
    "_directional_indicators",
    "_mfi",
    "_obv",
    "classify_symbol",
    "route_universe",
    "source_entry_flags",
]

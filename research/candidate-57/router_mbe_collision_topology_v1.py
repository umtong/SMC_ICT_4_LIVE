"""Frozen MBE2 collision-topology partition.

The public MBE2 per-symbol signal is unchanged.  This adapter exposes three
predeclared cross-asset event states through ``C57_MBE_TOPOLOGY_MODE``:

- ``ge2_control``: the existing at-least-two actionable-symbol policy;
- ``exact2``: exactly two actionable symbols, interpreted as local/partial
  exhaustion;
- ``ge3plus``: at least three actionable symbols, interpreted as market-wide
  synchronized momentum.

No price, indicator, stop, ROI, symbol, time or outcome threshold is changed.
"""
from __future__ import annotations

from dataclasses import replace
import math
import os
from typing import Mapping, Sequence

import router_mbe_base as base

BarObservation = base.BarObservation
FeatureObservation = base.FeatureObservation
RouteConfig = base.RouteConfig
RouteDecision = base.RouteDecision
MBE_STATE = base.MBE_STATE
PICASSO_STATE = MBE_STATE
SMA_OFFSET_STATE = MBE_STATE
UNRESOLVED = base.UNRESOLVED

_ALLOWED = {"ge2_control", "exact2", "ge3plus"}


def topology_mode() -> str:
    mode = os.environ.get("C57_MBE_TOPOLOGY_MODE", "ge2_control").strip().lower()
    if mode not in _ALLOWED:
        raise ValueError(f"unsupported frozen MBE topology mode: {mode!r}")
    return mode


def _keep(raw_count: int, mode: str) -> bool:
    if mode == "ge2_control":
        return True
    if mode == "exact2":
        return raw_count == 2
    return raw_count >= 3


def _rejected(
    decision: RouteDecision,
    *,
    mode: str,
    raw_count: int,
) -> RouteDecision:
    diagnostics = dict(decision.diagnostics or {})
    diagnostics.update(
        {
            "mbe_topology_mode": mode,
            "mbe_raw_actionable_symbols": raw_count,
            "mbe_topology_actionable": 0,
            "mbe_source_signal_changed": 0,
            "mbe_outcome_filter_used": 0,
        }
    )
    reason = (
        "MBE_EXACT2_REJECTED_NONPAIR_TOPOLOGY"
        if mode == "exact2"
        else "MBE_GE3PLUS_REJECTED_NONMARKETWIDE_TOPOLOGY"
    )
    return replace(
        decision,
        state=UNRESOLVED,
        side=0,
        score=0.0,
        entry_reference=math.nan,
        stop_reference=math.nan,
        objective_reference=math.nan,
        reasons=(reason,),
        diagnostics=diagnostics,
    )


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    _, raw = base.route_universe(
        bars_by_symbol=bars_by_symbol,
        features_by_symbol=features_by_symbol,
        config=config,
    )
    mode = topology_mode()
    actionable = [decision for decision in raw.values() if decision.actionable]
    raw_count = len(actionable)
    keep = _keep(raw_count, mode)
    decisions: dict[str, RouteDecision] = {}
    for symbol, decision in raw.items():
        if decision.actionable and not keep:
            decisions[symbol] = _rejected(
                decision,
                mode=mode,
                raw_count=raw_count,
            )
            continue
        diagnostics = dict(decision.diagnostics or {})
        diagnostics.update(
            {
                "mbe_topology_mode": mode,
                "mbe_raw_actionable_symbols": raw_count,
                "mbe_topology_actionable": int(decision.actionable and keep),
                "mbe_source_signal_changed": 0,
                "mbe_outcome_filter_used": 0,
            }
        )
        decisions[symbol] = replace(decision, diagnostics=diagnostics)

    filtered = [decision for decision in decisions.values() if decision.actionable]
    priority = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}
    filtered.sort(
        key=lambda item: (
            -float(item.score),
            priority.get(item.symbol, 99),
            int(item.episode_ts),
        )
    )
    return (filtered[0] if filtered else None), decisions


route_symbol = base.classify_symbol
classify_symbol = base.classify_symbol
mbe_source_flags = base.mbe_source_flags
source_signals_for_bars = base.source_signals_for_bars


__all__ = [
    "BarObservation",
    "FeatureObservation",
    "MBE_STATE",
    "PICASSO_STATE",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "classify_symbol",
    "mbe_source_flags",
    "route_symbol",
    "route_universe",
    "source_signals_for_bars",
    "topology_mode",
]

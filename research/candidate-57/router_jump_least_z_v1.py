"""Frozen least-qualifying-z arbitration for the 4h jump-reversion source.

The source classifier, qualifying threshold, structural stop and objective are
unchanged.  When several symbols qualify on the same completed 4h boundary,
the already-qualified candidate with the smallest absolute causal z-score is
selected.  No taker, OI, outcome, symbol or period filter is consulted.
"""
from __future__ import annotations

from dataclasses import replace
import json
from typing import Mapping, Sequence

from router_jump_base import (
    BarObservation,
    FeatureObservation,
    JUMP_REVERSION_STATE,
    RouteConfig,
    RouteDecision,
    SMA_OFFSET_STATE,
    UNRESOLVED,
    _SYMBOL_PRIORITY,
    route_universe as _base_route_universe,
)


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    source_config = replace(config, jump_selection_mode="source")
    _, raw = _base_route_universe(
        bars_by_symbol=bars_by_symbol,
        features_by_symbol=features_by_symbol,
        config=source_config,
    )
    snapshot = []
    for decision in raw.values():
        if not decision.actionable:
            continue
        diagnostics = decision.diagnostics or {}
        snapshot.append(
            {
                "symbol": decision.symbol,
                "side": int(decision.side),
                "absolute_z": abs(float(diagnostics.get("causal_zscore", 0.0))),
                "source_score": float(decision.score),
                "absolute_return": float(diagnostics.get("absolute_return", 0.0)),
                "stop_fraction": float(diagnostics.get("stop_fraction", 0.0)),
            }
        )
    snapshot.sort(key=lambda row: _SYMBOL_PRIORITY.get(str(row["symbol"]), 99))
    snapshot_json = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))

    decisions: dict[str, RouteDecision] = {}
    for symbol, decision in raw.items():
        if not decision.actionable:
            decisions[symbol] = decision
            continue
        diagnostics = dict(decision.diagnostics or {})
        absolute_z = abs(float(diagnostics.get("causal_zscore", 0.0)))
        diagnostics.update(
            {
                "jump_arbitration_mode": "least_qualifying_z",
                "jump_boundary_candidate_count": len(snapshot),
                "jump_boundary_candidate_set_json": snapshot_json,
                "jump_source_score": float(decision.score),
                "jump_absolute_z": absolute_z,
                "jump_effective_arbitration_score": -absolute_z,
                "jump_taker_filter_used": 0,
                "jump_outcome_filter_used": 0,
            }
        )
        decisions[symbol] = replace(
            decision,
            score=-absolute_z,
            diagnostics=diagnostics,
        )

    actionable = [decision for decision in decisions.values() if decision.actionable]
    actionable.sort(
        key=lambda item: (
            -float(item.score),
            _SYMBOL_PRIORITY.get(item.symbol, 99),
            int(item.episode_ts),
        )
    )
    if not actionable:
        return None, decisions
    selected = actionable[0]
    diagnostics = dict(selected.diagnostics or {})
    diagnostics["jump_selected_by_least_qualifying_z"] = 1
    selected = replace(selected, diagnostics=diagnostics)
    decisions[selected.symbol] = selected
    return selected, decisions


__all__ = [
    "BarObservation",
    "FeatureObservation",
    "JUMP_REVERSION_STATE",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "route_universe",
]

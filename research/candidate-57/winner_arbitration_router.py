"""Frozen Winner15m one-slot arbitration comparison.

The signal and source startup semantics come from the source-fidelity router.
This adapter changes exactly two things for the arbitration experiment:

* continuous source-condition re-entries are collapsed to one independent
  false->true causal episode;
* simultaneous candidates are selected either by the previous maximum-climax
  score or by the frozen least-volume-excess policy.

The second policy is not claimed to be optimal.  It is the one causal repair
pre-registered from the development forensic: once trend/momentum/participation
thresholds are satisfied, the global one-slot account should prefer the member
with more remaining auction space instead of the most climactic volume burst.
"""
from __future__ import annotations

import os
from typing import Mapping, Sequence

from router_source_fidelity import (
    EDGE_MR_STATE,
    SMA_OFFSET_STATE,
    UNRESOLVED,
    WINNER_STATE,
    BarObservation,
    FeatureObservation,
    RouteConfig,
    RouteDecision,
    _SYMBOL_PRIORITY,
    _classify_winner_source_true,
    _unresolved,
)


def classify_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    decision = _classify_winner_source_true(symbol, bars, config)
    if not decision.actionable:
        return decision
    diagnostics = decision.diagnostics or {}
    if int(diagnostics.get("persistent_source_condition", 0)):
        return _unresolved(
            symbol,
            "WINNER_PERSISTENT_SOURCE_CONDITION_COLLAPSED",
            decision.episode_ts,
            diagnostics,
        )
    diagnostics["independent_transition_only"] = 1
    return decision


classify_sma_offset = classify_symbol


def _mode() -> str:
    mode = os.environ.get("C57_ARBITRATION_MODE", "current_max_climax").strip().lower()
    if mode not in {"current_max_climax", "least_volume_excess"}:
        raise ValueError(f"unsupported C57_ARBITRATION_MODE={mode!r}")
    return mode


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
                FeatureObservation(
                    bars[-1].ts_event if bars else 0,
                    ready=True,
                ),
            ),
            config,
        )
        for symbol, bars in bars_by_symbol.items()
    }
    actionable = [decision for decision in decisions.values() if decision.actionable]
    mode = _mode()
    if mode == "current_max_climax":
        actionable.sort(
            key=lambda item: (
                -float(item.score),
                _SYMBOL_PRIORITY.get(item.symbol, 99),
                item.state,
            )
        )
    else:
        actionable.sort(
            key=lambda item: (
                float((item.diagnostics or {}).get("volume_ratio", float("inf"))),
                _SYMBOL_PRIORITY.get(item.symbol, 99),
                item.state,
            )
        )
    if actionable:
        selected = actionable[0]
        (selected.diagnostics or {})["arbitration_mode"] = mode
        (selected.diagnostics or {})["simultaneous_actionable_candidates"] = len(actionable)
        return selected, decisions
    return None, decisions


def sma_offset_exit_ready(
    bars: Sequence[BarObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[bool, dict[str, float | int | str]]:
    del bars, config
    return False, {"reason": "SOURCE_MANAGEMENT_OWNED_BY_STRATEGY"}


__all__ = [
    "BarObservation",
    "FeatureObservation",
    "RouteConfig",
    "RouteDecision",
    "WINNER_STATE",
    "EDGE_MR_STATE",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "classify_symbol",
    "classify_sma_offset",
    "route_universe",
    "sma_offset_exit_ready",
]

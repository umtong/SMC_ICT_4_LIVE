"""Frozen 4h jump arbitration contrast for Candidate 57.

The source router identifies every >=2-sigma completed 4h jump and computes the
same structural geometry.  This wrapper changes only the cross-symbol score at
simultaneous boundaries:

* ``source_max_z``: preserve the public/source highest absolute z-score;
* ``least_qualifying_z``: among already-qualified candidates, prefer the least
  absolute z-score.

The latter is a single pre-registered hypothesis from the all-candidate
forensic.  It is not a threshold search and does not remove any market-event
boundary.  The strategy consumes the selected object returned by this router,
so no downstream second sort is allowed.
"""
from __future__ import annotations

from dataclasses import replace
import json
import os
from typing import Mapping, Sequence

from router_jump_base import (
    FeatureObservation,
    JUMP_REVERSION_STATE,
    RouteConfig,
    RouteDecision,
    SMA_OFFSET_STATE,
    UNRESOLVED,
    BarObservation,
    _SYMBOL_PRIORITY,
    classify_symbol,
    route_universe as _base_route_universe,
)


def arbitration_mode() -> str:
    mode = os.environ.get(
        "C57_JUMP_ARBITRATION_MODE", "source_max_z"
    ).strip().lower()
    if mode not in {"source_max_z", "least_qualifying_z"}:
        raise ValueError(f"unsupported C57_JUMP_ARBITRATION_MODE={mode!r}")
    return mode


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    # Base source mode performs the exact causal source classification and
    # cross-sectional diagnostic enrichment.  Its returned source winner is
    # deliberately ignored before applying the one frozen score contrast.
    source_config = replace(config, jump_selection_mode="source")
    _, raw = _base_route_universe(
        bars_by_symbol=bars_by_symbol,
        features_by_symbol=features_by_symbol,
        config=source_config,
    )
    mode = arbitration_mode()
    actionable_raw = [decision for decision in raw.values() if decision.actionable]
    snapshot = []
    for decision in actionable_raw:
        diagnostics = decision.diagnostics or {}
        snapshot.append(
            {
                "symbol": decision.symbol,
                "side": int(decision.side),
                "absolute_z": abs(float(diagnostics.get("causal_zscore", 0.0))),
                "source_score": float(decision.score),
                "absolute_return": float(diagnostics.get("absolute_return", 0.0)),
                "residual_z": float(
                    diagnostics.get("cross_sectional_residual_z", 0.0)
                ),
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
        effective_score = (
            float(decision.score)
            if mode == "source_max_z"
            else -absolute_z
        )
        diagnostics.update(
            {
                "jump_effective_arbitration_mode": mode,
                "jump_effective_arbitration_score": effective_score,
                "jump_source_score": float(decision.score),
                "jump_absolute_z": absolute_z,
                "jump_boundary_candidate_count": len(actionable_raw),
                "jump_boundary_candidate_set_json": snapshot_json,
            }
        )
        decisions[symbol] = replace(
            decision,
            score=effective_score,
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
    selected_diagnostics = dict(selected.diagnostics or {})
    selected_diagnostics["jump_selected_by_effective_arbitration"] = 1
    selected = replace(selected, diagnostics=selected_diagnostics)
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
    "arbitration_mode",
    "classify_symbol",
    "route_universe",
]

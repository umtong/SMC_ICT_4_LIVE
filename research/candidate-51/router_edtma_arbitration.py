"""Cross-asset arbitration for the public EDTMA opportunity set.

The source entry condition is unchanged.  The original single-slot adapter
resolved simultaneous opportunities by maximizing a score that linearly rewards
ADX margin, trend separation and excess volume.  That is not part of the public
strategy and can select the most mature or climactic member of a correlated
crypto move.  This wrapper isolates arbitration from alpha generation:

* ``source_score`` reproduces the existing adapter exactly;
* ``freshest`` prefers the youngest contiguous source condition;
* ``moderate_volume`` treats volume > prior mean as a qualifier, not a quantity
  to maximize, and prefers the least climactic eligible observation;
* breadth and BTC-anchor constraints are optional market-context policies, not
  extra same-symbol confirmations.

All observations are completed one-hour candles and are available before entry.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Mapping, Sequence

import router_edtma_repair as _base

BarObservation = _base.BarObservation
FeatureObservation = _base.FeatureObservation
RouteDecision = _base.RouteDecision
EDTMA_STATE = _base.EDTMA_STATE
UNRESOLVED = _base.UNRESOLVED
inspect_condition = _base.inspect_condition
classify_symbol = _base.classify_symbol

_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}


@dataclass(frozen=True, slots=True)
class RouteConfig(_base.RouteConfig):
    edtma_arbitration_mode: str = "source_score"
    edtma_min_same_side_breadth: int = 1
    edtma_require_side_majority: bool = False
    edtma_require_btc_anchor: bool = False


def _finite(value: object, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _run_hours(decision: RouteDecision) -> int:
    return max(0, int(_finite(decision.diagnostics.get("condition_run_hours"), 10**9)))


def _volume_ratio(decision: RouteDecision) -> float:
    # Actionable source decisions already satisfy volume_ratio > 1.0.  Infinity
    # sorts last and prevents malformed observations from being preferred.
    return _finite(decision.diagnostics.get("volume_ratio"), math.inf)


def _source_key(decision: RouteDecision) -> tuple[float, int, int]:
    return (
        -float(decision.score),
        _SYMBOL_PRIORITY.get(decision.symbol, 99),
        int(decision.episode_ts),
    )


def _selection_key(decision: RouteDecision, mode: str) -> tuple[float | int, ...]:
    if mode == "source_score":
        return _source_key(decision)
    if mode == "freshest":
        return (
            _run_hours(decision),
            _volume_ratio(decision),
            *_source_key(decision),
        )
    if mode == "moderate_volume":
        return (
            _volume_ratio(decision),
            _run_hours(decision),
            *_source_key(decision),
        )
    raise ValueError(f"unsupported edtma_arbitration_mode={mode!r}")


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    # Delegate the expensive indicator construction and source classification to
    # the already-tested episode-aware adapter, then replace only arbitration.
    _, raw = _base.route_universe(bars_by_symbol, features_by_symbol, config)
    actionable = [decision for decision in raw.values() if decision.actionable]
    long_breadth = sum(int(decision.side) > 0 for decision in actionable)
    short_breadth = sum(int(decision.side) < 0 for decision in actionable)
    btc = raw.get("BTCUSDT")
    btc_side = int(btc.side) if btc is not None and btc.actionable else 0
    counts = {1: long_breadth, -1: short_breadth}

    minimum = max(1, int(config.edtma_min_same_side_breadth))
    require_majority = bool(config.edtma_require_side_majority)
    require_btc = bool(config.edtma_require_btc_anchor)
    mode = str(config.edtma_arbitration_mode).strip().lower()

    annotated: dict[str, RouteDecision] = {}
    eligible: list[RouteDecision] = []
    for symbol, decision in raw.items():
        same = counts.get(int(decision.side), 0) if decision.actionable else 0
        opposite = counts.get(-int(decision.side), 0) if decision.actionable else 0
        passes_breadth = decision.actionable and same >= minimum
        passes_majority = (not require_majority) or same > opposite
        passes_btc = (not require_btc) or (btc_side != 0 and int(decision.side) == btc_side)
        is_eligible = bool(passes_breadth and passes_majority and passes_btc)
        diagnostics = dict(decision.diagnostics)
        diagnostics.update(
            {
                "edtma_arbitration_mode": mode,
                "edtma_long_breadth": long_breadth,
                "edtma_short_breadth": short_breadth,
                "edtma_same_side_breadth": same,
                "edtma_opposite_side_breadth": opposite,
                "edtma_btc_anchor_side": btc_side,
                "edtma_min_same_side_breadth": minimum,
                "edtma_require_side_majority": int(require_majority),
                "edtma_require_btc_anchor": int(require_btc),
                "edtma_arbitration_eligible": int(is_eligible),
            }
        )
        candidate = replace(decision, diagnostics=diagnostics)
        annotated[symbol] = candidate
        if is_eligible:
            eligible.append(candidate)

    eligible.sort(key=lambda decision: _selection_key(decision, mode))
    if not eligible:
        return None, annotated
    winner = eligible[0]
    diagnostics = dict(winner.diagnostics)
    diagnostics.update(
        {
            "edtma_arbitration_candidate_count": len(eligible),
            "edtma_arbitration_selected_run_hours": _run_hours(winner),
            "edtma_arbitration_selected_volume_ratio": _volume_ratio(winner),
        }
    )
    winner = replace(winner, diagnostics=diagnostics)
    annotated[winner.symbol] = winner
    return winner, annotated


__all__ = [
    "BarObservation", "EDTMA_STATE", "FeatureObservation", "RouteConfig",
    "RouteDecision", "UNRESOLVED", "classify_symbol", "inspect_condition",
    "route_universe",
]

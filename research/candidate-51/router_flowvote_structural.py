"""Structural-risk wrapper around the public order-book/delta vote.

The vote itself is unchanged.  The first experiment used a six-minute timeout
and a sub-percent volatility stop, which forced high turnover and fee-heavy
notional.  This wrapper keeps the source's opposite-vote management while
placing invalidation beyond the completed recent balance boundary.  A remote
target prevents an invented fixed-profit exit from replacing the source logic.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Mapping, Sequence

import router_flowvote as _source

BarObservation = _source.BarObservation
FeatureObservation = _source.FeatureObservation
FLOWVOTE_STATE = _source.FLOWVOTE_STATE
SMA_OFFSET_STATE = _source.SMA_OFFSET_STATE
UNRESOLVED = _source.UNRESOLVED
RouteDecision = _source.RouteDecision


@dataclass(frozen=True, slots=True)
class RouteConfig(_source.RouteConfig):
    flowvote_structural_lookback_minutes: int = 30
    flowvote_structural_atr_buffer: float = 0.50
    flowvote_remote_target_r: float = 10.0


def flowvote_scores(**kwargs):
    return _source.flowvote_scores(**kwargs)


def classify_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    decision = _source.classify_symbol(symbol, bars, feature, config)
    if not decision.actionable:
        return decision
    lookback = max(2, int(config.flowvote_structural_lookback_minutes))
    if len(bars) < max(lookback, int(config.flowvote_atr_period) + 2):
        return replace(
            decision,
            state=UNRESOLVED,
            side=0,
            score=0.0,
            entry_reference=math.nan,
            stop_reference=math.nan,
            objective_reference=math.nan,
            reasons=("FLOWVOTE_STRUCTURAL_HISTORY_NOT_READY",),
        )
    atr = _source._atr(bars, int(config.flowvote_atr_period))[-1]
    if not math.isfinite(float(atr)) or float(atr) <= 0.0:
        return replace(
            decision,
            state=UNRESOLVED,
            side=0,
            score=0.0,
            entry_reference=math.nan,
            stop_reference=math.nan,
            objective_reference=math.nan,
            reasons=("FLOWVOTE_STRUCTURAL_ATR_NOT_READY",),
        )
    entry = float(bars[-1].close)
    buffer = float(config.flowvote_structural_atr_buffer) * float(atr)
    sample = bars[-lookback:]
    if decision.side > 0:
        structural_boundary = min(float(bar.low) for bar in sample)
        stop = structural_boundary - buffer
    else:
        structural_boundary = max(float(bar.high) for bar in sample)
        stop = structural_boundary + buffer
    risk = abs(entry - stop)
    objective = entry + int(decision.side) * float(config.flowvote_remote_target_r) * risk
    valid = (
        0.0 < stop < entry < objective
        if decision.side > 0
        else 0.0 < objective < entry < stop
    )
    if not valid:
        return replace(
            decision,
            state=UNRESOLVED,
            side=0,
            score=0.0,
            entry_reference=math.nan,
            stop_reference=math.nan,
            objective_reference=math.nan,
            reasons=("FLOWVOTE_STRUCTURAL_GEOMETRY_INVALID",),
        )
    diagnostics = dict(decision.diagnostics)
    diagnostics.update(
        {
            "risk_geometry": "recent-balance-boundary-plus-atr-buffer",
            "structural_lookback_minutes": lookback,
            "structural_boundary": structural_boundary,
            "structural_atr": float(atr),
            "structural_atr_buffer": buffer,
            "structural_stop_fraction": risk / entry,
            "remote_target_r": float(config.flowvote_remote_target_r),
            "management_policy": "opposite-strong-vote-or-timeout",
        }
    )
    return replace(
        decision,
        stop_reference=stop,
        objective_reference=objective,
        diagnostics=diagnostics,
        reasons=tuple(decision.reasons) + (
            "RECENT_BALANCE_BOUNDARY_INVALIDATION",
            "REMOTE_TARGET_PRESERVES_SOURCE_OPPOSITE_VOTE_EXIT",
        ),
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
                FeatureObservation(bars[-1].ts_event if bars else 0),
            ),
            config,
        )
        for symbol, bars in bars_by_symbol.items()
    }
    priority = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}
    actionable = [decision for decision in decisions.values() if decision.actionable]
    actionable.sort(
        key=lambda item: (
            -float(item.score),
            priority.get(item.symbol, 99),
            int(item.episode_ts),
        )
    )
    return (actionable[0] if actionable else None), decisions


__all__ = [
    "BarObservation",
    "FeatureObservation",
    "FLOWVOTE_STATE",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "classify_symbol",
    "flowvote_scores",
    "route_universe",
]

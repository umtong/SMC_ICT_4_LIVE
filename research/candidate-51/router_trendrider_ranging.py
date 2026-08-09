"""Latent-state router for the public TrendRider v2.11 long policy.

The source already computes a market regime.  Development attribution showed
that valid-fill expectancy was positive only in exact ``RANGING`` state and
strongly negative in bull, bear, and high-volatility states.  This wrapper does
not change a single public entry branch, confidence score, stop, target, ROI,
trailing rule, or cascading exit.  It assigns the public long policy only to
the latent state in which its auction logic behaved as mean-reverting.

The 2025-03-01..2025-05-29 period is development data for this routing choice.
Only later untouched accounts may provide evidence for the wrapper.
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence

import router_trendrider_cached as _base

BarObservation = _base.BarObservation
EntryEvaluation = _base.EntryEvaluation
FeatureObservation = _base.FeatureObservation
RouteConfig = _base.RouteConfig
RouteDecision = _base.RouteDecision
SMA_OFFSET_STATE = _base.SMA_OFFSET_STATE
TRENDRIDER_STATE = _base.TRENDRIDER_STATE
TrendSnapshot = _base.TrendSnapshot
UNRESOLVED = _base.UNRESOLVED
classify_symbol = _base.classify_symbol
evaluate_entry_aggregated = _base.evaluate_entry_aggregated
trendrider_exit_signal = _base.trendrider_exit_signal

ALLOWED_REGIMES = frozenset({"RANGING"})
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}


def _reject_regime(decision: RouteDecision) -> RouteDecision:
    regime = str(decision.diagnostics.get("regime", ""))
    diagnostics = dict(decision.diagnostics)
    diagnostics.update(
        {
            "source_decision_state": decision.state,
            "source_decision_side": decision.side,
            "allowed_regimes": "|".join(sorted(ALLOWED_REGIMES)),
            "regime_router_rejected": regime not in ALLOWED_REGIMES,
        }
    )
    if not decision.actionable or regime in ALLOWED_REGIMES:
        return RouteDecision(
            symbol=decision.symbol,
            state=decision.state,
            side=decision.side,
            score=decision.score,
            entry_reference=decision.entry_reference,
            stop_reference=decision.stop_reference,
            objective_reference=decision.objective_reference,
            episode_ts=decision.episode_ts,
            reasons=decision.reasons,
            diagnostics=diagnostics,
        )
    return RouteDecision(
        symbol=decision.symbol,
        state=UNRESOLVED,
        side=0,
        score=0.0,
        entry_reference=math.nan,
        stop_reference=math.nan,
        objective_reference=math.nan,
        episode_ts=decision.episode_ts,
        reasons=("TRENDRIDER_REGIME_ROUTER_REJECTED",),
        diagnostics=diagnostics,
    )


def route_universe_aggregated(
    hours_by_symbol: Mapping[str, Sequence[BarObservation]],
    four_hours_by_symbol: Mapping[str, Sequence[BarObservation]],
    days_by_symbol: Mapping[str, Sequence[BarObservation]],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    _, source = _base.route_universe_aggregated(
        hours_by_symbol,
        four_hours_by_symbol,
        days_by_symbol,
        config,
    )
    decisions = {
        symbol: _reject_regime(decision)
        for symbol, decision in source.items()
    }
    actionable = [decision for decision in decisions.values() if decision.actionable]
    actionable.sort(
        key=lambda decision: (
            -decision.score,
            _SYMBOL_PRIORITY.get(decision.symbol, 99),
            decision.episode_ts,
        )
    )
    return (actionable[0] if actionable else None), decisions


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    _, source = _base.route_universe(
        bars_by_symbol,
        features_by_symbol,
        config,
    )
    decisions = {
        symbol: _reject_regime(decision)
        for symbol, decision in source.items()
    }
    actionable = [decision for decision in decisions.values() if decision.actionable]
    actionable.sort(
        key=lambda decision: (
            -decision.score,
            _SYMBOL_PRIORITY.get(decision.symbol, 99),
            decision.episode_ts,
        )
    )
    return (actionable[0] if actionable else None), decisions


__all__ = [
    "ALLOWED_REGIMES",
    "BarObservation",
    "EntryEvaluation",
    "FeatureObservation",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "TRENDRIDER_STATE",
    "TrendSnapshot",
    "UNRESOLVED",
    "classify_symbol",
    "evaluate_entry_aggregated",
    "route_universe",
    "route_universe_aggregated",
    "trendrider_exit_signal",
]

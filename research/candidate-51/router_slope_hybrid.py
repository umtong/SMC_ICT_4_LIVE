"""Causal episode context for the public Slope-is-Dope family.

This adapter preserves the public signal while making the contiguous one-hour
condition run explicit.  The run start and age are available before entry and
allow re-entry policy, risk geometry and management to be tested independently.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Mapping, Sequence

import router_slope_dope as _base

BarObservation = _base.BarObservation
FeatureObservation = _base.FeatureObservation
RouteConfig = _base.RouteConfig
RouteDecision = _base.RouteDecision
SLOPE_STATE = _base.SLOPE_STATE
UNRESOLVED = _base.UNRESOLVED
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}


def inspect_condition(
    bars: Sequence[BarObservation],
    config: RouteConfig = RouteConfig(),
) -> dict[str, float | int | str]:
    if not bars:
        return {"ready": 0}
    candles = _base._ta._aggregate_complete(bars, int(config.slope_bucket_minutes))
    minimum = max(
        int(config.slope_market_period) + 15,
        int(config.slope_slow_period) + int(config.slope_lookback) + 5,
        int(config.slope_adx_period) * 2 + 5,
    )
    if len(candles) < minimum:
        return {"ready": 0, "hourly_candles": len(candles), "minimum": minimum}
    index = len(candles) - 1
    long_ok, short_ok, diagnostics = _base._state_at(candles, index, config)
    diagnostics = dict(diagnostics)
    diagnostics.update(
        {
            "long_condition": int(long_ok),
            "short_condition": int(short_ok),
            "hourly_candles": len(candles),
            "condition_observed_ts": int(candles[index].ts_event),
        }
    )
    return diagnostics


def _with_run_context(
    decision: RouteDecision,
    bars: Sequence[BarObservation],
    config: RouteConfig,
) -> RouteDecision:
    if not decision.actionable:
        return decision
    candles = _base._ta._aggregate_complete(bars, int(config.slope_bucket_minutes))
    if not candles:
        return decision
    side = int(decision.side)
    index = len(candles) - 1
    start = index
    while start > 0:
        long_ok, short_ok, _ = _base._state_at(candles, start - 1, config)
        active = long_ok if side > 0 else short_ok
        if not active:
            break
        start -= 1
    diagnostics = dict(decision.diagnostics)
    diagnostics.update(
        {
            "condition_run_start_ts": int(candles[start].ts_event),
            "condition_run_hours": int(index - start + 1),
            "condition_signal_ts": int(candles[index].ts_event),
        }
    )
    return replace(decision, diagnostics=diagnostics)


def classify_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    return _with_run_context(
        _base.classify_symbol(symbol, bars, feature, config),
        bars,
        config,
    )


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
        key=lambda decision: (
            -float(decision.score),
            _SYMBOL_PRIORITY.get(decision.symbol, 99),
            -int(decision.side),
        )
    )
    return (actionable[0] if actionable else None), decisions


__all__ = [
    "BarObservation",
    "FeatureObservation",
    "RouteConfig",
    "RouteDecision",
    "SLOPE_STATE",
    "UNRESOLVED",
    "classify_symbol",
    "inspect_condition",
    "route_universe",
]

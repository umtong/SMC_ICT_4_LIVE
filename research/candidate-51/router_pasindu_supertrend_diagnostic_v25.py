"""Implementation-correct diagnostic facade for Pasindu Supertrend v25.

v24 stopped before any economic evidence because the strategy adapter expected
``atr_at_entry`` while the diagnostic facade exposed the same value as
``atr_4h``.  This wrapper changes no signal, geometry, arbitration, sizing or
management rule; it only supplies the missing diagnostic alias.
"""
from __future__ import annotations

from typing import Mapping, Sequence

import router_pasindu_supertrend_diagnostic as _base
from router_pasindu_supertrend_diagnostic import *  # noqa: F401,F403


def _patch(decisions):
    for decision in decisions.values():
        diagnostics = decision.diagnostics
        if "atr_at_entry" not in diagnostics and "atr_4h" in diagnostics:
            diagnostics["atr_at_entry"] = diagnostics["atr_4h"]
    return decisions


def route_universe_aggregated(
    hours_by_symbol: Mapping[str, Sequence[BarObservation]],
    four_hours_by_symbol: Mapping[str, Sequence[BarObservation]],
    config: RouteConfig = RouteConfig(),
):
    winner, decisions = _base.route_universe_aggregated(
        hours_by_symbol,
        four_hours_by_symbol,
        config,
    )
    _patch(decisions)
    return winner, decisions


def classify_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
):
    del feature
    hours = _base._base._aggregate_complete(bars, 60)
    four_hours = _base._base._aggregate_complete(bars, 240)
    _, decisions = route_universe_aggregated(
        {symbol: hours},
        {symbol: four_hours},
        config,
    )
    return decisions[symbol]


classify_sma_offset = classify_symbol


def route_universe(
    bars_by_symbol,
    features_by_symbol,
    config: RouteConfig = RouteConfig(),
):
    del features_by_symbol
    return route_universe_aggregated(
        {
            symbol: _base._base._aggregate_complete(bars, 60)
            for symbol, bars in bars_by_symbol.items()
        },
        {
            symbol: _base._base._aggregate_complete(bars, 240)
            for symbol, bars in bars_by_symbol.items()
        },
        config,
    )


__all__ = list(_base.__all__)

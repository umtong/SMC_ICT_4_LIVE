"""Candidate 37 burst-shape and cross-asset propagation router."""
from __future__ import annotations

from typing import Mapping, Sequence

from burst_features import snapshot
from burst_states import common_candidate, endogenous_candidate
from model import (
    BarObservation,
    FeatureObservation,
    RouteConfig,
    RouteDecision,
    Snapshot,
    SYMBOLS,
)

__all__ = [
    "BarObservation", "FeatureObservation", "RouteConfig", "RouteDecision",
    "SYMBOLS", "route_universe",
]


def _unresolved(symbol: str, ts_event: int, reason: str, **diagnostics: object) -> RouteDecision:
    return RouteDecision(
        symbol=symbol, state="UNRESOLVED", episode_ts=ts_event,
        reasons=(reason,), diagnostics=diagnostics,
    )


def route_universe(
    *, bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation] | None = None,
    config: RouteConfig | None = None,
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    """Use completed bars only and select at most one global route."""
    del features_by_symbol
    config = config or RouteConfig()
    missing = [symbol for symbol in SYMBOLS if symbol not in bars_by_symbol]
    if missing:
        raise ValueError(f"missing Candidate 37 symbols: {missing}")
    lengths = {symbol: len(bars_by_symbol[symbol]) for symbol in SYMBOLS}
    if len(set(lengths.values())) != 1:
        raise ValueError(f"same-minute clock requires equal history lengths: {lengths}")
    length = next(iter(lengths.values()))
    latest_ts = bars_by_symbol["BTCUSDT"][-1].ts_event if length else 0
    minimum = max(config.activity_lookback, config.atr_period + config.ramp_bars + 3) + 3
    if length < minimum:
        return None, {
            symbol: _unresolved(symbol, latest_ts, "INSUFFICIENT_WARMUP", bars=length)
            for symbol in SYMBOLS
        }
    current_index = length - 1
    if len({bars_by_symbol[symbol][current_index].ts_event for symbol in SYMBOLS}) != 1:
        raise ValueError("latest four-symbol observations are not same-minute aligned")
    best = {
        symbol: _unresolved(symbol, latest_ts, "NO_CLASSIFIED_BURST")
        for symbol in SYMBOLS
    }
    for age in range(1, config.max_shock_age_bars + 1):
        anchor_index = current_index - age
        snapshots: dict[str, Snapshot] = {}
        for symbol in SYMBOLS:
            value = snapshot(bars_by_symbol[symbol], anchor_index, config)
            if value is None:
                break
            snapshots[symbol] = value
        if len(snapshots) != len(SYMBOLS):
            continue
        for symbol in SYMBOLS:
            candidates = [
                common_candidate(
                    symbol=symbol, bars_by_symbol=bars_by_symbol,
                    snapshots=snapshots, anchor_index=anchor_index,
                    current_index=current_index, config=config,
                ),
                endogenous_candidate(
                    symbol=symbol, bars_by_symbol=bars_by_symbol,
                    snapshots=snapshots, anchor_index=anchor_index,
                    current_index=current_index, config=config,
                ),
            ]
            candidates = [item for item in candidates if item is not None]
            if candidates:
                candidate = max(candidates, key=lambda item: item.score)
                if not best[symbol].actionable or candidate.score > best[symbol].score:
                    best[symbol] = candidate
    actionable = sorted(
        (item for item in best.values() if item.actionable),
        key=lambda item: (-item.score, item.symbol, item.state),
    )
    if not actionable or actionable[0].score < config.min_route_score:
        return None, best
    if len(actionable) > 1 and actionable[0].score - actionable[1].score < config.ambiguity_score_gap:
        return None, best
    return actionable[0], best

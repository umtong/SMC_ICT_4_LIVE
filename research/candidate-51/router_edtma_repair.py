"""Episode-aware wrapper around the public EDTMA source mechanism.

The source entry itself is unchanged.  This wrapper exposes two pieces of state
that the original implementation hides:

* the start and age of the current contiguous long/short condition;
* whether the held directional thesis is still true now.

Those observations let the strategy distinguish useful continuation re-entry
from repeated entries late in a failing trend without deciding by final PnL.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Mapping, Sequence

import router_edtma as _base

BarObservation = _base.BarObservation
FeatureObservation = _base.FeatureObservation
RouteConfig = _base.RouteConfig
RouteDecision = _base.RouteDecision
EDTMA_STATE = _base.EDTMA_STATE
UNRESOLVED = _base.UNRESOLVED


def inspect_condition(
    bars: Sequence[BarObservation],
    config: RouteConfig,
) -> dict[str, float | int | str]:
    candles = _base._ta._aggregate_complete(bars, int(config.edtma_bucket_minutes))
    minimum = max(
        int(config.edtma_long_ema_period) + 5,
        int(config.edtma_short_ema_period) + 5,
        int(config.edtma_long_tema_period) * 3 + 5,
        int(config.edtma_short_tema_period) * 3 + 5,
        int(config.edtma_adx_period) * 2 + 5,
        int(config.edtma_volume_period) + 5,
    )
    if len(candles) < minimum:
        return {"ready": 0, "hourly_candles": len(candles), "minimum": minimum}
    current_long, current_short, diagnostics = _base._condition_at(candles, len(candles) - 1, config)

    def run(side: int) -> tuple[int, int]:
        count = 0
        start = int(candles[-1].ts_event)
        for index in range(len(candles) - 1, max(-1, len(candles) - 250), -1):
            long_ok, short_ok, _ = _base._condition_at(candles, index, config)
            active = long_ok if side > 0 else short_ok
            if not active:
                break
            count += 1
            start = int(candles[index].ts_event)
        return count, start

    long_hours, long_start = run(1) if current_long else (0, 0)
    short_hours, short_start = run(-1) if current_short else (0, 0)
    return {
        **diagnostics,
        "ready": 1,
        "long_condition": int(current_long),
        "short_condition": int(current_short),
        "long_run_hours": long_hours,
        "short_run_hours": short_hours,
        "long_run_start_ts": long_start,
        "short_run_start_ts": short_start,
        "current_hour_ts": int(candles[-1].ts_event),
    }


def classify_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    requested = str(config.edtma_episode_mode).strip().lower()
    base_mode = "condition_reentry" if requested == "profit_reentry" else requested
    decision = _base.classify_symbol(
        symbol,
        bars,
        feature,
        replace(config, edtma_episode_mode=base_mode),
    )
    state = inspect_condition(bars, config)
    diagnostics = dict(decision.diagnostics)
    diagnostics.update(state)
    diagnostics["requested_episode_mode"] = requested
    if decision.actionable:
        side = int(decision.side)
        diagnostics["condition_run_hours"] = int(
            state.get("long_run_hours" if side > 0 else "short_run_hours", 0)
        )
        diagnostics["condition_run_start_ts"] = int(
            state.get("long_run_start_ts" if side > 0 else "short_run_start_ts", 0)
        )
    return replace(decision, diagnostics=diagnostics)


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
        key=lambda item: (
            -float(item.score),
            _base._SYMBOL_PRIORITY.get(item.symbol, 99),
            int(item.episode_ts),
        )
    )
    return (actionable[0] if actionable else None), decisions


__all__ = [
    "BarObservation", "EDTMA_STATE", "FeatureObservation", "RouteConfig",
    "RouteDecision", "UNRESOLVED", "classify_symbol", "inspect_condition",
    "route_universe",
]

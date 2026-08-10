"""Geometry- and episode-aware wrapper for the public Slope-is-Dope entry.

The public ADX/SMA/RSI entry remains the signal generator.  This wrapper exposes
one missing state variable and one source-relative geometry check:

* the age/start of the current contiguous source condition;
* whether fast/slow MA separation is large enough relative to the public
  trailing activation distance for the winner engine to have plausible runway.

The separation comparison is expressed as a multiple of the public trailing
activation, not as an independently tuned price threshold.  A multiple of zero
is exact source-entry reachability and serves as the control.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Mapping, Sequence

import router_slope_dope as _base

BarObservation = _base.BarObservation
FeatureObservation = _base.FeatureObservation
RouteDecision = _base.RouteDecision
SLOPE_STATE = _base.SLOPE_STATE
UNRESOLVED = _base.UNRESOLVED
_SYMBOL_PRIORITY = _base._SYMBOL_PRIORITY


@dataclass(frozen=True, slots=True)
class RouteConfig(_base.RouteConfig):
    slope_trailing_offset_profit_ratio: float = 0.021
    slope_min_separation_activation_multiple: float = 0.0


def inspect_state(
    bars: Sequence[BarObservation],
    config: RouteConfig,
) -> dict[str, float | int | str]:
    candles = _base._ta._aggregate_complete(bars, int(config.slope_bucket_minutes))
    minimum = max(
        int(config.slope_market_period) + 15,
        int(config.slope_slow_period) + int(config.slope_lookback) + 5,
        int(config.slope_adx_period) * 2 + 5,
    )
    if len(candles) < minimum:
        return {"ready": 0, "hourly_candles": len(candles), "minimum": minimum}
    current_long, current_short, diagnostics = _base._state_at(
        candles, len(candles) - 1, config
    )

    def contiguous(side: int) -> tuple[int, int]:
        count = 0
        start = int(candles[-1].ts_event)
        for index in range(len(candles) - 1, max(-1, len(candles) - 250), -1):
            long_ok, short_ok, _ = _base._state_at(candles, index, config)
            active = long_ok if side > 0 else short_ok
            if not active:
                break
            count += 1
            start = int(candles[index].ts_event)
        return count, start

    long_hours, long_start = contiguous(1) if current_long else (0, 0)
    short_hours, short_start = contiguous(-1) if current_short else (0, 0)
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
    decision = _base.classify_symbol(symbol, bars, feature, config)
    state = inspect_state(bars, config)
    diagnostics = dict(decision.diagnostics)
    diagnostics.update(state)
    multiple = max(0.0, float(config.slope_min_separation_activation_multiple))
    leverage = max(float(config.slope_source_leverage), 1e-12)
    activation = float(config.slope_trailing_offset_profit_ratio) / leverage
    required = activation * multiple
    separation = float(diagnostics.get("ma_separation_fraction") or 0.0)
    diagnostics.update(
        {
            "slope_trailing_activation_fraction": activation,
            "slope_min_separation_activation_multiple": multiple,
            "slope_required_ma_separation_fraction": required,
            "slope_geometry_pass": int(separation >= required),
        }
    )
    if decision.actionable:
        side = int(decision.side)
        diagnostics["condition_run_hours"] = int(
            state.get("long_run_hours" if side > 0 else "short_run_hours", 0)
        )
        diagnostics["condition_run_start_ts"] = int(
            state.get("long_run_start_ts" if side > 0 else "short_run_start_ts", 0)
        )
        if separation < required:
            return RouteDecision(
                symbol=symbol,
                state=UNRESOLVED,
                side=0,
                score=0.0,
                entry_reference=math.nan,
                stop_reference=math.nan,
                objective_reference=math.nan,
                episode_ts=int(decision.episode_ts),
                reasons=("SLOPE_MA_SEPARATION_BELOW_WINNER_ENGINE_GEOMETRY",),
                diagnostics=diagnostics,
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
        key=lambda decision: (
            -float(decision.score),
            _SYMBOL_PRIORITY.get(decision.symbol, 99),
            -int(decision.side),
        )
    )
    return (actionable[0] if actionable else None), decisions


__all__ = [
    "BarObservation", "FeatureObservation", "RouteConfig", "RouteDecision",
    "SLOPE_STATE", "UNRESOLVED", "classify_symbol", "inspect_state",
    "route_universe",
]

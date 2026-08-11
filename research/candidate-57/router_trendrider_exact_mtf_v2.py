"""Source-fidelity repair for public TrendRider informative-timeframe state.

The v1 adapter preserved the public no-data-provider fallback.  The actual
public source, when a data provider is available, gates the `trend_pullback`
branch above the completed daily EMA200 and awards the 4h confidence point only
when the pair's completed 4h trend is bullish with ADX > 20.  This module changes
only those two omitted source semantics.
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence

import router_trendrider_base as base
from router_picasso import _SYMBOL_PRIORITY
from trendrider_public_mtf_context_v2 import context_observation

TRENDRIDER_STATE = base.TRENDRIDER_STATE
PICASSO_STATE = TRENDRIDER_STATE
SMA_OFFSET_STATE = TRENDRIDER_STATE
UNRESOLVED = base.UNRESOLVED
BarObservation = base.BarObservation
FeatureObservation = base.FeatureObservation
RouteConfig = base.RouteConfig
RouteDecision = base.RouteDecision


def _exact_decision(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    *,
    btc_rsi: float,
    config: RouteConfig,
) -> RouteDecision:
    fallback = base._route_symbol_with_context(
        symbol,
        bars,
        feature,
        btc_rsi=btc_rsi,
        config=config,
    )
    diagnostics = dict(fallback.diagnostics)
    episode_ts = int(fallback.episode_ts or (bars[-1].ts_event if bars else 0))
    observation = context_observation(symbol, episode_ts)
    diagnostics.update(
        {
            "public_mtf_context_ready": int(observation.ready),
            "public_mtf_observed_time_ns": int(observation.observed_time_ns),
            "public_daily_ema_200": observation.daily_ema_200,
            "public_pair_4h_is_bull": int(observation.pair_4h_is_bull),
            "public_pair_4h_adx": observation.pair_4h_adx,
            "public_source_daily_filter_used": 1,
            "public_source_4h_confidence_used": 1,
            "public_no_dp_fallback_used": 0,
        }
    )
    if not observation.ready:
        return base._unresolved(symbol, "PUBLIC_MTF_CONTEXT_NOT_READY", episode_ts, diagnostics)

    close = float(diagnostics.get("close", math.nan))
    daily_pass = math.isfinite(close) and close > float(observation.daily_ema_200)
    diagnostics["public_daily_ema_200_pass"] = int(daily_pass)

    raw = float(diagnostics.get("source_confidence_raw", 0.0) or 0.0)
    local_bull = bool(int(diagnostics.get("source_is_bull", 0) or 0))
    local_adx = float(diagnostics.get("adx", math.nan))
    fallback_bonus = 1.5 if local_bull and math.isfinite(local_adx) and local_adx > 20.0 else 0.0
    actual_bonus = (
        1.5
        if int(observation.pair_4h_is_bull) == 1
        and float(observation.pair_4h_adx) > 20.0
        else 0.0
    )
    exact_raw = raw - fallback_bonus + actual_bonus
    exact_numeric = max(1, min(10, round(exact_raw * 10.0 / 17.5)))
    min_conf = int(config.trendrider_min_confidence)
    confidence_pass = exact_numeric >= min_conf
    base_signal = bool(int(diagnostics.get("source_base_signal", 0) or 0))
    exact_signal = base_signal and daily_pass and confidence_pass
    diagnostics.update(
        {
            "public_fallback_4h_bonus_removed": fallback_bonus,
            "public_exact_4h_bonus_added": actual_bonus,
            "public_exact_confidence_raw": exact_raw,
            "public_exact_confidence_numeric": exact_numeric,
            "public_exact_confidence_pass": int(confidence_pass),
            "public_exact_source_signal": int(exact_signal),
            "source_confidence_numeric_fallback": diagnostics.get(
                "source_confidence_numeric"
            ),
        }
    )

    if not base_signal:
        return base._unresolved(symbol, "TRENDRIDER_PULLBACK_NO_BASE_SIGNAL", episode_ts, diagnostics)
    if not daily_pass:
        return base._unresolved(symbol, "PUBLIC_DAILY_EMA200_REJECTED", episode_ts, diagnostics)
    if not confidence_pass:
        return base._unresolved(symbol, "PUBLIC_EXACT_CONFIDENCE_REJECTED", episode_ts, diagnostics)

    if not math.isfinite(close) or close <= 0.0:
        return base._unresolved(symbol, "PUBLIC_EXACT_ENTRY_NOT_FINITE", episode_ts, diagnostics)
    stop = close * (1.0 - float(config.trendrider_stop_fraction))
    objective = close * (1.0 + float(config.trendrider_emergency_objective_fraction))
    return RouteDecision(
        symbol=symbol,
        state=TRENDRIDER_STATE,
        side=1,
        score=float(exact_numeric),
        entry_reference=close,
        stop_reference=stop,
        objective_reference=objective,
        episode_ts=episode_ts,
        reasons=("PUBLIC_TRENDRIDER_TREND_PULLBACK_LONG_EXACT_MTF",),
        diagnostics=diagnostics,
    )


def route_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    return _exact_decision(
        symbol,
        bars,
        feature,
        btc_rsi=50.0,
        config=config,
    )


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    btc_bars = bars_by_symbol.get("BTCUSDT", ())
    btc_candles = base._aggregate_complete(
        btc_bars, int(config.picasso_bucket_minutes)
    )
    btc_rsi_array = base._rsi([float(candle.close) for candle in btc_candles], 14)
    btc_rsi = (
        float(btc_rsi_array[-1])
        if btc_rsi_array and base._finite(btc_rsi_array[-1])
        else 50.0
    )
    decisions = {
        symbol: _exact_decision(
            symbol,
            bars_by_symbol[symbol],
            features_by_symbol[symbol],
            btc_rsi=btc_rsi,
            config=config,
        )
        for symbol in bars_by_symbol
        if symbol in features_by_symbol
    }
    actionable = [decision for decision in decisions.values() if decision.actionable]
    actionable.sort(
        key=lambda decision: (
            -float(decision.score),
            _SYMBOL_PRIORITY.get(decision.symbol, 99),
            int(decision.episode_ts),
        )
    )
    return (actionable[0] if actionable else None), decisions


__all__ = [
    "BarObservation",
    "FeatureObservation",
    "PICASSO_STATE",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "TRENDRIDER_STATE",
    "UNRESOLVED",
    "route_symbol",
    "route_universe",
]

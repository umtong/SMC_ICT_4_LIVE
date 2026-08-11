"""External clean-trend state gate for exact-public-MTF TrendRider entries.

This module reuses only the MIT `regime_adaptive_htf` classifier defaults and
its slow-drift confirmation.  It never creates an entry; it may only reject an
already-valid exact-public TrendRider `trend_pullback` long candidate.
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence

import router_trendrider_exact_base as exact
from router_picasso import _SYMBOL_PRIORITY

TRENDRIDER_STATE = exact.TRENDRIDER_STATE
PICASSO_STATE = TRENDRIDER_STATE
SMA_OFFSET_STATE = TRENDRIDER_STATE
UNRESOLVED = exact.UNRESOLVED
BarObservation = exact.BarObservation
FeatureObservation = exact.FeatureObservation
RouteConfig = exact.RouteConfig
RouteDecision = exact.RouteDecision

_WARMUP = 0
_TREND_UP_CLEAN = 1
_TREND_UP_CHOPPY = 2
_TREND_DOWN_CLEAN = -1
_TREND_DOWN_CHOPPY = -2
_RANGING_DIRECTIONAL = 3
_RANGING_VOLATILE = 4
_RANGING_QUIET = 5
_LABEL_NAMES = {
    _WARMUP: "",
    _TREND_UP_CLEAN: "trending_up_clean",
    _TREND_UP_CHOPPY: "trending_up_choppy",
    _TREND_DOWN_CLEAN: "trending_down_clean",
    _TREND_DOWN_CHOPPY: "trending_down_choppy",
    _RANGING_DIRECTIONAL: "ranging_directional",
    _RANGING_VOLATILE: "ranging_volatile",
    _RANGING_QUIET: "ranging_quiet",
}
_HTF_MINUTES = 360
_CLASSIFICATION_PERIOD = 14
_ADX_THRESHOLD = 20.0
_RETURN_EFF_THRESHOLD = 0.05
_RANGE_EFF_THRESHOLD = 0.03
_EFFICIENCY_THRESHOLD = 0.5
_CONFIRM_BUCKETS = 2
_SLOW_ATR_PERIOD = 20
_SLOW_LOOKBACK = 100
_TREND_DRIFT_CONFIRM = 0.10
_EPS = 1e-12


def _atr_sma(candles: Sequence[BarObservation], period: int) -> list[float]:
    tr: list[float] = []
    for index, candle in enumerate(candles):
        high = float(candle.high)
        low = float(candle.low)
        if index == 0:
            tr.append(max(0.0, high - low))
        else:
            prior_close = float(candles[index - 1].close)
            tr.append(max(high - low, abs(high - prior_close), abs(low - prior_close)))
    return exact.base._sma(tr, period)


def _raw_labels(candles: Sequence[BarObservation]) -> list[int]:
    size = len(candles)
    labels = [_WARMUP] * size
    if size == 0:
        return labels
    period = _CLASSIFICATION_PERIOD
    atr = _atr_sma(candles, period)
    adx, _, _ = exact.base._dmi(candles, period)
    closes = [float(candle.close) for candle in candles]
    for index in range(size):
        first = index - period + 1
        if first < 0 or not exact.base._finite(atr[index]) or not exact.base._finite(adx[index]):
            continue
        denom = float(atr[index]) * period
        net = closes[index] - closes[first]
        if denom <= _EPS:
            return_eff = 0.0
            range_eff = 0.0
        else:
            return_eff = net / denom
            window = candles[first : index + 1]
            range_eff = (
                max(float(item.high) for item in window)
                - min(float(item.low) for item in window)
            ) / denom
        path = sum(
            abs(closes[offset] - closes[offset - 1])
            for offset in range(first + 1, index + 1)
        )
        efficiency = abs(net) / path if path > _EPS else 0.0
        big_move = abs(return_eff) >= _RETURN_EFF_THRESHOLD
        up = return_eff > 0.0
        high_adx = float(adx[index]) >= _ADX_THRESHOLD
        wide = range_eff >= _RANGE_EFF_THRESHOLD
        clean = efficiency >= _EFFICIENCY_THRESHOLD and high_adx
        if big_move and up and clean:
            label = _TREND_UP_CLEAN
        elif big_move and up:
            label = _TREND_UP_CHOPPY
        elif big_move and clean:
            label = _TREND_DOWN_CLEAN
        elif big_move:
            label = _TREND_DOWN_CHOPPY
        elif high_adx:
            label = _RANGING_DIRECTIONAL
        elif wide:
            label = _RANGING_VOLATILE
        else:
            label = _RANGING_QUIET
        labels[index] = label
    return labels


def _confirmed_labels(raw: Sequence[int]) -> list[int]:
    current = _WARMUP
    streak_label = _WARMUP
    streak = 0
    confirmed: list[int] = []
    for label in raw:
        if label == _WARMUP:
            streak_label = _WARMUP
            streak = 0
        elif label == streak_label:
            streak += 1
        else:
            streak_label = int(label)
            streak = 1
        if (
            streak_label != _WARMUP
            and streak >= _CONFIRM_BUCKETS
            and streak_label != current
        ):
            current = streak_label
        confirmed.append(current)
    return confirmed


def _slow_efficiency(one_hour: Sequence[BarObservation]) -> float:
    if len(one_hour) <= _SLOW_LOOKBACK:
        return math.nan
    atr = _atr_sma(one_hour, _SLOW_ATR_PERIOD)
    current_atr = float(atr[-1]) if atr else math.nan
    if not math.isfinite(current_atr) or current_atr <= _EPS:
        return math.nan
    close = float(one_hour[-1].close)
    prior = float(one_hour[-1 - _SLOW_LOOKBACK].close)
    return (close - prior) / (current_atr * _SLOW_LOOKBACK)


def _state_snapshot(bars: Sequence[BarObservation]) -> dict[str, float | int | str]:
    one_hour = exact.base._aggregate_complete(bars, 60)
    six_hour = exact.base._aggregate_complete(bars, _HTF_MINUTES)
    raw = _raw_labels(six_hour)
    confirmed = _confirmed_labels(raw)
    raw_label = int(raw[-1]) if raw else _WARMUP
    confirmed_label = int(confirmed[-1]) if confirmed else _WARMUP
    slow_eff = _slow_efficiency(one_hour)
    return {
        "rahtf_context_ready": int(
            confirmed_label != _WARMUP and math.isfinite(slow_eff)
        ),
        "rahtf_closed_1h_candles": len(one_hour),
        "rahtf_closed_6h_buckets": len(six_hour),
        "rahtf_raw_label_code": raw_label,
        "rahtf_raw_label": _LABEL_NAMES[raw_label],
        "rahtf_confirmed_label_code": confirmed_label,
        "rahtf_confirmed_label": _LABEL_NAMES[confirmed_label],
        "rahtf_slow_eff": slow_eff,
        "rahtf_htf_factor": 6,
        "rahtf_period": _CLASSIFICATION_PERIOD,
        "rahtf_adx_threshold": _ADX_THRESHOLD,
        "rahtf_return_eff_threshold": _RETURN_EFF_THRESHOLD,
        "rahtf_range_eff_threshold": _RANGE_EFF_THRESHOLD,
        "rahtf_efficiency_threshold": _EFFICIENCY_THRESHOLD,
        "rahtf_confirm_buckets": _CONFIRM_BUCKETS,
        "rahtf_slow_atr_period": _SLOW_ATR_PERIOD,
        "rahtf_slow_lookback": _SLOW_LOOKBACK,
        "rahtf_trend_drift_confirm": _TREND_DRIFT_CONFIRM,
        "rahtf_external_entry_logic_used": 0,
        "rahtf_external_fade_logic_used": 0,
        "rahtf_thresholds_searched": 0,
    }


def _clean_decision(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    *,
    btc_rsi: float,
    config: RouteConfig,
) -> RouteDecision:
    source = exact._exact_decision(
        symbol,
        bars,
        feature,
        btc_rsi=btc_rsi,
        config=config,
    )
    if not source.actionable:
        return source
    diagnostics = dict(source.diagnostics)
    state = _state_snapshot(bars)
    diagnostics.update(state)
    episode_ts = int(source.episode_ts)
    if not bool(int(state["rahtf_context_ready"])):
        return exact.base._unresolved(
            symbol, "RAHTF_CONTEXT_NOT_READY", episode_ts, diagnostics
        )
    if int(state["rahtf_confirmed_label_code"]) != _TREND_UP_CLEAN:
        return exact.base._unresolved(
            symbol, "RAHTF_CONFIRMED_LABEL_REJECTED", episode_ts, diagnostics
        )
    if float(state["rahtf_slow_eff"]) < _TREND_DRIFT_CONFIRM:
        return exact.base._unresolved(
            symbol, "RAHTF_SLOW_DRIFT_REJECTED", episode_ts, diagnostics
        )
    diagnostics["rahtf_clean_state_pass"] = 1
    return RouteDecision(
        symbol=source.symbol,
        state=source.state,
        side=source.side,
        score=source.score,
        entry_reference=source.entry_reference,
        stop_reference=source.stop_reference,
        objective_reference=source.objective_reference,
        episode_ts=source.episode_ts,
        reasons=(*source.reasons, "EXTERNAL_RAHTF_CLEAN_STATE_PASS"),
        diagnostics=diagnostics,
    )


def route_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    return _clean_decision(
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
    btc_candles = exact.base._aggregate_complete(
        btc_bars, int(config.picasso_bucket_minutes)
    )
    btc_rsi_array = exact.base._rsi(
        [float(candle.close) for candle in btc_candles], 14
    )
    btc_rsi = (
        float(btc_rsi_array[-1])
        if btc_rsi_array and exact.base._finite(btc_rsi_array[-1])
        else 50.0
    )
    decisions = {
        symbol: _clean_decision(
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

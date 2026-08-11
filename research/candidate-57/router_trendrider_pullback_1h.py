"""Causal adapter for the public TrendRider `trend_pullback` long branch.

Only the externally published branch that solves the current long bull-regime
continuation gap is translated.  The other five public OR entry families are not
silently imported.  All state is built from completed candles and cross-symbol
BTC context is supplied before global one-slot arbitration.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence

from router_picasso import (
    BarObservation,
    FeatureObservation,
    RouteConfig as _PicassoRouteConfig,
    RouteDecision,
    UNRESOLVED,
    _SYMBOL_PRIORITY,
    _aggregate_complete,
    _ema,
    _ema_nan,
    _finite,
    _macd,
    _rsi,
    _sma,
)

TRENDRIDER_STATE = "PUBLIC_TRENDRIDER_TREND_PULLBACK_1H"
PICASSO_STATE = TRENDRIDER_STATE
SMA_OFFSET_STATE = TRENDRIDER_STATE
_EPS = 1e-12


@dataclass(frozen=True, slots=True)
class RouteConfig(_PicassoRouteConfig):
    trendrider_ema_fast: int = 9
    trendrider_ema_slow: int = 16
    trendrider_ema_regime_fast: int = 50
    trendrider_ema_regime_slow: int = 200
    trendrider_rsi_period: int = 16
    trendrider_adx_period: int = 14
    trendrider_volume_ema_period: int = 20
    trendrider_obv_ema_period: int = 20
    trendrider_rsi_pullback_low: float = 30.0
    trendrider_rsi_pullback_high: float = 65.0
    trendrider_adx_threshold: float = 18.0
    trendrider_volume_factor: float = 0.7
    trendrider_pullback_tolerance: float = 0.02
    trendrider_min_confidence: int = 5
    trendrider_stop_fraction: float = 0.06
    trendrider_emergency_objective_fraction: float = 0.229


def _unresolved(
    symbol: str,
    reason: str,
    episode_ts: int = 0,
    diagnostics: Mapping[str, float | int | str] | None = None,
) -> RouteDecision:
    return RouteDecision(
        symbol=symbol,
        state=UNRESOLVED,
        side=0,
        score=0.0,
        entry_reference=math.nan,
        stop_reference=math.nan,
        objective_reference=math.nan,
        episode_ts=int(episode_ts),
        reasons=(reason,),
        diagnostics=dict(diagnostics or {}),
    )


def _rma(values: Sequence[float], period: int) -> list[float]:
    result = [math.nan] * len(values)
    if period <= 0 or len(values) < period:
        return result
    current = sum(float(value) for value in values[:period]) / period
    result[period - 1] = current
    for index in range(period, len(values)):
        current = (current * (period - 1) + float(values[index])) / period
        result[index] = current
    return result


def _dmi(
    candles: Sequence[BarObservation], period: int
) -> tuple[list[float], list[float], list[float]]:
    size = len(candles)
    adx_result = [math.nan] * size
    plus_result = [math.nan] * size
    minus_result = [math.nan] * size
    if period <= 0 or size <= period * 2:
        return adx_result, plus_result, minus_result
    tr = [0.0] * size
    plus_dm = [0.0] * size
    minus_dm = [0.0] * size
    for index in range(1, size):
        current, previous = candles[index], candles[index - 1]
        up = float(current.high) - float(previous.high)
        down = float(previous.low) - float(current.low)
        plus_dm[index] = up if up > down and up > 0.0 else 0.0
        minus_dm[index] = down if down > up and down > 0.0 else 0.0
        tr[index] = max(
            float(current.high) - float(current.low),
            abs(float(current.high) - float(previous.close)),
            abs(float(current.low) - float(previous.close)),
        )
    atr = _rma(tr[1:], period)
    plus = _rma(plus_dm[1:], period)
    minus = _rma(minus_dm[1:], period)
    dx = [math.nan] * (size - 1)
    for offset in range(size - 1):
        if not all(_finite(value) for value in (atr[offset], plus[offset], minus[offset])):
            continue
        target = offset + 1
        if float(atr[offset]) <= _EPS:
            plus_di = minus_di = 0.0
            dx[offset] = 0.0
        else:
            plus_di = 100.0 * float(plus[offset]) / float(atr[offset])
            minus_di = 100.0 * float(minus[offset]) / float(atr[offset])
            dx[offset] = 100.0 * abs(plus_di - minus_di) / max(
                plus_di + minus_di, _EPS
            )
        plus_result[target] = plus_di
        minus_result[target] = minus_di
    finite_start = next((i for i, value in enumerate(dx) if _finite(value)), None)
    if finite_start is None:
        return adx_result, plus_result, minus_result
    core = _rma([float(value) for value in dx[finite_start:]], period)
    for offset, value in enumerate(core):
        target = 1 + finite_start + offset
        if target < size and _finite(value):
            adx_result[target] = float(value)
    return adx_result, plus_result, minus_result


def _obv(candles: Sequence[BarObservation]) -> list[float]:
    values = [0.0] * len(candles)
    for index in range(1, len(candles)):
        close = float(candles[index].close)
        prior = float(candles[index - 1].close)
        volume = max(0.0, float(candles[index].volume))
        direction = 1.0 if close > prior else (-1.0 if close < prior else 0.0)
        values[index] = values[index - 1] + direction * volume
    return values


def _rolling_std(values: Sequence[float], period: int) -> list[float]:
    result = [math.nan] * len(values)
    if period <= 0:
        return result
    for index in range(period - 1, len(values)):
        sample = [float(value) for value in values[index - period + 1 : index + 1]]
        mean = sum(sample) / period
        result[index] = math.sqrt(
            sum((value - mean) ** 2 for value in sample) / period
        )
    return result


def _source_confidence(
    *,
    close: float,
    rsi: float,
    adx: float,
    volume_ratio: float,
    macd_hist: float,
    macd_hist_prior: float,
    obv: float,
    obv_ema: float,
    btc_rsi: float,
    is_bull: bool,
    bb_lower: float,
    bb_upper: float,
    plus_di: float,
    minus_di: float,
) -> tuple[float, int, tuple[str, ...]]:
    score = 0.0
    details: list[str] = []
    if 35.0 < rsi < 60.0:
        score += 1.5
        details.append("RSI_HEALTHY")
    if adx > 30.0:
        score += 2.5
        details.append("ADX_STRONG")
    elif adx > 18.0:
        score += 1.5
        details.append("ADX_MODERATE")
    if volume_ratio > 1.5:
        score += 2.5
        details.append("VOLUME_HIGH")
    elif volume_ratio > 1.0:
        score += 1.5
        details.append("VOLUME_NORMAL")
    if macd_hist > 0.0:
        score += 1.5
        if macd_hist > macd_hist_prior:
            score += 0.5
            details.append("MACD_POSITIVE_RISING")
        else:
            details.append("MACD_POSITIVE")
    if obv > obv_ema:
        score += 1.5
        details.append("OBV_ABOVE_EMA")
    if 40.0 < btc_rsi < 70.0:
        score += 1.5
        details.append("BTC_HEALTHY")
    # Public no-informative-data fallback uses the local 1h bull state/ADX here.
    if is_bull and adx > 20.0:
        score += 1.5
        details.append("FALLBACK_4H_ALIGNED")
    bb_range = bb_upper - bb_lower
    if bb_lower > 0.0 and bb_range > _EPS:
        position = (close - bb_lower) / bb_range
        if position < 0.35:
            score += 1.0
            details.append("NEAR_BB_LOWER")
    if plus_di - minus_di > 10.0:
        score += 1.0
        details.append("DI_SPREAD_STRONG")
    # Public strategy's neutral fallbacks always earn these two source points.
    score += 1.0
    details.append("FNG_NEUTRAL_FALLBACK")
    score += 1.0
    details.append("FUNDING_NEUTRAL_FALLBACK")
    numeric = max(1, min(10, round(score * 10.0 / 17.5)))
    return score, int(numeric), tuple(details)


def _route_symbol_with_context(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    *,
    btc_rsi: float,
    config: RouteConfig,
) -> RouteDecision:
    if not bars:
        return _unresolved(symbol, "NO_MINUTE_BARS")
    latest_ts = int(bars[-1].ts_event)
    if int(feature.observed_time_ns) > latest_ts:
        return _unresolved(symbol, "FUTURE_FEATURE_REJECTED", latest_ts)
    if not feature.ready:
        return _unresolved(symbol, "FEATURE_NOT_READY", latest_ts)
    candles = _aggregate_complete(bars, int(config.picasso_bucket_minutes))
    required = max(
        int(config.trendrider_ema_regime_slow) + 2,
        int(config.trendrider_ema_regime_fast) + 2,
        int(config.trendrider_ema_slow) + 2,
        int(config.trendrider_rsi_period) + 2,
        int(config.trendrider_adx_period) * 2 + 3,
        int(config.trendrider_volume_ema_period) + 2,
        int(config.trendrider_obv_ema_period) + 2,
        30,
    )
    if len(candles) < required:
        return _unresolved(
            symbol,
            "INSUFFICIENT_TRENDRIDER_WARMUP",
            int(candles[-1].ts_event) if candles else latest_ts,
            {"completed_source_candles": len(candles), "required": required},
        )

    closes = [float(candle.close) for candle in candles]
    volumes = [max(0.0, float(candle.volume)) for candle in candles]
    ema_fast = _ema(closes, int(config.trendrider_ema_fast))
    ema_slow = _ema(closes, int(config.trendrider_ema_slow))
    ema_50 = _ema(closes, int(config.trendrider_ema_regime_fast))
    ema_200 = _ema(closes, int(config.trendrider_ema_regime_slow))
    rsi = _rsi(closes, int(config.trendrider_rsi_period))
    adx, plus_di, minus_di = _dmi(candles, int(config.trendrider_adx_period))
    volume_ema = _ema(volumes, int(config.trendrider_volume_ema_period))
    obv = _obv(candles)
    obv_ema = _ema(obv, int(config.trendrider_obv_ema_period))
    macd_line, macd_signal = _macd(closes)
    macd_hist = [
        float(left) - float(right)
        if _finite(left) and _finite(right)
        else math.nan
        for left, right in zip(macd_line, macd_signal, strict=True)
    ]
    bb_mid = _sma(closes, 20)
    bb_std = _rolling_std(closes, 20)
    index = len(candles) - 1
    values = (
        ema_fast[index],
        ema_slow[index],
        ema_50[index],
        ema_200[index],
        rsi[index],
        adx[index],
        plus_di[index],
        minus_di[index],
        volume_ema[index],
        obv[index],
        obv_ema[index],
        macd_hist[index],
        macd_hist[index - 1],
        bb_mid[index],
        bb_std[index],
        btc_rsi,
    )
    episode_ts = int(candles[-1].ts_event)
    if not all(_finite(value) for value in values):
        return _unresolved(symbol, "TRENDRIDER_INDICATORS_NOT_READY", episode_ts)

    candle = candles[index]
    close = float(candle.close)
    opened = float(candle.open)
    low = float(candle.low)
    volume = float(candle.volume)
    volume_ratio = volume / max(float(volume_ema[index]), _EPS)
    is_bull = close > float(ema_200[index]) and float(ema_50[index]) > float(
        ema_200[index]
    )
    pullback = (
        low <= float(ema_slow[index]) * (1.0 + float(config.trendrider_pullback_tolerance))
        and close > float(ema_slow[index])
        and close > opened
    )
    rsi_ok = (
        float(rsi[index]) > float(config.trendrider_rsi_pullback_low)
        and float(rsi[index]) < float(config.trendrider_rsi_pullback_high)
        and float(rsi[index]) < 70.0
    )
    base_signal = (
        is_bull
        and pullback
        and rsi_ok
        and float(adx[index]) > float(config.trendrider_adx_threshold)
        and volume_ratio > float(config.trendrider_volume_factor)
        and float(plus_di[index]) > float(minus_di[index])
        and float(obv[index]) > float(obv_ema[index])
        and float(btc_rsi) > 35.0
        and volume > 0.0
    )
    bb_lower = float(bb_mid[index]) - 2.0 * float(bb_std[index])
    bb_upper = float(bb_mid[index]) + 2.0 * float(bb_std[index])
    raw_confidence, confidence, confidence_details = _source_confidence(
        close=close,
        rsi=float(rsi[index]),
        adx=float(adx[index]),
        volume_ratio=volume_ratio,
        macd_hist=float(macd_hist[index]),
        macd_hist_prior=float(macd_hist[index - 1]),
        obv=float(obv[index]),
        obv_ema=float(obv_ema[index]),
        btc_rsi=float(btc_rsi),
        is_bull=bool(is_bull),
        bb_lower=bb_lower,
        bb_upper=bb_upper,
        plus_di=float(plus_di[index]),
        minus_di=float(minus_di[index]),
    )
    signal = base_signal and confidence >= int(config.trendrider_min_confidence)
    diagnostics: dict[str, float | int | str] = {
        "source_branch": "trend_pullback",
        "source_bucket_minutes": int(config.picasso_bucket_minutes),
        "close": close,
        "open": opened,
        "low": low,
        "ema_fast": float(ema_fast[index]),
        "ema_slow": float(ema_slow[index]),
        "ema_50": float(ema_50[index]),
        "ema_200": float(ema_200[index]),
        "rsi": float(rsi[index]),
        "adx": float(adx[index]),
        "plus_di": float(plus_di[index]),
        "minus_di": float(minus_di[index]),
        "volume": volume,
        "volume_ema": float(volume_ema[index]),
        "volume_ratio": volume_ratio,
        "obv": float(obv[index]),
        "obv_ema": float(obv_ema[index]),
        "macd_hist": float(macd_hist[index]),
        "macd_hist_prior": float(macd_hist[index - 1]),
        "bb_lower": bb_lower,
        "bb_upper": bb_upper,
        "btc_rsi_1h": float(btc_rsi),
        "source_is_bull": int(is_bull),
        "source_pullback": int(pullback),
        "source_rsi_ok": int(rsi_ok),
        "source_base_signal": int(base_signal),
        "source_confidence_raw": raw_confidence,
        "source_confidence_numeric": confidence,
        "source_confidence_details": "|".join(confidence_details),
        "source_min_confidence": int(config.trendrider_min_confidence),
        "source_signal": int(signal),
        "source_daily_filter_mode": "PUBLIC_NO_DP_FALLBACK",
        "source_private_layers_used": 0,
    }
    if not signal:
        return _unresolved(symbol, "TRENDRIDER_PULLBACK_NO_SIGNAL", episode_ts, diagnostics)

    entry = close
    stop = entry * (1.0 - float(config.trendrider_stop_fraction))
    objective = entry * (1.0 + float(config.trendrider_emergency_objective_fraction))
    return RouteDecision(
        symbol=symbol,
        state=TRENDRIDER_STATE,
        side=1,
        score=float(confidence),
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=objective,
        episode_ts=episode_ts,
        reasons=("PUBLIC_TRENDRIDER_TREND_PULLBACK_LONG",),
        diagnostics=diagnostics,
    )


def route_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    return _route_symbol_with_context(
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
    btc_candles = _aggregate_complete(btc_bars, int(config.picasso_bucket_minutes))
    btc_rsi_array = _rsi(
        [float(candle.close) for candle in btc_candles],
        14,
    )
    btc_rsi = (
        float(btc_rsi_array[-1])
        if btc_rsi_array and _finite(btc_rsi_array[-1])
        else 50.0
    )
    decisions = {
        symbol: _route_symbol_with_context(
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

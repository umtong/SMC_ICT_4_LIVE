"""Causal adapter for the public 1h UTBotAlert completed solution.

The uploaded source contains a material implementation quirk: it initializes
``xATRTrailingStop`` to zeros and then applies vectorized ``np.where`` clauses
which all read the still-zero rolled array.  For positive crypto prices, the
last clause collapses the stop to ``close - key * ATR`` on every row.  The
resulting signal is effectively a large one-hour displacement detector rather
than the recursive UTBot stop normally intended.

Candidate 55 tests both meanings explicitly:

* ``exact_*`` reproduces the source's actual vectorized operator semantics;
* ``recursive_*`` implements the same clauses sequentially as an intended
  causal UTBot trailing stop.

ADX, EMA, volume, ROI, stop and trailing parameters remain source defaults.
All signals use completed 1h bars; execution remains real Binance 1m bars in
NautilusTrader.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
from typing import Mapping, Sequence

_HELPER_PATH = Path(__file__).resolve().with_name("router_zaratustra_v13.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_utbot_reused_indicators", _HELPER_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load reused DMI helpers: {_HELPER_PATH}")
_HELPER = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _HELPER
_SPEC.loader.exec_module(_HELPER)

BarObservation = _HELPER.BarObservation
FeatureObservation = _HELPER.FeatureObservation
RouteConfig = _HELPER.RouteConfig
RouteDecision = _HELPER.RouteDecision
UNRESOLVED = _HELPER.UNRESOLVED
_EPS = _HELPER._EPS

UTBOT_STATE = "PUBLIC_UTBOT_1H_DISPLACEMENT"
MBE2_STATE = UTBOT_STATE
PICASSO_STATE = UTBOT_STATE
SMA_OFFSET_STATE = UTBOT_STATE
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}

_aggregate_complete = _HELPER._aggregate_complete
_directional_indicators = _HELPER._directional_indicators
_adx_dx = _HELPER._adx_dx


def _talib_ema(values: Sequence[float], period: int) -> list[float]:
    output = [math.nan] * len(values)
    if period <= 0 or len(values) < period:
        return output
    seed = sum(float(value) for value in values[:period]) / period
    output[period - 1] = seed
    alpha = 2.0 / (period + 1.0)
    previous = seed
    for index in range(period, len(values)):
        previous = alpha * float(values[index]) + (1.0 - alpha) * previous
        output[index] = previous
    return output


def _rolling_shifted(values: Sequence[float], period: int) -> list[float]:
    """Exact pandas ``rolling(period).mean().shift(1)``."""
    output = [math.nan] * len(values)
    if period <= 0:
        return output
    running = 0.0
    for index, value in enumerate(values):
        if index >= period:
            output[index] = running / period
            running -= float(values[index - period])
        running += float(value)
    return output


def _atr(candles: Sequence[BarObservation], period: int) -> list[float]:
    """TA-Lib-style Wilder ATR, seeded from true ranges 1..period."""
    size = len(candles)
    output = [math.nan] * size
    if period <= 0 or size <= period:
        return output
    true_ranges = [0.0] * size
    for index in range(1, size):
        high = float(candles[index].high)
        low = float(candles[index].low)
        previous_close = float(candles[index - 1].close)
        true_ranges[index] = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )
    previous = sum(true_ranges[1 : period + 1]) / period
    output[period] = previous
    for index in range(period + 1, size):
        previous = ((period - 1.0) * previous + true_ranges[index]) / period
        output[index] = previous
    return output


def _exact_vectorized_stops(
    closes: Sequence[float], atr: Sequence[float], key: float
) -> list[float]:
    """Reproduce the source's actual zero-array vectorization for crypto prices."""
    output = [math.nan] * len(closes)
    for index, close in enumerate(closes):
        value = float(atr[index])
        if math.isfinite(value):
            # The source's final np.where(mask_3, src - nLoss, ...) sees a
            # rolled zero array.  Positive crypto closes make mask_3 true.
            output[index] = float(close) - float(key) * value
    return output


def _recursive_stops(
    closes: Sequence[float], atr: Sequence[float], key: float
) -> list[float]:
    """Sequential interpretation of the source's intended UTBot recurrence."""
    output = [math.nan] * len(closes)
    first = next(
        (index for index, value in enumerate(atr) if math.isfinite(float(value))),
        None,
    )
    if first is None:
        return output
    output[first] = float(closes[first]) - float(key) * float(atr[first])
    for index in range(first + 1, len(closes)):
        nloss = float(key) * float(atr[index])
        previous_stop = float(output[index - 1])
        close = float(closes[index])
        previous_close = float(closes[index - 1])
        if not (math.isfinite(nloss) and math.isfinite(previous_stop)):
            continue
        if close > previous_stop and previous_close > previous_stop:
            stop = max(previous_stop, close - nloss)
        elif close < previous_stop and previous_close < previous_stop:
            stop = min(previous_stop, close + nloss)
        elif close > previous_stop:
            stop = close - nloss
        else:
            stop = close + nloss
        output[index] = stop
    return output


def _decode_mode(mode: str) -> tuple[str, str]:
    normalized = str(mode).strip().lower().replace("-", "_")
    semantics = "recursive" if normalized.startswith("recursive_") else "exact"
    if normalized.endswith("_short"):
        side = "short"
    elif normalized.endswith("_long"):
        side = "long"
    elif normalized.endswith("_both"):
        side = "both"
    else:
        raise ValueError(f"unsupported UTBot variant: {mode}")
    return semantics, side


def _trend_flags(
    closes: Sequence[float], stops: Sequence[float], ema: Sequence[float], index: int
) -> tuple[bool, bool]:
    if index <= 0:
        return False, False
    values = (
        closes[index - 1], closes[index], stops[index - 1], stops[index], ema[index]
    )
    if not all(math.isfinite(float(value)) for value in values):
        return False, False
    previous_close = float(closes[index - 1])
    close = float(closes[index])
    previous_stop = float(stops[index - 1])
    stop = float(stops[index])
    moving_average = float(ema[index])
    pos_long = previous_close < stop and close > previous_stop
    pos_short = previous_close > stop and close < previous_stop
    return (
        bool(stop > moving_average and pos_long and close > moving_average),
        bool(stop < moving_average and pos_short and close < moving_average),
    )


def source_entry_flags(
    *,
    previous_close: float,
    close: float,
    previous_stop: float,
    stop: float,
    ema: float,
    adx: float,
    adx_min: float,
    adx_max: float,
    volume: float,
    shifted_volume_mean: float,
) -> tuple[bool, bool]:
    """Pure source contract before side-specific EMA differences."""
    values = (
        previous_close, close, previous_stop, stop, ema, adx,
        volume, shifted_volume_mean,
    )
    if not all(math.isfinite(float(value)) for value in values):
        return False, False
    long_trend = stop > ema and previous_close < stop and close > previous_stop and close > ema
    short_trend = stop < ema and previous_close > stop and close < previous_stop and close < ema
    common = adx > adx_min and adx < adx_max and volume > shifted_volume_mean
    return bool(long_trend and common and volume > 0.0), bool(short_trend and common)


def _unresolved(
    symbol: str,
    reason: str,
    episode_ts: int = 0,
    diagnostics: Mapping[str, float | int | str] | None = None,
) -> RouteDecision:
    return RouteDecision(
        symbol, UNRESOLVED, 0, 0.0, math.nan, math.nan, math.nan,
        int(episode_ts), (reason,), dict(diagnostics or {}),
    )


def classify_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    if not bars:
        return _unresolved(symbol, "NO_MINUTE_BARS")
    latest_ts = int(bars[-1].ts_event)
    if int(feature.observed_time_ns) > latest_ts:
        return _unresolved(symbol, "FUTURE_FEATURE_REJECTED", latest_ts)

    semantics, side_filter = _decode_mode(config.picasso_precedence_mode)
    candles = _aggregate_complete(bars, 60)
    minimum = 120
    if len(candles) < minimum:
        return _unresolved(
            symbol, "UTBOT_HISTORY_NOT_READY", latest_ts,
            {"hourly_candles": len(candles), "minimum": minimum},
        )

    closes = [float(candle.close) for candle in candles]
    volumes = [float(candle.volume) for candle in candles]
    plus_di, minus_di = _directional_indicators(candles, 14)
    _, adx = _adx_dx(plus_di, minus_di, 14)
    atr = _atr(candles, 8)
    ema_long = _talib_ema(closes, 63)
    ema_short = _talib_ema(closes, 53)
    volume_long = _rolling_shifted(volumes, 40)
    volume_short = _rolling_shifted(volumes, 37)
    stop_builder = _recursive_stops if semantics == "recursive" else _exact_vectorized_stops
    stops_long = stop_builder(closes, atr, 2.0)
    stops_short = stop_builder(closes, atr, 2.0)
    index = len(candles) - 1

    trend_long, _ = _trend_flags(closes, stops_long, ema_long, index)
    _, trend_short = _trend_flags(closes, stops_short, ema_short, index)
    adx_value = float(adx[index])
    volume = volumes[index]
    long_action = (
        14.0 < adx_value < 48.0
        and trend_long
        and volume > float(volume_long[index])
        and volume > 0.0
    )
    short_action = (
        8.0 < adx_value < 50.0
        and trend_short
        and volume > float(volume_short[index])
    )
    if side_filter == "long":
        short_action = False
    elif side_filter == "short":
        long_action = False

    diagnostics: dict[str, float | int | str] = {
        "candidate55_declared_mode": str(config.picasso_precedence_mode),
        "source_stop_semantics": semantics,
        "source_side_filter": side_filter,
        "previous_close": closes[index - 1],
        "close": closes[index],
        "atr_8": float(atr[index]),
        "adx_14": adx_value,
        "ema_long_63": float(ema_long[index]),
        "ema_short_53": float(ema_short[index]),
        "ut_stop_long": float(stops_long[index]),
        "ut_stop_short": float(stops_short[index]),
        "previous_ut_stop_long": float(stops_long[index - 1]),
        "previous_ut_stop_short": float(stops_short[index - 1]),
        "volume": volume,
        "volume_mean_long_40_shifted": float(volume_long[index]),
        "volume_mean_short_37_shifted": float(volume_short[index]),
        "trend_long": int(trend_long),
        "trend_short": int(trend_short),
        "long_action": int(long_action),
        "short_action": int(short_action),
        "complete_1h_candles_only": 1,
        "source_vectorized_zero_array_bug_preserved": int(semantics == "exact"),
    }
    if long_action == short_action:
        reason = "UTBOT_NO_SOURCE_ENTRY" if not long_action else "UTBOT_AMBIGUOUS_ENTRY"
        return _unresolved(symbol, reason, int(candles[index].ts_event), diagnostics)

    side = 1 if long_action else -1
    entry = closes[index]
    leverage = max(float(config.picasso_source_effective_leverage), _EPS)
    stop_fraction = float(config.picasso_source_stoploss) / leverage
    stop = entry * (1.0 - side * stop_fraction)
    objective = entry * (1.0 + side * float(config.picasso_emergency_target_fraction))
    displacement_atr = abs(closes[index] - closes[index - 1]) / max(float(atr[index]), _EPS)
    adx_margin = min(
        adx_value - (14.0 if side > 0 else 8.0),
        (48.0 if side > 0 else 50.0) - adx_value,
    )
    volume_ratio = volume / max(
        float(volume_long[index] if side > 0 else volume_short[index]), _EPS
    )
    score = 1.0 + min(6.0, displacement_atr) + min(4.0, max(0.0, adx_margin) / 3.0) + min(4.0, max(0.0, volume_ratio - 1.0))
    diagnostics.update(
        {
            "source_tag": "UTBot long" if side > 0 else "UTBot short",
            "source_effective_leverage": leverage,
            "source_stoploss_profit_ratio": float(config.picasso_source_stoploss),
            "underlying_stop_fraction": stop_fraction,
            "source_trailing_positive": float(config.picasso_trailing_positive),
            "source_trailing_offset": float(config.picasso_trailing_offset),
            "displacement_atr": displacement_atr,
            "adx_margin": adx_margin,
            "volume_ratio": volume_ratio,
        }
    )
    return RouteDecision(
        symbol, UTBOT_STATE, side, float(score), entry, stop, objective,
        int(candles[index].ts_event),
        (
            "PUBLIC_UTBOT_1H_ENTRY",
            "SOURCE_STOP_SEMANTICS_" + semantics.upper(),
            "SOURCE_SIDE_FILTER_" + side_filter.upper(),
            "SOURCE_RISK_NORMALIZED_BY_2X_LEVERAGE",
        ),
        diagnostics,
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
            _SYMBOL_PRIORITY.get(item.symbol, 99),
            int(item.episode_ts),
        )
    )
    return (actionable[0] if actionable else None), decisions


__all__ = [
    "BarObservation", "FeatureObservation", "MBE2_STATE", "PICASSO_STATE",
    "RouteConfig", "RouteDecision", "SMA_OFFSET_STATE", "UNRESOLVED", "UTBOT_STATE",
    "_adx_dx", "_aggregate_complete", "_atr", "_decode_mode",
    "_directional_indicators", "_exact_vectorized_stops", "_recursive_stops",
    "_rolling_shifted", "_talib_ema", "_trend_flags", "classify_symbol",
    "route_universe", "source_entry_flags",
]

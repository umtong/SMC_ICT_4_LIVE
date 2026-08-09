"""Causal adapter for public ``CusTrend_coralTrend_Adx_EMA_Oct_1h``.

The complete public policy is reused: a two-candle expansion pattern on 1h,
ADX/PSAR/RSI/EMA/volume confirmation, and a delayed complete-4h EMA50 filter.
Candidate 55 separates Freqtrade's source-compatible hourly level entries from
rising-edge deduplication, but changes no source threshold.
"""
from __future__ import annotations

from bisect import bisect_right
import importlib.util
import math
from pathlib import Path
import sys
from typing import Mapping, Sequence

_HELPER_PATH = Path(__file__).resolve().with_name("router_zaratustra_v13.py")
_SPEC = importlib.util.spec_from_file_location("candidate55_custrend_indicators", _HELPER_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load reused indicator helpers: {_HELPER_PATH}")
_HELPER = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _HELPER
_SPEC.loader.exec_module(_HELPER)

BarObservation = _HELPER.BarObservation
FeatureObservation = _HELPER.FeatureObservation
RouteConfig = _HELPER.RouteConfig
RouteDecision = _HELPER.RouteDecision
UNRESOLVED = _HELPER.UNRESOLVED
_EPS = _HELPER._EPS
_BASE = _HELPER._BASE

CUSTREND_STATE = "PUBLIC_CUSTREND_CORAL_ADX_EMA_1H"
MBE2_STATE = CUSTREND_STATE
PICASSO_STATE = CUSTREND_STATE
SMA_OFFSET_STATE = CUSTREND_STATE
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}

_aggregate_complete = _HELPER._aggregate_complete
_directional_indicators = _HELPER._directional_indicators
_adx_dx = _HELPER._adx_dx
_rsi = _HELPER._rsi


def _talib_ema(values: Sequence[float], period: int) -> list[float]:
    """SMA-seeded EMA matching TA-Lib's ordinary EMA warmup semantics."""
    output = [math.nan] * len(values)
    if period <= 0 or len(values) < period:
        return output
    seed = sum(float(value) for value in values[:period]) / period
    index = period - 1
    output[index] = seed
    alpha = 2.0 / (period + 1.0)
    previous = seed
    for index in range(period, len(values)):
        previous = alpha * float(values[index]) + (1.0 - alpha) * previous
        output[index] = previous
    return output


def _rolling_shifted(values: Sequence[float], period: int) -> list[float]:
    output = [math.nan] * len(values)
    if period <= 0:
        return output
    running = 0.0
    for index, value in enumerate(values):
        running += float(value)
        if index >= period:
            output[index] = running / period
            running -= float(values[index - period])
    return output


def _parabolic_sar(
    candles: Sequence[BarObservation], acceleration: float = 0.02, maximum: float = 0.2
) -> list[float]:
    """Causal Wilder parabolic SAR using the standard two-bar clamp."""
    size = len(candles)
    output = [math.nan] * size
    if size < 2:
        return output
    high0, high1 = float(candles[0].high), float(candles[1].high)
    low0, low1 = float(candles[0].low), float(candles[1].low)
    long = float(candles[1].close) >= float(candles[0].close)
    sar = low0 if long else high0
    extreme = max(high0, high1) if long else min(low0, low1)
    factor = float(acceleration)
    output[1] = sar
    for index in range(2, size):
        candidate = sar + factor * (extreme - sar)
        previous = candles[index - 1]
        previous2 = candles[index - 2]
        if long:
            candidate = min(candidate, float(previous.low), float(previous2.low))
            if float(candles[index].low) < candidate:
                long = False
                candidate = extreme
                extreme = float(candles[index].low)
                factor = float(acceleration)
            elif float(candles[index].high) > extreme:
                extreme = float(candles[index].high)
                factor = min(float(maximum), factor + float(acceleration))
        else:
            candidate = max(candidate, float(previous.high), float(previous2.high))
            if float(candles[index].high) > candidate:
                long = True
                candidate = extreme
                extreme = float(candles[index].high)
                factor = float(acceleration)
            elif float(candles[index].low) < extreme:
                extreme = float(candles[index].low)
                factor = min(float(maximum), factor + float(acceleration))
        sar = candidate
        output[index] = sar
    return output


def _trend_flag(previous: BarObservation, current: BarObservation) -> int:
    cp, cc = float(previous.close), float(current.close)
    hp, hc = float(previous.high), float(current.high)
    lp, lc = float(previous.low), float(current.low)
    op, oc = float(previous.open), float(current.open)
    long = (
        cp < cc and hp < hc and hp < cc and lp < lc
        and cc - oc > cp - op
        and oc < cc and op < cp
        and hp - cp < cp - op
        and hc - cc < cc - oc
    )
    short = (
        cp > cc and hp > hc and lp > lc and lp > cc
        and oc > cc and op > cp
        and oc - cc > op - cp
        and cp - lp < op - cp
        and cc - lc < oc - cc
    )
    return 1 if long else -1 if short else 0


def _decode_mode(mode: str) -> tuple[str, str]:
    normalized = str(mode).strip().lower().replace("-", "_")
    trigger = "edge" if normalized.startswith("edge_") else "level"
    side = "short" if normalized.endswith("_short") else "long" if normalized.endswith("_long") else "both"
    return trigger, side


def _unresolved(symbol: str, reason: str, episode_ts: int = 0,
                diagnostics: Mapping[str, float | int | str] | None = None) -> RouteDecision:
    return RouteDecision(symbol, UNRESOLVED, 0, 0.0, math.nan, math.nan, math.nan,
                         int(episode_ts), (reason,), dict(diagnostics or {}))


def _flags_at(candles: Sequence[BarObservation], index: int,
              ema_1h: Sequence[float], ema_4h: Sequence[float],
              ema_4h_ts: Sequence[int], rsi: Sequence[float], adx: Sequence[float],
              psar: Sequence[float], volume_mean: Sequence[float]) -> tuple[bool, bool, dict[str, float | int]]:
    current = candles[index]
    previous = candles[index - 1]
    four_index = bisect_right(ema_4h_ts, int(current.ts_event)) - 1
    if four_index < 0:
        return False, False, {"informative_not_ready": 1}
    values = {
        "trend": _trend_flag(previous, current),
        "adx": float(adx[index]),
        "psar": float(psar[index]),
        "ema_1h": float(ema_1h[index]),
        "ema_4h": float(ema_4h[four_index]),
        "rsi": float(rsi[index]),
        "volume": float(current.volume),
        "volume_mean": float(volume_mean[index]),
        "close": float(current.close),
        "informative_ts": int(ema_4h_ts[four_index]),
    }
    if not all(math.isfinite(float(values[key])) for key in ("adx", "psar", "ema_1h", "ema_4h", "rsi", "volume_mean")):
        return False, False, values
    close = float(current.close)
    long = (
        21.0 < float(values["adx"]) < 50.0
        and float(values["psar"]) < close
        and float(values["ema_1h"]) < close
        and float(values["ema_4h"]) < close
        and int(values["trend"]) == 1
        and float(values["rsi"]) > 50.0
        and float(values["volume"]) > float(values["volume_mean"])
    )
    short = (
        13.0 < float(values["adx"]) < 51.0
        and float(values["psar"]) > close
        and float(values["ema_1h"]) > close
        and float(values["ema_4h"]) > close
        and int(values["trend"]) == -1
        and float(values["rsi"]) < 50.0
        and float(values["volume"]) > float(values["volume_mean"])
    )
    return bool(long), bool(short), values


def classify_symbol(symbol: str, bars: Sequence[BarObservation], feature: FeatureObservation,
                    config: RouteConfig = RouteConfig()) -> RouteDecision:
    if not bars:
        return _unresolved(symbol, "NO_MINUTE_BARS")
    latest_ts = int(bars[-1].ts_event)
    if int(feature.observed_time_ns) > latest_ts:
        return _unresolved(symbol, "FUTURE_FEATURE_REJECTED", latest_ts)
    trigger, side_filter = _decode_mode(config.picasso_precedence_mode)
    hourly = _aggregate_complete(bars, 60)
    four_hour = _aggregate_complete(bars, 240)
    if len(hourly) < 145 or len(four_hour) < 50:
        return _unresolved(symbol, "CUSTREND_HISTORY_NOT_READY", latest_ts,
                           {"hourly": len(hourly), "four_hour": len(four_hour)})
    closes = [float(candle.close) for candle in hourly]
    four_closes = [float(candle.close) for candle in four_hour]
    volumes = [float(candle.volume) for candle in hourly]
    pdi, mdi = _directional_indicators(hourly, 14)
    _, adx = _adx_dx(pdi, mdi, 14)
    rsi = _rsi(closes, 14)
    ema_1h = _talib_ema(closes, 142)
    ema_4h = _talib_ema(four_closes, 50)
    ema_4h_ts = [int(candle.ts_event) for candle in four_hour]
    psar = _parabolic_sar(hourly)
    volume_mean = _rolling_shifted(volumes, 22)
    index = len(hourly) - 1
    current_long, current_short, diagnostics = _flags_at(
        hourly, index, ema_1h, ema_4h, ema_4h_ts, rsi, adx, psar, volume_mean
    )
    previous_long, previous_short, _ = _flags_at(
        hourly, index - 1, ema_1h, ema_4h, ema_4h_ts, rsi, adx, psar, volume_mean
    )
    if side_filter == "long":
        current_short = previous_short = False
    elif side_filter == "short":
        current_long = previous_long = False
    long_edge = current_long and not previous_long
    short_edge = current_short and not previous_short
    long_action, short_action = ((long_edge, short_edge) if trigger == "edge" else (current_long, current_short))
    diagnostics.update({
        "source_trigger_mode": trigger,
        "source_side_filter": side_filter,
        "current_long_level": int(current_long),
        "current_short_level": int(current_short),
        "previous_long_level": int(previous_long),
        "previous_short_level": int(previous_short),
        "long_edge": int(long_edge),
        "short_edge": int(short_edge),
        "complete_1h_4h_candles_only": 1,
    })
    if long_action == short_action:
        return _unresolved(symbol, "CUSTREND_NO_SOURCE_" + trigger.upper(),
                           int(hourly[index].ts_event), diagnostics)
    side = 1 if long_action else -1
    entry = float(hourly[index].close)
    leverage = max(float(config.picasso_source_effective_leverage), _EPS)
    stop_fraction = float(config.picasso_source_stoploss) / leverage
    stop = entry * (1.0 - side * stop_fraction)
    objective = entry * (1.0 + side * float(config.picasso_emergency_target_fraction))
    score = 1.0 + min(5.0, abs(float(diagnostics["adx"]) - (21.0 if side > 0 else 13.0)) / 4.0)
    diagnostics.update({
        "source_tag": "CusTrend long" if side > 0 else "CusTrend short",
        "source_effective_leverage": leverage,
        "source_stoploss_profit_ratio": float(config.picasso_source_stoploss),
        "underlying_stop_fraction": stop_fraction,
        "source_trailing_positive": float(config.picasso_trailing_positive),
        "source_trailing_offset": float(config.picasso_trailing_offset),
    })
    return RouteDecision(symbol, CUSTREND_STATE, side, float(score), entry, stop, objective,
                         int(hourly[index].ts_event),
                         ("PUBLIC_CUSTREND_1H_ENTRY", "COMPLETE_DELAYED_4H_EMA", "SOURCE_TRIGGER_" + trigger.upper()),
                         diagnostics)


classify_sma_offset = classify_symbol


def route_universe(bars_by_symbol: Mapping[str, Sequence[BarObservation]],
                   features_by_symbol: Mapping[str, FeatureObservation],
                   config: RouteConfig = RouteConfig()) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    decisions = {symbol: classify_symbol(symbol, bars,
                 features_by_symbol.get(symbol, FeatureObservation(bars[-1].ts_event if bars else 0, ready=True)), config)
                 for symbol, bars in bars_by_symbol.items()}
    actionable = [decision for decision in decisions.values() if decision.actionable]
    actionable.sort(key=lambda item: (-float(item.score), _SYMBOL_PRIORITY.get(item.symbol, 99), int(item.episode_ts)))
    return (actionable[0] if actionable else None), decisions


__all__ = ["BarObservation", "CUSTREND_STATE", "FeatureObservation", "MBE2_STATE", "PICASSO_STATE",
           "RouteConfig", "RouteDecision", "SMA_OFFSET_STATE", "UNRESOLVED", "_aggregate_complete",
           "_adx_dx", "_decode_mode", "_directional_indicators", "_parabolic_sar", "_rolling_shifted",
           "_talib_ema", "_trend_flag", "classify_symbol", "route_universe"]

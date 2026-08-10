"""Causal five-minute Ichimoku fan router mined from the public ichiV2 family.

The public strategy is long-only spot code.  This adapter preserves its completed
candle construction and exposes an explicitly declared mirrored short state for
futures experiments.  Chikou span is never computed or consumed.
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
    _ema_nan,
    _finite,
)

ICHI_STATE = "PUBLIC_ICHI_V2_5M_FAN"
PICASSO_STATE = ICHI_STATE
SMA_OFFSET_STATE = ICHI_STATE


@dataclass(frozen=True, slots=True)
class RouteConfig(_PicassoRouteConfig):
    ichi_trigger_mode: str = "level"
    ichi_side_mode: str = "long"
    ichi_profile: str = "report"
    ichi_shift_inputs_one_candle: bool = True
    ichi_above_cloud_level: int = 1
    ichi_bullish_level: int = 4
    ichi_fan_shift_value: int = 3
    ichi_min_fan_magnitude_gain: float = 1.0013
    ichi_conversion_period: int = 20
    ichi_base_period: int = 60
    ichi_lagging_span_period: int = 120
    ichi_displacement: int = 30
    ichi_stop_fraction: float = 0.04
    ichi_objective_fraction: float = 0.10


_TREND_PERIODS: tuple[tuple[str, int], ...] = (
    ("5m", 1),
    ("15m", 3),
    ("30m", 6),
    ("1h", 12),
    ("2h", 24),
    ("4h", 48),
    ("6h", 72),
    ("8h", 96),
)


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


def _shift_one(values: Sequence[float]) -> list[float]:
    if not values:
        return []
    return [math.nan, *[float(value) for value in values[:-1]]]


def _rolling_mid(
    highs: Sequence[float], lows: Sequence[float], period: int
) -> list[float]:
    output = [math.nan] * len(highs)
    if period <= 0 or len(highs) != len(lows):
        return output
    for index in range(period - 1, len(highs)):
        sample_high = [float(value) for value in highs[index - period + 1 : index + 1]]
        sample_low = [float(value) for value in lows[index - period + 1 : index + 1]]
        if not all(_finite(value) for value in (*sample_high, *sample_low)):
            continue
        output[index] = (max(sample_high) + min(sample_low)) / 2.0
    return output


def _heikin_ashi(
    candles: Sequence[BarObservation],
) -> tuple[list[float], list[float], list[float], list[float]]:
    """Match qtpylib.heikinashi for the source OHLC transformation."""
    if not candles:
        return [], [], [], []
    ha_close = [
        (
            float(candle.open)
            + float(candle.high)
            + float(candle.low)
            + float(candle.close)
        )
        / 4.0
        for candle in candles
    ]
    ha_open = [0.0] * len(candles)
    ha_open[0] = (float(candles[0].open) + float(candles[0].close)) / 2.0
    for index in range(1, len(candles)):
        ha_open[index] = (ha_open[index - 1] + ha_close[index - 1]) / 2.0
    ha_high = [
        max(float(candle.high), ha_open[index], ha_close[index])
        for index, candle in enumerate(candles)
    ]
    ha_low = [
        min(float(candle.low), ha_open[index], ha_close[index])
        for index, candle in enumerate(candles)
    ]
    return ha_open, ha_high, ha_low, ha_close


def _source_arrays(
    candles: Sequence[BarObservation], config: RouteConfig
) -> dict[str, list[float]]:
    closes = [float(candle.close) for candle in candles]
    ha_open, ha_high, ha_low, _ = _heikin_ashi(candles)
    shifted = bool(config.ichi_shift_inputs_one_candle)
    trend_close_5m = _shift_one(closes) if shifted else list(closes)
    trend_open_5m = _shift_one(ha_open) if shifted else list(ha_open)
    cloud_high = _shift_one(ha_high) if shifted else list(ha_high)
    cloud_low = _shift_one(ha_low) if shifted else list(ha_low)

    arrays: dict[str, list[float]] = {
        "raw_close": closes,
        "ha_open": ha_open,
        "ha_high": ha_high,
        "ha_low": ha_low,
        "trend_close_5m": trend_close_5m,
        "trend_open_5m": trend_open_5m,
    }
    for label, period in _TREND_PERIODS:
        if period == 1:
            arrays[f"trend_close_{label}"] = list(trend_close_5m)
            arrays[f"trend_open_{label}"] = list(trend_open_5m)
        else:
            arrays[f"trend_close_{label}"] = _ema_nan(trend_close_5m, period)
            arrays[f"trend_open_{label}"] = _ema_nan(trend_open_5m, period)
    arrays["trend_close_1.5h"] = _ema_nan(trend_close_5m, 18)

    tenkan = _rolling_mid(
        cloud_high, cloud_low, int(config.ichi_conversion_period)
    )
    kijun = _rolling_mid(cloud_high, cloud_low, int(config.ichi_base_period))
    leading_a = [
        (float(left) + float(right)) / 2.0
        if _finite(left) and _finite(right)
        else math.nan
        for left, right in zip(tenkan, kijun)
    ]
    leading_b = _rolling_mid(
        cloud_high, cloud_low, int(config.ichi_lagging_span_period)
    )
    shift = max(0, int(config.ichi_displacement) - 1)
    senkou_a = [math.nan] * len(candles)
    senkou_b = [math.nan] * len(candles)
    for index in range(shift, len(candles)):
        senkou_a[index] = float(leading_a[index - shift])
        senkou_b[index] = float(leading_b[index - shift])
    arrays.update(
        {
            "tenkan_sen": tenkan,
            "kijun_sen": kijun,
            "leading_senkou_span_a": leading_a,
            "leading_senkou_span_b": leading_b,
            "senkou_a": senkou_a,
            "senkou_b": senkou_b,
        }
    )

    fan = [math.nan] * len(candles)
    gain = [math.nan] * len(candles)
    close_1h = arrays["trend_close_1h"]
    close_8h = arrays["trend_close_8h"]
    for index in range(len(candles)):
        denominator = float(close_8h[index])
        numerator = float(close_1h[index])
        if _finite(numerator) and _finite(denominator) and abs(denominator) > 1e-12:
            fan[index] = numerator / denominator
        if index > 0 and _finite(fan[index]) and _finite(fan[index - 1]):
            prior = float(fan[index - 1])
            if abs(prior) > 1e-12:
                gain[index] = float(fan[index]) / prior
    arrays["fan_magnitude"] = fan
    arrays["fan_magnitude_gain"] = gain
    return arrays


def _side_mode(value: str) -> str:
    mode = str(value).strip().lower()
    if mode not in {"long", "short", "both"}:
        raise ValueError(f"unsupported ichi_side_mode={mode!r}")
    return mode


def _trigger_mode(value: str) -> str:
    mode = str(value).strip().lower()
    if mode not in {"level", "edge"}:
        raise ValueError(f"unsupported ichi_trigger_mode={mode!r}")
    return mode


def _signal_at(
    arrays: Mapping[str, Sequence[float]], index: int, config: RouteConfig
) -> tuple[bool, bool, dict[str, float | int | str]]:
    if index < 1:
        return False, False, {}
    cloud_a = float(arrays["senkou_a"][index])
    cloud_b = float(arrays["senkou_b"][index])
    fan = float(arrays["fan_magnitude"][index])
    fan_gain = float(arrays["fan_magnitude_gain"][index])
    required = (cloud_a, cloud_b, fan, fan_gain)
    if not all(_finite(value) for value in required):
        return False, False, {}

    above_level = max(0, min(8, int(config.ichi_above_cloud_level)))
    bullish_level = max(0, min(8, int(config.ichi_bullish_level)))
    long_ok = True
    short_ok = True
    labels = [label for label, _ in _TREND_PERIODS]
    for label in labels[:above_level]:
        value = float(arrays[f"trend_close_{label}"][index])
        if not _finite(value):
            return False, False, {}
        long_ok = long_ok and value > cloud_a and value > cloud_b
        short_ok = short_ok and value < cloud_a and value < cloud_b
    for label in labels[:bullish_level]:
        close_value = float(arrays[f"trend_close_{label}"][index])
        open_value = float(arrays[f"trend_open_{label}"][index])
        if not _finite(close_value) or not _finite(open_value):
            return False, False, {}
        long_ok = long_ok and close_value > open_value
        short_ok = short_ok and close_value < open_value

    gain_threshold = float(config.ichi_min_fan_magnitude_gain)
    if gain_threshold <= 1.0:
        raise ValueError("ichi_min_fan_magnitude_gain must exceed one")
    long_ok = long_ok and fan > 1.0 and fan_gain >= gain_threshold
    short_ok = short_ok and fan < 1.0 and fan_gain <= 1.0 / gain_threshold
    shifts = max(0, int(config.ichi_fan_shift_value))
    for offset in range(1, shifts + 1):
        prior_index = index - offset
        if prior_index < 0:
            return False, False, {}
        prior = float(arrays["fan_magnitude"][prior_index])
        if not _finite(prior):
            return False, False, {}
        long_ok = long_ok and fan > prior
        short_ok = short_ok and fan < prior

    mode = _side_mode(config.ichi_side_mode)
    if mode == "long":
        short_ok = False
    elif mode == "short":
        long_ok = False
    close = float(arrays["raw_close"][index])
    cloud_top = max(cloud_a, cloud_b)
    cloud_bottom = min(cloud_a, cloud_b)
    diagnostics: dict[str, float | int | str] = {
        "raw_close": close,
        "senkou_a": cloud_a,
        "senkou_b": cloud_b,
        "cloud_top": cloud_top,
        "cloud_bottom": cloud_bottom,
        "fan_magnitude": fan,
        "fan_magnitude_gain": fan_gain,
        "trend_close_5m": float(arrays["trend_close_5m"][index]),
        "trend_close_15m": float(arrays["trend_close_15m"][index]),
        "trend_close_30m": float(arrays["trend_close_30m"][index]),
        "trend_close_1h": float(arrays["trend_close_1h"][index]),
        "trend_close_1.5h": float(arrays["trend_close_1.5h"][index]),
        "trend_close_8h": float(arrays["trend_close_8h"][index]),
        "trend_open_5m": float(arrays["trend_open_5m"][index]),
        "trend_open_15m": float(arrays["trend_open_15m"][index]),
        "trend_open_30m": float(arrays["trend_open_30m"][index]),
        "trend_open_1h": float(arrays["trend_open_1h"][index]),
        "source_long_level": int(long_ok),
        "source_short_level": int(short_ok),
        "source_profile": str(config.ichi_profile),
        "source_side_mode": mode,
        "source_above_cloud_level": above_level,
        "source_bullish_level": bullish_level,
        "source_fan_shift_value": shifts,
        "source_min_fan_gain": gain_threshold,
        "source_shift_inputs_one_candle": int(
            bool(config.ichi_shift_inputs_one_candle)
        ),
    }
    return bool(long_ok), bool(short_ok), diagnostics


def route_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    if not feature.ready:
        return _unresolved(symbol, "FEATURE_NOT_READY")
    candles = _aggregate_complete(bars, int(config.picasso_bucket_minutes))
    required = max(
        96 + 2,
        int(config.ichi_lagging_span_period)
        + int(config.ichi_displacement)
        + 2,
    )
    if len(candles) < required:
        return _unresolved(
            symbol,
            "ICHI_SOURCE_WARMUP",
            int(candles[-1].ts_event) if candles else 0,
            {"completed_source_candles": len(candles), "required": required},
        )
    arrays = _source_arrays(candles, config)
    index = len(candles) - 1
    long_level, short_level, diagnostics = _signal_at(arrays, index, config)
    trigger = _trigger_mode(config.ichi_trigger_mode)
    long_action = long_level
    short_action = short_level
    if trigger == "edge":
        prior_long, prior_short, _ = _signal_at(arrays, index - 1, config)
        long_action = long_level and not prior_long
        short_action = short_level and not prior_short
    diagnostics.update(
        {
            "source_trigger_mode": trigger,
            "source_long_action": int(long_action),
            "source_short_action": int(short_action),
        }
    )
    episode_ts = int(candles[-1].ts_event)
    if long_action == short_action:
        reason = (
            "ICHI_SOURCE_NO_SIGNAL"
            if not long_action
            else "ICHI_SOURCE_AMBIGUOUS"
        )
        return _unresolved(symbol, reason, episode_ts, diagnostics)

    side = 1 if long_action else -1
    entry = float(candles[-1].close)
    stop_fraction = float(config.ichi_stop_fraction)
    objective_fraction = float(config.ichi_objective_fraction)
    if not (0.0 < stop_fraction < 1.0 and objective_fraction > 0.0):
        return _unresolved(symbol, "ICHI_INVALID_RISK_GEOMETRY", episode_ts, diagnostics)
    stop = entry * (1.0 - side * stop_fraction)
    objective = entry * (1.0 + side * objective_fraction)
    fan = float(diagnostics["fan_magnitude"])
    fan_gain = float(diagnostics["fan_magnitude_gain"])
    cloud_reference = (
        float(diagnostics["cloud_top"])
        if side > 0
        else float(diagnostics["cloud_bottom"])
    )
    cloud_distance_bps = abs(entry - cloud_reference) / max(entry, 1e-12) * 10_000.0
    fan_distance_bps = abs(fan - 1.0) * 10_000.0
    gain_distance_bps = abs(fan_gain - 1.0) * 10_000.0
    score = cloud_distance_bps + fan_distance_bps + gain_distance_bps
    diagnostics.update(
        {
            "source_side": side,
            "source_score": score,
            "source_stop_fraction": stop_fraction,
            "source_objective_fraction": objective_fraction,
            "source_mirrored_short": int(side < 0),
        }
    )
    return RouteDecision(
        symbol=symbol,
        state=ICHI_STATE,
        side=side,
        score=float(score),
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=objective,
        episode_ts=episode_ts,
        reasons=(
            "PUBLIC_ICHI_V2_LONG" if side > 0 else "MIRRORED_ICHI_V2_SHORT",
            "COMPLETED_5M_ONLY",
            "CHIKOU_NOT_USED",
            "SOURCE_TRIGGER_" + trigger.upper(),
        ),
        diagnostics=diagnostics,
    )


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    decisions = {
        symbol: route_symbol(
            symbol,
            bars_by_symbol[symbol],
            features_by_symbol[symbol],
            config,
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
    "ICHI_STATE",
    "PICASSO_STATE",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "_aggregate_complete",
    "_heikin_ashi",
    "_signal_at",
    "_source_arrays",
    "route_symbol",
    "route_universe",
]

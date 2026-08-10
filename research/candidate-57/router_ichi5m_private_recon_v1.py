"""Causal reconstruction of the private one-minute ``ichi_5m`` family.

Only the indicator footprint and public performance table are known.  This
router therefore exposes a small set of source-consistent structural policies
without pretending to possess the hidden code.  It uses completed one-minute
candles and never computes chikou span.
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
    _atr,
    _ema,
    _finite,
)

ICHI5_STATE = "PRIVATE_ICHI5M_1M_RECON"
PICASSO_STATE = ICHI5_STATE
SMA_OFFSET_STATE = ICHI5_STATE


@dataclass(frozen=True, slots=True)
class RouteConfig(_PicassoRouteConfig):
    ichi5_entry_mode: str = "anchor_cloud"
    ichi5_side_mode: str = "long"
    ichi5_trigger_mode: str = "level"
    ichi5_risk_mode: str = "auction_structure"
    ichi5_min_fan_gain: float = 1.001
    ichi5_fan_shift_value: int = 2
    ichi5_target_fraction: float = 0.010
    ichi5_source_stop_fraction: float = 0.100
    ichi5_structural_lookback: int = 12
    ichi5_atr_period: int = 14
    ichi5_stop_atr_buffer: float = 0.25
    ichi5_min_stop_fraction: float = 0.0015
    ichi5_conversion_period: int = 9
    ichi5_base_period: int = 26
    ichi5_lagging_span_period: int = 52
    ichi5_displacement: int = 26


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


def _rolling_mid(
    highs: Sequence[float], lows: Sequence[float], period: int
) -> list[float]:
    output = [math.nan] * len(highs)
    if period <= 0 or len(highs) != len(lows):
        return output
    for index in range(period - 1, len(highs)):
        sample_high = highs[index - period + 1 : index + 1]
        sample_low = lows[index - period + 1 : index + 1]
        if not all(_finite(value) for value in (*sample_high, *sample_low)):
            continue
        output[index] = (
            max(float(value) for value in sample_high)
            + min(float(value) for value in sample_low)
        ) / 2.0
    return output


def _arrays(
    candles: Sequence[BarObservation], config: RouteConfig
) -> dict[str, list[float]]:
    opens = [float(candle.open) for candle in candles]
    highs = [float(candle.high) for candle in candles]
    lows = [float(candle.low) for candle in candles]
    closes = [float(candle.close) for candle in candles]
    result: dict[str, list[float]] = {
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "ema_close_12": _ema(closes, 12),
        "ema_close_24": _ema(closes, 24),
        "ema_close_48": _ema(closes, 48),
        "ema_close_96": _ema(closes, 96),
        "ema_open_48": _ema(opens, 48),
        "atr": _atr(candles, int(config.ichi5_atr_period)),
    }

    tenkan = _rolling_mid(
        highs, lows, int(config.ichi5_conversion_period)
    )
    kijun = _rolling_mid(highs, lows, int(config.ichi5_base_period))
    leading_a = [
        (float(left) + float(right)) / 2.0
        if _finite(left) and _finite(right)
        else math.nan
        for left, right in zip(tenkan, kijun)
    ]
    leading_b = _rolling_mid(
        highs, lows, int(config.ichi5_lagging_span_period)
    )
    shift = max(0, int(config.ichi5_displacement) - 1)
    senkou_a = [math.nan] * len(candles)
    senkou_b = [math.nan] * len(candles)
    for index in range(shift, len(candles)):
        senkou_a[index] = float(leading_a[index - shift])
        senkou_b[index] = float(leading_b[index - shift])
    result.update(
        {
            "senkou_span_a": leading_a,
            "senkou_span_b": leading_b,
            "senkou_a": senkou_a,
            "senkou_b": senkou_b,
        }
    )

    fan = [math.nan] * len(candles)
    gain = [math.nan] * len(candles)
    ema48 = result["ema_close_48"]
    ema96 = result["ema_close_96"]
    for index in range(len(candles)):
        numerator = float(ema48[index])
        denominator = float(ema96[index])
        if _finite(numerator) and _finite(denominator) and abs(denominator) > 1e-12:
            fan[index] = numerator / denominator
        if index > 0 and _finite(fan[index]) and _finite(fan[index - 1]):
            prior = float(fan[index - 1])
            if abs(prior) > 1e-12:
                gain[index] = float(fan[index]) / prior
    result["fan_magnitude"] = fan
    result["fan_magnitude_gain"] = gain
    return result


def _mode(value: str, allowed: set[str], name: str) -> str:
    normalized = str(value).strip().lower()
    if normalized not in allowed:
        raise ValueError(f"unsupported {name}={normalized!r}")
    return normalized


def _signal_at(
    arrays: Mapping[str, Sequence[float]], index: int, config: RouteConfig
) -> tuple[bool, bool, dict[str, float | int | str]]:
    if index < max(96, int(config.ichi5_fan_shift_value)):
        return False, False, {}
    names = (
        "close",
        "ema_close_12",
        "ema_close_24",
        "ema_close_48",
        "ema_close_96",
        "ema_open_48",
        "senkou_a",
        "senkou_b",
        "fan_magnitude",
        "fan_magnitude_gain",
        "atr",
    )
    values = {name: float(arrays[name][index]) for name in names}
    if not all(_finite(value) for value in values.values()):
        return False, False, {}

    entry_mode = _mode(
        config.ichi5_entry_mode,
        {"anchor_cloud", "ordered_cloud", "fast_cloud", "ordered_no_cloud"},
        "ichi5_entry_mode",
    )
    side_mode = _mode(
        config.ichi5_side_mode, {"long", "short", "both"}, "ichi5_side_mode"
    )
    cloud_top = max(values["senkou_a"], values["senkou_b"])
    cloud_bottom = min(values["senkou_a"], values["senkou_b"])
    fan = values["fan_magnitude"]
    fan_gain = values["fan_magnitude_gain"]
    threshold = float(config.ichi5_min_fan_gain)
    if threshold <= 1.0:
        raise ValueError("ichi5_min_fan_gain must exceed one")

    cloud_long = values["ema_close_12"] > cloud_top
    cloud_short = values["ema_close_12"] < cloud_bottom
    anchor_long = (
        values["ema_close_12"] > values["ema_open_48"]
        and values["ema_close_24"] > values["ema_open_48"]
        and values["ema_close_48"] > values["ema_open_48"]
    )
    anchor_short = (
        values["ema_close_12"] < values["ema_open_48"]
        and values["ema_close_24"] < values["ema_open_48"]
        and values["ema_close_48"] < values["ema_open_48"]
    )
    fast_long = (
        values["ema_close_12"] > values["ema_open_48"]
        and values["ema_close_24"] > values["ema_open_48"]
    )
    fast_short = (
        values["ema_close_12"] < values["ema_open_48"]
        and values["ema_close_24"] < values["ema_open_48"]
    )
    ordered_long = (
        values["ema_close_12"]
        > values["ema_close_24"]
        > values["ema_close_48"]
        > values["ema_close_96"]
    )
    ordered_short = (
        values["ema_close_12"]
        < values["ema_close_24"]
        < values["ema_close_48"]
        < values["ema_close_96"]
    )

    if entry_mode == "anchor_cloud":
        context_long, context_short = cloud_long and anchor_long, cloud_short and anchor_short
    elif entry_mode == "ordered_cloud":
        context_long, context_short = cloud_long and anchor_long and ordered_long, cloud_short and anchor_short and ordered_short
    elif entry_mode == "fast_cloud":
        context_long, context_short = cloud_long and fast_long, cloud_short and fast_short
    else:
        context_long, context_short = ordered_long and anchor_long, ordered_short and anchor_short

    long_ok = context_long and fan > 1.0 and fan_gain >= threshold
    short_ok = context_short and fan < 1.0 and fan_gain <= 1.0 / threshold
    shift_value = max(0, int(config.ichi5_fan_shift_value))
    for offset in range(1, shift_value + 1):
        prior = float(arrays["fan_magnitude"][index - offset])
        if not _finite(prior):
            return False, False, {}
        long_ok = long_ok and fan > prior
        short_ok = short_ok and fan < prior
    if side_mode == "long":
        short_ok = False
    elif side_mode == "short":
        long_ok = False

    diagnostics: dict[str, float | int | str] = {
        **values,
        "cloud_top": cloud_top,
        "cloud_bottom": cloud_bottom,
        "source_entry_mode": entry_mode,
        "source_side_mode": side_mode,
        "source_cloud_long": int(cloud_long),
        "source_cloud_short": int(cloud_short),
        "source_anchor_long": int(anchor_long),
        "source_anchor_short": int(anchor_short),
        "source_fast_long": int(fast_long),
        "source_fast_short": int(fast_short),
        "source_ordered_long": int(ordered_long),
        "source_ordered_short": int(ordered_short),
        "source_long_level": int(long_ok),
        "source_short_level": int(short_ok),
        "source_min_fan_gain": threshold,
        "source_fan_shift_value": shift_value,
    }
    return bool(long_ok), bool(short_ok), diagnostics


def _geometry(
    candles: Sequence[BarObservation],
    arrays: Mapping[str, Sequence[float]],
    index: int,
    side: int,
    config: RouteConfig,
) -> tuple[float, float, dict[str, float | int | str]]:
    entry = float(candles[index].close)
    risk_mode = _mode(
        config.ichi5_risk_mode,
        {"source_fraction", "auction_structure"},
        "ichi5_risk_mode",
    )
    target_fraction = float(config.ichi5_target_fraction)
    if target_fraction <= 0.0:
        raise ValueError("ichi5_target_fraction must be positive")
    if risk_mode == "source_fraction":
        stop_fraction = float(config.ichi5_source_stop_fraction)
        if not 0.0 < stop_fraction < 1.0:
            raise ValueError("ichi5_source_stop_fraction must be in (0,1)")
        stop = entry * (1.0 - side * stop_fraction)
        anchor = stop
        buffer = 0.0
    else:
        lookback = max(2, int(config.ichi5_structural_lookback))
        recent = candles[max(0, index - lookback + 1) : index + 1]
        atr = float(arrays["atr"][index])
        minimum = entry * float(config.ichi5_min_stop_fraction)
        buffer = max(
            atr * float(config.ichi5_stop_atr_buffer),
            entry * 0.0002,
        )
        ema_open = float(arrays["ema_open_48"][index])
        cloud_top = max(
            float(arrays["senkou_a"][index]),
            float(arrays["senkou_b"][index]),
        )
        cloud_bottom = min(
            float(arrays["senkou_a"][index]),
            float(arrays["senkou_b"][index]),
        )
        if side > 0:
            recent_extreme = min(float(candle.low) for candle in recent)
            candidates = [value for value in (recent_extreme, ema_open, cloud_bottom) if 0.0 < value < entry]
            anchor = max(candidates) if candidates else recent_extreme
            stop = min(anchor - buffer, entry - minimum)
        else:
            recent_extreme = max(float(candle.high) for candle in recent)
            candidates = [value for value in (recent_extreme, ema_open, cloud_top) if value > entry]
            anchor = min(candidates) if candidates else recent_extreme
            stop = max(anchor + buffer, entry + minimum)
    target = entry * (1.0 + side * target_fraction)
    return stop, target, {
        "source_risk_mode": risk_mode,
        "source_stop_anchor": anchor,
        "source_stop_buffer": buffer,
        "source_stop_fraction": abs(entry - stop) / entry,
        "source_target_fraction": target_fraction,
    }


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
        100,
        int(config.ichi5_lagging_span_period)
        + int(config.ichi5_displacement)
        + 2,
    )
    if len(candles) < required:
        return _unresolved(
            symbol,
            "ICHI5_RECON_WARMUP",
            int(candles[-1].ts_event) if candles else 0,
            {"completed_candles": len(candles), "required": required},
        )
    arrays = _arrays(candles, config)
    index = len(candles) - 1
    long_level, short_level, diagnostics = _signal_at(arrays, index, config)
    trigger = _mode(
        config.ichi5_trigger_mode, {"level", "edge"}, "ichi5_trigger_mode"
    )
    long_action, short_action = long_level, short_level
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
        return _unresolved(
            symbol,
            "ICHI5_RECON_NO_SIGNAL" if not long_action else "ICHI5_RECON_AMBIGUOUS",
            episode_ts,
            diagnostics,
        )
    side = 1 if long_action else -1
    entry = float(candles[-1].close)
    stop, target, geometry = _geometry(candles, arrays, index, side, config)
    valid = (
        0.0 < stop < entry < target
        if side > 0
        else 0.0 < target < entry < stop
    )
    if not valid:
        return _unresolved(
            symbol,
            "ICHI5_RECON_INVALID_GEOMETRY",
            episode_ts,
            {**diagnostics, **geometry},
        )
    cloud_reference = (
        float(diagnostics["cloud_top"])
        if side > 0
        else float(diagnostics["cloud_bottom"])
    )
    cloud_bps = abs(entry - cloud_reference) / entry * 10_000.0
    fan_bps = abs(float(diagnostics["fan_magnitude"]) - 1.0) * 10_000.0
    gain_bps = abs(float(diagnostics["fan_magnitude_gain"]) - 1.0) * 10_000.0
    ema_spread_bps = abs(
        float(diagnostics["ema_close_12"])
        - float(diagnostics["ema_close_48"])
    ) / entry * 10_000.0
    score = cloud_bps + fan_bps + gain_bps + ema_spread_bps
    diagnostics.update(
        {
            **geometry,
            "source_side": side,
            "source_score": score,
            "source_hidden_code_available": 0,
            "source_chikou_used": 0,
        }
    )
    return RouteDecision(
        symbol=symbol,
        state=ICHI5_STATE,
        side=side,
        score=float(score),
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=target,
        episode_ts=episode_ts,
        reasons=(
            "PRIVATE_ICHI5M_INDICATOR_FOOTPRINT_RECON",
            "COMPLETED_ONE_MINUTE_ONLY",
            "SOURCE_TRIGGER_" + trigger.upper(),
            "RISK_MODE_" + str(config.ichi5_risk_mode).upper(),
            "CHIKOU_NOT_USED",
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
    "ICHI5_STATE",
    "PICASSO_STATE",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "_aggregate_complete",
    "_arrays",
    "_signal_at",
    "route_symbol",
    "route_universe",
]

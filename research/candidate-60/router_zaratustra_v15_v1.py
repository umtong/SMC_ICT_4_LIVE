"""Causal one-slot adapter for public ZaratustraV15.

The external source supplies two five-minute entry families:

* directional-flow state: DMI/DX + OBV + MFI + absolute ATR guard;
* Bollinger upper/lower breakout crossings.

All indicators use completed candles.  Freqtrade's documented collision rule is
preserved: a candle carrying both long and short entries is unresolved.  Source
mode keeps the DI family as a level condition; edge mode is a diagnostic that
counts only false-to-true directional episodes.  No outcome-tuned threshold or
post-entry information is used.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence

import router_trendrider_base as trend
from router_picasso import (
    BarObservation,
    FeatureObservation,
    RouteConfig as _BaseRouteConfig,
    RouteDecision,
    UNRESOLVED,
    _SYMBOL_PRIORITY,
    _aggregate_complete,
    _atr,
    _finite,
)

Z15_STATE = "PUBLIC_ZARATUSTRA_V15"
PICASSO_STATE = Z15_STATE
SMA_OFFSET_STATE = Z15_STATE
_EPS = 1e-12
_ALLOWED_FAMILIES = {"combined", "di", "bb"}
_ALLOWED_TRIGGERS = {"source", "edge"}


@dataclass(frozen=True, slots=True)
class RouteConfig(_BaseRouteConfig):
    z15_family: str = "combined"
    z15_trigger_mode: str = "source"
    z15_dmi_period: int = 14
    z15_mfi_period: int = 14
    z15_atr_period: int = 14
    z15_bb_period: int = 20
    z15_bb_stds: float = 2.0
    z15_mfi_midpoint: float = 50.0
    z15_atr_absolute_max: float = 0.2
    z15_stop_fraction: float = 0.015
    z15_emergency_objective_fraction: float = 0.20


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


def _mfi(candles: Sequence[BarObservation], period: int) -> list[float]:
    result = [math.nan] * len(candles)
    if period <= 0 or len(candles) <= period:
        return result
    typical = [
        (float(item.high) + float(item.low) + float(item.close)) / 3.0
        for item in candles
    ]
    positive = [0.0] * len(candles)
    negative = [0.0] * len(candles)
    for index in range(1, len(candles)):
        flow = typical[index] * max(0.0, float(candles[index].volume))
        if typical[index] > typical[index - 1]:
            positive[index] = flow
        elif typical[index] < typical[index - 1]:
            negative[index] = flow
    for index in range(period, len(candles)):
        first = index - period + 1
        pos = sum(positive[first : index + 1])
        neg = sum(negative[first : index + 1])
        if neg <= _EPS:
            result[index] = 100.0 if pos > _EPS else 50.0
        else:
            result[index] = 100.0 - 100.0 / (1.0 + pos / neg)
    return result


def _sample_std(values: Sequence[float], period: int) -> list[float]:
    result = [math.nan] * len(values)
    if period <= 1:
        return result
    for index in range(period - 1, len(values)):
        sample = [float(value) for value in values[index - period + 1 : index + 1]]
        mean = sum(sample) / period
        result[index] = math.sqrt(
            sum((value - mean) ** 2 for value in sample) / (period - 1)
        )
    return result


def _arrays(
    candles: Sequence[BarObservation], config: RouteConfig
) -> dict[str, list[float]]:
    adx, plus_di, minus_di = trend._dmi(candles, int(config.z15_dmi_period))
    dx = [
        (
            100.0 * abs(float(plus) - float(minus))
            / max(float(plus) + float(minus), _EPS)
            if _finite(plus) and _finite(minus)
            else math.nan
        )
        for plus, minus in zip(plus_di, minus_di, strict=True)
    ]
    typical = [
        (float(item.high) + float(item.low) + float(item.close)) / 3.0
        for item in candles
    ]
    period = int(config.z15_bb_period)
    middle = [math.nan] * len(typical)
    for index in range(period - 1, len(typical)):
        middle[index] = sum(typical[index - period + 1 : index + 1]) / period
    deviation = _sample_std(typical, period)
    stds = float(config.z15_bb_stds)
    upper = [
        float(mid) + stds * float(dev)
        if _finite(mid) and _finite(dev)
        else math.nan
        for mid, dev in zip(middle, deviation, strict=True)
    ]
    lower = [
        float(mid) - stds * float(dev)
        if _finite(mid) and _finite(dev)
        else math.nan
        for mid, dev in zip(middle, deviation, strict=True)
    ]
    return {
        "adx": adx,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "dx": dx,
        "atr": _atr(candles, int(config.z15_atr_period)),
        "obv": trend._obv(candles),
        "mfi": _mfi(candles, int(config.z15_mfi_period)),
        "bb_middle": middle,
        "bb_upper": upper,
        "bb_lower": lower,
    }


def _flags_at(
    candles: Sequence[BarObservation],
    arrays: Mapping[str, Sequence[float]],
    index: int,
    config: RouteConfig,
) -> tuple[bool, bool, dict[str, float | int | str]]:
    if index <= 0:
        return False, False, {}
    close = float(candles[index].close)
    prior_close = float(candles[index - 1].close)
    values = {
        name: float(series[index])
        for name, series in arrays.items()
    }
    prior_upper = float(arrays["bb_upper"][index - 1])
    prior_lower = float(arrays["bb_lower"][index - 1])
    if not all(
        _finite(value)
        for value in (
            values["adx"],
            values["plus_di"],
            values["minus_di"],
            values["dx"],
            values["atr"],
            values["mfi"],
            values["bb_upper"],
            values["bb_lower"],
            prior_upper,
            prior_lower,
        )
    ):
        return False, False, {}
    obv = values["obv"]
    prior_obv = float(arrays["obv"][index - 1])
    mfi_mid = float(config.z15_mfi_midpoint)
    atr_max = float(config.z15_atr_absolute_max)
    di_long = (
        values["dx"] > values["minus_di"]
        and values["adx"] > values["minus_di"]
        and values["plus_di"] > values["minus_di"]
        and obv > prior_obv
        and values["mfi"] > mfi_mid
        and values["atr"] < atr_max
    )
    di_short = (
        values["dx"] > values["plus_di"]
        and values["adx"] > values["plus_di"]
        and values["minus_di"] > values["plus_di"]
        and obv < prior_obv
        and values["mfi"] < mfi_mid
        and values["atr"] < atr_max
    )
    bb_long = close > values["bb_upper"] and prior_close <= prior_upper
    bb_short = close < values["bb_lower"] and prior_close >= prior_lower
    family = str(config.z15_family).strip().lower()
    if family not in _ALLOWED_FAMILIES:
        raise ValueError(f"unsupported z15_family={family!r}")
    long_ok = bb_long if family == "bb" else di_long if family == "di" else di_long or bb_long
    short_ok = bb_short if family == "bb" else di_short if family == "di" else di_short or bb_short
    diagnostics: dict[str, float | int | str] = {
        "source_family": family,
        "source_di_long": int(di_long),
        "source_di_short": int(di_short),
        "source_bb_long": int(bb_long),
        "source_bb_short": int(bb_short),
        "source_long_level": int(long_ok),
        "source_short_level": int(short_ok),
        "source_close_5m": close,
        "source_adx_5m": values["adx"],
        "source_dx_5m": values["dx"],
        "source_plus_di_5m": values["plus_di"],
        "source_minus_di_5m": values["minus_di"],
        "source_atr_5m": values["atr"],
        "source_obv_5m": obv,
        "source_obv_prior_5m": prior_obv,
        "source_mfi_5m": values["mfi"],
        "source_bb_middle_5m": values["bb_middle"],
        "source_bb_upper_5m": values["bb_upper"],
        "source_bb_lower_5m": values["bb_lower"],
        "source_atr_absolute_max": atr_max,
        "source_mfi_midpoint": mfi_mid,
    }
    return bool(long_ok), bool(short_ok), diagnostics


def route_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    latest_ts = int(bars[-1].ts_event) if bars else 0
    if not bars:
        return _unresolved(symbol, "Z15_NO_MINUTE_BARS")
    if int(feature.observed_time_ns) > latest_ts:
        return _unresolved(symbol, "Z15_FUTURE_FEATURE_REJECTED", latest_ts)
    if not feature.ready:
        return _unresolved(symbol, "Z15_FEATURE_NOT_READY", latest_ts)
    candles = _aggregate_complete(bars, 5)
    required = max(
        int(config.z15_dmi_period) * 2 + 3,
        int(config.z15_mfi_period) + 3,
        int(config.z15_atr_period) + 3,
        int(config.z15_bb_period) + 3,
    )
    if len(candles) < required:
        return _unresolved(symbol, "Z15_SOURCE_WARMUP", latest_ts)
    arrays = _arrays(candles, config)
    long_level, short_level, diagnostics = _flags_at(
        candles, arrays, len(candles) - 1, config
    )
    if not diagnostics:
        return _unresolved(symbol, "Z15_SOURCE_WARMUP", latest_ts)
    trigger = str(config.z15_trigger_mode).strip().lower()
    if trigger not in _ALLOWED_TRIGGERS:
        raise ValueError(f"unsupported z15_trigger_mode={trigger!r}")
    long_action = long_level
    short_action = short_level
    if trigger == "edge":
        prior_long, prior_short, _ = _flags_at(
            candles, arrays, len(candles) - 2, config
        )
        long_action = long_level and not prior_long
        short_action = short_level and not prior_short
    diagnostics.update(
        {
            "source_trigger_mode": trigger,
            "source_long_action": int(long_action),
            "source_short_action": int(short_action),
            "source_collision_rule": "freqtrade_ignore_long_short_collision",
            "source_thresholds_searched": 0,
        }
    )
    episode_ts = int(candles[-1].ts_event)
    if long_action and short_action:
        return _unresolved(symbol, "Z15_SOURCE_DIRECTION_COLLISION", episode_ts, diagnostics)
    if not long_action and not short_action:
        return _unresolved(symbol, "Z15_SOURCE_NO_SIGNAL", episode_ts, diagnostics)
    side = 1 if long_action else -1
    entry = float(candles[-1].close)
    stop_fraction = float(config.z15_stop_fraction)
    objective_fraction = float(config.z15_emergency_objective_fraction)
    if not (0.0 < stop_fraction < 1.0 and objective_fraction > 0.0):
        raise ValueError("invalid ZaratustraV15 geometry")
    stop = entry * (1.0 - side * stop_fraction)
    target = entry * (1.0 + side * objective_fraction)
    diagnostics.update(
        {
            "source_side": side,
            "source_score": 0.0,
            "source_arbitration": "deterministic_symbol_priority",
            "source_effective_leverage": 10.0,
            "source_stoploss_profit_ratio": 0.15,
            "source_stop_fraction_underlying": stop_fraction,
            "source_trailing_activation_underlying": 0.0107,
            "source_trailing_distance_underlying": 0.0012,
            "source_emergency_objective_fraction": objective_fraction,
        }
    )
    branch = (
        "DI_AND_BB"
        if diagnostics[f"source_di_{'long' if side > 0 else 'short'}"]
        and diagnostics[f"source_bb_{'long' if side > 0 else 'short'}"]
        else "DI"
        if diagnostics[f"source_di_{'long' if side > 0 else 'short'}"]
        else "BB"
    )
    diagnostics["source_selected_branch"] = branch
    return RouteDecision(
        symbol=symbol,
        state=Z15_STATE,
        side=side,
        score=0.0,
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=target,
        episode_ts=episode_ts,
        reasons=(
            "PUBLIC_ZARATUSTRA_V15_LONG" if side > 0 else "PUBLIC_ZARATUSTRA_V15_SHORT",
            "SOURCE_BRANCH_" + branch,
            "SOURCE_TRIGGER_" + trigger.upper(),
            "COMPLETED_5M",
            "SOURCE_LEVERAGE_NORMALIZED_TO_UNDERLYING",
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
    "UNRESOLVED",
    "Z15_STATE",
    "route_symbol",
    "route_universe",
]

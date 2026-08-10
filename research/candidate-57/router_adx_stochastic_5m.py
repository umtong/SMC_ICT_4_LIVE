"""Causal entry router for the public ADXStochastic five-minute strategy."""
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
    _adx,
    _aggregate_complete,
    _atr,
    _finite,
    _sma,
)

ADX_STOCH_STATE = "PUBLIC_ADX_STOCHASTIC_5M"
PICASSO_STATE = ADX_STOCH_STATE
SMA_OFFSET_STATE = ADX_STOCH_STATE


@dataclass(frozen=True, slots=True)
class RouteConfig(_PicassoRouteConfig):
    adxstoch_risk_mode: str = "source_fraction"
    adxstoch_adx_period: int = 14
    adxstoch_fastk_period: int = 5
    adxstoch_fastd_period: int = 3
    adxstoch_entry_adx: float = 50.0
    adxstoch_entry_stoch: float = 20.0
    adxstoch_source_stop_fraction: float = 0.10 / 9.0
    adxstoch_target_fraction: float = 0.05
    adxstoch_structural_lookback_5m: int = 8
    adxstoch_atr_period_5m: int = 14
    adxstoch_stop_atr_buffer: float = 0.25
    adxstoch_min_stop_fraction: float = 0.0015


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


def _fast_stochastic(
    candles: Sequence[BarObservation], fastk_period: int, fastd_period: int
) -> tuple[list[float], list[float]]:
    size = len(candles)
    fastk = [math.nan] * size
    if fastk_period <= 0 or fastd_period <= 0:
        return fastk, [math.nan] * size
    for index in range(fastk_period - 1, size):
        sample = candles[index - fastk_period + 1 : index + 1]
        highest = max(float(candle.high) for candle in sample)
        lowest = min(float(candle.low) for candle in sample)
        denominator = highest - lowest
        if denominator > 1e-12:
            fastk[index] = 100.0 * (float(candles[index].close) - lowest) / denominator
        else:
            fastk[index] = 50.0
    fastd = _sma(fastk, fastd_period)
    return fastk, fastd


def _entry_snapshot(
    bars: Sequence[BarObservation], config: RouteConfig
) -> tuple[bool, dict[str, float | int | str]]:
    candles = _aggregate_complete(bars, 5)
    minimum = max(
        int(config.adxstoch_adx_period) * 2 + 3,
        int(config.adxstoch_fastk_period) + int(config.adxstoch_fastd_period) + 3,
    )
    if len(candles) < minimum:
        return False, {}
    adx = _adx(candles, int(config.adxstoch_adx_period))
    fastk, fastd = _fast_stochastic(
        candles,
        int(config.adxstoch_fastk_period),
        int(config.adxstoch_fastd_period),
    )
    index = len(candles) - 1
    previous = index - 1
    values = (
        float(adx[index]),
        float(fastk[index]),
        float(fastd[index]),
        float(fastk[previous]),
        float(fastd[previous]),
    )
    if not all(_finite(value) for value in values):
        return False, {}
    crossed = float(fastk[index]) > float(fastd[index]) and float(fastk[previous]) <= float(fastd[previous])
    signal = (
        float(adx[index]) > float(config.adxstoch_entry_adx)
        and float(fastk[previous]) < float(config.adxstoch_entry_stoch)
        and float(fastd[previous]) < float(config.adxstoch_entry_stoch)
        and crossed
        and float(candles[index].volume) > 0.0
    )
    diagnostics: dict[str, float | int | str] = {
        "close_5m": float(candles[index].close),
        "adx_5m": float(adx[index]),
        "fastk_5m": float(fastk[index]),
        "fastd_5m": float(fastd[index]),
        "previous_fastk_5m": float(fastk[previous]),
        "previous_fastd_5m": float(fastd[previous]),
        "stochastic_crossed_above": int(crossed),
        "source_entry_signal": int(signal),
        "source_entry_adx_threshold": float(config.adxstoch_entry_adx),
        "source_entry_stoch_threshold": float(config.adxstoch_entry_stoch),
        "source_ts_5m": int(candles[index].ts_event),
    }
    return bool(signal), diagnostics


def _geometry(
    bars: Sequence[BarObservation], config: RouteConfig
) -> tuple[float, float, dict[str, float | int | str]]:
    candles = _aggregate_complete(bars, 5)
    entry = float(candles[-1].close)
    risk_mode = str(config.adxstoch_risk_mode).strip().lower()
    if risk_mode == "source_fraction":
        fraction = float(config.adxstoch_source_stop_fraction)
        if not 0.0 < fraction < 1.0:
            raise ValueError("invalid adxstoch source stop fraction")
        stop = entry * (1.0 - fraction)
        anchor = stop
        buffer = 0.0
    elif risk_mode == "auction_structure":
        lookback = max(2, int(config.adxstoch_structural_lookback_5m))
        recent = candles[-lookback:]
        anchor = min(float(candle.low) for candle in recent)
        atr = float(_atr(candles, int(config.adxstoch_atr_period_5m))[-1])
        buffer = max(
            atr * float(config.adxstoch_stop_atr_buffer),
            entry * 0.0002,
        )
        minimum = entry * float(config.adxstoch_min_stop_fraction)
        stop = min(anchor - buffer, entry - minimum)
    else:
        raise ValueError(f"unsupported adxstoch_risk_mode={risk_mode!r}")
    target_fraction = float(config.adxstoch_target_fraction)
    if target_fraction <= 0.0:
        raise ValueError("adxstoch target fraction must be positive")
    target = entry * (1.0 + target_fraction)
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
    signal, diagnostics = _entry_snapshot(bars, config)
    episode_ts = int(bars[-1].ts_event) if bars else 0
    if not diagnostics:
        return _unresolved(symbol, "ADXSTOCH_SOURCE_WARMUP", episode_ts)
    if not signal:
        return _unresolved(symbol, "ADXSTOCH_SOURCE_NO_SIGNAL", episode_ts, diagnostics)
    candles = _aggregate_complete(bars, 5)
    entry = float(candles[-1].close)
    stop, target, geometry = _geometry(bars, config)
    if not 0.0 < stop < entry < target:
        return _unresolved(
            symbol,
            "ADXSTOCH_INVALID_GEOMETRY",
            episode_ts,
            {**diagnostics, **geometry},
        )
    score = (
        max(0.0, float(diagnostics["adx_5m"]) - float(config.adxstoch_entry_adx))
        + max(0.0, float(config.adxstoch_entry_stoch) - float(diagnostics["previous_fastk_5m"]))
        + max(0.0, float(config.adxstoch_entry_stoch) - float(diagnostics["previous_fastd_5m"]))
        + max(0.0, float(diagnostics["fastk_5m"]) - float(diagnostics["fastd_5m"]))
    )
    diagnostics.update(
        {
            **geometry,
            "source_side": 1,
            "source_score": score,
            "source_effective_leverage": 9.0,
            "source_stoploss_profit_ratio": 0.10,
            "source_roi_0_underlying": 0.04 / 9.0,
            "source_roi_30_underlying": 0.02 / 9.0,
            "source_roi_60_underlying": 0.01 / 9.0,
        }
    )
    return RouteDecision(
        symbol=symbol,
        state=ADX_STOCH_STATE,
        side=1,
        score=float(score),
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=target,
        episode_ts=episode_ts,
        reasons=(
            "PUBLIC_ADX_STOCHASTIC_LONG",
            "COMPLETED_5M_CROSS",
            "SOURCE_LEVERAGE_NORMALIZED_TO_UNDERLYING",
            "RISK_MODE_" + str(config.adxstoch_risk_mode).upper(),
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
    "ADX_STOCH_STATE",
    "BarObservation",
    "FeatureObservation",
    "PICASSO_STATE",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "_aggregate_complete",
    "_entry_snapshot",
    "_fast_stochastic",
    "_geometry",
    "route_symbol",
    "route_universe",
]

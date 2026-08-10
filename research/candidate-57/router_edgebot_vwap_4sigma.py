"""Causal reconstruction of EdgeBot's public 4σ rolling-VWAP mean reversion."""
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
    _finite,
)

EDGE_STATE = "PUBLIC_EDGEBOT_VWAP_4SIGMA"
PICASSO_STATE = EDGE_STATE
SMA_OFFSET_STATE = EDGE_STATE


@dataclass(frozen=True, slots=True)
class RouteConfig(_PicassoRouteConfig):
    edge_signal_mode: str = "weighted_band"
    edge_scope: str = "all"
    edge_mean_exit_mode: str = "static_entry_mean"
    edge_risk_mode: str = "six_sigma"
    edge_vwap_period: int = 20
    edge_entry_sigma: float = 4.0
    edge_stop_sigma: float = 6.0
    edge_residual_window: int = 20
    edge_atr_period: int = 14
    edge_stop_atr_buffer: float = 0.25
    edge_min_stop_fraction: float = 0.0015
    edge_dynamic_emergency_target_fraction: float = 0.20


def _unresolved(symbol: str, reason: str, episode_ts: int = 0,
                diagnostics: Mapping[str, float | int | str] | None = None) -> RouteDecision:
    return RouteDecision(
        symbol=symbol, state=UNRESOLVED, side=0, score=0.0,
        entry_reference=math.nan, stop_reference=math.nan,
        objective_reference=math.nan, episode_ts=int(episode_ts),
        reasons=(reason,), diagnostics=dict(diagnostics or {}),
    )


def _rolling_vwap(values: Sequence[float], volumes: Sequence[float], period: int) -> list[float]:
    result = [math.nan] * len(values)
    if period <= 0 or len(values) != len(volumes):
        return result
    pv = 0.0
    vv = 0.0
    for index, (price, volume) in enumerate(zip(values, volumes)):
        weight = max(0.0, float(volume))
        pv += float(price) * weight
        vv += weight
        if index >= period:
            old_weight = max(0.0, float(volumes[index - period]))
            pv -= float(values[index - period]) * old_weight
            vv -= old_weight
        if index >= period - 1 and vv > 1e-12:
            result[index] = pv / vv
    return result


def _weighted_sigma(values: Sequence[float], volumes: Sequence[float], period: int) -> list[float]:
    result = [math.nan] * len(values)
    if period <= 0 or len(values) != len(volumes):
        return result
    pv = p2v = vv = 0.0
    for index, (price, volume) in enumerate(zip(values, volumes)):
        p = float(price)
        w = max(0.0, float(volume))
        pv += p * w
        p2v += p * p * w
        vv += w
        if index >= period:
            old_p = float(values[index - period])
            old_w = max(0.0, float(volumes[index - period]))
            pv -= old_p * old_w
            p2v -= old_p * old_p * old_w
            vv -= old_w
        if index >= period - 1 and vv > 1e-12:
            mean = pv / vv
            variance = max(0.0, p2v / vv - mean * mean)
            result[index] = math.sqrt(variance)
    return result


def _prior_residual_sigma(residuals: Sequence[float], window: int) -> list[float]:
    result = [math.nan] * len(residuals)
    if window <= 1:
        return result
    for index in range(window, len(residuals)):
        sample = [float(value) for value in residuals[index - window:index] if _finite(value)]
        if len(sample) != window:
            continue
        mean = sum(sample) / window
        variance = sum((value - mean) ** 2 for value in sample) / window
        if variance > 1e-24:
            result[index] = math.sqrt(variance)
    return result


def _source_arrays(candles: Sequence[BarObservation], config: RouteConfig) -> dict[str, list[float]]:
    prices = [
        (float(candle.high) + float(candle.low) + float(candle.close)) / 3.0
        for candle in candles
    ]
    volumes = [max(0.0, float(candle.volume)) for candle in candles]
    vwap = _rolling_vwap(prices, volumes, int(config.edge_vwap_period))
    residual = [
        float(price) - float(mean) if _finite(mean) else math.nan
        for price, mean in zip(prices, vwap)
    ]
    weighted = _weighted_sigma(prices, volumes, int(config.edge_vwap_period))
    prior = _prior_residual_sigma(residual, int(config.edge_residual_window))
    return {
        "price": prices,
        "volume": volumes,
        "vwap": vwap,
        "residual": residual,
        "weighted_sigma": weighted,
        "prior_residual_sigma": prior,
        "atr": _atr(candles, int(config.edge_atr_period)),
    }


def _snapshot(candles: Sequence[BarObservation], config: RouteConfig) -> dict[str, float | int | str] | None:
    arrays = _source_arrays(candles, config)
    index = len(candles) - 1
    mode = str(config.edge_signal_mode).strip().lower()
    if mode == "weighted_band":
        sigma = float(arrays["weighted_sigma"][index])
    elif mode == "prior_residual":
        sigma = float(arrays["prior_residual_sigma"][index])
    else:
        raise ValueError(f"unsupported edge_signal_mode={mode!r}")
    price = float(arrays["price"][index])
    vwap = float(arrays["vwap"][index])
    residual = float(arrays["residual"][index])
    atr = float(arrays["atr"][index])
    if not all(_finite(value) for value in (price, vwap, residual, sigma, atr)) or sigma <= 1e-12:
        return None
    return {
        "source_price": price,
        "source_vwap": vwap,
        "source_residual": residual,
        "source_sigma": sigma,
        "source_z": residual / sigma,
        "source_atr": atr,
        "source_signal_mode": mode,
        "source_close": float(candles[index].close),
        "source_high": float(candles[index].high),
        "source_low": float(candles[index].low),
        "source_ts_15m": int(candles[index].ts_event),
    }


def route_symbol(symbol: str, bars: Sequence[BarObservation], feature: FeatureObservation,
                 config: RouteConfig = RouteConfig()) -> RouteDecision:
    if not feature.ready:
        return _unresolved(symbol, "FEATURE_NOT_READY")
    scope = str(config.edge_scope).strip().lower()
    if scope == "btc" and symbol != "BTCUSDT":
        return _unresolved(symbol, "EDGEBOT_BTC_SCOPE")
    if scope not in {"btc", "all"}:
        raise ValueError(f"unsupported edge_scope={scope!r}")
    candles = _aggregate_complete(bars, int(config.picasso_bucket_minutes))
    required = max(
        int(config.edge_vwap_period) + 2,
        int(config.edge_vwap_period) + int(config.edge_residual_window) + 2,
        int(config.edge_atr_period) + 2,
    )
    if len(candles) < required:
        return _unresolved(
            symbol, "EDGEBOT_SOURCE_WARMUP",
            int(candles[-1].ts_event) if candles else 0,
            {"completed_15m_candles": len(candles), "required": required},
        )
    snap = _snapshot(candles, config)
    episode_ts = int(candles[-1].ts_event)
    if snap is None:
        return _unresolved(symbol, "EDGEBOT_SOURCE_NOT_READY", episode_ts)
    z = float(snap["source_z"])
    threshold = float(config.edge_entry_sigma)
    side = -1 if z >= threshold else 1 if z <= -threshold else 0
    diagnostics: dict[str, float | int | str] = {
        **snap,
        "source_entry_sigma": threshold,
        "source_scope": scope,
        "source_side": side,
    }
    if side == 0:
        return _unresolved(symbol, "EDGEBOT_BELOW_4SIGMA", episode_ts, diagnostics)

    entry = float(candles[-1].close)
    vwap = float(snap["source_vwap"])
    sigma = float(snap["source_sigma"])
    risk_mode = str(config.edge_risk_mode).strip().lower()
    if risk_mode == "six_sigma":
        stop = vwap - float(config.edge_stop_sigma) * sigma if side > 0 else vwap + float(config.edge_stop_sigma) * sigma
        anchor = stop
        buffer = 0.0
    elif risk_mode == "impulse_extreme":
        atr = float(snap["source_atr"])
        buffer = max(
            atr * float(config.edge_stop_atr_buffer),
            entry * 0.0002,
        )
        minimum = entry * float(config.edge_min_stop_fraction)
        if side > 0:
            anchor = float(candles[-1].low)
            stop = min(anchor - buffer, entry - minimum)
        else:
            anchor = float(candles[-1].high)
            stop = max(anchor + buffer, entry + minimum)
    else:
        raise ValueError(f"unsupported edge_risk_mode={risk_mode!r}")

    exit_mode = str(config.edge_mean_exit_mode).strip().lower()
    if exit_mode == "static_entry_mean":
        target = vwap
    elif exit_mode == "dynamic_mean":
        target = entry * (
            1.0 + side * float(config.edge_dynamic_emergency_target_fraction)
        )
    else:
        raise ValueError(f"unsupported edge_mean_exit_mode={exit_mode!r}")
    valid = (
        0.0 < stop < entry < target
        if side > 0
        else 0.0 < target < entry < stop
    )
    diagnostics.update(
        {
            "source_risk_mode": risk_mode,
            "source_mean_exit_mode": exit_mode,
            "source_stop_anchor": anchor,
            "source_stop_buffer": buffer,
            "source_stop_fraction": abs(entry - stop) / entry,
            "source_target_fraction": abs(target - entry) / entry,
        }
    )
    if not valid:
        return _unresolved(symbol, "EDGEBOT_INVALID_GEOMETRY", episode_ts, diagnostics)
    score = abs(z) + abs(entry - vwap) / entry * 10_000.0
    diagnostics["source_score"] = score
    return RouteDecision(
        symbol=symbol, state=EDGE_STATE, side=side, score=float(score),
        entry_reference=entry, stop_reference=stop,
        objective_reference=target, episode_ts=episode_ts,
        reasons=(
            "PUBLIC_EDGEBOT_4SIGMA_MEAN_REVERSION",
            "COMPLETED_15M_ONLY",
            "SIGNAL_MODE_" + str(config.edge_signal_mode).upper(),
            "MEAN_EXIT_" + exit_mode.upper(),
            "RISK_MODE_" + risk_mode.upper(),
        ),
        diagnostics=diagnostics,
    )


def route_universe(bars_by_symbol: Mapping[str, Sequence[BarObservation]],
                   features_by_symbol: Mapping[str, FeatureObservation],
                   config: RouteConfig = RouteConfig()) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    decisions = {
        symbol: route_symbol(symbol, bars_by_symbol[symbol], features_by_symbol[symbol], config)
        for symbol in bars_by_symbol if symbol in features_by_symbol
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
    "BarObservation", "EDGE_STATE", "FeatureObservation", "PICASSO_STATE",
    "RouteConfig", "RouteDecision", "SMA_OFFSET_STATE", "UNRESOLVED",
    "_aggregate_complete", "_prior_residual_sigma", "_rolling_vwap",
    "_snapshot", "_source_arrays", "_weighted_sigma", "route_symbol",
    "route_universe",
]

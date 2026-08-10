"""Causal adapter for the public EDTMA long/short trend system.

Source:
``syuraj/freq-test``
``EDTMA_long_short_prot_CE_1h_3Lev_3mt_March.py``

The source claim is not treated as evidence.  This module reuses the executable
entry mechanism and exposes its unusual exit geometry for direct comparison:

* long: ADX > 35, TEMA(7) > DEMA(45) > EMA(177), volume > prior 22h mean;
* short: ADX > 26, TEMA(19) < DEMA(53) < EMA(102), volume > prior 22h mean;
* source stop: 12% leveraged trade return at 3x, i.e. 4% underlying;
* source trailing/ROI/chandelier management is implemented by the strategy.

All decisions use completed one-hour candles only.  The router supports both the
source condition-reentry interpretation and a one-entry-per-contiguous-episode
interpretation so the role of repeated entries can be measured rather than
assumed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Sequence

import router_picasso as _ta

BarObservation = _ta.BarObservation
FeatureObservation = _ta.FeatureObservation
UNRESOLVED = "UNRESOLVED"
EDTMA_STATE = "PUBLIC_EDTMA_TEMA_DEMA_EMA_ADX"
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}


@dataclass(frozen=True, slots=True)
class RouteConfig:
    atr_period: int = 30
    min_impulse_atr_continuation: float = 0.75
    min_impulse_atr_reversal: float = 1.05
    min_response_atr: float = 0.12
    min_participation_ratio: float = 1.05
    min_route_score: float = 3.10
    ambiguity_score_gap: float = 0.20
    continuation_target_r: float = 2.20
    reversal_target_r: float = 1.80

    edtma_bucket_minutes: int = 60
    edtma_episode_mode: str = "condition_reentry"
    edtma_adx_period: int = 14
    edtma_volume_period: int = 22
    edtma_long_adx_min: float = 35.0
    edtma_long_tema_period: int = 7
    edtma_long_dema_period: int = 45
    edtma_long_ema_period: int = 177
    edtma_short_adx_min: float = 26.0
    edtma_short_tema_period: int = 19
    edtma_short_dema_period: int = 53
    edtma_short_ema_period: int = 102
    edtma_source_leverage: float = 3.0
    edtma_source_stoploss_profit_ratio: float = 0.12
    edtma_remote_target_fraction: float = 0.10


@dataclass(frozen=True, slots=True)
class RouteDecision:
    symbol: str
    state: str
    side: int
    score: float
    entry_reference: float
    stop_reference: float
    objective_reference: float
    episode_ts: int
    reasons: tuple[str, ...] = ()
    diagnostics: Mapping[str, float | int | str] = field(default_factory=dict)

    @property
    def actionable(self) -> bool:
        return self.side in (-1, 1) and self.state != UNRESOLVED


def _finite(value: float) -> bool:
    return math.isfinite(float(value))


def _ema_layers(values: Sequence[float], period: int) -> tuple[list[float], list[float], list[float]]:
    first = _ta._ema(values, period)
    second = _ta._ema_nan(first, period)
    third = _ta._ema_nan(second, period)
    return first, second, third


def _dema(values: Sequence[float], period: int) -> list[float]:
    first, second, _ = _ema_layers(values, period)
    return [
        2.0 * a - b if _finite(a) and _finite(b) else math.nan
        for a, b in zip(first, second, strict=True)
    ]


def _tema(values: Sequence[float], period: int) -> list[float]:
    first, second, third = _ema_layers(values, period)
    return [
        3.0 * a - 3.0 * b + c if _finite(a) and _finite(b) and _finite(c) else math.nan
        for a, b, c in zip(first, second, third, strict=True)
    ]


def _unresolved(symbol: str, reason: str, episode_ts: int = 0,
                diagnostics: Mapping[str, float | int | str] | None = None) -> RouteDecision:
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


def _condition_at(candles: Sequence[BarObservation], index: int,
                  config: RouteConfig) -> tuple[bool, bool, dict[str, float | int | str]]:
    closes = [float(candle.close) for candle in candles]
    volumes = [max(0.0, float(candle.volume)) for candle in candles]
    adx = _ta._adx(candles, int(config.edtma_adx_period))
    long_tema = _tema(closes, int(config.edtma_long_tema_period))
    long_dema = _dema(closes, int(config.edtma_long_dema_period))
    long_ema = _ta._ema(closes, int(config.edtma_long_ema_period))
    short_tema = _tema(closes, int(config.edtma_short_tema_period))
    short_dema = _dema(closes, int(config.edtma_short_dema_period))
    short_ema = _ta._ema(closes, int(config.edtma_short_ema_period))
    volume_mean = _ta._rolling_mean_shifted(volumes, int(config.edtma_volume_period))
    values = (
        adx[index], long_tema[index], long_dema[index], long_ema[index],
        short_tema[index], short_dema[index], short_ema[index], volume_mean[index],
    )
    if not all(_finite(value) for value in values):
        return False, False, {"ready": 0}
    candle = candles[index]
    volume_ratio = float(candle.volume) / max(float(volume_mean[index]), 1e-12)
    long_ok = (
        float(adx[index]) > float(config.edtma_long_adx_min)
        and float(long_tema[index]) > float(long_dema[index])
        and float(long_dema[index]) > float(long_ema[index])
        and volume_ratio > 1.0
    )
    short_ok = (
        float(adx[index]) > float(config.edtma_short_adx_min)
        and float(short_tema[index]) < float(short_dema[index])
        and float(short_dema[index]) < float(short_ema[index])
        and volume_ratio > 1.0
    )
    diagnostics: dict[str, float | int | str] = {
        "ready": 1,
        "adx": float(adx[index]),
        "volume": float(candle.volume),
        "volume_mean_22_shifted": float(volume_mean[index]),
        "volume_ratio": volume_ratio,
        "long_tema": float(long_tema[index]),
        "long_dema": float(long_dema[index]),
        "long_ema": float(long_ema[index]),
        "short_tema": float(short_tema[index]),
        "short_dema": float(short_dema[index]),
        "short_ema": float(short_ema[index]),
        "long_condition": int(long_ok),
        "short_condition": int(short_ok),
    }
    return long_ok, short_ok, diagnostics


def classify_symbol(symbol: str, bars: Sequence[BarObservation],
                    feature: FeatureObservation,
                    config: RouteConfig = RouteConfig()) -> RouteDecision:
    if not bars:
        return _unresolved(symbol, "NO_MINUTE_BARS")
    latest_ts = int(bars[-1].ts_event)
    if int(feature.observed_time_ns) > latest_ts:
        return _unresolved(symbol, "FUTURE_FEATURE_REJECTED", latest_ts)
    candles = _ta._aggregate_complete(bars, int(config.edtma_bucket_minutes))
    minimum = max(
        int(config.edtma_long_ema_period) + 5,
        int(config.edtma_short_ema_period) + 5,
        int(config.edtma_long_tema_period) * 3 + 5,
        int(config.edtma_short_tema_period) * 3 + 5,
        int(config.edtma_adx_period) * 2 + 5,
        int(config.edtma_volume_period) + 5,
    )
    if len(candles) < minimum:
        return _unresolved(
            symbol,
            "EDTMA_HISTORY_NOT_READY",
            latest_ts,
            {"candles": len(candles), "minimum": minimum},
        )
    current_long, current_short, diagnostics = _condition_at(candles, len(candles) - 1, config)
    previous_long, previous_short, _ = _condition_at(candles, len(candles) - 2, config)
    diagnostics.update(
        {
            "previous_long_condition": int(previous_long),
            "previous_short_condition": int(previous_short),
            "episode_mode": str(config.edtma_episode_mode),
        }
    )
    if current_long and current_short:
        return _unresolved(symbol, "EDTMA_AMBIGUOUS_DIRECTION", candles[-1].ts_event, diagnostics)
    if not current_long and not current_short:
        return _unresolved(symbol, "EDTMA_NO_SOURCE_CONDITION", candles[-1].ts_event, diagnostics)
    side = 1 if current_long else -1
    mode = str(config.edtma_episode_mode).strip().lower()
    if mode == "rising_edge":
        prior = previous_long if side > 0 else previous_short
        if prior:
            return _unresolved(symbol, "EDTMA_CONTIGUOUS_EPISODE_ALREADY_ACTIVE", candles[-1].ts_event, diagnostics)
    elif mode != "condition_reentry":
        raise ValueError(f"unsupported edtma_episode_mode={mode!r}")

    entry = float(candles[-1].close)
    leverage = max(float(config.edtma_source_leverage), 1e-12)
    stop_fraction = float(config.edtma_source_stoploss_profit_ratio) / leverage
    target_fraction = float(config.edtma_remote_target_fraction)
    stop = entry * (1.0 - side * stop_fraction)
    target = entry * (1.0 + side * target_fraction)
    if side > 0:
        separation = (
            (float(diagnostics["long_tema"]) - float(diagnostics["long_dema"]))
            + (float(diagnostics["long_dema"]) - float(diagnostics["long_ema"]))
        ) / entry
        adx_margin = float(diagnostics["adx"]) - float(config.edtma_long_adx_min)
        tag = "LONG"
    else:
        separation = (
            (float(diagnostics["short_dema"]) - float(diagnostics["short_tema"]))
            + (float(diagnostics["short_ema"]) - float(diagnostics["short_dema"]))
        ) / entry
        adx_margin = float(diagnostics["adx"]) - float(config.edtma_short_adx_min)
        tag = "SHORT"
    score = (
        min(max(adx_margin, 0.0) / 10.0, 5.0)
        + min(max(float(diagnostics["volume_ratio"]) - 1.0, 0.0), 5.0)
        + min(max(separation * 10_000.0, 0.0) / 10.0, 5.0)
    )
    diagnostics.update(
        {
            "source_tag": tag,
            "trend_separation_fraction": separation,
            "source_leverage": leverage,
            "source_stoploss_profit_ratio": float(config.edtma_source_stoploss_profit_ratio),
            "underlying_stop_fraction": stop_fraction,
        }
    )
    return RouteDecision(
        symbol=symbol,
        state=EDTMA_STATE,
        side=side,
        score=float(score),
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=target,
        episode_ts=int(candles[-1].ts_event),
        reasons=(
            "PUBLIC_EDTMA_ADX_TEMA_DEMA_EMA_VOLUME_ENTRY",
            "COMPLETED_ONE_HOUR_CANDLE",
            "SOURCE_RISK_NORMALIZED_BY_EFFECTIVE_LEVERAGE",
        ),
        diagnostics=diagnostics,
    )


def route_universe(bars_by_symbol: Mapping[str, Sequence[BarObservation]],
                   features_by_symbol: Mapping[str, FeatureObservation],
                   config: RouteConfig = RouteConfig()) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
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
    "BarObservation", "EDTMA_STATE", "FeatureObservation", "RouteConfig",
    "RouteDecision", "UNRESOLVED", "classify_symbol", "route_universe",
]

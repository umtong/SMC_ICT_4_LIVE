"""Source-faithful Winner15m signal router for Candidate 57.

The earlier adapter intentionally converted the public Freqtrade condition into
one transition-only trade per condition episode.  That is useful for causal
independence, but it is not a faithful reproduction of the source program:
Freqtrade evaluates the entry column on every completed 15-minute candle and
can re-enter while a condition remains true after a previous trade closes.

This module preserves the exact public entry condition on every completed
source candle.  It also records the start of the continuous source-condition
episode so raw trades and independent causal episodes can be reported
separately.  The project-wide one-slot account constraint remains enforced by
the Nautilus strategy shell, not by pretending the source itself had one slot.
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence

from router_transition import (
    EDGE_MR_STATE,
    SMA_OFFSET_STATE,
    UNRESOLVED,
    WINNER_STATE,
    BarObservation,
    FeatureObservation,
    RouteConfig,
    RouteDecision,
    _EPS,
    _SYMBOL_PRIORITY,
    _adx,
    _aggregate_complete,
    _ema,
    _macd,
    _roc,
    _sma,
    _unresolved,
)


def _finite(value: float) -> bool:
    return math.isfinite(float(value))


def _winner_series(
    candles: Sequence[BarObservation],
    config: RouteConfig,
) -> tuple[list[int], list[dict[str, float | int | str]]]:
    closes = [float(candle.close) for candle in candles]
    volumes = [float(candle.volume) for candle in candles]
    ema_fast = _ema(closes, config.winner_ema_fast)
    ema_slow = _ema(closes, config.winner_ema_slow)
    macd_line, macd_signal = _macd(
        closes,
        config.winner_macd_fast,
        config.winner_macd_slow,
        config.winner_macd_signal,
    )
    momentum = _roc(closes, config.winner_roc_period)
    adx = _adx(candles, config.winner_adx_period)
    volume_sma = _sma(volumes, config.winner_volume_period)

    sides: list[int] = []
    diagnostics: list[dict[str, float | int | str]] = []
    for index in range(len(candles)):
        fields: dict[str, float | int | str] = {
            "close": closes[index],
            "ema_fast": float(ema_fast[index]),
            "ema_slow": float(ema_slow[index]),
            "macd": float(macd_line[index]),
            "macd_signal": float(macd_signal[index]),
            "roc": float(momentum[index]),
            "adx": float(adx[index]),
            "volume": volumes[index],
            "volume_sma": float(volume_sma[index]),
        }
        if not all(_finite(value) for value in fields.values()):
            sides.append(0)
            diagnostics.append({**fields, "ready": 0})
            continue
        volume_ratio = float(fields["volume"]) / max(
            float(fields["volume_sma"]), _EPS
        )
        long_ok = (
            float(fields["ema_fast"]) > float(fields["ema_slow"])
            and float(fields["macd"]) > float(fields["macd_signal"])
            and float(fields["roc"]) > config.winner_roc_threshold
            and float(fields["adx"]) > config.winner_adx_threshold
            and volume_ratio > config.winner_volume_ratio
            and float(fields["volume"]) > 0.0
        )
        short_ok = (
            float(fields["ema_fast"]) < float(fields["ema_slow"])
            and float(fields["macd"]) < float(fields["macd_signal"])
            and float(fields["roc"]) < -config.winner_roc_threshold
            and float(fields["adx"]) > config.winner_adx_threshold
            and volume_ratio > config.winner_volume_ratio
            and float(fields["volume"]) > 0.0
        )
        side = 1 if long_ok and not short_ok else -1 if short_ok and not long_ok else 0
        sides.append(side)
        diagnostics.append(
            {
                **fields,
                "ready": 1,
                "volume_ratio": volume_ratio,
                "long_condition": int(long_ok),
                "short_condition": int(short_ok),
            }
        )
    return sides, diagnostics


def _classify_winner_source_true(
    symbol: str,
    bars: Sequence[BarObservation],
    config: RouteConfig,
) -> RouteDecision:
    candles = _aggregate_complete(bars, config.winner_bucket_minutes)
    source_startup = 200
    mathematical_minimum = max(
        config.winner_ema_slow + 3,
        config.winner_macd_slow + config.winner_macd_signal + 3,
        config.winner_adx_period * 2 + 3,
        config.winner_volume_period + 3,
    )
    minimum = max(source_startup, mathematical_minimum)
    if len(candles) < minimum:
        return _unresolved(
            symbol,
            "WINNER_SOURCE_STARTUP_NOT_READY",
            bars[-1].ts_event if bars else 0,
            {
                "candles": len(candles),
                "minimum": minimum,
                "source_startup_candles": source_startup,
            },
        )

    sides, rows = _winner_series(candles, config)
    index = len(candles) - 1
    side = sides[index]
    row = dict(rows[index])
    previous_side = sides[index - 1] if index > 0 else 0
    row["previous_side"] = previous_side
    if side == 0:
        return _unresolved(
            symbol,
            "WINNER_NO_SOURCE_ENTRY",
            candles[index].ts_event,
            row,
        )

    episode_start_index = index
    while episode_start_index > 0 and sides[episode_start_index - 1] == side:
        episode_start_index -= 1
    causal_episode_start = int(candles[episode_start_index].ts_event)

    entry = float(candles[index].close)
    stop_fraction = max(config.winner_stop_fraction, 1e-6)
    target_fraction = max(config.winner_initial_target_fraction, 1e-6)
    stop = entry * (1.0 - side * stop_fraction)
    objective = entry * (1.0 + side * target_fraction)
    reward_r = target_fraction / stop_fraction
    quality = (
        max(0.0, float(row["adx"]) - config.winner_adx_threshold) / 10.0
        + max(0.0, abs(float(row["roc"])) - config.winner_roc_threshold)
        + max(0.0, float(row["volume_ratio"]) - config.winner_volume_ratio)
    )
    row.update(
        {
            "source_tag": "L" if side > 0 else "S",
            "reward_r": reward_r,
            "source_stop_fraction": stop_fraction,
            "source_initial_roi": target_fraction,
            "family": "BTCquant_Winner15m",
            "source_semantics": "TRUE_ON_EVERY_COMPLETED_SOURCE_CANDLE",
            "causal_episode_start_ts": causal_episode_start,
            "causal_episode_age_bars": index - episode_start_index,
            "persistent_source_condition": int(previous_side == side),
        }
    )
    return RouteDecision(
        symbol=symbol,
        state=WINNER_STATE,
        side=side,
        score=reward_r * 10.0 + quality,
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=objective,
        # Current source candle, rather than condition-start time, deliberately
        # allows source-consistent re-entry.  Independent frequency is grouped
        # later by causal_episode_start_ts.
        episode_ts=int(candles[index].ts_event),
        reasons=(
            "PUBLIC_BTCQUANT_EMA_MACD_ROC_ADX_VOLUME_ENTRY",
            "SOURCE_TRUE_ON_EVERY_COMPLETED_15M_CANDLE",
        ),
        diagnostics=row,
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
    mode = config.external_family_mode.strip().lower()
    if mode != "winner":
        return _unresolved(
            symbol,
            "SOURCE_FIDELITY_ROUTER_SUPPORTS_WINNER_ONLY",
            latest_ts,
            {"mode": mode},
        )
    return _classify_winner_source_true(symbol, bars, config)


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
                FeatureObservation(
                    bars[-1].ts_event if bars else 0,
                    ready=True,
                ),
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
            item.state,
        )
    )
    return (actionable[0] if actionable else None), decisions


def sma_offset_exit_ready(
    bars: Sequence[BarObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[bool, dict[str, float | int | str]]:
    del bars, config
    return False, {"reason": "SOURCE_MANAGEMENT_OWNED_BY_STRATEGY"}


__all__ = [
    "BarObservation",
    "FeatureObservation",
    "RouteConfig",
    "RouteDecision",
    "WINNER_STATE",
    "EDGE_MR_STATE",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "classify_symbol",
    "classify_sma_offset",
    "route_universe",
    "sma_offset_exit_ready",
]

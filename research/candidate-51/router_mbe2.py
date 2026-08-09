"""Candidate 51 adapter for the public myshortingstrategiembe2 futures bot.

The source's effective v3 entry policy is preserved:

* 5-minute RSI(14) crossing above 30 with rising TEMA(9) below the
  Bollinger middle band enters long;
* RSI crossing below 70 with falling TEMA above the middle band enters short;
* every crossing is a naturally independent causal episode;
* the source stop, ROI and trailing values are leverage-normalized while the
  NautilusTrader shell sizes the trade from exactly 3% current NAV risk.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Sequence

MBE2_STATE = "PUBLIC_MYSHORTING_MBE2_RSI_TEMA_REVERSAL"
SMA_OFFSET_STATE = MBE2_STATE
UNRESOLVED = "UNRESOLVED"
_EPS = 1e-12
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}


@dataclass(frozen=True, slots=True)
class BarObservation:
    ts_event: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class FeatureObservation:
    observed_time_ns: int
    ready: bool = True
    flow_open_10s: float = math.nan
    notional_open_10s_burst: float = math.nan
    flow_60s: float = math.nan
    efficiency_60s: float = math.nan
    oi_change_15m: float = math.nan
    premium_z: float = math.nan


@dataclass(frozen=True, slots=True)
class RouteConfig:
    # Compatibility fields used by the reused execution shell.
    atr_period: int = 30
    min_impulse_atr_continuation: float = 0.20
    min_impulse_atr_reversal: float = 1.05
    min_response_atr: float = 0.08
    min_participation_ratio: float = 0.85
    min_route_score: float = 3.20
    ambiguity_score_gap: float = 0.10
    continuation_target_r: float = 2.00
    reversal_target_r: float = 1.65

    bucket_minutes: int = 5
    mbe_rsi_period: int = 14
    mbe_tema_period: int = 9
    mbe_bb_period: int = 20
    mbe_long_rsi_cross: float = 30.0
    mbe_short_rsi_cross: float = 70.0
    mbe_source_effective_leverage: float = 6.46
    mbe_source_stoploss: float = 0.22
    mbe_trailing_positive: float = 0.015
    mbe_trailing_offset: float = 0.025
    mbe_emergency_target_fraction: float = 0.10

    # Legacy source-policy constructor compatibility.
    sma_offset_period: int = 8
    sma_offset_low: float = 0.960
    sma_offset_high: float = 1.012
    sma_trend_fast: int = 20
    sma_trend_slow: int = 25
    sma_stop_min_fraction: float = 0.0075
    sma_stop_max_fraction: float = 0.1000
    sma_stop_atr_buffer: float = 0.50
    sma_structural_lookback: int = 6
    sma_min_reward_r: float = 1.00


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


def _aggregate_complete(
    bars: Sequence[BarObservation],
    bucket_minutes: int,
) -> list[BarObservation]:
    if bucket_minutes <= 0:
        raise ValueError("bucket_minutes must be positive")
    if not bars:
        return []
    minute_ns = 60_000_000_000
    phase = int(bars[-1].ts_event) % minute_ns
    grouped: dict[int, list[tuple[int, BarObservation]]] = {}
    for bar in bars:
        timestamp = int(bar.ts_event)
        if timestamp % minute_ns != phase:
            continue
        ordinal = (timestamp - phase) // minute_ns
        grouped.setdefault(ordinal // bucket_minutes, []).append((ordinal, bar))
    output: list[BarObservation] = []
    for unordered in grouped.values():
        indexed = sorted(unordered, key=lambda item: item[0])
        if len(indexed) != bucket_minutes:
            continue
        ordinals = [item[0] for item in indexed]
        if ordinals[0] % bucket_minutes != 0 or ordinals[-1] % bucket_minutes != bucket_minutes - 1:
            continue
        if any(ordinals[i] - ordinals[i - 1] != 1 for i in range(1, len(ordinals))):
            continue
        items = [item[1] for item in indexed]
        output.append(BarObservation(
            ts_event=int(items[-1].ts_event),
            open=float(items[0].open),
            high=max(float(item.high) for item in items),
            low=min(float(item.low) for item in items),
            close=float(items[-1].close),
            volume=sum(max(0.0, float(item.volume)) for item in items),
        ))
    output.sort(key=lambda item: item.ts_event)
    return output


def _sma(values: Sequence[float], period: int) -> list[float]:
    result = [math.nan] * len(values)
    if period <= 0:
        return result
    running = 0.0
    for index, raw in enumerate(values):
        running += float(raw)
        if index >= period:
            running -= float(values[index - period])
        if index >= period - 1:
            result[index] = running / period
    return result


def _ema(values: Sequence[float], period: int) -> list[float]:
    result = [math.nan] * len(values)
    if period <= 0 or len(values) < period:
        return result
    current = sum(float(value) for value in values[:period]) / period
    result[period - 1] = current
    alpha = 2.0 / (period + 1.0)
    for index in range(period, len(values)):
        current = alpha * float(values[index]) + (1.0 - alpha) * current
        result[index] = current
    return result


def _ema_nan(values: Sequence[float], period: int) -> list[float]:
    """EMA beginning at the first complete finite run, for nested TEMA EMAs."""
    result = [math.nan] * len(values)
    finite_indices = [index for index, value in enumerate(values) if _finite(value)]
    if period <= 0 or len(finite_indices) < period:
        return result
    start = finite_indices[0]
    if finite_indices[:period] != list(range(start, start + period)):
        return result
    seed_end = start + period
    current = sum(float(values[index]) for index in range(start, seed_end)) / period
    result[seed_end - 1] = current
    alpha = 2.0 / (period + 1.0)
    for index in range(seed_end, len(values)):
        value = float(values[index])
        if not _finite(value):
            continue
        current = alpha * value + (1.0 - alpha) * current
        result[index] = current
    return result


def _tema(values: Sequence[float], period: int) -> list[float]:
    first = _ema(values, period)
    second = _ema_nan(first, period)
    third = _ema_nan(second, period)
    return [
        3.0 * a - 3.0 * b + c if all(_finite(value) for value in (a, b, c)) else math.nan
        for a, b, c in zip(first, second, third, strict=True)
    ]


def _rsi(values: Sequence[float], period: int) -> list[float]:
    result = [math.nan] * len(values)
    if period <= 0 or len(values) <= period:
        return result
    gains = [0.0] * len(values)
    losses = [0.0] * len(values)
    for index in range(1, len(values)):
        change = float(values[index]) - float(values[index - 1])
        gains[index] = max(change, 0.0)
        losses[index] = max(-change, 0.0)
    average_gain = sum(gains[1 : period + 1]) / period
    average_loss = sum(losses[1 : period + 1]) / period

    def value(gain: float, loss: float) -> float:
        if loss <= _EPS:
            return 100.0 if gain > _EPS else 50.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    result[period] = value(average_gain, average_loss)
    for index in range(period + 1, len(values)):
        average_gain = (average_gain * (period - 1) + gains[index]) / period
        average_loss = (average_loss * (period - 1) + losses[index]) / period
        result[index] = value(average_gain, average_loss)
    return result


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
    candles = _aggregate_complete(bars, config.bucket_minutes)
    minimum = max(config.mbe_rsi_period + 2, config.mbe_bb_period + 2, config.mbe_tema_period * 3 + 3)
    if len(candles) < minimum:
        return _unresolved(
            symbol,
            "MBE2_HISTORY_NOT_READY",
            latest_ts,
            {"five_minute_candles": len(candles), "minimum": minimum},
        )
    closes = [float(candle.close) for candle in candles]
    rsi = _rsi(closes, config.mbe_rsi_period)
    tema = _tema(closes, config.mbe_tema_period)
    middle = _sma(closes, config.mbe_bb_period)
    index = len(candles) - 1
    previous = index - 1
    values = {
        "rsi": float(rsi[index]),
        "rsi_previous": float(rsi[previous]),
        "tema": float(tema[index]),
        "tema_previous": float(tema[previous]),
        "bb_middle": float(middle[index]),
        "close": float(candles[index].close),
        "volume": float(candles[index].volume),
    }
    if not all(_finite(value) for value in values.values()):
        return _unresolved(symbol, "MBE2_INDICATORS_NOT_READY", candles[index].ts_event)

    long_ok = (
        values["rsi_previous"] <= config.mbe_long_rsi_cross
        and values["rsi"] > config.mbe_long_rsi_cross
        and values["tema"] <= values["bb_middle"]
        and values["tema"] > values["tema_previous"]
        and values["volume"] > 0.0
    )
    short_ok = (
        values["rsi_previous"] >= config.mbe_short_rsi_cross
        and values["rsi"] < config.mbe_short_rsi_cross
        and values["tema"] > values["bb_middle"]
        and values["tema"] < values["tema_previous"]
        and values["volume"] > 0.0
    )
    diagnostics: dict[str, float | int | str] = {
        **values,
        "long_condition": int(long_ok),
        "short_condition": int(short_ok),
    }
    if long_ok == short_ok:
        reason = "MBE2_NO_SOURCE_ENTRY" if not long_ok else "MBE2_AMBIGUOUS_SOURCE_ENTRY"
        return _unresolved(symbol, reason, candles[index].ts_event, diagnostics)

    side = 1 if long_ok else -1
    entry = values["close"]
    leverage = max(config.mbe_source_effective_leverage, _EPS)
    stop_fraction = config.mbe_source_stoploss / leverage
    if side > 0:
        stop = entry * (1.0 - stop_fraction)
        objective = entry * (1.0 + config.mbe_emergency_target_fraction)
        score = (
            max(0.0, values["rsi"] - config.mbe_long_rsi_cross)
            + max(0.0, values["tema"] - values["tema_previous"]) / entry * 10_000.0
            + max(0.0, values["bb_middle"] - values["tema"]) / entry * 10_000.0
        )
        tag = "rsi_cross_long"
    else:
        stop = entry * (1.0 + stop_fraction)
        objective = entry * (1.0 - config.mbe_emergency_target_fraction)
        score = (
            max(0.0, config.mbe_short_rsi_cross - values["rsi"])
            + max(0.0, values["tema_previous"] - values["tema"]) / entry * 10_000.0
            + max(0.0, values["tema"] - values["bb_middle"]) / entry * 10_000.0
        )
        tag = "rsi_cross_short"
    diagnostics.update({
        "source_tag": tag,
        "source_effective_leverage": leverage,
        "source_stoploss_profit_ratio": config.mbe_source_stoploss,
        "underlying_stop_fraction": stop_fraction,
        "source_trailing_positive": config.mbe_trailing_positive,
        "source_trailing_offset": config.mbe_trailing_offset,
    })
    return RouteDecision(
        symbol=symbol,
        state=MBE2_STATE,
        side=side,
        score=float(score),
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=objective,
        episode_ts=int(candles[index].ts_event),
        reasons=(
            "PUBLIC_MBE2_RSI_CROSS_TEMA_BOLLINGER_ENTRY",
            "SOURCE_MANAGEMENT_NORMALIZED_BY_EFFECTIVE_LEVERAGE",
        ),
        diagnostics=diagnostics,
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
    actionable.sort(key=lambda item: (
        -float(item.score),
        _SYMBOL_PRIORITY.get(item.symbol, 99),
        item.symbol,
    ))
    return (actionable[0] if actionable else None), decisions


def sma_offset_exit_ready(
    bars: Sequence[BarObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[bool, dict[str, float | int | str]]:
    del bars, config
    return False, {"source_exit": 0, "reason": "MBE2_MANAGEMENT_IN_STRATEGY"}


__all__ = [
    "BarObservation", "FeatureObservation", "RouteConfig", "RouteDecision",
    "MBE2_STATE", "SMA_OFFSET_STATE", "UNRESOLVED", "classify_symbol",
    "classify_sma_offset", "route_universe", "sma_offset_exit_ready",
]

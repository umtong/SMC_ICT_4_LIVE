"""Causal NASOSv5 decision-family adapter for Candidate 51.

This is a direct decision-level adaptation of the public 5-minute NASOSv5
source. It preserves the three EWO/RSI/EMA entries and the source EMA/HMA exit,
then adds only the project's structural invalidation, causal episode identity,
and four-symbol one-winner arbitration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Sequence

SMA_OFFSET_STATE = "NASOS_V5_EWO_PULLBACK_LONG"
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
    # Constructor compatibility with the reused NautilusTrader shell.
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
    sma_offset_period: int = 8
    sma_offset_low: float = 0.981
    sma_offset_high: float = 1.097
    sma_trend_fast: int = 50
    sma_trend_slow: int = 200
    sma_stop_min_fraction: float = 0.0075
    sma_stop_max_fraction: float = 0.1500
    sma_stop_atr_buffer: float = 0.50
    sma_structural_lookback: int = 6
    sma_min_reward_r: float = 1.00

    nasos_sell_ema_period: int = 16
    nasos_low_offset_2: float = 0.942
    nasos_high_offset_2: float = 1.472
    nasos_ewo_high: float = 3.553
    nasos_ewo_high_2: float = -5.585
    nasos_ewo_low: float = -14.378
    nasos_rsi_buy: float = 78.0
    nasos_rsi_fast_buy: float = 37.0
    nasos_profit_lookback_15m: int = 32
    nasos_profit_threshold: float = 1.037


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
        if ordinals[0] % bucket_minutes != 0:
            continue
        if ordinals[-1] % bucket_minutes != bucket_minutes - 1:
            continue
        if any(
            ordinals[index] - ordinals[index - 1] != 1
            for index in range(1, len(ordinals))
        ):
            continue
        items = [item[1] for item in indexed]
        output.append(
            BarObservation(
                ts_event=int(items[-1].ts_event),
                open=float(items[0].open),
                high=max(float(item.high) for item in items),
                low=min(float(item.low) for item in items),
                close=float(items[-1].close),
                volume=sum(max(0.0, float(item.volume)) for item in items),
            ),
        )
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


def _wma(values: Sequence[float], period: int) -> list[float]:
    result = [math.nan] * len(values)
    if period <= 0:
        return result
    denominator = period * (period + 1) / 2
    for index in range(period - 1, len(values)):
        window = values[index - period + 1 : index + 1]
        if all(_finite(value) for value in window):
            result[index] = sum(
                (weight + 1) * float(value)
                for weight, value in enumerate(window)
            ) / denominator
    return result


def _hma(values: Sequence[float], period: int) -> list[float]:
    half = _wma(values, max(1, period // 2))
    full = _wma(values, period)
    raw = [
        2.0 * fast - slow if _finite(fast) and _finite(slow) else math.nan
        for fast, slow in zip(half, full, strict=True)
    ]
    return _wma(raw, max(1, int(math.sqrt(period))))


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
        ratio = gain / loss
        return 100.0 - 100.0 / (1.0 + ratio)

    result[period] = value(average_gain, average_loss)
    for index in range(period + 1, len(values)):
        average_gain = (average_gain * (period - 1) + gains[index]) / period
        average_loss = (average_loss * (period - 1) + losses[index]) / period
        result[index] = value(average_gain, average_loss)
    return result


def _true_range(bars: Sequence[BarObservation], index: int) -> float:
    if index == 0:
        return float(bars[index].high) - float(bars[index].low)
    previous_close = float(bars[index - 1].close)
    return max(
        float(bars[index].high) - float(bars[index].low),
        abs(float(bars[index].high) - previous_close),
        abs(float(bars[index].low) - previous_close),
    )


def _atr(bars: Sequence[BarObservation], period: int = 14) -> float:
    if len(bars) < period + 1:
        return math.nan
    values = [
        _true_range(bars, index)
        for index in range(len(bars) - period, len(bars))
    ]
    return sum(values) / len(values)


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


def _state(
    bars: Sequence[BarObservation],
    config: RouteConfig,
) -> tuple[list[BarObservation], dict[str, list[float]]]:
    candles = _aggregate_complete(bars, config.bucket_minutes)
    closes = [float(candle.close) for candle in candles]
    lows = [float(candle.low) for candle in candles]
    ema_buy = _ema(closes, config.sma_offset_period)
    ema_sell = _ema(closes, config.nasos_sell_ema_period)
    ema_fast = _ema(closes, config.sma_trend_fast)
    ema_slow = _ema(closes, config.sma_trend_slow)
    ewo = [
        (fast - slow) / max(low, _EPS) * 100.0
        if _finite(fast) and _finite(slow)
        else math.nan
        for fast, slow, low in zip(ema_fast, ema_slow, lows, strict=True)
    ]
    return candles, {
        "ema_buy": ema_buy,
        "ema_sell": ema_sell,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "ewo": ewo,
        "rsi": _rsi(closes, 14),
        "rsi_fast": _rsi(closes, 4),
        "rsi_slow": _rsi(closes, 20),
        "sma9": _sma(closes, 9),
        "hma50": _hma(closes, 50),
    }


def classify_sma_offset(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    if not bars:
        return _unresolved(symbol, "NO_MINUTE_BARS")
    latest_ts = int(bars[-1].ts_event)
    if not feature.ready:
        return _unresolved(symbol, "FEATURE_NOT_READY", latest_ts)
    if int(feature.observed_time_ns) > latest_ts:
        return _unresolved(symbol, "FUTURE_FEATURE_REJECTED", latest_ts)

    candles, indicators = _state(bars, config)
    minimum = max(
        config.sma_trend_slow + 2,
        config.nasos_profit_lookback_15m * 3 + 2,
    )
    if len(candles) < minimum:
        return _unresolved(
            symbol,
            "NASOS_HISTORY_NOT_READY",
            candles[-1].ts_event if candles else latest_ts,
            {"five_minute_candles": len(candles)},
        )
    index = len(candles) - 1
    values = {name: float(series[index]) for name, series in indicators.items()}
    required = (
        "ema_buy",
        "ema_sell",
        "ewo",
        "rsi",
        "rsi_fast",
        "rsi_slow",
        "hma50",
        "sma9",
    )
    if not all(_finite(values[name]) for name in required):
        return _unresolved(symbol, "NASOS_INDICATORS_NOT_READY", candles[index].ts_event)

    close = float(candles[index].close)
    volume_ok = float(candles[index].volume) > 0.0
    fifteen_minute = _aggregate_complete(bars, 15)
    recovery_space = False
    maximum_close = math.nan
    if len(fifteen_minute) >= config.nasos_profit_lookback_15m:
        maximum_close = max(
            float(candle.close)
            for candle in fifteen_minute[-config.nasos_profit_lookback_15m :]
        )
        recovery_space = maximum_close >= close * config.nasos_profit_threshold

    common = (
        values["rsi_fast"] < config.nasos_rsi_fast_buy
        and close < values["ema_sell"] * config.sma_offset_high
        and volume_ok
        and recovery_space
    )
    ewo1 = (
        common
        and close < values["ema_buy"] * config.sma_offset_low
        and values["ewo"] > config.nasos_ewo_high
        and values["rsi"] < config.nasos_rsi_buy
    )
    ewo2 = (
        common
        and close < values["ema_buy"] * config.nasos_low_offset_2
        and values["ewo"] > config.nasos_ewo_high_2
        and values["rsi"] < 25.0
    )
    ewo_low = (
        common
        and close < values["ema_buy"] * config.sma_offset_low
        and values["ewo"] < config.nasos_ewo_low
    )
    tag = "ewo1" if ewo1 else "ewo2" if ewo2 else "ewolow" if ewo_low else ""
    diagnostics: dict[str, float | int | str] = {
        "five_minute_candles": len(candles),
        "close": close,
        "volume": float(candles[index].volume),
        "ema_buy": values["ema_buy"],
        "ema_sell": values["ema_sell"],
        "ewo": values["ewo"],
        "rsi": values["rsi"],
        "rsi_fast": values["rsi_fast"],
        "rsi_slow": values["rsi_slow"],
        "hma50": values["hma50"],
        "sma9": values["sma9"],
        "max_close_15m": maximum_close,
        "recovery_space": int(recovery_space),
        "source_tag": tag,
    }
    if not tag:
        return _unresolved(
            symbol,
            "NASOS_SOURCE_ENTRY_NOT_PRESENT",
            candles[index].ts_event,
            diagnostics,
        )

    episode_index = index
    while episode_index > 0:
        previous_close = float(candles[episode_index - 1].close)
        previous_ema = float(indicators["ema_buy"][episode_index - 1])
        if not _finite(previous_ema):
            break
        if previous_close >= previous_ema * config.sma_offset_low:
            break
        episode_index -= 1
    episode_ts = int(candles[episode_index].ts_event)

    atr = _atr(candles, 14)
    if not _finite(atr) or atr <= 0.0:
        return _unresolved(symbol, "NASOS_ATR_NOT_READY", episode_ts, diagnostics)
    structural_low = min(
        float(candle.low)
        for candle in candles[-max(1, config.sma_structural_lookback) :]
    )
    raw_stop = structural_low - config.sma_stop_atr_buffer * atr
    stop_distance = max(
        close - raw_stop,
        close * config.sma_stop_min_fraction,
    )
    if stop_distance <= 0.0 or stop_distance > close * config.sma_stop_max_fraction:
        diagnostics.update(
            {
                "atr_5m": atr,
                "raw_stop": raw_stop,
                "stop_distance": stop_distance,
            },
        )
        return _unresolved(
            symbol,
            "NASOS_STRUCTURAL_STOP_TOO_WIDE",
            episode_ts,
            diagnostics,
        )
    stop = close - stop_distance
    objective = values["ema_sell"] * config.sma_offset_high
    reward_r = (objective - close) / max(stop_distance, _EPS)
    diagnostics.update(
        {
            "atr_5m": atr,
            "structural_low": structural_low,
            "stop": stop,
            "objective": objective,
            "reward_r": reward_r,
        },
    )
    if objective <= close or reward_r < config.sma_min_reward_r:
        return _unresolved(
            symbol,
            "NASOS_REWARD_SPACE_INSUFFICIENT",
            episode_ts,
            diagnostics,
        )

    discount = max(
        0.0,
        values["ema_buy"] * config.sma_offset_low / max(close, _EPS) - 1.0,
    )
    score = (
        1.0
        + min(4.0, discount / 0.003)
        + min(3.0, reward_r)
        + (1.5 if tag == "ewo2" else 1.0 if tag == "ewolow" else 0.5)
    )
    return RouteDecision(
        symbol=symbol,
        state=SMA_OFFSET_STATE,
        side=1,
        score=score,
        entry_reference=close,
        stop_reference=stop,
        objective_reference=objective,
        episode_ts=episode_ts,
        reasons=(
            f"NASOS_SOURCE_{tag.upper()}",
            "NASOS_RECOVERY_SPACE",
            "NASOS_STRUCTURAL_RISK_VALID",
        ),
        diagnostics=diagnostics,
    )


def classify_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    return classify_sma_offset(symbol, bars, feature, config)


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    decisions = {
        symbol: classify_symbol(
            symbol,
            bars,
            features_by_symbol.get(symbol, FeatureObservation(0, ready=False)),
            config,
        )
        for symbol, bars in bars_by_symbol.items()
    }
    actionable = [decision for decision in decisions.values() if decision.actionable]
    actionable.sort(
        key=lambda decision: (
            -decision.score,
            _SYMBOL_PRIORITY.get(decision.symbol, 99),
            decision.symbol,
        ),
    )
    return (actionable[0] if actionable else None), decisions


def sma_offset_exit_ready(
    bars: Sequence[BarObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[bool, dict[str, float | int]]:
    candles, indicators = _state(bars, config)
    if len(candles) < config.sma_trend_slow + 2:
        return False, {
            "sma_exit_ready": 0,
            "sma_exit_history_ready": 0,
            "five_minute_candles": len(candles),
        }
    index = len(candles) - 1
    close = float(candles[index].close)
    values = {name: float(series[index]) for name, series in indicators.items()}
    required = ("ema_sell", "sma9", "hma50", "rsi", "rsi_fast", "rsi_slow")
    if not all(_finite(values[name]) for name in required):
        return False, {
            "sma_exit_ready": 0,
            "sma_exit_history_ready": 0,
            "five_minute_candles": len(candles),
        }
    volume_ok = float(candles[index].volume) > 0.0
    exit_one = (
        close > values["sma9"]
        and close > values["ema_sell"] * config.nasos_high_offset_2
        and values["rsi"] > 50.0
        and values["rsi_fast"] > values["rsi_slow"]
        and volume_ok
    )
    exit_two = (
        close < values["hma50"]
        and close > values["ema_sell"] * config.sma_offset_high
        and values["rsi_fast"] > values["rsi_slow"]
        and volume_ok
    )
    ready = exit_one or exit_two
    return ready, {
        "sma_exit_ready": int(ready),
        "sma_exit_history_ready": 1,
        "nasos_exit_1": int(exit_one),
        "nasos_exit_2": int(exit_two),
        "close": close,
        "ema_sell": values["ema_sell"],
        "hma50": values["hma50"],
        "sma9": values["sma9"],
        "rsi": values["rsi"],
        "rsi_fast": values["rsi_fast"],
        "rsi_slow": values["rsi_slow"],
    }


__all__ = [
    "BarObservation",
    "FeatureObservation",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "classify_sma_offset",
    "classify_symbol",
    "route_universe",
    "sma_offset_exit_ready",
]

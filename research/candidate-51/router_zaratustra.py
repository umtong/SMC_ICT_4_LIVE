"""Candidate 51 adapter for the public ZaratustraV5 futures bot.

The source policy is deliberately preserved at decision level:

* 5m, 15m and 30m RSI must agree above/below 50;
* +DI/-DI on every timeframe must exceed 25 in the trade direction;
* every timeframe close must be on the same side of its 20-period
  Bollinger middle band;
* long and short are both enabled;
* the source 10x leverage stop/trailing percentages are converted to
  equivalent underlying-price distances, while the project shell sizes
  quantity from a strict 3% current-NAV loss budget.

The module is dependency-free so the policy contract can be tested before the
NautilusTrader run.  All observations are completed candles only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Sequence

ZARATUSTRA_STATE = "PUBLIC_ZARATUSTRA_V5_MTF_TREND"
SMA_OFFSET_STATE = ZARATUSTRA_STATE  # compatibility with the reused shell
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
    # Compatibility with Candidate 51's reused execution shell.
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
    zara_rsi_period: int = 14
    zara_di_period: int = 14
    zara_bb_period: int = 20
    zara_rsi_level: float = 50.0
    zara_di_level: float = 25.0
    zara_source_leverage: float = 10.0
    zara_source_stoploss: float = 0.296
    zara_trailing_positive: float = 0.013
    zara_trailing_offset: float = 0.071
    zara_emergency_target_fraction: float = 0.10

    # Legacy constructor fields accepted by strategy.py/config variants.
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
    """Aggregate phase-aligned complete minute bars without assuming 1ns closes."""
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


def _rsi(values: Sequence[float], period: int) -> list[float]:
    """Wilder RSI, matching TA-Lib's causal recurrence closely."""
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


def _directional_indicators(
    candles: Sequence[BarObservation],
    period: int,
) -> tuple[list[float], list[float]]:
    """Wilder +DI and -DI on completed candles."""
    size = len(candles)
    plus = [math.nan] * size
    minus = [math.nan] * size
    if period <= 0 or size <= period:
        return plus, minus
    tr = [0.0] * size
    plus_dm = [0.0] * size
    minus_dm = [0.0] * size
    for index in range(1, size):
        current = candles[index]
        previous = candles[index - 1]
        up = float(current.high) - float(previous.high)
        down = float(previous.low) - float(current.low)
        plus_dm[index] = up if up > down and up > 0.0 else 0.0
        minus_dm[index] = down if down > up and down > 0.0 else 0.0
        previous_close = float(previous.close)
        tr[index] = max(
            float(current.high) - float(current.low),
            abs(float(current.high) - previous_close),
            abs(float(current.low) - previous_close),
        )
    smoothed_tr = sum(tr[1 : period + 1])
    smoothed_plus = sum(plus_dm[1 : period + 1])
    smoothed_minus = sum(minus_dm[1 : period + 1])
    if smoothed_tr > _EPS:
        plus[period] = 100.0 * smoothed_plus / smoothed_tr
        minus[period] = 100.0 * smoothed_minus / smoothed_tr
    for index in range(period + 1, size):
        smoothed_tr = smoothed_tr - smoothed_tr / period + tr[index]
        smoothed_plus = smoothed_plus - smoothed_plus / period + plus_dm[index]
        smoothed_minus = smoothed_minus - smoothed_minus / period + minus_dm[index]
        if smoothed_tr > _EPS:
            plus[index] = 100.0 * smoothed_plus / smoothed_tr
            minus[index] = 100.0 * smoothed_minus / smoothed_tr
    return plus, minus


def _timeframe_state(
    bars: Sequence[BarObservation],
    minutes: int,
    config: RouteConfig,
) -> tuple[list[BarObservation], dict[str, list[float]]]:
    candles = _aggregate_complete(bars, minutes)
    closes = [float(candle.close) for candle in candles]
    # qtpylib.bollinger_bands in the public source is applied to typical_price,
    # not close.  The middle band is therefore the rolling mean of HLC3.
    typical_prices = [
        (float(candle.high) + float(candle.low) + float(candle.close)) / 3.0
        for candle in candles
    ]
    plus_di, minus_di = _directional_indicators(candles, config.zara_di_period)
    return candles, {
        "rsi": _rsi(closes, config.zara_rsi_period),
        "plus_di": plus_di,
        "minus_di": minus_di,
        "bb_mid": _sma(typical_prices, config.zara_bb_period),
    }


def _condition_at(
    candles: Sequence[BarObservation],
    indicators: Mapping[str, Sequence[float]],
    index: int,
    side: int,
    config: RouteConfig,
) -> bool:
    if index < 0 or index >= len(candles):
        return False
    close = float(candles[index].close)
    rsi = float(indicators["rsi"][index])
    plus_di = float(indicators["plus_di"][index])
    minus_di = float(indicators["minus_di"][index])
    middle = float(indicators["bb_mid"][index])
    if not all(_finite(value) for value in (close, rsi, plus_di, minus_di, middle)):
        return False
    if side > 0:
        return (
            rsi > config.zara_rsi_level
            and plus_di > config.zara_di_level
            and close > middle
        )
    return (
        rsi < config.zara_rsi_level
        and minus_di > config.zara_di_level
        and close < middle
    )


def _condition_start(
    candles: Sequence[BarObservation],
    indicators: Mapping[str, Sequence[float]],
    side: int,
    config: RouteConfig,
) -> int:
    index = len(candles) - 1
    if not _condition_at(candles, indicators, index, side, config):
        return 0
    while index > 0 and _condition_at(candles, indicators, index - 1, side, config):
        index -= 1
    return int(candles[index].ts_event)


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

    frames: dict[int, tuple[list[BarObservation], dict[str, list[float]]]] = {
        minutes: _timeframe_state(bars, minutes, config)
        for minutes in (5, 15, 30)
    }
    minimum = max(config.zara_rsi_period + 2, config.zara_di_period + 2, config.zara_bb_period)
    counts = {minutes: len(candles) for minutes, (candles, _) in frames.items()}
    if any(count < minimum for count in counts.values()):
        return _unresolved(
            symbol,
            "ZARA_HISTORY_NOT_READY",
            latest_ts,
            {f"candles_{minutes}m": count for minutes, count in counts.items()},
        )

    long_ok = all(
        _condition_at(candles, indicators, len(candles) - 1, 1, config)
        for candles, indicators in frames.values()
    )
    short_ok = all(
        _condition_at(candles, indicators, len(candles) - 1, -1, config)
        for candles, indicators in frames.values()
    )
    diagnostics: dict[str, float | int | str] = {}
    long_components: list[float] = []
    short_components: list[float] = []
    for minutes, (candles, indicators) in frames.items():
        index = len(candles) - 1
        close = float(candles[index].close)
        rsi = float(indicators["rsi"][index])
        plus_di = float(indicators["plus_di"][index])
        minus_di = float(indicators["minus_di"][index])
        middle = float(indicators["bb_mid"][index])
        diagnostics.update({
            f"close_{minutes}m": close,
            f"rsi_{minutes}m": rsi,
            f"pdi_{minutes}m": plus_di,
            f"mdi_{minutes}m": minus_di,
            f"bbm_{minutes}m": middle,
        })
        long_components.extend((
            max(0.0, rsi - config.zara_rsi_level) / 10.0,
            max(0.0, plus_di - config.zara_di_level) / 10.0,
            max(0.0, close / max(middle, _EPS) - 1.0) * 100.0,
        ))
        short_components.extend((
            max(0.0, config.zara_rsi_level - rsi) / 10.0,
            max(0.0, minus_di - config.zara_di_level) / 10.0,
            max(0.0, 1.0 - close / max(middle, _EPS)) * 100.0,
        ))
    diagnostics["long_condition"] = int(long_ok)
    diagnostics["short_condition"] = int(short_ok)
    if long_ok == short_ok:
        reason = "ZARA_NO_SOURCE_ENTRY" if not long_ok else "ZARA_AMBIGUOUS_SOURCE_ENTRY"
        return _unresolved(symbol, reason, latest_ts, diagnostics)

    side = 1 if long_ok else -1
    starts = [
        _condition_start(candles, indicators, side, config)
        for candles, indicators in frames.values()
    ]
    episode_ts = max(starts)
    entry = float(frames[5][0][-1].close)
    stop_fraction = config.zara_source_stoploss / max(config.zara_source_leverage, _EPS)
    target_fraction = config.zara_emergency_target_fraction
    if side > 0:
        stop = entry * (1.0 - stop_fraction)
        objective = entry * (1.0 + target_fraction)
        score = sum(long_components)
        tag = "Bullish trend"
    else:
        stop = entry * (1.0 + stop_fraction)
        objective = entry * (1.0 - target_fraction)
        score = sum(short_components)
        tag = "Bearish trend"
    diagnostics.update({
        "source_tag": tag,
        "source_leverage": config.zara_source_leverage,
        "source_stoploss_profit_ratio": config.zara_source_stoploss,
        "underlying_stop_fraction": stop_fraction,
        "source_trailing_offset": config.zara_trailing_offset,
        "source_trailing_positive": config.zara_trailing_positive,
        "episode_start_5m": starts[0],
        "episode_start_15m": starts[1],
        "episode_start_30m": starts[2],
    })
    return RouteDecision(
        symbol=symbol,
        state=ZARATUSTRA_STATE,
        side=side,
        score=float(score),
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=objective,
        episode_ts=episode_ts,
        reasons=(
            "PUBLIC_ZARATUSTRA_V5_5M_15M_30M_RSI_DI_BB_AGREEMENT",
            "SOURCE_10X_STOP_CONVERTED_TO_UNDERLYING_DISTANCE",
        ),
        diagnostics=diagnostics,
    )


# Compatibility alias used by the older source-policy adapter.
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
    actionable.sort(
        key=lambda item: (
            -float(item.score),
            _SYMBOL_PRIORITY.get(item.symbol, 99),
            item.symbol,
        ),
    )
    return (actionable[0] if actionable else None), decisions


def sma_offset_exit_ready(
    bars: Sequence[BarObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[bool, dict[str, float | int | str]]:
    """Compatibility hook; Zaratustra exits are handled by its trailing adapter."""
    del bars, config
    return False, {"source_exit": 0, "reason": "ZARA_TRAILING_MANAGED_IN_STRATEGY"}


__all__ = [
    "BarObservation",
    "FeatureObservation",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "ZARATUSTRA_STATE",
    "UNRESOLVED",
    "classify_sma_offset",
    "classify_symbol",
    "route_universe",
    "sma_offset_exit_ready",
]

"""External Candidate 51 adapter for the public Picasso RSI/BB/MACD futures strategy.

This module deliberately preserves two source interpretations:

* ``exact`` keeps the source's Python operator precedence.  The narrow low-ADX
  branch can therefore create a signal without the directional/volume clauses.
* ``corrected`` applies the directional and volume clauses to both ADX ranges.

The source code and its embedded backtest claim are discovery signals only.  All
actual evidence is produced in the project's NautilusTrader account.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Sequence

PICASSO_STATE = "PUBLIC_PICASSO_RSI_BB_MACD_ADX"
SMA_OFFSET_STATE = PICASSO_STATE
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
    atr_period: int = 30
    min_impulse_atr_continuation: float = 0.20
    min_impulse_atr_reversal: float = 1.05
    min_response_atr: float = 0.08
    min_participation_ratio: float = 0.85
    min_route_score: float = 3.20
    ambiguity_score_gap: float = 0.10
    continuation_target_r: float = 2.00
    reversal_target_r: float = 1.65

    picasso_bucket_minutes: int = 15
    picasso_precedence_mode: str = "exact"
    picasso_adx_period: int = 14
    picasso_rsi_long_period: int = 22
    picasso_rsi_short_period: int = 17
    picasso_bb_long_period: int = 16
    picasso_bb_short_period: int = 20
    picasso_volume_long_period: int = 38
    picasso_volume_short_period: int = 20
    picasso_adx_long_min_1: float = 5.7
    picasso_adx_long_max_1: float = 6.5
    picasso_adx_long_min_2: float = 20.9
    picasso_adx_long_max_2: float = 50.7
    picasso_adx_short_min_1: float = 9.9
    picasso_adx_short_max_1: float = 21.4
    picasso_adx_short_min_2: float = 30.3
    picasso_adx_short_max_2: float = 50.8
    picasso_source_effective_leverage: float = 5.0
    picasso_source_stoploss: float = 0.317
    picasso_trailing_positive: float = 0.012
    picasso_trailing_offset: float = 0.030
    picasso_emergency_target_fraction: float = 0.10

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


def _aggregate_complete(bars: Sequence[BarObservation], bucket_minutes: int) -> list[BarObservation]:
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
            ts_event=int(items[-1].ts_event), open=float(items[0].open),
            high=max(float(item.high) for item in items),
            low=min(float(item.low) for item in items), close=float(items[-1].close),
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


def _rolling_mean_shifted(values: Sequence[float], period: int) -> list[float]:
    base = _sma(values, period)
    return [math.nan, *base[:-1]] if base else []


def _rolling_std(values: Sequence[float], period: int) -> list[float]:
    result = [math.nan] * len(values)
    if period <= 0:
        return result
    for index in range(period - 1, len(values)):
        sample = [float(value) for value in values[index - period + 1:index + 1]]
        mean = sum(sample) / period
        result[index] = math.sqrt(sum((value - mean) ** 2 for value in sample) / period)
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
    result = [math.nan] * len(values)
    if period <= 0:
        return result
    start = next((index for index, value in enumerate(values) if _finite(value)), None)
    if start is None or start + period > len(values):
        return result
    seed = values[start:start + period]
    if not all(_finite(value) for value in seed):
        return result
    current = sum(float(value) for value in seed) / period
    result[start + period - 1] = current
    alpha = 2.0 / (period + 1.0)
    for index in range(start + period, len(values)):
        value = float(values[index])
        if not _finite(value):
            continue
        current = alpha * value + (1.0 - alpha) * current
        result[index] = current
    return result


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
    average_gain = sum(gains[1:period + 1]) / period
    average_loss = sum(losses[1:period + 1]) / period

    def convert(gain: float, loss: float) -> float:
        if loss <= _EPS:
            return 100.0 if gain > _EPS else 50.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    result[period] = convert(average_gain, average_loss)
    for index in range(period + 1, len(values)):
        average_gain = (average_gain * (period - 1) + gains[index]) / period
        average_loss = (average_loss * (period - 1) + losses[index]) / period
        result[index] = convert(average_gain, average_loss)
    return result


def _rma(values: Sequence[float], period: int) -> list[float]:
    result = [math.nan] * len(values)
    if period <= 0 or len(values) < period:
        return result
    current = sum(float(value) for value in values[:period]) / period
    result[period - 1] = current
    for index in range(period, len(values)):
        current = (current * (period - 1) + float(values[index])) / period
        result[index] = current
    return result


def _adx(candles: Sequence[BarObservation], period: int) -> list[float]:
    size = len(candles)
    result = [math.nan] * size
    if period <= 0 or size <= period * 2:
        return result
    tr = [0.0] * size
    plus_dm = [0.0] * size
    minus_dm = [0.0] * size
    for index in range(1, size):
        current, previous = candles[index], candles[index - 1]
        up = float(current.high) - float(previous.high)
        down = float(previous.low) - float(current.low)
        plus_dm[index] = up if up > down and up > 0.0 else 0.0
        minus_dm[index] = down if down > up and down > 0.0 else 0.0
        tr[index] = max(float(current.high) - float(current.low),
                        abs(float(current.high) - float(previous.close)),
                        abs(float(current.low) - float(previous.close)))
    atr = _rma(tr[1:], period)
    plus = _rma(plus_dm[1:], period)
    minus = _rma(minus_dm[1:], period)
    dx = [math.nan] * (size - 1)
    for offset in range(size - 1):
        if not all(_finite(value) for value in (atr[offset], plus[offset], minus[offset])):
            continue
        if float(atr[offset]) <= _EPS:
            dx[offset] = 0.0
            continue
        plus_di = 100.0 * float(plus[offset]) / float(atr[offset])
        minus_di = 100.0 * float(minus[offset]) / float(atr[offset])
        dx[offset] = 100.0 * abs(plus_di - minus_di) / max(plus_di + minus_di, _EPS)
    finite_start = next((index for index, value in enumerate(dx) if _finite(value)), None)
    if finite_start is None:
        return result
    core = _rma([float(value) for value in dx[finite_start:]], period)
    for offset, value in enumerate(core):
        target = 1 + finite_start + offset
        if target < size and _finite(value):
            result[target] = float(value)
    return result


def _atr(candles: Sequence[BarObservation], period: int) -> list[float]:
    size = len(candles)
    tr = [0.0] * size
    for index in range(1, size):
        current, previous = candles[index], candles[index - 1]
        tr[index] = max(float(current.high) - float(current.low),
                        abs(float(current.high) - float(previous.close)),
                        abs(float(current.low) - float(previous.close)))
    core = _rma(tr[1:], period)
    result = [math.nan] * size
    for offset, value in enumerate(core):
        if _finite(value):
            result[offset + 1] = float(value)
    return result


def _macd(values: Sequence[float]) -> tuple[list[float], list[float]]:
    fast = _ema(values, 12)
    slow = _ema(values, 26)
    line = [float(a) - float(b) if _finite(a) and _finite(b) else math.nan
            for a, b in zip(fast, slow, strict=True)]
    return line, _ema_nan(line, 9)


def picasso_source_flags(*, mode: str, adx: float, trend_long: bool, trend_short: bool,
                         volume: float, volume_mean_long: float, volume_mean_short: float,
                         pump_warning: bool, config: RouteConfig = RouteConfig()) -> tuple[bool, bool]:
    if pump_warning or not all(_finite(value) for value in (adx, volume)):
        return False, False
    low_long = config.picasso_adx_long_min_1 < adx < config.picasso_adx_long_max_1
    high_long = config.picasso_adx_long_min_2 < adx < config.picasso_adx_long_max_2
    low_short = config.picasso_adx_short_min_1 < adx < config.picasso_adx_short_max_1
    high_short = config.picasso_adx_short_min_2 < adx < config.picasso_adx_short_max_2
    long_volume = _finite(volume_mean_long) and volume > volume_mean_long and volume > 0.0
    short_volume = _finite(volume_mean_short) and volume > volume_mean_short and volume > 0.0
    normalized = str(mode).strip().lower()
    if normalized == "exact":
        return low_long or (high_long and trend_long and long_volume), low_short or (high_short and trend_short and short_volume)
    if normalized == "corrected":
        return (low_long or high_long) and trend_long and long_volume, (low_short or high_short) and trend_short and short_volume
    if normalized == "directional_relaxed":
        return (config.picasso_adx_long_min_1 < adx < config.picasso_adx_long_max_2 and trend_long and volume > 0.0,
                config.picasso_adx_short_min_1 < adx < config.picasso_adx_short_max_2 and trend_short and volume > 0.0)
    raise ValueError(f"unsupported picasso precedence mode: {mode}")


def _signal_at(candles: Sequence[BarObservation], index: int, config: RouteConfig,
               arrays: Mapping[str, Sequence[float]]) -> tuple[bool, bool, dict[str, float | int | str]]:
    candle = candles[index]
    close, opened, high, low, volume = map(float, (candle.close, candle.open, candle.high, candle.low, candle.volume))
    rsi_l, rsi_s = float(arrays["rsi_l"][index]), float(arrays["rsi_s"][index])
    mid_l, mid_s = float(arrays["mid_l"][index]), float(arrays["mid_s"][index])
    std_l, std_s = float(arrays["std_l"][index]), float(arrays["std_s"][index])
    macd, signal, adx = float(arrays["macd"][index]), float(arrays["signal"][index]), float(arrays["adx"][index])
    volume_mean_l = float(arrays["volume_mean_l"][index])
    volume_mean_s = float(arrays["volume_mean_s"][index])
    historical_volume = float(arrays["pump_reference"][index])
    upper_l, lower_s = mid_l + 2.0 * std_l, mid_s - 2.0 * std_s
    trend_long = (rsi_l > 50.0 and close > mid_l and close < upper_l and macd > signal
                  and (high - close) < (close - opened) and close > opened)
    trend_short = (rsi_s < 50.0 and close < mid_s and close > lower_s and macd < signal
                   and (close - low) < (opened - close) and close < opened)
    pump_warning = _finite(historical_volume) and historical_volume > _EPS and volume / historical_volume > 5.0
    long_ok, short_ok = picasso_source_flags(mode=config.picasso_precedence_mode, adx=adx,
        trend_long=trend_long, trend_short=trend_short, volume=volume,
        volume_mean_long=volume_mean_l, volume_mean_short=volume_mean_s,
        pump_warning=pump_warning, config=config)
    diagnostics: dict[str, float | int | str] = {
        "mode": str(config.picasso_precedence_mode), "bucket_minutes": int(config.picasso_bucket_minutes),
        "adx": adx, "rsi_long": rsi_l, "rsi_short": rsi_s,
        "bb_middle_long": mid_l, "bb_upper_long": upper_l,
        "bb_middle_short": mid_s, "bb_lower_short": lower_s,
        "macd": macd, "macd_signal": signal, "volume": volume,
        "volume_mean_long": volume_mean_l, "volume_mean_short": volume_mean_s,
        "pump_reference_volume": historical_volume, "pump_warning": int(pump_warning),
        "trend_long": int(trend_long), "trend_short": int(trend_short),
        "long_condition": int(long_ok), "short_condition": int(short_ok),
    }
    return long_ok, short_ok, diagnostics


def _unresolved(symbol: str, reason: str, episode_ts: int = 0,
                diagnostics: Mapping[str, float | int | str] | None = None) -> RouteDecision:
    return RouteDecision(symbol, UNRESOLVED, 0, 0.0, math.nan, math.nan, math.nan,
                         int(episode_ts), (reason,), dict(diagnostics or {}))


def classify_symbol(symbol: str, bars: Sequence[BarObservation], feature: FeatureObservation,
                    config: RouteConfig = RouteConfig()) -> RouteDecision:
    if not bars:
        return _unresolved(symbol, "NO_MINUTE_BARS")
    latest_ts = int(bars[-1].ts_event)
    if int(feature.observed_time_ns) > latest_ts:
        return _unresolved(symbol, "FUTURE_FEATURE_REJECTED", latest_ts)
    candles = _aggregate_complete(bars, int(config.picasso_bucket_minutes))
    minimum = max(60, config.picasso_volume_long_period + 25,
                  config.picasso_volume_short_period + 25, config.picasso_adx_period * 2 + 5, 35)
    if len(candles) < minimum:
        return _unresolved(symbol, "PICASSO_HISTORY_NOT_READY", latest_ts,
                           {"candles": len(candles), "minimum": minimum})
    closes = [float(candle.close) for candle in candles]
    volumes = [max(0.0, float(candle.volume)) for candle in candles]
    arrays: dict[str, Sequence[float]] = {
        "rsi_l": _rsi(closes, config.picasso_rsi_long_period),
        "rsi_s": _rsi(closes, config.picasso_rsi_short_period),
        "mid_l": _sma(closes, config.picasso_bb_long_period),
        "std_l": _rolling_std(closes, config.picasso_bb_long_period),
        "mid_s": _sma(closes, config.picasso_bb_short_period),
        "std_s": _rolling_std(closes, config.picasso_bb_short_period),
        "adx": _adx(candles, config.picasso_adx_period),
        "volume_mean_l": _rolling_mean_shifted(volumes, config.picasso_volume_long_period),
        "volume_mean_s": _rolling_mean_shifted(volumes, config.picasso_volume_short_period),
    }
    macd, signal = _macd(closes)
    arrays["macd"], arrays["signal"] = macd, signal
    pump_reference = [math.nan] * len(volumes)
    for index in range(len(volumes)):
        if index >= 24:
            sample = volumes[index - 24:index - 19]
            if len(sample) == 5:
                pump_reference[index] = sum(sample) / 5.0
    arrays["pump_reference"] = pump_reference
    index, previous = len(candles) - 1, len(candles) - 2
    required_values = [arrays[name][index] for name in
        ("rsi_l", "rsi_s", "mid_l", "std_l", "mid_s", "std_s", "adx",
         "volume_mean_l", "volume_mean_s", "macd", "signal")]
    if not all(_finite(value) for value in required_values):
        return _unresolved(symbol, "PICASSO_INDICATORS_NOT_READY", candles[index].ts_event)
    long_ok, short_ok, diagnostics = _signal_at(candles, index, config, arrays)
    previous_long, previous_short, _ = _signal_at(candles, previous, config, arrays)
    long_edge, short_edge = long_ok and not previous_long, short_ok and not previous_short
    diagnostics.update({"previous_long_condition": int(previous_long),
                        "previous_short_condition": int(previous_short),
                        "long_rising_edge": int(long_edge), "short_rising_edge": int(short_edge)})
    if long_edge == short_edge:
        return _unresolved(symbol, "PICASSO_NO_SOURCE_EDGE" if not long_edge else "PICASSO_AMBIGUOUS_SOURCE_EDGE",
                           candles[index].ts_event, diagnostics)
    side = 1 if long_edge else -1
    entry = float(candles[index].close)
    leverage = max(float(config.picasso_source_effective_leverage), _EPS)
    stop_fraction = float(config.picasso_source_stoploss) / leverage
    target_fraction = float(config.picasso_emergency_target_fraction)
    stop = entry * (1.0 - side * stop_fraction)
    objective = entry * (1.0 + side * target_fraction)
    directional = int(diagnostics["trend_long"] if side > 0 else diagnostics["trend_short"])
    volume_mean = float(diagnostics["volume_mean_long"] if side > 0 else diagnostics["volume_mean_short"])
    volume_ratio = float(diagnostics["volume"]) / max(volume_mean, _EPS)
    macd_gap = abs(float(diagnostics["macd"]) - float(diagnostics["macd_signal"])) / entry * 10_000.0
    score = 1.0 + 2.0 * directional + min(3.0, max(0.0, volume_ratio - 1.0)) + min(3.0, macd_gap)
    diagnostics.update({"source_tag": "buy_1" if side > 0 else "buy_2",
        "source_effective_leverage": leverage,
        "source_stoploss_profit_ratio": float(config.picasso_source_stoploss),
        "underlying_stop_fraction": stop_fraction,
        "source_trailing_positive": float(config.picasso_trailing_positive),
        "source_trailing_offset": float(config.picasso_trailing_offset),
        "source_precedence_preserved": int(str(config.picasso_precedence_mode).lower() == "exact")})
    return RouteDecision(symbol, PICASSO_STATE, side, float(score), entry, stop, objective,
        int(candles[index].ts_event),
        ("PUBLIC_PICASSO_RSI_BB_MACD_ADX_ENTRY",
         "SOURCE_PRECEDENCE_MODE_" + str(config.picasso_precedence_mode).upper(),
         "SOURCE_RISK_NORMALIZED_BY_EFFECTIVE_LEVERAGE"), diagnostics)


classify_sma_offset = classify_symbol


def route_universe(bars_by_symbol: Mapping[str, Sequence[BarObservation]],
                   features_by_symbol: Mapping[str, FeatureObservation],
                   config: RouteConfig = RouteConfig()) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    decisions = {symbol: classify_symbol(symbol, bars,
        features_by_symbol.get(symbol, FeatureObservation(bars[-1].ts_event if bars else 0, ready=True)), config)
        for symbol, bars in bars_by_symbol.items()}
    actionable = [decision for decision in decisions.values() if decision.actionable]
    actionable.sort(key=lambda item: (-float(item.score), _SYMBOL_PRIORITY.get(item.symbol, 99), int(item.episode_ts)))
    return (actionable[0] if actionable else None), decisions


__all__ = ["BarObservation", "FeatureObservation", "PICASSO_STATE", "RouteConfig",
           "RouteDecision", "SMA_OFFSET_STATE", "UNRESOLVED", "_aggregate_complete",
           "_atr", "_ema", "classify_symbol", "picasso_source_flags", "route_universe"]

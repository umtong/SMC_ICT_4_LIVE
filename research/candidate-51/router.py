"""Candidate 51 causal adaptation of the public Freqtrade SMAOffsetV2 policy.

The source policy is preserved at the decision level:

* completed 1-hour EMA(20) above EMA(25) defines the long regime;
* a completed 5-minute close below SMA(20) times ``sma_offset_low`` opens a
  deep-pullback episode;
* regime failure or a close above SMA(20) times ``sma_offset_high`` exits.

Project-specific additions are limited to causal aggregation, a stable episode
identifier, structural invalidation for current-NAV risk sizing, and ranking the
four-symbol opportunity set into at most one account decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Sequence


SMA_OFFSET_STATE = "SMA_OFFSET_V2_DEEP_PULLBACK_LONG"
UNRESOLVED = "UNRESOLVED"

_EPS = 1e-12
_SYMBOL_PRIORITY = {
    "BTCUSDT": 0,
    "ETHUSDT": 1,
    "SOLUSDT": 2,
    "XRPUSDT": 3,
}


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
    # Constructor compatibility with the reused NautilusTrader execution shell.
    atr_period: int = 30
    min_impulse_atr_continuation: float = 0.20
    min_impulse_atr_reversal: float = 1.05
    min_response_atr: float = 0.08
    min_participation_ratio: float = 0.85
    min_route_score: float = 3.20
    ambiguity_score_gap: float = 0.10
    continuation_target_r: float = 2.00
    reversal_target_r: float = 1.65

    # Public SMAOffsetV2 decision parameters.
    bucket_minutes: int = 5
    sma_offset_period: int = 20
    sma_offset_low: float = 0.960
    sma_offset_high: float = 1.012
    sma_trend_fast: int = 20
    sma_trend_slow: int = 25

    # Project-required executable invalidation.
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


def _mean(values: Sequence[float]) -> float:
    clean = [float(value) for value in values if _finite(float(value))]
    return sum(clean) / len(clean) if clean else math.nan


def _sma(values: Sequence[float], period: int) -> list[float]:
    if period <= 0:
        raise ValueError("SMA period must be positive")
    result = [math.nan] * len(values)
    running = 0.0
    invalid = 0
    for index, raw in enumerate(values):
        value = float(raw)
        if _finite(value):
            running += value
        else:
            invalid += 1
        if index >= period:
            old = float(values[index - period])
            if _finite(old):
                running -= old
            else:
                invalid -= 1
        if index >= period - 1 and invalid == 0:
            result[index] = running / period
    return result


def _ema(values: Sequence[float], period: int) -> list[float]:
    """Causal TA-Lib-style EMA seeded from the first complete SMA window."""
    if period <= 0:
        raise ValueError("EMA period must be positive")
    result = [math.nan] * len(values)
    if len(values) < period:
        return result
    seed_index: int | None = None
    for end in range(period - 1, len(values)):
        window = [float(value) for value in values[end - period + 1 : end + 1]]
        if all(_finite(value) for value in window):
            seed_index = end
            current = sum(window) / period
            result[end] = current
            break
    if seed_index is None:
        return result
    alpha = 2.0 / (period + 1.0)
    current = float(result[seed_index])
    for index in range(seed_index + 1, len(values)):
        value = float(values[index])
        if not _finite(value):
            result[index] = math.nan
            continue
        current = alpha * value + (1.0 - alpha) * current
        result[index] = current
    return result


def _aggregate_complete(
    bars: Sequence[BarObservation],
    bucket_minutes: int,
) -> list[BarObservation]:
    """Aggregate only complete contiguous UTC buckets from minute-end timestamps."""
    if bucket_minutes <= 0:
        raise ValueError("bucket_minutes must be positive")
    minute_ns = 60_000_000_000
    if not bars:
        return []

    # Binance historical klines close one millisecond before a minute
    # boundary, while synthetic contracts commonly use one nanosecond.
    # Derive the phase from the observed stream instead of hard-coding
    # either representation. A phase change is treated as a data gap.
    phases = [int(bar.ts_event) % minute_ns for bar in bars]
    phase = phases[-1]
    grouped: dict[int, list[tuple[int, BarObservation]]] = {}
    for bar, observed_phase in zip(bars, phases, strict=True):
        if observed_phase != phase:
            continue
        ordinal = (int(bar.ts_event) - phase) // minute_ns
        grouped.setdefault(ordinal // bucket_minutes, []).append((ordinal, bar))

    output: list[BarObservation] = []
    for key in sorted(grouped):
        indexed = sorted(grouped[key], key=lambda item: item[0])
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
    return output


def _true_range(bars: Sequence[BarObservation], index: int) -> float:
    current = bars[index]
    if index == 0:
        return max(0.0, float(current.high) - float(current.low))
    previous = float(bars[index - 1].close)
    return max(
        float(current.high) - float(current.low),
        abs(float(current.high) - previous),
        abs(float(current.low) - previous),
    )


def _atr(bars: Sequence[BarObservation], period: int = 14) -> float:
    if len(bars) < period + 1:
        return math.nan
    start = len(bars) - period
    return _mean([_true_range(bars, index) for index in range(start, len(bars))])


def _unresolved(
    symbol: str,
    reason: str,
    *,
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


def _latest_trend(
    minute_bars: Sequence[BarObservation],
    config: RouteConfig,
) -> tuple[bool, dict[str, float | int]]:
    hourly = _aggregate_complete(minute_bars, 60)
    required = max(config.sma_trend_fast, config.sma_trend_slow)
    if len(hourly) < required:
        return False, {
            "hourly_candles": len(hourly),
            "trend_ready": 0,
        }
    closes = [float(bar.close) for bar in hourly]
    fast = _ema(closes, config.sma_trend_fast)[-1]
    slow = _ema(closes, config.sma_trend_slow)[-1]
    ready = _finite(fast) and _finite(slow)
    long_regime = ready and fast > slow
    spread = fast / slow - 1.0 if ready and slow > 0.0 else math.nan
    return bool(long_regime), {
        "hourly_candles": len(hourly),
        "trend_ready": int(ready),
        "ema_fast_1h": fast,
        "ema_slow_1h": slow,
        "ema_spread_fraction": spread,
        "trend_long": int(long_regime),
    }


def _five_minute_state(
    minute_bars: Sequence[BarObservation],
    config: RouteConfig,
) -> tuple[list[BarObservation], list[float], int]:
    candles = _aggregate_complete(minute_bars, config.bucket_minutes)
    closes = [float(bar.close) for bar in candles]
    sma = _sma(closes, config.sma_offset_period)
    return candles, sma, len(candles) - 1


def classify_sma_offset(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    """Classify the latest completed five-minute source candle."""
    if not bars:
        return _unresolved(symbol, "NO_MINUTE_BARS")
    latest_ts = int(bars[-1].ts_event)
    if not feature.ready:
        return _unresolved(symbol, "FEATURE_NOT_READY", episode_ts=latest_ts)
    if int(feature.observed_time_ns) > latest_ts:
        return _unresolved(symbol, "FUTURE_FEATURE_REJECTED", episode_ts=latest_ts)

    candles, sma_values, index = _five_minute_state(bars, config)
    if index < config.sma_offset_period - 1:
        return _unresolved(
            symbol,
            "SMA_OFFSET_HISTORY_NOT_READY",
            episode_ts=candles[-1].ts_event if candles else latest_ts,
            diagnostics={"five_minute_candles": len(candles)},
        )
    trend_long, trend_details = _latest_trend(bars, config)
    episode_fallback = int(candles[index].ts_event)
    if not trend_details.get("trend_ready"):
        return _unresolved(
            symbol,
            "SMA_OFFSET_TREND_HISTORY_NOT_READY",
            episode_ts=episode_fallback,
            diagnostics=trend_details,
        )

    current = candles[index]
    current_sma = float(sma_values[index])
    threshold = current_sma * config.sma_offset_low
    deep = (
        trend_long
        and _finite(current_sma)
        and float(current.close) < threshold
        and float(current.volume) > 0.0
    )

    diagnostics: dict[str, float | int | str] = {
        **trend_details,
        "five_minute_candles": len(candles),
        "sma_5m": current_sma,
        "sma_entry_threshold": threshold,
        "source_close": float(current.close),
        "source_volume": float(current.volume),
        "deep_pullback": int(deep),
        "sma_offset_low": config.sma_offset_low,
        "sma_offset_high": config.sma_offset_high,
    }
    if not trend_long:
        return _unresolved(
            symbol,
            "SMA_OFFSET_LONG_TREND_NOT_PRESENT",
            episode_ts=episode_fallback,
            diagnostics=diagnostics,
        )
    if not deep:
        return _unresolved(
            symbol,
            "SMA_OFFSET_DEEP_PULLBACK_NOT_PRESENT",
            episode_ts=episode_fallback,
            diagnostics=diagnostics,
        )

    deep_flags: list[bool] = []
    for candle_index, candle in enumerate(candles):
        value = float(sma_values[candle_index])
        deep_flags.append(
            _finite(value)
            and float(candle.close) < value * config.sma_offset_low
            and float(candle.volume) > 0.0
        )
    episode_index = index
    while episode_index > 0 and deep_flags[episode_index - 1]:
        episode_index -= 1
    episode_ts = int(candles[episode_index].ts_event)

    entry = float(current.close)
    atr = _atr(candles, 14)
    if not _finite(atr) or atr <= 0.0:
        return _unresolved(
            symbol,
            "SMA_OFFSET_ATR_NOT_READY",
            episode_ts=episode_ts,
            diagnostics=diagnostics,
        )

    lookback = max(1, int(config.sma_structural_lookback))
    structural_low = min(float(candle.low) for candle in candles[-lookback:])
    structural_stop = structural_low - config.sma_stop_atr_buffer * atr
    raw_stop_distance = entry - structural_stop
    minimum_distance = entry * config.sma_stop_min_fraction
    maximum_distance = entry * config.sma_stop_max_fraction
    stop_distance = max(raw_stop_distance, minimum_distance)
    if (
        not _finite(stop_distance)
        or stop_distance <= 0.0
        or stop_distance > maximum_distance
    ):
        diagnostics.update(
            {
                "atr_5m": atr,
                "structural_low": structural_low,
                "structural_stop": structural_stop,
                "raw_stop_distance": raw_stop_distance,
                "maximum_stop_distance": maximum_distance,
            },
        )
        return _unresolved(
            symbol,
            "SMA_OFFSET_STRUCTURAL_STOP_TOO_WIDE",
            episode_ts=episode_ts,
            diagnostics=diagnostics,
        )
    stop = entry - stop_distance
    objective = current_sma * config.sma_offset_high
    reward = objective - entry
    reward_risk = reward / max(stop_distance, _EPS)
    if (
        not _finite(objective)
        or objective <= entry
        or not _finite(reward_risk)
        or reward_risk < config.sma_min_reward_r
    ):
        diagnostics.update(
            {
                "atr_5m": atr,
                "structural_low": structural_low,
                "structural_stop": structural_stop,
                "stop_distance": stop_distance,
                "sma_objective": objective,
                "sma_reward_risk": reward_risk,
            },
        )
        return _unresolved(
            symbol,
            "SMA_OFFSET_REWARD_SPACE_INSUFFICIENT",
            episode_ts=episode_ts,
            diagnostics=diagnostics,
        )

    discount_fraction = max(0.0, threshold / max(entry, _EPS) - 1.0)
    trend_spread = float(trend_details.get("ema_spread_fraction", 0.0))
    score = (
        1.0
        + min(4.0, discount_fraction / 0.005)
        + min(3.0, max(0.0, trend_spread) / 0.0025)
        + min(3.0, reward_risk)
    )
    diagnostics.update(
        {
            "atr_5m": atr,
            "structural_low": structural_low,
            "structural_stop": structural_stop,
            "stop_distance": stop_distance,
            "sma_objective": objective,
            "sma_reward_risk": reward_risk,
            "discount_beyond_threshold": discount_fraction,
            "episode_start_index": episode_index,
        },
    )
    return RouteDecision(
        symbol=symbol,
        state=SMA_OFFSET_STATE,
        side=1,
        score=score,
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=objective,
        episode_ts=episode_ts,
        reasons=(
            "SMA_OFFSET_1H_LONG_REGIME",
            "SMA_OFFSET_5M_DEEP_PULLBACK",
            "SMA_OFFSET_STRUCTURAL_RISK_VALID",
            "SMA_OFFSET_OBJECTIVE_REACHABLE",
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
    """Classify all symbols before selecting at most one actual account action."""
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
    if not actionable:
        return None, decisions
    actionable.sort(
        key=lambda decision: (
            -float(decision.score),
            _SYMBOL_PRIORITY.get(decision.symbol, 99),
            decision.symbol,
        ),
    )
    return actionable[0], decisions


def sma_offset_exit_ready(
    bars: Sequence[BarObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[bool, dict[str, float | int]]:
    """Evaluate the public source exit using completed 5m and 1h candles only."""
    candles, sma_values, index = _five_minute_state(bars, config)
    if index < config.sma_offset_period - 1:
        return False, {
            "sma_exit_ready": 0,
            "sma_exit_history_ready": 0,
            "five_minute_candles": len(candles),
        }
    trend_long, trend_details = _latest_trend(bars, config)
    current = candles[index]
    current_sma = float(sma_values[index])
    upper = current_sma * config.sma_offset_high
    volume_ok = float(current.volume) > 0.0
    upper_exit = _finite(upper) and float(current.close) > upper and volume_ok
    trend_exit = bool(trend_details.get("trend_ready")) and not trend_long and volume_ok
    ready = trend_exit or upper_exit
    return bool(ready), {
        "sma_exit_ready": int(ready),
        "sma_exit_history_ready": 1,
        "sma_exit_trend_failure": int(trend_exit),
        "sma_exit_upper_offset": int(upper_exit),
        "sma_exit_close": float(current.close),
        "sma_exit_threshold": upper,
        "sma_exit_volume": float(current.volume),
        "ema_fast_1h": float(trend_details.get("ema_fast_1h", math.nan)),
        "ema_slow_1h": float(trend_details.get("ema_slow_1h", math.nan)),
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

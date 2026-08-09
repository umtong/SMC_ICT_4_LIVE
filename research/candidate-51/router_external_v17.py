"""External-source tournament for Candidate 51.

Families:
- BTCquant Winner15m: public EMA/MACD/ROC/ADX/volume trend strategy.
- EdgeBot-style VWAP sigma reversion: public 4-sigma-to-VWAP decision description,
  completed with a hard outer-sigma invalidation so it can be executed safely.

The module is deliberately stateless: a signal is actionable only when the
source condition transitions from false to true, making each trade episode
causally independent rather than re-entering every bar of one persistent trend.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Sequence

WINNER_STATE = "PUBLIC_BTCQUANT_WINNER15M"
EDGE_MR_STATE = "PUBLIC_EDGEBOT_VWAP_SIGMA_REVERSION"
SMA_OFFSET_STATE = WINNER_STATE
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
    # Compatibility fields consumed by the reused NautilusTrader shell.
    atr_period: int = 30
    min_impulse_atr_continuation: float = 0.20
    min_impulse_atr_reversal: float = 1.05
    min_response_atr: float = 0.08
    min_participation_ratio: float = 0.85
    min_route_score: float = 3.20
    ambiguity_score_gap: float = 0.10
    continuation_target_r: float = 2.00
    reversal_target_r: float = 1.65

    external_family_mode: str = "winner"
    winner_bucket_minutes: int = 15
    winner_ema_fast: int = 10
    winner_ema_slow: int = 30
    winner_macd_fast: int = 12
    winner_macd_slow: int = 26
    winner_macd_signal: int = 9
    winner_roc_period: int = 3
    winner_roc_threshold: float = 0.10
    winner_adx_period: int = 14
    winner_adx_threshold: float = 18.0
    winner_volume_period: int = 20
    winner_volume_ratio: float = 1.0
    winner_stop_fraction: float = 0.025
    winner_initial_target_fraction: float = 0.080

    edge_bucket_minutes: int = 15
    edge_vwap_period: int = 20
    edge_entry_z: float = 4.0
    edge_stop_z: float = 6.0
    edge_min_sigma_fraction: float = 0.00075
    edge_min_reward_r: float = 1.25

    # Legacy constructor compatibility for old execution adapters.
    bucket_minutes: int = 15
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
            )
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


def _ema_from_finite(values: Sequence[float], period: int) -> list[float]:
    result = [math.nan] * len(values)
    if period <= 0:
        return result
    start = next((i for i, value in enumerate(values) if _finite(value)), None)
    if start is None or start + period > len(values):
        return result
    seed = values[start : start + period]
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


def _macd(
    values: Sequence[float],
    fast: int,
    slow: int,
    signal: int,
) -> tuple[list[float], list[float]]:
    fast_values = _ema(values, fast)
    slow_values = _ema(values, slow)
    line = [
        a - b if _finite(a) and _finite(b) else math.nan
        for a, b in zip(fast_values, slow_values, strict=True)
    ]
    return line, _ema_from_finite(line, signal)


def _roc(values: Sequence[float], period: int) -> list[float]:
    result = [math.nan] * len(values)
    if period <= 0:
        return result
    for index in range(period, len(values)):
        previous = float(values[index - period])
        if abs(previous) > _EPS:
            result[index] = (float(values[index]) / previous - 1.0) * 100.0
    return result


def _adx(bars: Sequence[BarObservation], period: int) -> list[float]:
    """Wilder ADX aligned to the input bars."""
    size = len(bars)
    result = [math.nan] * size
    if period <= 0 or size < period * 2 + 1:
        return result
    tr = [0.0] * size
    plus_dm = [0.0] * size
    minus_dm = [0.0] * size
    for index in range(1, size):
        current = bars[index]
        previous = bars[index - 1]
        up = float(current.high) - float(previous.high)
        down = float(previous.low) - float(current.low)
        plus_dm[index] = up if up > down and up > 0.0 else 0.0
        minus_dm[index] = down if down > up and down > 0.0 else 0.0
        tr[index] = max(
            float(current.high) - float(current.low),
            abs(float(current.high) - float(previous.close)),
            abs(float(current.low) - float(previous.close)),
        )

    smoothed_tr = sum(tr[1 : period + 1])
    smoothed_plus = sum(plus_dm[1 : period + 1])
    smoothed_minus = sum(minus_dm[1 : period + 1])
    dx = [math.nan] * size
    for index in range(period, size):
        if index > period:
            smoothed_tr = smoothed_tr - smoothed_tr / period + tr[index]
            smoothed_plus = smoothed_plus - smoothed_plus / period + plus_dm[index]
            smoothed_minus = smoothed_minus - smoothed_minus / period + minus_dm[index]
        if smoothed_tr <= _EPS:
            plus_di = minus_di = 0.0
        else:
            plus_di = 100.0 * smoothed_plus / smoothed_tr
            minus_di = 100.0 * smoothed_minus / smoothed_tr
        denominator = plus_di + minus_di
        dx[index] = (
            100.0 * abs(plus_di - minus_di) / denominator
            if denominator > _EPS
            else 0.0
        )
    first_adx_index = period * 2 - 1
    seed = [dx[index] for index in range(period, first_adx_index + 1)]
    if not all(_finite(value) for value in seed):
        return result
    current_adx = sum(float(value) for value in seed) / period
    result[first_adx_index] = current_adx
    for index in range(first_adx_index + 1, size):
        current_adx = (
            current_adx * (period - 1) + float(dx[index])
        ) / period
        result[index] = current_adx
    return result


def _winner_condition(
    candles: Sequence[BarObservation],
    config: RouteConfig,
    index: int,
) -> tuple[int, dict[str, float | int | str]]:
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
    fields = {
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
        return 0, {**fields, "ready": 0}
    volume_ratio = fields["volume"] / max(fields["volume_sma"], _EPS)
    long_ok = (
        fields["ema_fast"] > fields["ema_slow"]
        and fields["macd"] > fields["macd_signal"]
        and fields["roc"] > config.winner_roc_threshold
        and fields["adx"] > config.winner_adx_threshold
        and volume_ratio > config.winner_volume_ratio
        and fields["volume"] > 0.0
    )
    short_ok = (
        fields["ema_fast"] < fields["ema_slow"]
        and fields["macd"] < fields["macd_signal"]
        and fields["roc"] < -config.winner_roc_threshold
        and fields["adx"] > config.winner_adx_threshold
        and volume_ratio > config.winner_volume_ratio
        and fields["volume"] > 0.0
    )
    side = 1 if long_ok and not short_ok else -1 if short_ok and not long_ok else 0
    diagnostics: dict[str, float | int | str] = {
        **fields,
        "ready": 1,
        "volume_ratio": volume_ratio,
        "long_condition": int(long_ok),
        "short_condition": int(short_ok),
    }
    return side, diagnostics


def _classify_winner(
    symbol: str,
    bars: Sequence[BarObservation],
    config: RouteConfig,
) -> RouteDecision:
    candles = _aggregate_complete(bars, config.winner_bucket_minutes)
    minimum = max(
        config.winner_ema_slow + 3,
        config.winner_macd_slow + config.winner_macd_signal + 3,
        config.winner_adx_period * 2 + 3,
        config.winner_volume_period + 3,
    )
    if len(candles) < minimum:
        return _unresolved(
            symbol,
            "WINNER_HISTORY_NOT_READY",
            bars[-1].ts_event if bars else 0,
            {"candles": len(candles), "minimum": minimum},
        )
    index = len(candles) - 1
    side, diagnostics = _winner_condition(candles, config, index)
    previous_side, _ = _winner_condition(candles, config, index - 1)
    diagnostics["previous_side"] = previous_side
    if side == 0:
        return _unresolved(
            symbol,
            "WINNER_NO_SOURCE_ENTRY",
            candles[index].ts_event,
            diagnostics,
        )
    if previous_side == side:
        return _unresolved(
            symbol,
            "WINNER_PERSISTENT_SOURCE_EPISODE",
            candles[index].ts_event,
            diagnostics,
        )
    entry = float(candles[index].close)
    stop_fraction = max(config.winner_stop_fraction, 1e-6)
    target_fraction = max(config.winner_initial_target_fraction, 1e-6)
    stop = entry * (1.0 - side * stop_fraction)
    objective = entry * (1.0 + side * target_fraction)
    reward_r = target_fraction / stop_fraction
    quality = (
        max(0.0, float(diagnostics["adx"]) - config.winner_adx_threshold) / 10.0
        + max(
            0.0,
            abs(float(diagnostics["roc"])) - config.winner_roc_threshold,
        )
        + max(0.0, float(diagnostics["volume_ratio"]) - config.winner_volume_ratio)
    )
    diagnostics.update(
        {
            "source_tag": "L" if side > 0 else "S",
            "reward_r": reward_r,
            "source_stop_fraction": stop_fraction,
            "source_initial_roi": target_fraction,
            "family": "BTCquant_Winner15m",
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
        episode_ts=int(candles[index].ts_event),
        reasons=(
            "PUBLIC_BTCQUANT_EMA_MACD_ROC_ADX_VOLUME_ENTRY",
            "ENTRY_ONLY_ON_NEW_SOURCE_CONDITION_EPISODE",
        ),
        diagnostics=diagnostics,
    )


def _weighted_mean_sigma(
    candles: Sequence[BarObservation],
    period: int,
    index: int,
) -> tuple[float, float]:
    if period <= 1 or index + 1 < period:
        return math.nan, math.nan
    window = candles[index - period + 1 : index + 1]
    weights = [max(float(candle.volume), 0.0) for candle in window]
    total = sum(weights)
    if total <= _EPS:
        weights = [1.0] * len(window)
        total = float(len(window))
    mean = sum(
        float(candle.close) * weight
        for candle, weight in zip(window, weights, strict=True)
    ) / total
    variance = sum(
        weight * (float(candle.close) - mean) ** 2
        for candle, weight in zip(window, weights, strict=True)
    ) / total
    return mean, math.sqrt(max(variance, 0.0))


def _edge_side(
    candles: Sequence[BarObservation],
    config: RouteConfig,
    index: int,
) -> tuple[int, dict[str, float | int | str]]:
    mean, sigma = _weighted_mean_sigma(
        candles,
        config.edge_vwap_period,
        index,
    )
    close = float(candles[index].close)
    sigma_fraction = sigma / max(abs(mean), _EPS) if _finite(sigma) else math.nan
    zscore = (
        (close - mean) / sigma
        if _finite(mean) and _finite(sigma) and sigma > _EPS
        else math.nan
    )
    ready = (
        _finite(mean)
        and _finite(sigma)
        and _finite(zscore)
        and sigma_fraction >= config.edge_min_sigma_fraction
    )
    side = (
        -1
        if ready and zscore >= config.edge_entry_z
        else 1
        if ready and zscore <= -config.edge_entry_z
        else 0
    )
    return side, {
        "close": close,
        "vwap_mean": mean,
        "sigma": sigma,
        "sigma_fraction": sigma_fraction,
        "zscore": zscore,
        "ready": int(ready),
        "entry_z": config.edge_entry_z,
        "stop_z": config.edge_stop_z,
    }


def _classify_edge(
    symbol: str,
    bars: Sequence[BarObservation],
    config: RouteConfig,
) -> RouteDecision:
    candles = _aggregate_complete(bars, config.edge_bucket_minutes)
    minimum = config.edge_vwap_period + 2
    if len(candles) < minimum:
        return _unresolved(
            symbol,
            "EDGE_MR_HISTORY_NOT_READY",
            bars[-1].ts_event if bars else 0,
            {"candles": len(candles), "minimum": minimum},
        )
    index = len(candles) - 1
    side, diagnostics = _edge_side(candles, config, index)
    previous_side, _ = _edge_side(candles, config, index - 1)
    diagnostics["previous_side"] = previous_side
    if side == 0:
        return _unresolved(
            symbol,
            "EDGE_MR_NO_QUALIFYING_DEVIATION",
            candles[index].ts_event,
            diagnostics,
        )
    if previous_side == side:
        return _unresolved(
            symbol,
            "EDGE_MR_PERSISTENT_DEVIATION_EPISODE",
            candles[index].ts_event,
            diagnostics,
        )
    entry = float(diagnostics["close"])
    mean = float(diagnostics["vwap_mean"])
    sigma = float(diagnostics["sigma"])
    stop = mean - config.edge_stop_z * sigma if side > 0 else mean + config.edge_stop_z * sigma
    objective = mean
    risk = abs(entry - stop)
    reward = abs(objective - entry)
    reward_r = reward / max(risk, _EPS)
    diagnostics.update(
        {
            "reward_r": reward_r,
            "family": "EdgeBot_mr_meanrev_v3_description",
            "invalidation_completion": "OUTER_SIGMA_HARD_STOP",
        }
    )
    if not (
        (side > 0 and stop < entry < objective)
        or (side < 0 and objective < entry < stop)
    ):
        return _unresolved(
            symbol,
            "EDGE_MR_INVALID_GEOMETRY",
            candles[index].ts_event,
            diagnostics,
        )
    if reward_r < config.edge_min_reward_r:
        return _unresolved(
            symbol,
            "EDGE_MR_INSUFFICIENT_REWARD_SPACE",
            candles[index].ts_event,
            diagnostics,
        )
    excess = max(0.0, abs(float(diagnostics["zscore"])) - config.edge_entry_z)
    return RouteDecision(
        symbol=symbol,
        state=EDGE_MR_STATE,
        side=side,
        score=reward_r * 10.0 + excess,
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=objective,
        episode_ts=int(candles[index].ts_event),
        reasons=(
            "PUBLIC_EDGEBOT_FOUR_SIGMA_VWAP_DEVIATION",
            "PUBLIC_MEAN_EXIT_WITH_OUTER_SIGMA_INVALIDATION",
        ),
        diagnostics=diagnostics,
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
    if mode == "winner":
        return _classify_winner(symbol, bars, config)
    if mode == "edge":
        return _classify_edge(symbol, bars, config)
    if mode == "combined":
        winner = _classify_winner(symbol, bars, config)
        edge = _classify_edge(symbol, bars, config)
        candidates = [item for item in (winner, edge) if item.actionable]
        if not candidates:
            return _unresolved(
                symbol,
                "EXTERNAL_TOURNAMENT_NO_FAMILY_ENTRY",
                latest_ts,
                {
                    "winner_reason": "|".join(winner.reasons),
                    "edge_reason": "|".join(edge.reasons),
                },
            )
        candidates.sort(key=lambda item: (-item.score, item.state))
        return candidates[0]
    return _unresolved(
        symbol,
        "UNKNOWN_EXTERNAL_FAMILY_MODE",
        latest_ts,
        {"mode": mode},
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

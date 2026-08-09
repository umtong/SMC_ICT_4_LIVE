"""Live-effective Pasindu Supertrend policies on completed bars.

The external repository's strategy class says ATR10 x 3, but the orchestrator
actually calls ``IndicatorEngine.calculate_all()`` without overrides, so live
signals consume Supertrend ATR8 x 2.  This module implements that executable
wiring and the two source-defined policies worth testing:

* ``flip_only``: direct completed 4H Supertrend direction flips;
* ``reduced_live``: direct 4H flip, otherwise the active 1H continuation level.

The repository's disabled 15m, aligned-trend, and higher-timeframe-cascade
routes are not resurrected.  Reported external returns are not evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Sequence

from router_picasso import (
    BarObservation,
    _adx,
    _aggregate_complete,
    _ema,
    _rolling_std,
    _rsi,
    _sma,
)

PASINDU_FLIP_STATE = "PASINDU_LIVE_ST8X2_4H_FLIP"
PASINDU_CONTINUATION_STATE = "PASINDU_LIVE_ST8X2_1H_CONTINUATION"
SMA_OFFSET_STATE = PASINDU_FLIP_STATE
UNRESOLVED = "UNRESOLVED"
_EPS = 1e-12
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}


@dataclass(frozen=True, slots=True)
class FeatureObservation:
    observed_time_ns: int
    ready: bool = True


@dataclass(frozen=True, slots=True)
class RouteConfig:
    # Generic execution-shell compatibility.
    atr_period: int = 30
    min_impulse_atr_continuation: float = 0.20
    min_impulse_atr_reversal: float = 1.05
    min_response_atr: float = 0.08
    min_participation_ratio: float = 0.85
    min_route_score: float = 3.20
    ambiguity_score_gap: float = 0.10
    continuation_target_r: float = 2.00
    reversal_target_r: float = 1.65

    pasindu_mode: str = "flip_only"  # flip_only | reduced_live
    pasindu_supertrend_period: int = 8
    pasindu_supertrend_multiplier: float = 2.0
    pasindu_adx_period: int = 14
    pasindu_adx_min: float = 18.0
    pasindu_confidence_min: float = 45.0
    pasindu_established_4h_bars: int = 3
    pasindu_continuation_lookback_1h: int = 8
    pasindu_trail_activate_atr: float = 2.0
    pasindu_trail_distance_atr: float = 2.5

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


@dataclass(frozen=True, slots=True)
class IndicatorState:
    ts_event: int
    close: float
    atr: float
    adx: float
    ema9: float
    ema21: float
    rsi: float
    supertrend: float
    direction: int
    bb_width_ratio: float
    atr_ratio: float
    volume_ratio: float
    hurst: float
    regime: str
    regime_confidence: float


@dataclass(frozen=True, slots=True)
class SourceSignal:
    state: str
    side: int
    confidence: float
    entry: float
    stop: float
    target: float
    episode_ts: int
    regime: str
    atr: float
    signal_kind: str
    flip_age: int
    diagnostics: Mapping[str, float | int | str]


# ---- Exact live indicator plumbing ---------------------------------------


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(value)) for value in values)


def _true_ranges(candles: Sequence[BarObservation]) -> list[float]:
    if not candles:
        return []
    result = [math.nan] * len(candles)
    for index in range(1, len(candles)):
        current = candles[index]
        previous = candles[index - 1]
        result[index] = max(
            float(current.high) - float(current.low),
            abs(float(current.high) - float(previous.close)),
            abs(float(current.low) - float(previous.close)),
        )
    return result


def _wilder_atr(candles: Sequence[BarObservation], period: int) -> list[float]:
    size = len(candles)
    result = [math.nan] * size
    if period <= 0 or size <= period:
        return result
    tr = _true_ranges(candles)
    seed = tr[1 : period + 1]
    if len(seed) != period or not all(math.isfinite(value) for value in seed):
        return result
    result[period] = sum(seed) / period
    for index in range(period + 1, size):
        result[index] = (
            result[index - 1] * (period - 1) + tr[index]
        ) / period
    return result


def _supertrend(
    candles: Sequence[BarObservation],
    period: int,
    multiplier: float,
) -> tuple[list[float], list[float]]:
    size = len(candles)
    trend = [math.nan] * size
    direction = [math.nan] * size
    if not candles:
        return trend, direction
    atr = _wilder_atr(candles, period)
    upper = [math.nan] * size
    lower = [math.nan] * size
    for index, candle in enumerate(candles):
        if not math.isfinite(atr[index]):
            continue
        midpoint = 0.5 * (float(candle.high) + float(candle.low))
        upper[index] = midpoint + multiplier * atr[index]
        lower[index] = midpoint - multiplier * atr[index]
    trend[0] = upper[0] if math.isfinite(upper[0]) else 0.0
    direction[0] = 1.0
    for index in range(1, size):
        if not math.isfinite(atr[index]):
            continue
        if (
            lower[index] > 0.0
            and math.isfinite(lower[index - 1])
            and lower[index] < lower[index - 1]
            and float(candles[index - 1].close) > lower[index - 1]
        ):
            lower[index] = lower[index - 1]
        if (
            upper[index] > 0.0
            and math.isfinite(upper[index - 1])
            and upper[index] > upper[index - 1]
            and float(candles[index - 1].close) < upper[index - 1]
        ):
            upper[index] = upper[index - 1]
        previous_direction = direction[index - 1]
        if not math.isfinite(previous_direction):
            previous_direction = 1.0
        if previous_direction == 1.0:
            if float(candles[index].close) < lower[index]:
                direction[index] = -1.0
                trend[index] = upper[index]
            else:
                direction[index] = 1.0
                trend[index] = lower[index]
        else:
            if float(candles[index].close) > upper[index]:
                direction[index] = 1.0
                trend[index] = lower[index]
            else:
                direction[index] = -1.0
                trend[index] = upper[index]
    return trend, direction


def _hurst(candles: Sequence[BarObservation], max_lag: int = 100) -> float:
    values = [float(candle.close) for candle in candles if float(candle.close) > 0.0]
    if len(values) < 21:
        return 0.5
    returns = [math.log(values[index] / values[index - 1]) for index in range(1, len(values))]
    max_k = min(max_lag, len(returns) // 2)
    if max_k < 4:
        return 0.5
    lags: list[int] = []
    rs_means: list[float] = []
    k = 4
    while k <= max_k:
        lags.append(k)
        k = int(k * 1.5) if k < 16 else k * 2
    if lags and lags[-1] != max_k and max_k > lags[-1]:
        lags.append(max_k)
    used_lags: list[int] = []
    for lag in lags:
        chunks = len(returns) // lag
        ratios: list[float] = []
        for chunk_index in range(chunks):
            chunk = returns[chunk_index * lag : (chunk_index + 1) * lag]
            mean = sum(chunk) / len(chunk)
            cumulative = []
            running = 0.0
            for value in chunk:
                running += value - mean
                cumulative.append(running)
            spread = max(cumulative) - min(cumulative)
            variance = sum((value - mean) ** 2 for value in chunk) / max(1, len(chunk) - 1)
            standard = math.sqrt(max(variance, 0.0))
            if standard > 1e-12:
                ratios.append(spread / standard)
        if ratios:
            used_lags.append(lag)
            rs_means.append(sum(ratios) / len(ratios))
    if len(rs_means) < 2 or any(value <= 0.0 for value in rs_means):
        return 0.5
    x = [math.log(float(value)) for value in used_lags]
    y = [math.log(float(value)) for value in rs_means]
    mx = sum(x) / len(x)
    my = sum(y) / len(y)
    denominator = sum((value - mx) ** 2 for value in x)
    if denominator <= _EPS:
        return 0.5
    slope = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / denominator
    return max(0.0, min(1.0, slope))


def _regime_scores(adx: float, bbr: float, atrr: float, volr: float, hurst: float) -> dict[str, float]:
    trending = 0.0
    if adx >= 20.0:
        trending += 1.0 + min(0.5, (adx - 20.0) / 25.0)
    elif adx >= 15.0:
        trending += 0.2
    if 0.8 <= bbr <= 1.5:
        trending += 0.8
    elif bbr > 1.5:
        trending += 0.3
    if 0.8 <= atrr <= 1.4:
        trending += 0.8
    elif atrr > 1.4:
        trending += 0.3
    elif atrr >= 0.5:
        trending += 0.4
    if volr >= 0.7:
        trending += 0.6 + min(0.4, (volr - 0.7) / 2.0)
    elif volr >= 0.2:
        trending += 0.3
    if hurst >= 0.6:
        trending += 0.5 + min(0.3, (hurst - 0.6) / 0.4)
    elif hurst >= 0.5:
        trending += 0.1

    ranging = 0.0
    if adx <= 20.0:
        ranging += 1.0 + min(0.5, (20.0 - adx) / 20.0)
    elif adx <= 25.0:
        ranging += 0.3
    if bbr <= 0.8:
        ranging += 1.0
    elif bbr <= 1.0:
        ranging += 0.5
    if atrr <= 0.8:
        ranging += 1.0
    elif atrr <= 1.0:
        ranging += 0.4
    if volr <= 0.7:
        ranging += 0.8
    elif volr <= 1.0:
        ranging += 0.3
    if adx >= 20.0:
        ranging *= 0.3
    if hurst <= 0.4:
        ranging += 0.5 + min(0.3, (0.4 - hurst) / 0.4)
    elif hurst <= 0.5:
        ranging += 0.1

    volatile = 0.0
    if 15.0 <= adx <= 30.0:
        volatile += 0.8
    elif adx > 30.0:
        volatile += 0.3
    if bbr >= 1.5:
        volatile += 1.2 + min(0.3, (bbr - 1.5) / 2.0)
    elif bbr >= 1.2:
        volatile += 0.5
    if atrr >= 1.2:
        volatile += 1.0 + min(0.3, (atrr - 1.2) / 1.0)
    elif atrr >= 1.0:
        volatile += 0.4
    if volr >= 1.5:
        volatile += 1.0
    elif volr >= 1.0:
        volatile += 0.3

    quiet = 0.0
    if adx <= 15.0:
        quiet += 1.2 + min(0.3, (15.0 - adx) / 15.0)
    elif adx <= 20.0:
        quiet += 0.3
    if bbr <= 0.5:
        quiet += 1.2
    elif bbr <= 0.8:
        quiet += 0.6
    if atrr <= 0.5:
        quiet += 1.0
    elif atrr <= 0.8:
        quiet += 0.5
    if volr <= 0.5:
        quiet += 1.0
    elif volr <= 0.7:
        quiet += 0.4
    return {"trending": trending, "ranging": ranging, "volatile": volatile, "quiet": quiet}


def _indicator_state(candles: Sequence[BarObservation], config: RouteConfig) -> IndicatorState | None:
    if len(candles) < 105:
        return None
    closes = [float(candle.close) for candle in candles]
    volumes = [max(0.0, float(candle.volume)) for candle in candles]
    trend, directions = _supertrend(
        candles,
        int(config.pasindu_supertrend_period),
        float(config.pasindu_supertrend_multiplier),
    )
    atr = _wilder_atr(candles, 14)
    adx = _adx(candles, int(config.pasindu_adx_period))
    ema9 = _ema(closes, 9)
    ema21 = _ema(closes, 21)
    rsi = _rsi(closes, 14)
    middle = _sma(closes, 20)
    standard = _rolling_std(closes, 20)
    widths = [
        4.0 * float(std) / max(abs(float(mid)), _EPS)
        if _finite(std, mid) else math.nan
        for std, mid in zip(standard, middle, strict=True)
    ]
    clean_widths = [0.0 if not math.isfinite(value) else value for value in widths]
    width_average = _sma(clean_widths, 100)
    atr_average = _sma([0.0 if not math.isfinite(value) else value for value in atr], 100)
    volume_average = _sma(volumes, 20)
    required = (
        trend[-1], directions[-1], atr[-1], adx[-1], ema9[-1], ema21[-1], rsi[-1],
        widths[-1], width_average[-1], atr_average[-1], volume_average[-1],
    )
    if not _finite(*required) or width_average[-1] <= _EPS or atr_average[-1] <= _EPS or volume_average[-1] <= _EPS:
        return None
    bbr = widths[-1] / width_average[-1]
    atrr = atr[-1] / atr_average[-1]
    volr = volumes[-1] / volume_average[-1]
    hurst = _hurst(candles[-101:])
    scores = _regime_scores(float(adx[-1]), bbr, atrr, volr, hurst)
    regime = max(scores, key=scores.get)
    confidence = min(100.0, max(0.0, scores[regime] / 4.0 * 100.0))
    candle = candles[-1]
    return IndicatorState(
        ts_event=int(candle.ts_event), close=float(candle.close), atr=float(atr[-1]),
        adx=float(adx[-1]), ema9=float(ema9[-1]), ema21=float(ema21[-1]),
        rsi=float(rsi[-1]), supertrend=float(trend[-1]), direction=int(directions[-1]),
        bb_width_ratio=bbr, atr_ratio=atrr, volume_ratio=volr, hurst=hurst,
        regime=regime, regime_confidence=confidence,
    )


def _confidence(state: IndicatorState, side: int, continuation: bool) -> float:
    score = 30.0 if continuation else 40.0
    score += min(20.0, max(0.0, (state.adx - 18.0) / 22.0) * 20.0)
    aligned = (side > 0 and state.ema9 > state.ema21) or (side < 0 and state.ema9 < state.ema21)
    score += 20.0 if aligned else 5.0
    if (side > 0 and 30.0 < state.rsi < 65.0) or (side < 0 and 35.0 < state.rsi < 70.0):
        score += 10.0
    if not continuation:
        score += 10.0
    return min(80.0 if continuation else 100.0, max(0.0, score))


def _recent_flip(directions: Sequence[float], expected: int, lookback: int) -> tuple[int, int] | None:
    clean = [(index, int(value)) for index, value in enumerate(directions) if math.isfinite(value)]
    if len(clean) < 2:
        return None
    max_check = min(lookback, len(clean) - 1)
    for age in range(1, max_check + 1):
        current_index, current = clean[-age]
        _, previous = clean[-age - 1]
        if previous != expected and current == expected:
            return age, current_index
    return None


def _source_signal(
    hours: Sequence[BarObservation],
    four_hours: Sequence[BarObservation],
    config: RouteConfig,
) -> SourceSignal | None:
    if len(hours) < 105 or len(four_hours) < 105:
        return None
    state4 = _indicator_state(four_hours, config)
    if state4 is None or state4.adx < float(config.pasindu_adx_min):
        return None
    if state4.regime not in {"trending", "ranging"}:
        return None
    # Reduced live dead-zone rule also rejects ranging ADX below 18.
    if state4.regime == "ranging" and state4.adx < 18.0:
        return None
    _, directions4 = _supertrend(
        four_hours,
        int(config.pasindu_supertrend_period),
        float(config.pasindu_supertrend_multiplier),
    )
    valid4 = [int(value) for value in directions4 if math.isfinite(value)]
    if len(valid4) < 2:
        return None
    current4, previous4 = valid4[-1], valid4[-2]
    current_hour = hours[-1]
    side = 0
    kind = ""
    episode_ts = int(four_hours[-1].ts_event)
    flip_age = 1
    if previous4 != current4:
        side = current4
        kind = "4h_flip"
    elif str(config.pasindu_mode).strip().lower() == "reduced_live":
        established = int(config.pasindu_established_4h_bars)
        if len(valid4) < established or len(set(valid4[-established:])) != 1:
            return None
        _, directions1 = _supertrend(
            hours,
            int(config.pasindu_supertrend_period),
            float(config.pasindu_supertrend_multiplier),
        )
        valid1 = [value for value in directions1 if math.isfinite(value)]
        if not valid1 or int(valid1[-1]) != current4:
            return None
        recent = _recent_flip(
            directions1,
            current4,
            int(config.pasindu_continuation_lookback_1h),
        )
        if recent is None:
            return None
        flip_age, source_index = recent
        side = current4
        kind = "1h_continuation"
        episode_ts = int(hours[source_index].ts_event)
    else:
        return None

    continuation = kind == "1h_continuation"
    confidence = _confidence(state4, side, continuation)
    if continuation:
        confidence *= max(0.6, 1.0 - (flip_age - 1) * 0.1)
    if confidence < float(config.pasindu_confidence_min):
        return None
    entry = float(current_hour.close)
    sl_mult, tp_mult = (2.5, 5.0) if state4.regime == "ranging" else (3.0, 6.0)
    stop = entry - side * state4.atr * sl_mult
    target = entry + side * state4.atr * tp_mult
    valid = 0.0 < stop < entry < target if side > 0 else 0.0 < target < entry < stop
    if not valid:
        return None
    return SourceSignal(
        state=PASINDU_CONTINUATION_STATE if continuation else PASINDU_FLIP_STATE,
        side=side, confidence=confidence, entry=entry, stop=stop, target=target,
        episode_ts=episode_ts, regime=state4.regime, atr=state4.atr,
        signal_kind=kind, flip_age=flip_age,
        diagnostics={
            "live_supertrend_period": int(config.pasindu_supertrend_period),
            "live_supertrend_multiplier": float(config.pasindu_supertrend_multiplier),
            "signal_kind": kind, "flip_age": flip_age, "regime": state4.regime,
            "regime_confidence": state4.regime_confidence, "adx_4h": state4.adx,
            "ema9_4h": state4.ema9, "ema21_4h": state4.ema21,
            "rsi_4h": state4.rsi, "atr_4h": state4.atr,
            "supertrend_4h": state4.supertrend, "supertrend_direction_4h": state4.direction,
            "bb_width_ratio": state4.bb_width_ratio, "atr_ratio": state4.atr_ratio,
            "volume_ratio": state4.volume_ratio, "hurst": state4.hurst,
            "sl_atr_mult": sl_mult, "tp_atr_mult": tp_mult,
            "confidence": confidence,
        },
    )


def _unresolved(symbol: str, reason: str, ts: int = 0) -> RouteDecision:
    return RouteDecision(symbol, UNRESOLVED, 0, 0.0, math.nan, math.nan, math.nan, ts, (reason,), {})


def route_universe_aggregated(
    hours_by_symbol: Mapping[str, Sequence[BarObservation]],
    four_hours_by_symbol: Mapping[str, Sequence[BarObservation]],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    decisions: dict[str, RouteDecision] = {}
    for symbol, hours in hours_by_symbol.items():
        signal = _source_signal(hours, four_hours_by_symbol.get(symbol, ()), config)
        if signal is None:
            ts = int(hours[-1].ts_event) if hours else 0
            decisions[symbol] = _unresolved(symbol, "PASINDU_NO_SOURCE_SIGNAL", ts)
            continue
        diagnostics = dict(signal.diagnostics)
        diagnostics.update({"source_policy": str(config.pasindu_mode), "atr_at_entry": signal.atr})
        decisions[symbol] = RouteDecision(
            symbol=symbol, state=signal.state, side=signal.side,
            score=float(signal.confidence), entry_reference=signal.entry,
            stop_reference=signal.stop, objective_reference=signal.target,
            episode_ts=signal.episode_ts,
            reasons=(
                "LIVE_EFFECTIVE_SUPERTREND8X2",
                f"SOURCE_{signal.signal_kind.upper()}",
                "SOURCE_REDUCED_LIVE_REGIME_ROUTER",
                "SOURCE_CONFIDENCE_GATE_45",
                "SOURCE_REGIME_SPECIFIC_TWO_R_BRACKET",
                "SOURCE_REVERSAL_AND_ATR_TRAIL_MANAGEMENT",
            ),
            diagnostics=diagnostics,
        )
    actionable = [decision for decision in decisions.values() if decision.actionable]
    actionable.sort(key=lambda item: (-item.score, _SYMBOL_PRIORITY.get(item.symbol, 99), item.episode_ts))
    return (actionable[0] if actionable else None), decisions


def classify_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    del feature
    hours = _aggregate_complete(bars, 60)
    four_hours = _aggregate_complete(bars, 240)
    _, decisions = route_universe_aggregated({symbol: hours}, {symbol: four_hours}, config)
    return decisions[symbol]


classify_sma_offset = classify_symbol


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    del features_by_symbol
    return route_universe_aggregated(
        {symbol: _aggregate_complete(bars, 60) for symbol, bars in bars_by_symbol.items()},
        {symbol: _aggregate_complete(bars, 240) for symbol, bars in bars_by_symbol.items()},
        config,
    )


__all__ = [
    "BarObservation", "FeatureObservation", "IndicatorState",
    "PASINDU_CONTINUATION_STATE", "PASINDU_FLIP_STATE", "RouteConfig",
    "RouteDecision", "SMA_OFFSET_STATE", "SourceSignal", "UNRESOLVED",
    "classify_symbol", "route_universe", "route_universe_aggregated",
]

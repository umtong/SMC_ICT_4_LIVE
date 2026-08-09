"""Causal multi-timeframe trend/volatility router for Candidate 51.

The primary family is a direct, auditable adaptation of the public Freqtrade
``ichiV1/ichiV2`` idea: multi-horizon EMA/Heikin-Ashi agreement, price above or
below a causal Ichimoku cloud, and an accelerating 1h/8h fan magnitude.  A
second, independent NR7 range-expansion family reuses the classic narrow-range
breakout mechanism.  Both families operate only on completed one-minute bars.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from statistics import median
from typing import Mapping, Sequence

_EPS = 1e-12
_NS_MINUTE = 60_000_000_000


def _finite(value: float, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _sign(value: float, deadband: float = 0.0) -> int:
    if value > deadband:
        return 1
    if value < -deadband:
        return -1
    return 0


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
    # Existing Candidate 35 constructor fields are retained so the audited
    # Nautilus execution shell can be reused without changing its contract.
    atr_period: int = 30
    prior_bars: int = 15
    response_bars: int = 3
    min_impulse_atr_continuation: float = 0.20
    min_impulse_atr_reversal: float = 1.05
    min_response_atr: float = 0.08
    min_break_acceptance_atr: float = 0.015
    min_sweep_penetration_atr: float = 0.06
    min_participation_ratio: float = 0.85
    min_flow_alignment: float = 0.02
    min_efficiency: float = 0.20
    min_breadth_fraction: float = 0.50
    continuation_target_r: float = 2.00
    reversal_target_r: float = 1.65
    min_route_score: float = 3.20
    ambiguity_score_gap: float = 0.10
    stop_buffer_atr: float = 0.10
    min_stop_atr: float = 0.55
    max_stop_atr: float = 1.85

    # Public ichiV1/ichiV2 structure, scaled from 5m candles to 1m bars.
    trend_periods: tuple[int, ...] = (5, 15, 30, 60, 120, 240, 360, 480)
    bullish_levels: int = 4
    cloud_tenkan: int = 100
    cloud_kijun: int = 300
    cloud_span_b: int = 600
    fan_fast: int = 60
    fan_slow: int = 480
    fan_shift_minutes: int = 5
    fan_rising_steps: int = 3
    min_fan_magnitude: float = 1.00045
    min_fan_gain: float = 1.00008
    max_extension_atr: float = 3.20

    # Independent narrow-range expansion family.
    nr_interval_minutes: int = 15
    nr_lookback: int = 7
    nr_break_buffer_atr: float = 0.05
    nr_max_range_atr: float = 2.20
    nr_target_r: float = 1.90


@dataclass(frozen=True, slots=True)
class RouteDecision:
    symbol: str
    state: str
    side: int
    score: float
    expected_target_r: float
    atr: float
    entry_reference: float
    stop_reference: float
    objective_reference: float
    episode_ts: int
    reasons: tuple[str, ...] = ()
    diagnostics: Mapping[str, float | int | bool | str] = field(default_factory=dict)

    @property
    def actionable(self) -> bool:
        return self.state != "UNRESOLVED" and self.side in (-1, 1)


def true_range(current: BarObservation, previous_close: float) -> float:
    return max(
        current.high - current.low,
        abs(current.high - previous_close),
        abs(current.low - previous_close),
    )


def causal_atr(bars: Sequence[BarObservation], period: int) -> float:
    if period < 2 or len(bars) < period + 1:
        return math.nan
    selected = bars[-(period + 1) :]
    values = [
        true_range(selected[index], selected[index - 1].close)
        for index in range(1, len(selected))
    ]
    clean = [value for value in values if math.isfinite(value) and value > 0.0]
    return sum(clean) / len(clean) if clean else math.nan


def _ema_series(values: Sequence[float], period: int) -> list[float]:
    if period <= 0 or len(values) < period:
        return [math.nan] * len(values)
    result = [math.nan] * len(values)
    seed = sum(float(value) for value in values[:period]) / period
    result[period - 1] = seed
    alpha = 2.0 / (period + 1.0)
    previous = seed
    for index in range(period, len(values)):
        previous = alpha * float(values[index]) + (1.0 - alpha) * previous
        result[index] = previous
    return result


def _heikin_open(bars: Sequence[BarObservation]) -> list[float]:
    if not bars:
        return []
    output = [(bars[0].open + bars[0].close) / 2.0]
    previous_close = (bars[0].open + bars[0].high + bars[0].low + bars[0].close) / 4.0
    for bar in bars[1:]:
        current_open = (output[-1] + previous_close) / 2.0
        output.append(current_open)
        previous_close = (bar.open + bar.high + bar.low + bar.close) / 4.0
    return output


def _midpoint(bars: Sequence[BarObservation], length: int) -> float:
    if len(bars) < length:
        return math.nan
    selected = bars[-length:]
    return (max(bar.high for bar in selected) + min(bar.low for bar in selected)) / 2.0


def _volume_ratio(bars: Sequence[BarObservation]) -> float:
    if len(bars) < 65:
        return 0.0
    recent = [bar.volume for bar in bars[-5:] if bar.volume > 0.0]
    baseline = [bar.volume for bar in bars[-65:-5] if bar.volume > 0.0]
    if not recent or not baseline:
        return 0.0
    return (sum(recent) / len(recent)) / max(median(baseline), _EPS)


def _aggregate_complete(
    bars: Sequence[BarObservation],
    interval_minutes: int,
) -> list[tuple[int, BarObservation, int]]:
    groups: list[tuple[int, list[BarObservation]]] = []
    current_key: int | None = None
    current: list[BarObservation] = []
    divisor = interval_minutes * _NS_MINUTE
    for bar in bars:
        key = int(bar.ts_event // divisor)
        if current_key is None or key == current_key:
            current_key = key
            current.append(bar)
            continue
        groups.append((current_key, current))
        current_key = key
        current = [bar]
    if current_key is not None:
        groups.append((current_key, current))

    completed: list[tuple[int, BarObservation, int]] = []
    for key, items in groups:
        if len(items) != interval_minutes:
            continue
        completed.append(
            (
                key,
                BarObservation(
                    ts_event=items[-1].ts_event,
                    open=items[0].open,
                    high=max(item.high for item in items),
                    low=min(item.low for item in items),
                    close=items[-1].close,
                    volume=sum(max(item.volume, 0.0) for item in items),
                ),
                len(items),
            ),
        )
    return completed


def _raw_context(
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig,
) -> dict[str, float | int | bool | str]:
    required = max(config.cloud_span_b, config.fan_slow) + 20
    if len(bars) < required:
        raise ValueError(f"need at least {required} completed bars, got {len(bars)}")
    if any(bars[index].ts_event >= bars[index + 1].ts_event for index in range(len(bars) - 1)):
        raise ValueError("bar timestamps must be strictly increasing")

    atr = causal_atr(bars, config.atr_period)
    if not math.isfinite(atr) or atr <= 0.0:
        raise ValueError("causal ATR is unavailable")

    closes = [bar.close for bar in bars]
    ha_open = _heikin_open(bars)
    close_emas = {period: _ema_series(closes, period) for period in config.trend_periods}
    open_emas = {period: _ema_series(ha_open, period) for period in config.trend_periods}
    fast = _ema_series(closes, config.fan_fast)
    slow = _ema_series(closes, config.fan_slow)

    last = bars[-1]
    trend_votes: list[int] = []
    for period in config.trend_periods:
        close_value = close_emas[period][-1]
        open_value = open_emas[period][-1]
        trend_votes.append(_sign(close_value - open_value, deadband=0.005 * atr))

    tenkan = _midpoint(bars, config.cloud_tenkan)
    kijun = _midpoint(bars, config.cloud_kijun)
    span_b = _midpoint(bars, config.cloud_span_b)
    span_a = (tenkan + kijun) / 2.0
    cloud_top = max(span_a, span_b)
    cloud_bottom = min(span_a, span_b)
    cloud_side = 1 if last.close > cloud_top else -1 if last.close < cloud_bottom else 0

    fan_values: list[float] = []
    endpoints = [len(bars) - 1 - config.fan_shift_minutes * step for step in range(config.fan_rising_steps + 1)]
    for endpoint in endpoints:
        fast_value = fast[endpoint] if endpoint >= 0 else math.nan
        slow_value = slow[endpoint] if endpoint >= 0 else math.nan
        if not (math.isfinite(fast_value) and math.isfinite(slow_value) and slow_value > 0.0):
            fan_values.append(math.nan)
        else:
            fan_values.append(fast_value / slow_value)

    current_fan = fan_values[0]
    previous_fan = fan_values[1] if len(fan_values) > 1 else math.nan
    fan_side = _sign(current_fan - 1.0, deadband=config.min_fan_magnitude - 1.0)
    directional_fan = current_fan if fan_side > 0 else (1.0 / current_fan if fan_side < 0 and current_fan > 0.0 else 0.0)
    if fan_side > 0:
        directional_history = fan_values
    elif fan_side < 0:
        directional_history = [1.0 / value if value > 0.0 else math.nan for value in fan_values]
    else:
        directional_history = fan_values
    fan_gain = (
        directional_history[0] / directional_history[1]
        if len(directional_history) > 1
        and math.isfinite(directional_history[0])
        and math.isfinite(directional_history[1])
        and directional_history[1] > 0.0
        else 0.0
    )
    rising_steps = sum(
        math.isfinite(directional_history[index])
        and math.isfinite(directional_history[index + 1])
        and directional_history[index] > directional_history[index + 1]
        for index in range(len(directional_history) - 1)
    )

    positive_votes = sum(vote > 0 for vote in trend_votes)
    negative_votes = sum(vote < 0 for vote in trend_votes)
    background_side = 1 if positive_votes >= config.bullish_levels else -1 if negative_votes >= config.bullish_levels else 0
    background_levels = positive_votes if background_side > 0 else negative_votes if background_side < 0 else 0
    trend_side = cloud_side if cloud_side != 0 and cloud_side == fan_side else 0
    aligned_levels = sum(vote == trend_side for vote in trend_votes) if trend_side else 0
    opposite_levels = sum(vote == -trend_side for vote in trend_votes) if trend_side else 0
    recent_progress = trend_side * (last.close - bars[-6].close) / atr if trend_side else 0.0
    ema30 = close_emas[30][-1]
    extension_atr = trend_side * (last.close - ema30) / atr if trend_side else math.inf
    volume_ratio = _volume_ratio(bars)
    flow_alignment = trend_side * _finite(feature.flow_60s) if trend_side else 0.0
    opening_flow_alignment = trend_side * _finite(feature.flow_open_10s) if trend_side else 0.0
    efficiency = _finite(feature.efficiency_60s)
    oi_alignment = trend_side * _finite(feature.oi_change_15m) if trend_side else 0.0
    crowd_alignment = trend_side * _finite(feature.premium_z) if trend_side else 0.0

    current_bucket = int(last.ts_event // (config.nr_interval_minutes * _NS_MINUTE))
    aggregated = _aggregate_complete(bars[-(config.nr_interval_minutes * (config.nr_lookback + 3)) :], config.nr_interval_minutes)
    nr_side = 0
    nr_range = math.nan
    nr_high = math.nan
    nr_low = math.nan
    nr_key = 0
    if len(aggregated) >= config.nr_lookback:
        candidate_key, candidate, _ = aggregated[-1]
        history = [item[1].high - item[1].low for item in aggregated[-config.nr_lookback :]]
        nr_range = candidate.high - candidate.low
        nr_high = candidate.high
        nr_low = candidate.low
        nr_key = candidate_key
        is_nr7 = nr_range <= min(history) + _EPS and nr_range / atr <= config.nr_max_range_atr
        if is_nr7 and current_bucket == candidate_key + 1:
            if last.close > candidate.high + config.nr_break_buffer_atr * atr:
                nr_side = 1
            elif last.close < candidate.low - config.nr_break_buffer_atr * atr:
                nr_side = -1

    return {
        "atr": atr,
        "entry": last.close,
        "episode_ts": last.ts_event,
        "trend_side": trend_side,
        "background_side": background_side,
        "background_levels": background_levels,
        "cloud_side": cloud_side,
        "fan_side": fan_side,
        "aligned_levels": aligned_levels,
        "opposite_levels": opposite_levels,
        "fan_magnitude": directional_fan,
        "fan_gain": fan_gain,
        "fan_rising_steps": rising_steps,
        "recent_progress_atr": recent_progress,
        "extension_atr": extension_atr,
        "volume_ratio": volume_ratio,
        "flow_alignment": flow_alignment,
        "opening_flow_alignment": opening_flow_alignment,
        "efficiency": efficiency,
        "oi_alignment": oi_alignment,
        "crowd_alignment": crowd_alignment,
        "recent_high": max(bar.high for bar in bars[-20:]),
        "recent_low": min(bar.low for bar in bars[-20:]),
        "nr_side": nr_side,
        "nr_range": nr_range,
        "nr_high": nr_high,
        "nr_low": nr_low,
        "nr_key": nr_key,
        "current_bucket": current_bucket,
        "feature_ready": feature.ready,
        "feature_observed_time_ns": feature.observed_time_ns,
    }


def _unresolved(symbol: str, context: Mapping[str, float | int | bool | str], reason: str) -> RouteDecision:
    return RouteDecision(
        symbol=symbol,
        state="UNRESOLVED",
        side=0,
        score=0.0,
        expected_target_r=0.0,
        atr=_finite(context.get("atr", math.nan), math.nan),
        entry_reference=_finite(context.get("entry", math.nan), math.nan),
        stop_reference=math.nan,
        objective_reference=math.nan,
        episode_ts=int(context.get("episode_ts", 0)),
        reasons=(reason,),
        diagnostics=dict(context),
    )


def _geometry(
    *,
    side: int,
    entry: float,
    atr: float,
    anchor: float,
    target_r: float,
    config: RouteConfig,
) -> tuple[float, float]:
    raw = side * (entry - anchor)
    distance = _clamp(
        raw + config.stop_buffer_atr * atr,
        config.min_stop_atr * atr,
        config.max_stop_atr * atr,
    )
    stop = entry - side * distance
    target = entry + side * target_r * distance
    return stop, target


def classify_symbol(
    *,
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    breadth_fraction: float,
    btc_impulse_side: int,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    try:
        context = _raw_context(bars, feature, config)
    except ValueError as exc:
        return _unresolved(symbol, {}, str(exc))
    if not feature.ready:
        return _unresolved(symbol, context, "FEATURE_NOT_READY")

    atr = float(context["atr"])
    entry = float(context["entry"])
    trend_side = int(context["trend_side"])
    nr_side = int(context["nr_side"])

    ichi_eligible = (
        trend_side in (-1, 1)
        and int(context["aligned_levels"]) >= config.bullish_levels
        and float(context["fan_magnitude"]) >= config.min_fan_magnitude
        and float(context["fan_gain"]) >= config.min_fan_gain
        and int(context["fan_rising_steps"]) >= config.fan_rising_steps - 1
        and float(context["recent_progress_atr"]) >= config.min_response_atr
        and -0.25 <= float(context["extension_atr"]) <= config.max_extension_atr
        and float(context["volume_ratio"]) >= config.min_participation_ratio
        and (breadth_fraction >= 0.25 or btc_impulse_side in (0, trend_side) or symbol == "BTCUSDT")
    )
    ichi_score = 0.0
    if trend_side:
        ichi_score = (
            0.50 * min(int(context["aligned_levels"]), 8)
            + 1.10 * _clamp((float(context["fan_magnitude"]) - 1.0) / max(config.min_fan_magnitude - 1.0, _EPS), 0.0, 2.0)
            + 0.90 * _clamp((float(context["fan_gain"]) - 1.0) / max(config.min_fan_gain - 1.0, _EPS), 0.0, 2.0)
            + 0.45 * _clamp(float(context["recent_progress_atr"]) / max(config.min_response_atr, _EPS), 0.0, 2.0)
            + 0.35 * _clamp(float(context["volume_ratio"]) / max(config.min_participation_ratio, _EPS), 0.0, 2.0)
            + 0.35 * _clamp(breadth_fraction / 0.50, 0.0, 2.0)
            + 0.20 * _clamp(max(float(context["flow_alignment"]), float(context["opening_flow_alignment"]), 0.0) / max(config.min_flow_alignment, _EPS), 0.0, 2.0)
            + 0.10 * _clamp(max(float(context["oi_alignment"]), 0.0) / 0.0025, 0.0, 2.0)
            - 0.10 * _clamp(max(float(context["crowd_alignment"]), 0.0) / 2.0, 0.0, 2.0)
        )

    background_side = int(context["background_side"])
    nr_trend_support = nr_side != 0 and (
        background_side == nr_side
        or (
            int(context["background_levels"]) >= 3
            and btc_impulse_side in (0, nr_side)
        )
    )
    nr_eligible = (
        nr_side in (-1, 1)
        and nr_trend_support
        and float(context["volume_ratio"]) >= max(config.min_participation_ratio, 1.0)
    )
    nr_score = 0.0
    if nr_side:
        nr_score = (
            2.20
            + 0.45 * min(int(context["background_levels"]), 6)
            + 0.75 * _clamp(float(context["volume_ratio"]), 0.0, 2.0)
            + 0.35 * _clamp(breadth_fraction / 0.50, 0.0, 2.0)
            + 0.25 * _clamp(max(nr_side * _finite(feature.flow_60s), 0.0) / max(config.min_flow_alignment, _EPS), 0.0, 2.0)
        )

    if ichi_eligible and ichi_score >= config.min_route_score and ichi_score >= nr_score:
        side = trend_side
        anchor = float(context["recent_low"] if side > 0 else context["recent_high"])
        stop, target = _geometry(
            side=side,
            entry=entry,
            atr=atr,
            anchor=anchor,
            target_r=config.continuation_target_r,
            config=config,
        )
        state = "ICHI_FAN_ACCELERATION_CONTINUATION"
        score = ichi_score
        target_r = config.continuation_target_r
        reasons = (
            "CAUSAL_CLOUD_CLEARANCE",
            "MULTI_HORIZON_TREND_ALIGNMENT",
            "FAN_MAGNITUDE_ACCELERATION",
            "RECENT_PROGRESS_WITH_PARTICIPATION",
        )
    elif nr_eligible and nr_score >= config.min_route_score:
        side = nr_side
        anchor = float(context["nr_low"] if side > 0 else context["nr_high"])
        stop, target = _geometry(
            side=side,
            entry=entry,
            atr=atr,
            anchor=anchor,
            target_r=config.nr_target_r,
            config=config,
        )
        state = "NR7_RANGE_EXPANSION"
        score = nr_score
        target_r = config.nr_target_r
        reasons = (
            "COMPLETED_NR7_COMPRESSION",
            "ADJACENT_BUCKET_BREAKOUT",
            "TREND_AND_PARTICIPATION_SUPPORT",
        )
    else:
        reason = "ICHI_THRESHOLDS_NOT_COHERENT"
        if trend_side == 0 and nr_side == 0:
            reason = "NO_DIRECTIONAL_STATE"
        elif nr_side != 0 and not nr_trend_support:
            reason = "NR7_BREAKOUT_WITHOUT_TREND_SUPPORT"
        return _unresolved(symbol, context, reason)

    diagnostics = dict(context)
    diagnostics.update(
        {
            "breadth_fraction": breadth_fraction,
            "btc_impulse_side": btc_impulse_side,
            "ichi_eligible": ichi_eligible,
            "ichi_score": ichi_score,
            "nr_eligible": nr_eligible,
            "nr_score": nr_score,
        },
    )
    return RouteDecision(
        symbol=symbol,
        state=state,
        side=side,
        score=score,
        expected_target_r=target_r,
        atr=atr,
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=target,
        episode_ts=int(context["episode_ts"]),
        reasons=reasons,
        diagnostics=diagnostics,
    )


def route_universe(
    *,
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    contexts: dict[str, dict[str, float | int | bool | str]] = {}
    for symbol, bars in bars_by_symbol.items():
        feature = features_by_symbol.get(symbol, FeatureObservation(0, ready=False))
        try:
            contexts[symbol] = _raw_context(bars, feature, config)
        except ValueError:
            contexts[symbol] = {"trend_side": 0, "nr_side": 0}

    market_sides = [
        int(context.get("trend_side", 0)) or int(context.get("nr_side", 0)) or int(context.get("background_side", 0))
        for context in contexts.values()
    ]
    btc_side = (
        int(contexts.get("BTCUSDT", {}).get("trend_side", 0))
        or int(contexts.get("BTCUSDT", {}).get("nr_side", 0))
        or int(contexts.get("BTCUSDT", {}).get("background_side", 0))
    )

    decisions: dict[str, RouteDecision] = {}
    for symbol, bars in bars_by_symbol.items():
        side = (
            int(contexts.get(symbol, {}).get("trend_side", 0))
            or int(contexts.get(symbol, {}).get("nr_side", 0))
            or int(contexts.get(symbol, {}).get("background_side", 0))
        )
        nonzero = [other for other in market_sides if other != 0]
        breadth = sum(other == side for other in nonzero) / len(nonzero) if side and nonzero else 0.0
        decisions[symbol] = classify_symbol(
            symbol=symbol,
            bars=bars,
            feature=features_by_symbol.get(symbol, FeatureObservation(0, ready=False)),
            breadth_fraction=breadth,
            btc_impulse_side=btc_side,
            config=config,
        )

    actionable = [decision for decision in decisions.values() if decision.actionable]
    if not actionable:
        return None, decisions
    actionable.sort(
        key=lambda item: (
            item.score * item.expected_target_r,
            item.score,
            1 if item.symbol == "BTCUSDT" else 0,
            item.symbol,
        ),
        reverse=True,
    )
    return actionable[0], decisions


__all__ = [
    "BarObservation",
    "FeatureObservation",
    "RouteConfig",
    "RouteDecision",
    "causal_atr",
    "classify_symbol",
    "route_universe",
]

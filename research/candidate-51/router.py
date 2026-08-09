"""Candidate 51 open-book router: causal adaptation of public Freqtrade ichiV2_5.

The public strategy is not treated as evidence.  Its complete decision policy is
reproduced first, then adapted only where the project constraints require it:
completed five-minute bars, one global slot, causal one-bar input shift,
structural risk sizing, and an explicit independent episode edge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Iterable, Mapping, Sequence


ICHI_STATE = "ICHI_V25_FAN_ACCELERATION_LONG"
UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class BarObservation:
    ts_event: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class FeatureObservation:
    observed_time_ns: int
    ready: bool = True
    flow_open_10s: float = math.nan
    notional_open_10s_burst: float = math.nan
    flow_60s: float = math.nan
    efficiency_60s: float = math.nan
    oi_change_15m: float = math.nan
    premium_z: float = math.nan


@dataclass(frozen=True)
class RouteConfig:
    # Legacy constructor fields retained because the reused Nautilus executor
    # instantiates RouteConfig from its StrategyConfig.
    atr_period: int = 30
    min_impulse_atr_continuation: float = 0.20
    min_impulse_atr_reversal: float = 1.05
    min_response_atr: float = 0.08
    min_participation_ratio: float = 0.85
    min_route_score: float = 3.20
    ambiguity_score_gap: float = 0.10
    continuation_target_r: float = 2.00
    reversal_target_r: float = 1.65

    # Public ichiV2 parameters (5m source strategy).
    bucket_minutes: int = 5
    bullish_levels: int = 4
    cloud_levels: int = 1
    fan_rising_lookback: int = 2
    min_fan_gain: float = 1.0007
    fan_fast: int = 12
    fan_slow: int = 96
    exit_ema_period: int = 18
    cloud_conversion: int = 20
    cloud_base: int = 60
    cloud_span_b: int = 120
    cloud_displacement: int = 30

    # Project-required hard invalidation.  The public strategy used a 10%
    # emergency stop; here expected trend invalidation determines quantity.
    hard_stop_min_fraction: float = 0.0035
    hard_stop_max_fraction: float = 0.0600
    stop_atr_buffer: float = 0.25
    public_roi_target_fraction: float = 0.30
    max_entry_extension_atr: float = 4.0


@dataclass(frozen=True)
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
        return self.side != 0 and self.state != UNRESOLVED


def _finite(value: float) -> bool:
    return math.isfinite(value)


def _mean(values: Sequence[float]) -> float:
    clean = [float(value) for value in values if _finite(float(value))]
    return sum(clean) / len(clean) if clean else math.nan


def _ema(values: Sequence[float], period: int) -> list[float]:
    """Causal TA-Lib-compatible EMA seeded by the first full SMA window."""
    if period <= 0:
        raise ValueError("EMA period must be positive")
    result = [math.nan] * len(values)
    finite_start = next(
        (index for index, value in enumerate(values) if _finite(float(value))),
        None,
    )
    if finite_start is None or finite_start + period > len(values):
        return result
    seed_values = [float(value) for value in values[finite_start : finite_start + period]]
    if not all(_finite(value) for value in seed_values):
        return result
    seed_index = finite_start + period - 1
    current = sum(seed_values) / period
    result[seed_index] = current
    alpha = 2.0 / (period + 1.0)
    for index in range(seed_index + 1, len(values)):
        value = float(values[index])
        if not _finite(value):
            result[index] = math.nan
            continue
        current = alpha * value + (1.0 - alpha) * current
        result[index] = current
    return result


def _rolling_midpoint(highs: Sequence[float], lows: Sequence[float], period: int) -> list[float]:
    result = [math.nan] * len(highs)
    for index in range(period - 1, len(highs)):
        window_high = highs[index - period + 1 : index + 1]
        window_low = lows[index - period + 1 : index + 1]
        if all(_finite(float(value)) for value in (*window_high, *window_low)):
            result[index] = (max(window_high) + min(window_low)) / 2.0
    return result


def _true_range_at(bars: Sequence[BarObservation], index: int) -> float:
    bar = bars[index]
    if index <= 0:
        return max(0.0, bar.high - bar.low)
    previous = bars[index - 1].close
    return max(bar.high - bar.low, abs(bar.high - previous), abs(bar.low - previous))


def _atr(bars: Sequence[BarObservation], period: int = 14) -> float:
    if len(bars) < period + 1:
        return math.nan
    ranges = [_true_range_at(bars, index) for index in range(len(bars) - period, len(bars))]
    return _mean(ranges)


def _aggregate_complete(
    bars: Sequence[BarObservation],
    bucket_minutes: int,
) -> list[BarObservation]:
    """Aggregate only complete, contiguous UTC minute buckets."""
    if bucket_minutes <= 0:
        raise ValueError("bucket_minutes must be positive")
    minute_ns = 60_000_000_000
    bucket_ns = bucket_minutes * minute_ns
    grouped: dict[int, list[BarObservation]] = {}
    for bar in bars:
        grouped.setdefault(int(bar.ts_event) // bucket_ns, []).append(bar)

    output: list[BarObservation] = []
    for key in sorted(grouped):
        items = sorted(grouped[key], key=lambda item: item.ts_event)
        if len(items) != bucket_minutes:
            continue
        if any(items[i].ts_event - items[i - 1].ts_event != minute_ns for i in range(1, len(items))):
            continue
        output.append(
            BarObservation(
                ts_event=items[-1].ts_event,
                open=items[0].open,
                high=max(item.high for item in items),
                low=min(item.low for item in items),
                close=items[-1].close,
                volume=sum(max(0.0, item.volume) for item in items),
            ),
        )
    return output


def _heikin_ashi(bars: Sequence[BarObservation]) -> tuple[list[float], list[float], list[float], list[float]]:
    ha_open: list[float] = []
    ha_high: list[float] = []
    ha_low: list[float] = []
    ha_close: list[float] = []
    for index, bar in enumerate(bars):
        close = (bar.open + bar.high + bar.low + bar.close) / 4.0
        if index == 0:
            open_ = (bar.open + bar.close) / 2.0
        else:
            open_ = (ha_open[-1] + ha_close[-1]) / 2.0
        ha_open.append(open_)
        ha_close.append(close)
        ha_high.append(max(bar.high, open_, close))
        ha_low.append(min(bar.low, open_, close))
    return ha_open, ha_high, ha_low, ha_close


def _shift(values: Sequence[float], amount: int = 1) -> list[float]:
    if amount < 0:
        raise ValueError("only causal positive shifts are allowed")
    return [math.nan] * amount + [float(value) for value in values[:-amount or None]]


def _ichimoku_cloud(
    highs: Sequence[float],
    lows: Sequence[float],
    config: RouteConfig,
) -> tuple[list[float], list[float]]:
    tenkan = _rolling_midpoint(highs, lows, config.cloud_conversion)
    kijun = _rolling_midpoint(highs, lows, config.cloud_base)
    leading_a = [
        (a + b) / 2.0 if _finite(a) and _finite(b) else math.nan
        for a, b in zip(tenkan, kijun, strict=True)
    ]
    leading_b = _rolling_midpoint(highs, lows, config.cloud_span_b)
    displacement = max(0, config.cloud_displacement - 1)
    return _shift(leading_a, displacement), _shift(leading_b, displacement)


def _level_periods(levels: int) -> tuple[int, ...]:
    all_periods = (1, 3, 6, 12, 24, 48, 72, 96)
    return all_periods[: max(0, min(levels, len(all_periods)))]


def _indicator_frame(
    minute_bars: Sequence[BarObservation],
    config: RouteConfig,
) -> dict[str, object] | None:
    candles = _aggregate_complete(minute_bars[-1_000:], config.bucket_minutes)
    required = config.cloud_span_b + config.cloud_displacement + 8
    if len(candles) < required:
        return None

    ha_open, ha_high, ha_low, _ = _heikin_ashi(candles)
    raw_close = [bar.close for bar in candles]
    shifted_close = _shift(raw_close, 1)
    shifted_open = _shift(ha_open, 1)
    shifted_high = _shift(ha_high, 1)
    shifted_low = _shift(ha_low, 1)

    close_emas = {period: _ema(shifted_close, period) for period in _level_periods(8)}
    open_emas = {period: _ema(shifted_open, period) for period in _level_periods(8)}
    fan_fast = close_emas[config.fan_fast]
    fan_slow = close_emas[config.fan_slow]
    fan = [
        fast / slow if _finite(fast) and _finite(slow) and slow > 0.0 else math.nan
        for fast, slow in zip(fan_fast, fan_slow, strict=True)
    ]
    fan_gain = [math.nan]
    for index in range(1, len(fan)):
        previous = fan[index - 1]
        fan_gain.append(fan[index] / previous if _finite(fan[index]) and _finite(previous) and previous > 0 else math.nan)

    cloud_a, cloud_b = _ichimoku_cloud(shifted_high, shifted_low, config)
    exit_ema = _ema(shifted_close, config.exit_ema_period)

    return {
        "candles": candles,
        "shifted_close": shifted_close,
        "close_emas": close_emas,
        "open_emas": open_emas,
        "fan": fan,
        "fan_gain": fan_gain,
        "cloud_a": cloud_a,
        "cloud_b": cloud_b,
        "exit_ema": exit_ema,
    }


def _eligible(frame: Mapping[str, object], index: int, config: RouteConfig) -> tuple[bool, dict[str, float | int]]:
    shifted_close = frame["shifted_close"]
    close_emas = frame["close_emas"]
    open_emas = frame["open_emas"]
    fan = frame["fan"]
    fan_gain = frame["fan_gain"]
    cloud_a = frame["cloud_a"]
    cloud_b = frame["cloud_b"]

    assert isinstance(shifted_close, list)
    assert isinstance(close_emas, dict)
    assert isinstance(open_emas, dict)
    assert isinstance(fan, list)
    assert isinstance(fan_gain, list)
    assert isinstance(cloud_a, list)
    assert isinstance(cloud_b, list)

    close5 = float(shifted_close[index])
    current_fan = float(fan[index])
    current_gain = float(fan_gain[index])
    cloud_top = max(float(cloud_a[index]), float(cloud_b[index]))

    trend_votes = 0
    for period in _level_periods(config.bullish_levels):
        close_value = float(close_emas[period][index])
        open_value = float(open_emas[period][index])
        if _finite(close_value) and _finite(open_value) and close_value > open_value:
            trend_votes += 1

    rising_votes = 0
    for shift in range(1, config.fan_rising_lookback + 1):
        if index - shift >= 0 and _finite(current_fan) and _finite(float(fan[index - shift])) and float(fan[index - shift]) < current_fan:
            rising_votes += 1

    cloud_clear = _finite(close5) and _finite(cloud_top) and close5 > cloud_top
    trend_ok = trend_votes == config.bullish_levels
    fan_gain_ok = _finite(current_gain) and current_gain >= config.min_fan_gain
    fan_magnitude_ok = _finite(current_fan) and current_fan > 1.0
    fan_rising_ok = rising_votes == config.fan_rising_lookback
    eligible = cloud_clear and trend_ok and fan_gain_ok and fan_magnitude_ok and fan_rising_ok
    return eligible, {
        "trend_votes": trend_votes,
        "rising_votes": rising_votes,
        "cloud_clear": int(cloud_clear),
        "trend_ok": int(trend_ok),
        "fan_gain_ok": int(fan_gain_ok),
        "fan_magnitude_ok": int(fan_magnitude_ok),
        "fan_rising_ok": int(fan_rising_ok),
        "trend_close_5m": close5,
        "cloud_top": cloud_top,
        "fan_magnitude": current_fan,
        "fan_gain": current_gain,
    }


def ichi_exit_crossed(
    bars: Sequence[BarObservation],
    config: RouteConfig,
) -> tuple[bool, dict[str, float | int]]:
    frame = _indicator_frame(bars, config)
    if frame is None:
        return False, {"exit_ready": 0}
    shifted_close = frame["shifted_close"]
    exit_ema = frame["exit_ema"]
    assert isinstance(shifted_close, list)
    assert isinstance(exit_ema, list)
    index = len(shifted_close) - 1
    previous = index - 1
    values = (
        float(shifted_close[index]),
        float(exit_ema[index]),
        float(shifted_close[previous]),
        float(exit_ema[previous]),
    )
    ready = all(_finite(value) for value in values)
    crossed = ready and values[0] < values[1] and values[2] >= values[3]
    return crossed, {
        "exit_ready": int(ready),
        "exit_close": values[0],
        "exit_ema": values[1],
        "previous_exit_close": values[2],
        "previous_exit_ema": values[3],
    }


def classify_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig,
) -> RouteDecision:
    if not feature.ready:
        return RouteDecision(symbol, UNRESOLVED, 0, 0.0, math.nan, math.nan, math.nan, 0, ("FEATURE_NOT_READY",))
    frame = _indicator_frame(bars, config)
    if frame is None:
        return RouteDecision(symbol, UNRESOLVED, 0, 0.0, math.nan, math.nan, math.nan, 0, ("INSUFFICIENT_5M_HISTORY",))

    candles = frame["candles"]
    assert isinstance(candles, list)
    if feature.observed_time_ns > candles[-1].ts_event:
        return RouteDecision(
            symbol,
            UNRESOLVED,
            0,
            0.0,
            candles[-1].close,
            math.nan,
            math.nan,
            candles[-1].ts_event,
            ("FUTURE_FEATURE_REJECTED",),
            {"feature_observed_time_ns": feature.observed_time_ns},
        )
    index = len(candles) - 1
    current_ok, diagnostics = _eligible(frame, index, config)
    previous_ok, _ = _eligible(frame, index - 1, config)
    if not current_ok:
        reasons: list[str] = []
        if not int(diagnostics["cloud_clear"]):
            reasons.append("ICHI_CLOUD_NOT_CLEAR")
        if not int(diagnostics["trend_ok"]):
            reasons.append("ICHI_BULLISH_LEVELS_NOT_ALIGNED")
        if not int(diagnostics["fan_magnitude_ok"]):
            reasons.append("ICHI_FAN_NOT_BULLISH")
        if not int(diagnostics["fan_gain_ok"]):
            reasons.append("ICHI_V25_FAN_GAIN_BELOW_PUBLIC_THRESHOLD")
        if not int(diagnostics["fan_rising_ok"]):
            reasons.append("ICHI_V25_FAN_NOT_RISING_REQUIRED_STEPS")
        return RouteDecision(
            symbol,
            UNRESOLVED,
            0,
            0.0,
            candles[-1].close,
            math.nan,
            math.nan,
            candles[-1].ts_event,
            tuple(reasons or ("ICHI_V25_ENTRY_NOT_READY",)),
            diagnostics,
        )
    # Preserve the source strategy's persistent buy condition, but attach every
    # eligible bar to the first bar of its contiguous causal episode.  The
    # execution adapter consumes each episode at most once across the universe.
    episode_index = index
    while episode_index > 0:
        was_ok, _ = _eligible(frame, episode_index - 1, config)
        if not was_ok:
            break
        episode_index -= 1

    entry = float(candles[-1].close)
    atr5 = _atr(candles, 14)
    exit_ema = frame["exit_ema"]
    assert isinstance(exit_ema, list)
    ema18 = float(exit_ema[index])
    recent_swing = min(bar.low for bar in candles[-6:])
    if not (_finite(entry) and _finite(atr5) and atr5 > 0.0 and _finite(ema18)):
        return RouteDecision(symbol, UNRESOLVED, 0, 0.0, entry, math.nan, math.nan, candles[-1].ts_event, ("INVALID_RISK_INPUT",), diagnostics)

    natural_stop = min(ema18, recent_swing) - config.stop_atr_buffer * atr5
    natural_distance = entry - natural_stop
    min_distance = entry * config.hard_stop_min_fraction
    max_distance = entry * config.hard_stop_max_fraction
    if natural_distance <= 0.0 or natural_distance > max_distance:
        diagnostics = dict(diagnostics)
        diagnostics.update({"atr5": atr5, "ema18": ema18, "natural_stop_distance_fraction": natural_distance / entry})
        return RouteDecision(symbol, UNRESOLVED, 0, 0.0, entry, math.nan, math.nan, candles[-1].ts_event, ("STRUCTURAL_STOP_TOO_FAR",), diagnostics)
    stop_distance = max(natural_distance, min_distance)
    stop = entry - stop_distance

    fast = float(frame["close_emas"][config.fan_fast][index])
    extension_atr = (entry - fast) / atr5 if atr5 > 0.0 else math.inf
    if extension_atr > config.max_entry_extension_atr:
        diagnostics = dict(diagnostics)
        diagnostics.update({"atr5": atr5, "ema18": ema18, "extension_atr": extension_atr})
        return RouteDecision(symbol, UNRESOLVED, 0, 0.0, entry, stop, math.nan, candles[-1].ts_event, ("ENTRY_TOO_EXTENDED",), diagnostics)

    fan_gain = float(diagnostics["fan_gain"])
    fan_mag = float(diagnostics["fan_magnitude"])
    cloud_top = float(diagnostics["cloud_top"])
    score = (
        5.0
        + min(4.0, max(0.0, (fan_gain - 1.0) * 10_000.0 / 3.0))
        + min(2.0, max(0.0, (fan_mag - 1.0) * 1_000.0))
        + min(2.0, max(0.0, (entry - cloud_top) / atr5))
        - 0.25 * max(0.0, extension_atr)
    )
    objective = entry * (1.0 + config.public_roi_target_fraction)
    diagnostics = dict(diagnostics)
    diagnostics.update(
        {
            "atr5": atr5,
            "ema18": ema18,
            "recent_swing_low": recent_swing,
            "natural_stop_distance_fraction": natural_distance / entry,
            "planned_stop_distance_fraction": stop_distance / entry,
            "extension_atr": extension_atr,
            "feature_observed_time_ns": feature.observed_time_ns,
        },
    )
    return RouteDecision(
        symbol=symbol,
        state=ICHI_STATE,
        side=1,
        score=score,
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=objective,
        episode_ts=candles[episode_index].ts_event,
        reasons=(
            "PUBLIC_ICHI_V25_CAUSAL_ENTRY",
            "ONE_BAR_SHIFTED_INPUTS",
            "PERSISTENT_ELIGIBLE_CAUSAL_EPISODE",
            "EMA18_STRUCTURAL_INVALIDATION",
        ),
        diagnostics=diagnostics,
    )


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig,
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    decisions = {
        symbol: classify_symbol(symbol, bars_by_symbol[symbol], features_by_symbol[symbol], config)
        for symbol in sorted(bars_by_symbol)
    }
    candidates = [decision for decision in decisions.values() if decision.actionable]
    if not candidates:
        return None, decisions
    candidates.sort(key=lambda item: (-item.score, item.symbol))
    return candidates[0], decisions


__all__ = [
    "BarObservation",
    "FeatureObservation",
    "ICHI_STATE",
    "RouteConfig",
    "RouteDecision",
    "UNRESOLVED",
    "classify_symbol",
    "ichi_exit_crossed",
    "route_universe",
]

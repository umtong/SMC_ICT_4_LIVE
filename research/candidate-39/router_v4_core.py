"""Core data and causal aggregation for Candidate 39 V4 trader-derived router.

The two scenario families are intentionally independent:

``FIRST_PULLBACK_CONTINUATION``
    A strong multi-hour impulse in an established trend is followed by the first
    controlled pullback into dynamic value.  A separately completed 15-minute
    bar must reclaim/hold that value before a passive retest entry is offered.

``FAILED_LEVEL_REACCEPTANCE``
    Price attacks a completed prior UTC day or prior eight-hour-session extreme,
    closes back inside, and only becomes actionable after a later completed
    15-minute retest rejects the attacked level.

All inputs are completed one-minute bars.  Fifteen-minute evidence is aggregated
causally; the event bar and confirmation bar are never the same observation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from statistics import median
from typing import Mapping, Sequence

from router import BarObservation, RouteDecision, causal_atr

MINUTE_NS = 60_000_000_000
FIFTEEN_MINUTES_NS = 15 * MINUTE_NS
_EPS = 1e-12


@dataclass(frozen=True, slots=True)
class TraderDerivedConfig:
    atr_period: int = 32
    min_completed_15m_bars: int = 112

    # First controlled pullback after multi-hour initiative.
    trend_fast_bars: int = 8
    trend_slow_bars: int = 32
    trend_slope_lookback: int = 8
    min_trend_slope_atr: float = 0.45
    impulse_window_bars: int = 12
    min_impulse_bars: int = 4
    min_impulse_atr: float = 1.80
    min_impulse_efficiency: float = 0.38
    min_impulse_volume_ratio: float = 1.05
    min_pullback_bars: int = 2
    max_pullback_bars: int = 6
    min_retrace_fraction: float = 0.18
    max_retrace_fraction: float = 0.70
    value_touch_tolerance_atr: float = 0.28
    min_confirmation_body_fraction: float = 0.42
    min_confirmation_close_location: float = 0.66
    pullback_stop_buffer_atr: float = 0.10
    continuation_target_r: float = 1.90

    # Failed attack of an objective time-anchored level.
    min_sweep_atr: float = 0.08
    max_sweep_atr: float = 1.50
    min_reaccept_depth_atr: float = 0.02
    retest_tolerance_atr: float = 0.24
    max_confirmation_extension_atr: float = 0.12
    failed_break_stop_buffer_atr: float = 0.10
    failed_break_event_lookback: int = 4
    reversal_target_r_floor: float = 1.55

    min_stop_atr: float = 0.28
    max_stop_atr: float = 2.40
    min_route_score: float = 3.10
    ambiguity_score_gap: float = 0.22


@dataclass(frozen=True, slots=True)
class LevelReference:
    name: str
    high: float
    low: float
    start_ns: int
    end_ns: int


@dataclass(frozen=True, slots=True)
class SymbolContext:
    symbol: str
    bars15: tuple[BarObservation, ...]
    atr: float
    return_4h_atr: float
    volume_baseline: float


def _finite(value: float) -> bool:
    return math.isfinite(value)


def _safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    if not _finite(denominator) or abs(denominator) <= _EPS:
        return default
    return numerator / denominator


def _body_fraction(bar: BarObservation) -> float:
    return abs(bar.close - bar.open) / max(bar.high - bar.low, _EPS)


def _close_location(bar: BarObservation) -> float:
    return (bar.close - bar.low) / max(bar.high - bar.low, _EPS)


def _path_efficiency(bars: Sequence[BarObservation]) -> float:
    if not bars:
        return 0.0
    path = sum(max(item.high - item.low, 0.0) for item in bars)
    return abs(bars[-1].close - bars[0].open) / max(path, _EPS)


def _ema(values: Sequence[float], period: int) -> float:
    if period <= 0 or len(values) < period:
        return math.nan
    alpha = 2.0 / (period + 1.0)
    result = float(values[-period])
    for value in values[-period + 1 :]:
        result = alpha * float(value) + (1.0 - alpha) * result
    return result


def _ema_series(values: Sequence[float], period: int) -> list[float]:
    if period <= 0 or len(values) < period:
        return []
    alpha = 2.0 / (period + 1.0)
    seed = sum(float(item) for item in values[:period]) / period
    result = [math.nan] * (period - 1) + [seed]
    current = seed
    for value in values[period:]:
        current = alpha * float(value) + (1.0 - alpha) * current
        result.append(current)
    return result


def _anchored_vwap(bars: Sequence[BarObservation]) -> float:
    weighted = 0.0
    volume = 0.0
    for item in bars:
        if not _finite(item.volume) or item.volume <= 0.0:
            continue
        typical = (item.high + item.low + item.close) / 3.0
        weighted += typical * item.volume
        volume += item.volume
    return weighted / volume if volume > _EPS else math.nan


def aggregate_completed_15m(
    minute_bars: Sequence[BarObservation],
) -> tuple[BarObservation, ...]:
    """Aggregate exact, complete UTC-aligned 15-minute buckets only."""
    buckets: dict[int, list[BarObservation]] = {}
    for bar in minute_bars:
        key = int(bar.ts_event // FIFTEEN_MINUTES_NS)
        buckets.setdefault(key, []).append(bar)

    completed: list[BarObservation] = []
    for key in sorted(buckets):
        rows = sorted(buckets[key], key=lambda item: item.ts_event)
        if len(rows) != 15:
            continue
        # Require 15 distinct one-minute timestamps.  This avoids silently
        # turning missing bars into a valid higher-timeframe observation.
        if any(
            rows[index].ts_event <= rows[index - 1].ts_event
            or rows[index].ts_event - rows[index - 1].ts_event > MINUTE_NS + 1
            for index in range(1, len(rows))
        ):
            continue
        completed.append(
            BarObservation(
                ts_event=rows[-1].ts_event,
                open=rows[0].open,
                high=max(item.high for item in rows),
                low=min(item.low for item in rows),
                close=rows[-1].close,
                volume=sum(max(item.volume, 0.0) for item in rows),
            )
        )
    return tuple(completed)


def _make_context(
    symbol: str,
    minute_bars: Sequence[BarObservation],
    config: TraderDerivedConfig,
) -> SymbolContext | None:
    bars15 = aggregate_completed_15m(minute_bars)
    if len(bars15) < config.min_completed_15m_bars:
        return None
    atr = causal_atr(bars15[:-1], config.atr_period)
    if not _finite(atr) or atr <= 0.0:
        return None
    return_4h_atr = (bars15[-1].close - bars15[-17].close) / atr
    volumes = [
        item.volume
        for item in bars15[-65:-1]
        if _finite(item.volume) and item.volume > 0.0
    ]
    baseline = median(volumes) if volumes else 0.0
    return SymbolContext(
        symbol=symbol,
        bars15=bars15,
        atr=atr,
        return_4h_atr=return_4h_atr,
        volume_baseline=baseline,
    )


def _decision(
    *,
    context: SymbolContext,
    state: str,
    side: int,
    score: float,
    entry: float,
    stop: float,
    target: float,
    episode_ts: int,
    reasons: Sequence[str],
    diagnostics: Mapping[str, float | int | bool | str],
    policy_floor: float,
) -> RouteDecision | None:
    atr = context.atr
    if side > 0 and not (stop < entry < target):
        return None
    if side < 0 and not (target < entry < stop):
        return None
    stop_atr = abs(entry - stop) / atr
    raw_r = abs(target - entry) / max(abs(entry - stop), _EPS)
    if stop_atr < context_config_min_stop(diagnostics) or stop_atr > context_config_max_stop(diagnostics):
        return None
    if raw_r + 1e-12 < policy_floor:
        return None
    data = dict(diagnostics)
    data.update(
        {
            "policy_target_r_floor": policy_floor,
            "raw_structural_r": raw_r,
            "stop_atr": stop_atr,
            "entry_policy": "PASSIVE_RETEST_LIMIT",
            "event_confirmation_separated": True,
            "non_scalping": True,
        }
    )
    return RouteDecision(
        symbol=context.symbol,
        state=state,
        side=side,
        score=score,
        expected_target_r=raw_r,
        atr=atr,
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=target,
        episode_ts=episode_ts,
        reasons=tuple(reasons),
        diagnostics=data,
    )


def context_config_min_stop(diagnostics: Mapping[str, object]) -> float:
    return float(diagnostics.get("_min_stop_atr", 0.28))


def context_config_max_stop(diagnostics: Mapping[str, object]) -> float:
    return float(diagnostics.get("_max_stop_atr", 2.40))

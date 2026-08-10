"""Causal four-asset adapter for the public ``ichiV2_1`` mechanism.

This is intentionally different from Candidate 47's later ``ichiV2`` variant.
The public ``ichiV2_1`` source accidentally interprets labels such as ``480m``
as an EMA *period of 480 five-minute candles* (40 hours), has no active cloud
filter, uses a 5/3/1/0 percent ROI schedule, a 5 percent stop, and exits on an
EMA(5)-below-EMA(120) cross.  Those executable semantics are preserved here.

A spectacular external report named ``ichiV2`` has nearly identical ROI versus
exit-signal anatomy, but its code was not published with the report.  The report
is only a search clue; this module tests the independently available public
``ichiV2_1`` code rather than claiming source identity or trusting its result.

All observations are completed five-minute candles.  Orders are submitted only
thereafter and therefore fill on later market data.  The optional reciprocal
short is a separately labelled adaptation, never conflated with the long-only
source control.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Sequence

import router_picasso as _ta

BarObservation = _ta.BarObservation
FeatureObservation = _ta.FeatureObservation
UNRESOLVED = "UNRESOLVED"
ICHIV21_STATE = "PUBLIC_ICHIV21_MULTI_EMA_FAN"
_PERIODS = (5, 15, 30, 60, 120, 240, 360, 480)
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}


@dataclass(frozen=True, slots=True)
class RouteConfig:
    atr_period: int = 30
    min_impulse_atr_continuation: float = 0.75
    min_impulse_atr_reversal: float = 1.05
    min_response_atr: float = 0.12
    min_participation_ratio: float = 1.05
    min_route_score: float = 3.10
    ambiguity_score_gap: float = 0.20
    continuation_target_r: float = 2.20
    reversal_target_r: float = 1.80

    ichiv21_bucket_minutes: int = 5
    ichiv21_episode_mode: str = "condition_reentry"
    ichiv21_direction_mode: str = "long_only"
    ichiv21_alignment_mode: str = "all8"
    ichiv21_fan_gain_min: float = 1.002
    ichiv21_fan_shift_count: int = 3
    ichiv21_stop_fraction: float = 0.05
    ichiv21_remote_target_fraction: float = 0.05


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
class IchiV21State:
    ts_event: int
    ready: bool
    entry: bool
    exit_cross_down: bool
    fan_magnitude: float = math.nan
    fan_gain: float = math.nan
    alignment_count: int = 0
    required_alignment_count: int = 0
    trend_close_5: float = math.nan
    trend_close_30: float = math.nan
    trend_close_60: float = math.nan
    trend_close_120: float = math.nan
    trend_close_480: float = math.nan
    transformed_signal_open: float = math.nan
    transformed_signal_high: float = math.nan
    transformed_signal_low: float = math.nan
    transformed_signal_close: float = math.nan
    score: float = 0.0


def _finite(value: float) -> bool:
    return math.isfinite(float(value))


def aggregate_five_minute(bars: Sequence[BarObservation]) -> list[BarObservation]:
    return list(_ta._aggregate_complete(bars, 5))


def reciprocal_candles(candles: Sequence[BarObservation]) -> list[BarObservation]:
    output: list[BarObservation] = []
    for candle in candles:
        values = (candle.open, candle.high, candle.low, candle.close)
        if not all(_finite(value) and float(value) > 0.0 for value in values):
            continue
        output.append(
            BarObservation(
                ts_event=int(candle.ts_event),
                open=1.0 / float(candle.open),
                high=1.0 / float(candle.low),
                low=1.0 / float(candle.high),
                close=1.0 / float(candle.close),
                volume=float(candle.volume),
            )
        )
    return output


def _heikin_ashi_open(candles: Sequence[BarObservation]) -> list[float]:
    opens: list[float] = []
    closes: list[float] = []
    for candle in candles:
        ha_close = (
            float(candle.open) + float(candle.high)
            + float(candle.low) + float(candle.close)
        ) / 4.0
        ha_open = (
            (float(candle.open) + float(candle.close)) / 2.0
            if not opens else (opens[-1] + closes[-1]) / 2.0
        )
        opens.append(ha_open)
        closes.append(ha_close)
    return opens


def _alignment_periods(mode: str) -> tuple[int, ...]:
    normalized = str(mode).strip().lower()
    if normalized == "all8":
        return _PERIODS
    if normalized == "fast4":
        return _PERIODS[:4]
    if normalized == "slow4":
        return _PERIODS[4:]
    if normalized == "fan_only":
        return ()
    raise ValueError(f"unsupported ichiv21_alignment_mode={mode!r}")


def ichiv21_states(
    candles: Sequence[BarObservation],
    config: RouteConfig = RouteConfig(),
    *,
    reciprocal: bool = False,
) -> list[IchiV21State]:
    transformed = reciprocal_candles(candles) if reciprocal else list(candles)
    if len(transformed) != len(candles) or not transformed:
        return []
    closes = [float(candle.close) for candle in transformed]
    ha_opens = _heikin_ashi_open(transformed)
    close_emas = {period: _ta._ema(closes, period) for period in _PERIODS}
    open_emas = {period: _ta._ema(ha_opens, period) for period in _PERIODS}
    alignment_periods = _alignment_periods(config.ichiv21_alignment_mode)
    required = len(alignment_periods)

    fan = [math.nan] * len(transformed)
    gain = [math.nan] * len(transformed)
    for index in range(len(transformed)):
        numerator = close_emas[60][index]
        denominator = close_emas[480][index]
        if _finite(numerator) and _finite(denominator) and denominator > 0.0:
            fan[index] = numerator / denominator
        if index > 0 and _finite(fan[index]) and _finite(fan[index - 1]) and fan[index - 1] > 0.0:
            gain[index] = fan[index] / fan[index - 1]

    states: list[IchiV21State] = []
    shift_count = max(1, int(config.ichiv21_fan_shift_count))
    for index, candle in enumerate(transformed):
        all_values = [
            close_emas[period][index]
            for period in set((*alignment_periods, 5, 60, 120, 480))
        ] + [
            open_emas[period][index]
            for period in alignment_periods
        ] + [fan[index], gain[index]]
        ready = index >= shift_count and all(_finite(value) for value in all_values)
        aligned_count = 0
        entry = False
        score = 0.0
        if ready:
            aligned_count = sum(
                close_emas[period][index] > open_emas[period][index]
                for period in alignment_periods
            )
            accelerating = all(
                _finite(fan[index - shift]) and fan[index] > fan[index - shift]
                for shift in range(1, shift_count + 1)
            )
            entry = (
                aligned_count == required
                and gain[index] >= float(config.ichiv21_fan_gain_min)
                and fan[index] > 1.0
                and accelerating
            )
            alignment_margin = 0.0
            for period in alignment_periods:
                close_value = close_emas[period][index]
                open_value = open_emas[period][index]
                alignment_margin += max(close_value / max(open_value, 1e-18) - 1.0, 0.0)
            score = (
                10_000.0 * max(gain[index] - float(config.ichiv21_fan_gain_min), 0.0)
                + 1_000.0 * max(fan[index] - 1.0, 0.0)
                + 100.0 * alignment_margin
                + aligned_count / max(required, 1)
            )

        exit_cross = False
        if index > 0:
            current_fast = close_emas[5][index]
            current_slow = close_emas[120][index]
            prior_fast = close_emas[5][index - 1]
            prior_slow = close_emas[120][index - 1]
            exit_cross = (
                all(_finite(value) for value in (current_fast, current_slow, prior_fast, prior_slow))
                and current_fast < current_slow
                and prior_fast >= prior_slow
            )

        states.append(
            IchiV21State(
                ts_event=int(candle.ts_event),
                ready=ready,
                entry=entry,
                exit_cross_down=exit_cross,
                fan_magnitude=fan[index],
                fan_gain=gain[index],
                alignment_count=aligned_count,
                required_alignment_count=required,
                trend_close_5=close_emas[5][index],
                trend_close_30=close_emas[30][index],
                trend_close_60=close_emas[60][index],
                trend_close_120=close_emas[120][index],
                trend_close_480=close_emas[480][index],
                transformed_signal_open=float(candle.open),
                transformed_signal_high=float(candle.high),
                transformed_signal_low=float(candle.low),
                transformed_signal_close=float(candle.close),
                score=score,
            )
        )
    return states


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


def _direction_candidate(
    symbol: str,
    candles: Sequence[BarObservation],
    config: RouteConfig,
    *,
    side: int,
) -> RouteDecision | None:
    reciprocal = side < 0
    states = ichiv21_states(candles, config, reciprocal=reciprocal)
    if len(states) < 2 or not states[-1].ready:
        return None
    current = states[-1]
    previous = states[-2]
    if not current.entry:
        return None
    episode_mode = str(config.ichiv21_episode_mode).strip().lower()
    if episode_mode == "rising_edge" and previous.entry:
        return None
    if episode_mode not in {"condition_reentry", "rising_edge"}:
        raise ValueError(f"unsupported ichiv21_episode_mode={episode_mode!r}")

    entry = float(candles[-1].close)
    stop_fraction = float(config.ichiv21_stop_fraction)
    target_fraction = float(config.ichiv21_remote_target_fraction)
    stop = entry * (1.0 - side * stop_fraction)
    target = entry * (1.0 + side * target_fraction)
    transformed_ema120 = float(current.trend_close_120)
    anchor_ema120 = (
        transformed_ema120
        if side > 0 else 1.0 / transformed_ema120
    )
    diagnostics: dict[str, float | int | str] = {
        "source_tag": "LONG" if side > 0 else "RECIPROCAL_SHORT",
        "source_period_semantics": "EMA_PERIODS_ARE_FIVE_MINUTE_CANDLES",
        "alignment_mode": str(config.ichiv21_alignment_mode),
        "episode_mode": episode_mode,
        "fan_magnitude": float(current.fan_magnitude),
        "fan_gain": float(current.fan_gain),
        "alignment_count": int(current.alignment_count),
        "required_alignment_count": int(current.required_alignment_count),
        "trend_close_5_transformed": float(current.trend_close_5),
        "trend_close_30_transformed": float(current.trend_close_30),
        "trend_close_60_transformed": float(current.trend_close_60),
        "trend_close_120_transformed": transformed_ema120,
        "trend_close_480_transformed": float(current.trend_close_480),
        "ema120_anchor_original_price": anchor_ema120,
        "signal_open_original": float(candles[-1].open),
        "signal_high_original": float(candles[-1].high),
        "signal_low_original": float(candles[-1].low),
        "signal_close_original": float(candles[-1].close),
        "source_stop_fraction": stop_fraction,
        "source_target_fraction": target_fraction,
        "reciprocal": int(reciprocal),
    }
    return RouteDecision(
        symbol=symbol,
        state=ICHIV21_STATE,
        side=side,
        score=float(current.score),
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=target,
        episode_ts=int(current.ts_event),
        reasons=(
            "PUBLIC_ICHIV21_MULTI_HORIZON_HEIKIN_ASHI_ALIGNMENT",
            "PUBLIC_ICHIV21_FAN_GAIN_AND_THREE_STEP_ACCELERATION",
            "COMPLETED_FIVE_MINUTE_SIGNAL_NEXT_DATA_EXECUTION",
            "RECIPROCAL_PRICE_ADAPTATION" if side < 0 else "PUBLIC_LONG_DIRECTION",
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
    candles = aggregate_five_minute(bars)
    minimum = 490
    if len(candles) < minimum:
        return _unresolved(
            symbol,
            "ICHIV21_HISTORY_NOT_READY",
            latest_ts,
            {"five_minute_candles": len(candles), "minimum": minimum},
        )
    direction_mode = str(config.ichiv21_direction_mode).strip().lower()
    sides = {
        "long_only": (1,),
        "reciprocal_short": (-1,),
        "dual": (1, -1),
    }.get(direction_mode)
    if sides is None:
        raise ValueError(f"unsupported ichiv21_direction_mode={direction_mode!r}")
    candidates = [
        candidate
        for side in sides
        if (candidate := _direction_candidate(symbol, candles, config, side=side)) is not None
    ]
    if not candidates:
        return _unresolved(
            symbol,
            "ICHIV21_NO_SOURCE_ENTRY",
            int(candles[-1].ts_event),
            {
                "direction_mode": direction_mode,
                "alignment_mode": str(config.ichiv21_alignment_mode),
                "episode_mode": str(config.ichiv21_episode_mode),
            },
        )
    candidates.sort(key=lambda decision: (-decision.score, -decision.side))
    if len(candidates) > 1 and abs(candidates[0].score - candidates[1].score) < 0.10:
        return _unresolved(
            symbol,
            "ICHIV21_DIRECTION_AMBIGUITY",
            int(candles[-1].ts_event),
            {"long_score": candidates[0].score, "short_score": candidates[1].score},
        )
    return candidates[0]


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
        key=lambda decision: (
            -float(decision.score),
            _SYMBOL_PRIORITY.get(decision.symbol, 99),
            -int(decision.side),
        )
    )
    return (actionable[0] if actionable else None), decisions


__all__ = [
    "BarObservation", "FeatureObservation", "ICHIV21_STATE", "IchiV21State",
    "RouteConfig", "RouteDecision", "UNRESOLVED", "aggregate_five_minute",
    "classify_symbol", "ichiv21_states", "reciprocal_candles", "route_universe",
]

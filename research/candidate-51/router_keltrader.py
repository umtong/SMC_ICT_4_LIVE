"""Causal public-core adaptation of the Keltrader squeeze breakout.

The external repository redacts ``signal_generator.py`` but leaves the complete
indicator implementation, optimizer ranges and exact winning trade-log header.
This module reuses only those public rules:

* Bollinger Bands: 19 bars, 2.47 sample standard deviations;
* Keltner Channels: 17-bar EMA, 2.38 times 14-bar simple ATR;
* at least two completed squeeze bars followed by a completed release;
* volume-ratio and simple-RSI guards;
* direction from a band break, otherwise the public normalized momentum sign;
* stop 3.45 ATR and target 4.0 ATR from a separately completed ATR timeframe.

No redacted rule or external fill assumption is fabricated.  Alternate signal
timeframes are predeclared experiments for the project's day-trading frequency.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Sequence

from router_picasso import BarObservation, _aggregate_complete

KELTRADER_STATE = "PUBLIC_KELTRADER_SQUEEZE_RELEASE"
SMA_OFFSET_STATE = KELTRADER_STATE
UNRESOLVED = "UNRESOLVED"
_EPS = 1e-12
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}


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

    keltrader_signal_minutes: int = 240
    keltrader_atr_minutes: int = 60
    keltrader_bb_period: int = 19
    keltrader_bb_std: float = 2.47
    keltrader_kc_period: int = 17
    keltrader_kc_atr_mult: float = 2.38
    keltrader_indicator_atr_period: int = 14
    keltrader_momentum_period: int = 12
    keltrader_rsi_period: int = 14
    keltrader_rsi_overbought: float = 70.0
    keltrader_rsi_oversold: float = 30.0
    keltrader_volume_period: int = 20
    keltrader_min_volume_ratio: float = 1.0
    keltrader_min_squeeze_bars: int = 2
    keltrader_require_band_break: bool = False
    keltrader_stop_atr_mult: float = 3.45
    keltrader_target_atr_mult: float = 4.0

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


def _rolling_mean(values: Sequence[float], period: int) -> list[float]:
    output = [math.nan] * len(values)
    if period <= 0:
        return output
    running = 0.0
    for index, raw in enumerate(values):
        running += float(raw)
        if index >= period:
            running -= float(values[index - period])
        if index >= period - 1:
            output[index] = running / period
    return output


def _rolling_sample_std(values: Sequence[float], period: int) -> list[float]:
    output = [math.nan] * len(values)
    if period <= 1:
        return output
    for index in range(period - 1, len(values)):
        sample = [float(value) for value in values[index - period + 1:index + 1]]
        mean = sum(sample) / period
        variance = sum((value - mean) ** 2 for value in sample) / (period - 1)
        output[index] = math.sqrt(max(variance, 0.0))
    return output


def _ewm_adjust_false(values: Sequence[float], span: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (float(span) + 1.0)
    output = [float(values[0])]
    current = float(values[0])
    for raw in values[1:]:
        current = alpha * float(raw) + (1.0 - alpha) * current
        output.append(current)
    return output


def _simple_atr(candles: Sequence[BarObservation], period: int) -> list[float]:
    true_ranges: list[float] = []
    for index, candle in enumerate(candles):
        high = float(candle.high)
        low = float(candle.low)
        if index == 0:
            true_ranges.append(high - low)
        else:
            previous_close = float(candles[index - 1].close)
            true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return _rolling_mean(true_ranges, period)


def _simple_rsi(values: Sequence[float], period: int) -> list[float]:
    gains = [0.0] * len(values)
    losses = [0.0] * len(values)
    for index in range(1, len(values)):
        change = float(values[index]) - float(values[index - 1])
        gains[index] = max(change, 0.0)
        losses[index] = max(-change, 0.0)
    average_gain = _rolling_mean(gains, period)
    average_loss = _rolling_mean(losses, period)
    output = [math.nan] * len(values)
    for index, (gain, loss) in enumerate(zip(average_gain, average_loss, strict=True)):
        if not _finite(gain) or not _finite(loss):
            continue
        if float(loss) <= _EPS:
            output[index] = 100.0 if float(gain) > _EPS else 50.0
        else:
            ratio = float(gain) / float(loss)
            output[index] = 100.0 - 100.0 / (1.0 + ratio)
    return output


def _indicator_state(
    candles: Sequence[BarObservation],
    config: RouteConfig,
) -> dict[str, list[float | bool]]:
    closes = [float(candle.close) for candle in candles]
    volumes = [max(0.0, float(candle.volume)) for candle in candles]
    bb_mid = _rolling_mean(closes, int(config.keltrader_bb_period))
    bb_std = _rolling_sample_std(closes, int(config.keltrader_bb_period))
    atr = _simple_atr(candles, int(config.keltrader_indicator_atr_period))
    kc_mid = _ewm_adjust_false(closes, int(config.keltrader_kc_period))
    bb_upper: list[float] = []
    bb_lower: list[float] = []
    kc_upper: list[float] = []
    kc_lower: list[float] = []
    squeeze: list[bool] = []
    momentum: list[float] = [math.nan] * len(candles)
    volume_ma = _rolling_mean(volumes, int(config.keltrader_volume_period))
    volume_ratio: list[float] = []
    for index in range(len(candles)):
        if _finite(bb_mid[index]) and _finite(bb_std[index]):
            upper = float(bb_mid[index]) + float(config.keltrader_bb_std) * float(bb_std[index])
            lower = float(bb_mid[index]) - float(config.keltrader_bb_std) * float(bb_std[index])
        else:
            upper = lower = math.nan
        if _finite(atr[index]):
            k_upper = float(kc_mid[index]) + float(config.keltrader_kc_atr_mult) * float(atr[index])
            k_lower = float(kc_mid[index]) - float(config.keltrader_kc_atr_mult) * float(atr[index])
        else:
            k_upper = k_lower = math.nan
        bb_upper.append(upper)
        bb_lower.append(lower)
        kc_upper.append(k_upper)
        kc_lower.append(k_lower)
        squeeze.append(
            all(_finite(value) for value in (upper, lower, k_upper, k_lower))
            and lower > k_lower
            and upper < k_upper
        )
        reference = index - int(config.keltrader_momentum_period)
        if reference >= 0 and _finite(bb_mid[reference]) and _finite(atr[index]) and float(atr[index]) > _EPS:
            momentum[index] = (closes[index] - float(bb_mid[reference])) / float(atr[index])
        volume_ratio.append(
            volumes[index] / float(volume_ma[index])
            if _finite(volume_ma[index]) and float(volume_ma[index]) > _EPS
            else math.nan
        )
    return {
        "bb_mid": bb_mid,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "kc_mid": kc_mid,
        "kc_upper": kc_upper,
        "kc_lower": kc_lower,
        "atr": atr,
        "squeeze": squeeze,
        "momentum_norm": momentum,
        "rsi": _simple_rsi(closes, int(config.keltrader_rsi_period)),
        "volume_ratio": volume_ratio,
    }


def keltrader_release_signal(
    candles: Sequence[BarObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[int, dict[str, float | int | str]]:
    """Return -1, 0 or +1 from the latest completed public squeeze state."""
    state = _indicator_state(candles, config)
    index = len(candles) - 1
    previous = index - 1
    diagnostics: dict[str, float | int | str] = {}
    if previous < 0:
        return 0, diagnostics
    squeeze_now = bool(state["squeeze"][index])
    squeeze_previous = bool(state["squeeze"][previous])
    duration = 0
    cursor = previous
    while cursor >= 0 and bool(state["squeeze"][cursor]):
        duration += 1
        cursor -= 1
    values = {
        "bb_mid": float(state["bb_mid"][index]),
        "bb_upper": float(state["bb_upper"][index]),
        "bb_lower": float(state["bb_lower"][index]),
        "kc_mid": float(state["kc_mid"][index]),
        "kc_upper": float(state["kc_upper"][index]),
        "kc_lower": float(state["kc_lower"][index]),
        "indicator_atr": float(state["atr"][index]),
        "momentum_norm": float(state["momentum_norm"][index]),
        "rsi": float(state["rsi"][index]),
        "volume_ratio": float(state["volume_ratio"][index]),
    }
    diagnostics.update(
        {
            **values,
            "squeeze_previous": int(squeeze_previous),
            "squeeze_now": int(squeeze_now),
            "squeeze_duration": duration,
            "require_band_break": int(bool(config.keltrader_require_band_break)),
        }
    )
    if not all(_finite(value) for value in values.values()):
        return 0, diagnostics
    released = squeeze_previous and not squeeze_now
    if not released or duration < int(config.keltrader_min_squeeze_bars):
        return 0, diagnostics
    if values["volume_ratio"] < float(config.keltrader_min_volume_ratio):
        return 0, diagnostics
    close = float(candles[index].close)
    if close > values["bb_upper"]:
        side = 1
    elif close < values["bb_lower"]:
        side = -1
    elif bool(config.keltrader_require_band_break):
        return 0, diagnostics
    elif values["momentum_norm"] > 0.0:
        side = 1
    elif values["momentum_norm"] < 0.0:
        side = -1
    else:
        return 0, diagnostics
    if side > 0 and values["rsi"] >= float(config.keltrader_rsi_overbought):
        return 0, diagnostics
    if side < 0 and values["rsi"] <= float(config.keltrader_rsi_oversold):
        return 0, diagnostics
    diagnostics["side"] = side
    return side, diagnostics


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
    signal_minutes = int(config.keltrader_signal_minutes)
    signal_candles = _aggregate_complete(bars, signal_minutes)
    minimum = max(
        int(config.keltrader_bb_period) + int(config.keltrader_momentum_period) + 3,
        int(config.keltrader_kc_period) + int(config.keltrader_indicator_atr_period) + 3,
        int(config.keltrader_volume_period) + 3,
        int(config.keltrader_rsi_period) + 3,
    )
    if len(signal_candles) < minimum:
        return _unresolved(
            symbol,
            "KELTRADER_SIGNAL_HISTORY_NOT_READY",
            latest_ts,
            {"signal_candles": len(signal_candles), "minimum": minimum},
        )
    side, diagnostics = keltrader_release_signal(signal_candles, config)
    diagnostics.update(
        {
            "signal_minutes": signal_minutes,
            "atr_minutes": int(config.keltrader_atr_minutes),
            "bb_period": int(config.keltrader_bb_period),
            "bb_std": float(config.keltrader_bb_std),
            "kc_period": int(config.keltrader_kc_period),
            "kc_atr_mult": float(config.keltrader_kc_atr_mult),
            "minimum_volume_ratio": float(config.keltrader_min_volume_ratio),
        }
    )
    if side == 0:
        return _unresolved(
            symbol,
            "KELTRADER_NO_COMPLETED_RELEASE_SIGNAL",
            signal_candles[-1].ts_event,
            diagnostics,
        )
    atr_candles = _aggregate_complete(bars, int(config.keltrader_atr_minutes))
    atr_values = _simple_atr(atr_candles, int(config.keltrader_indicator_atr_period))
    if not atr_values or not _finite(atr_values[-1]) or float(atr_values[-1]) <= 0.0:
        return _unresolved(
            symbol,
            "KELTRADER_STOP_ATR_NOT_READY",
            signal_candles[-1].ts_event,
            diagnostics,
        )
    stop_atr = float(atr_values[-1])
    entry = float(signal_candles[-1].close)
    stop = entry - side * float(config.keltrader_stop_atr_mult) * stop_atr
    objective = entry + side * float(config.keltrader_target_atr_mult) * stop_atr
    valid = (
        0.0 < stop < entry < objective
        if side > 0
        else 0.0 < objective < entry < stop
    )
    if not valid:
        return _unresolved(
            symbol,
            "KELTRADER_GEOMETRY_INVALID",
            signal_candles[-1].ts_event,
            diagnostics,
        )
    bb_upper = float(diagnostics["bb_upper"])
    bb_lower = float(diagnostics["bb_lower"])
    band_penetration = (
        max(0.0, entry - bb_upper)
        if side > 0
        else max(0.0, bb_lower - entry)
    ) / max(stop_atr, _EPS)
    score = (
        float(diagnostics["squeeze_duration"])
        + min(4.0, float(diagnostics["volume_ratio"]))
        + min(4.0, abs(float(diagnostics["momentum_norm"])))
        + min(2.0, band_penetration)
    )
    diagnostics.update(
        {
            "stop_atr": stop_atr,
            "stop_atr_multiple": float(config.keltrader_stop_atr_mult),
            "target_atr_multiple": float(config.keltrader_target_atr_mult),
            "reward_risk": float(config.keltrader_target_atr_mult)
            / float(config.keltrader_stop_atr_mult),
            "stop_fraction": abs(entry - stop) / entry,
            "band_penetration_atr": band_penetration,
        }
    )
    return RouteDecision(
        symbol=symbol,
        state=KELTRADER_STATE,
        side=side,
        score=float(score),
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=objective,
        episode_ts=int(signal_candles[-1].ts_event),
        reasons=(
            "PUBLIC_BB_INSIDE_KC_COMPRESSION",
            "COMPLETED_SQUEEZE_RELEASE",
            "PUBLIC_VOLUME_AND_RSI_GUARDS",
            "PUBLIC_ATR_STOP_AND_TARGET",
            "NO_REDACTED_SIGNAL_RULE_INVENTED",
        ),
        diagnostics=diagnostics,
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
                FeatureObservation(bars[-1].ts_event if bars else 0),
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
            int(item.episode_ts),
        )
    )
    return (actionable[0] if actionable else None), decisions


__all__ = [
    "BarObservation",
    "FeatureObservation",
    "KELTRADER_STATE",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "classify_symbol",
    "keltrader_release_signal",
    "route_universe",
]

"""Causal 5-minute E0V1EN router adapted from eovie/freqtrade_strs.

The public source's entry equations and exported 2025-03-27 parameters are
preserved.  Indicators are calculated only from completed right-labeled bars;
there is no backfill, informative-timeframe leakage, pair cherry-picking or
source performance assumption.  Cross-asset arbitration is the only necessary
addition because this project permits one position globally.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Sequence

E0V1EN_STATE = "PUBLIC_E0V1EN_5M_DIP"
SMA_OFFSET_STATE = E0V1EN_STATE
UNRESOLVED = "UNRESOLVED"


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

    e0v1en_entry_mode: str = "exact"
    e0v1en_rsi_fast_period: int = 4
    e0v1en_rsi_period: int = 14
    e0v1en_rsi_slow_period: int = 20
    e0v1en_sma_period: int = 15
    e0v1en_cti_period: int = 20
    e0v1en_cci_period: int = 20
    e0v1en_stoch_period: int = 5
    e0v1en_24h_bars: int = 288
    e0v1en_buy1_rsi_fast_max: float = 40.0
    e0v1en_buy1_rsi_min: float = 42.0
    e0v1en_buy1_sma_fraction: float = 0.973
    e0v1en_buy1_cti_max: float = 0.69
    e0v1en_buy1_change_min_pct: float = -25.8
    e0v1en_buy1_change_max_pct: float = 122.9
    e0v1en_buynew_rsi_fast_max: float = 34.0
    e0v1en_buynew_rsi_min: float = 28.0
    e0v1en_buynew_sma_fraction: float = 0.96
    e0v1en_buynew_cti_max: float = 0.69
    e0v1en_buynew_change_min_pct: float = -24.3
    e0v1en_buynew_change_max_pct: float = 24.3
    e0v1en_stop_fraction: float = 0.25
    e0v1en_objective_fraction: float = 1.0


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
    diagnostics: Mapping[str, float | int | str | bool] = field(default_factory=dict)

    @property
    def actionable(self) -> bool:
        return self.side in (-1, 1) and self.state != UNRESOLVED


def _unresolved(symbol: str, ts_event: int, reason: str, **diagnostics) -> RouteDecision:
    return RouteDecision(
        symbol=symbol,
        state=UNRESOLVED,
        side=0,
        score=0.0,
        entry_reference=math.nan,
        stop_reference=math.nan,
        objective_reference=math.nan,
        episode_ts=int(ts_event),
        reasons=(reason,),
        diagnostics=diagnostics,
    )


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else math.nan


def _wilder_rsi(closes: Sequence[float], period: int) -> float:
    if period <= 0 or len(closes) <= period:
        return math.nan
    changes = [float(closes[i]) - float(closes[i - 1]) for i in range(1, len(closes))]
    gains = [max(value, 0.0) for value in changes]
    losses = [max(-value, 0.0) for value in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss <= 1e-15:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _cti(closes: Sequence[float], period: int) -> float:
    if period <= 1 or len(closes) < period:
        return math.nan
    y = [float(value) for value in closes[-period:]]
    x = [float(index) for index in range(period)]
    mx, my = _mean(x), _mean(y)
    numerator = sum((a - mx) * (b - my) for a, b in zip(x, y))
    dx = sum((a - mx) ** 2 for a in x)
    dy = sum((b - my) ** 2 for b in y)
    denominator = math.sqrt(dx * dy)
    return numerator / denominator if denominator > 0.0 else 0.0


def _cci(candles: Sequence[BarObservation], period: int) -> float:
    if period <= 1 or len(candles) < period:
        return math.nan
    typical = [
        (float(bar.high) + float(bar.low) + float(bar.close)) / 3.0
        for bar in candles[-period:]
    ]
    average = _mean(typical)
    deviation = _mean([abs(value - average) for value in typical])
    return (typical[-1] - average) / (0.015 * deviation) if deviation > 1e-15 else 0.0


def _fastk(candles: Sequence[BarObservation], period: int) -> float:
    if period <= 1 or len(candles) < period:
        return math.nan
    sample = candles[-period:]
    low = min(float(bar.low) for bar in sample)
    high = max(float(bar.high) for bar in sample)
    return 100.0 * (float(sample[-1].close) - low) / (high - low) if high > low else 50.0


def indicators(
    candles: Sequence[BarObservation], config: RouteConfig
) -> dict[str, float] | None:
    minimum = max(
        int(config.e0v1en_24h_bars) + 1,
        int(config.e0v1en_rsi_slow_period) + 2,
        int(config.e0v1en_cti_period),
        int(config.e0v1en_sma_period),
        int(config.e0v1en_cci_period),
        int(config.e0v1en_stoch_period),
    )
    if len(candles) < minimum:
        return None
    closes = [float(bar.close) for bar in candles]
    sma = _mean(closes[-int(config.e0v1en_sma_period):])
    rsi_fast = _wilder_rsi(closes, int(config.e0v1en_rsi_fast_period))
    rsi = _wilder_rsi(closes, int(config.e0v1en_rsi_period))
    rsi_slow = _wilder_rsi(closes, int(config.e0v1en_rsi_slow_period))
    rsi_slow_previous = _wilder_rsi(closes[:-1], int(config.e0v1en_rsi_slow_period))
    change_reference = closes[-1 - int(config.e0v1en_24h_bars)]
    change_pct = 100.0 * (closes[-1] / change_reference - 1.0) if change_reference > 0 else math.nan
    result = {
        "sma_15": sma,
        "cti_20": _cti(closes, int(config.e0v1en_cti_period)),
        "rsi_14": rsi,
        "rsi_fast_4": rsi_fast,
        "rsi_slow_20": rsi_slow,
        "rsi_slow_20_previous": rsi_slow_previous,
        "change_24h_pct": change_pct,
        "fastk_5": _fastk(candles, int(config.e0v1en_stoch_period)),
        "cci_20": _cci(candles, int(config.e0v1en_cci_period)),
    }
    return result if all(math.isfinite(value) for value in result.values()) else None


def classify_symbol(
    symbol: str,
    candles: Sequence[BarObservation],
    feature: FeatureObservation | None = None,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    del feature
    ts_event = int(candles[-1].ts_event) if candles else 0
    values = indicators(candles, config)
    if values is None:
        return _unresolved(symbol, ts_event, "E0V1EN_WARMUP")
    close = float(candles[-1].close)
    sma = values["sma_15"]
    falling_slow_rsi = values["rsi_slow_20"] < values["rsi_slow_20_previous"]
    buy1 = (
        falling_slow_rsi
        and values["rsi_fast_4"] < float(config.e0v1en_buy1_rsi_fast_max)
        and values["rsi_14"] > float(config.e0v1en_buy1_rsi_min)
        and close < sma * float(config.e0v1en_buy1_sma_fraction)
        and values["cti_20"] < float(config.e0v1en_buy1_cti_max)
        and values["change_24h_pct"] > float(config.e0v1en_buy1_change_min_pct)
        and values["change_24h_pct"] < float(config.e0v1en_buy1_change_max_pct)
    )
    buynew = (
        falling_slow_rsi
        and values["rsi_fast_4"] < float(config.e0v1en_buynew_rsi_fast_max)
        and values["rsi_14"] > float(config.e0v1en_buynew_rsi_min)
        and close < sma * float(config.e0v1en_buynew_sma_fraction)
        and values["cti_20"] < float(config.e0v1en_buynew_cti_max)
        and values["change_24h_pct"] > float(config.e0v1en_buynew_change_min_pct)
        and values["change_24h_pct"] < float(config.e0v1en_buynew_change_max_pct)
    )
    mode = str(config.e0v1en_entry_mode).strip().lower()
    if mode == "exact":
        active = buy1 or buynew
    elif mode == "buy1_only":
        active = buy1
    elif mode == "buynew_only":
        active = buynew
    else:
        return _unresolved(symbol, ts_event, "E0V1EN_UNKNOWN_ENTRY_MODE", mode=mode)
    if not active:
        return _unresolved(
            symbol,
            ts_event,
            "E0V1EN_NO_SOURCE_ENTRY",
            buy1=buy1,
            buynew=buynew,
            **values,
        )
    if buy1 and buynew:
        tag = "buy_1+buy_new"
        threshold_fraction = min(
            float(config.e0v1en_buy1_sma_fraction),
            float(config.e0v1en_buynew_sma_fraction),
        )
    elif buynew:
        tag = "buy_new"
        threshold_fraction = float(config.e0v1en_buynew_sma_fraction)
    else:
        tag = "buy_1"
        threshold_fraction = float(config.e0v1en_buy1_sma_fraction)
    threshold_price = sma * threshold_fraction
    dip_severity = max(0.0, threshold_price - close) / threshold_price if threshold_price > 0.0 else 0.0
    oversold_severity = max(0.0, 50.0 - values["rsi_fast_4"]) / 50.0
    score = 100.0 * dip_severity + oversold_severity + max(0.0, -values["cti_20"])
    stop = close * (1.0 - float(config.e0v1en_stop_fraction))
    objective = close * (1.0 + float(config.e0v1en_objective_fraction))
    if not (0.0 < stop < close < objective):
        return _unresolved(symbol, ts_event, "E0V1EN_INVALID_GEOMETRY")
    return RouteDecision(
        symbol=symbol,
        state=E0V1EN_STATE,
        side=1,
        score=score,
        entry_reference=close,
        stop_reference=stop,
        objective_reference=objective,
        episode_ts=ts_event,
        reasons=("PUBLIC_E0V1EN_SOURCE_ENTRY", tag),
        diagnostics={
            "entry_tag": tag,
            "buy1": buy1,
            "buynew": buynew,
            "source_stop_fraction": float(config.e0v1en_stop_fraction),
            "source_objective_fraction": float(config.e0v1en_objective_fraction),
            **values,
        },
    )


classify_sma_offset = classify_symbol


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
):
    decisions = {
        symbol: classify_symbol(symbol, bars, features_by_symbol.get(symbol), config)
        for symbol, bars in bars_by_symbol.items()
    }
    candidates = [decision for decision in decisions.values() if decision.actionable]
    candidates.sort(key=lambda decision: (-float(decision.score), decision.symbol))
    return (candidates[0] if candidates else None), decisions


__all__ = [
    "BarObservation", "FeatureObservation", "RouteConfig", "RouteDecision",
    "E0V1EN_STATE", "SMA_OFFSET_STATE", "UNRESOLVED", "indicators",
    "classify_symbol", "classify_sma_offset", "route_universe",
]

"""Causal trend-quality state shared by V15 forensics and execution.

The state is adapted from the public ``go-trader`` composite regime policy
which separated clean directional expansion from choppy directional drift.
It is intentionally not a parameter search.  Candidate 55 fixes one economic
question before replay:

    Are V15 Bollinger short losses concentrated in choppy/non-directional
    auctions while the gross-profit engine is concentrated in clean downside
    price discovery?

Only completed bars are consumed.  Net displacement and range are normalized
by straight-line ATR travel, Kaufman efficiency measures path cleanliness, and
Wilder ADX corroborates directional persistence.  The fixed labels are used
both by the result-blind episode audit and by the one-slot Nautilus policy so
the diagnostic and executable state cannot silently diverge.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Sequence

from router import (
    BarObservation,
    _adx_dx,
    _aggregate_complete,
    _atr,
    _directional_indicators,
)


TRENDING_UP_CLEAN = "trending_up_clean"
TRENDING_UP_CHOPPY = "trending_up_choppy"
TRENDING_DOWN_CLEAN = "trending_down_clean"
TRENDING_DOWN_CHOPPY = "trending_down_choppy"
RANGING_QUIET = "ranging_quiet"
RANGING_VOLATILE = "ranging_volatile"
RANGING_DIRECTIONAL_UP = "ranging_directional_up"
RANGING_DIRECTIONAL_DOWN = "ranging_directional_down"


@dataclass(frozen=True)
class RegimeThresholds:
    return_eff: float = 0.05
    range_eff: float = 0.03
    adx: float = 25.0
    efficiency: float = 0.50


@dataclass(frozen=True)
class RegimeSnapshot:
    observed_time_ns: int
    ready: bool
    label: str
    return_eff: float
    range_eff: float
    efficiency: float
    adx: float
    plus_di: float
    minus_di: float
    atr_fraction: float
    window_net_fraction: float

    def diagnostics(self) -> dict[str, Any]:
        return asdict(self)


def map_label(
    *,
    return_eff: float,
    range_eff: float,
    efficiency: float,
    adx: float,
    thresholds: RegimeThresholds = RegimeThresholds(),
) -> str:
    """Map one fully observed metric tuple to a fixed auction-state label."""
    big_move = abs(return_eff) >= float(thresholds.return_eff)
    high_adx = adx >= float(thresholds.adx)
    clean = efficiency >= float(thresholds.efficiency) and high_adx
    if big_move:
        if return_eff > 0.0:
            return TRENDING_UP_CLEAN if clean else TRENDING_UP_CHOPPY
        return TRENDING_DOWN_CLEAN if clean else TRENDING_DOWN_CHOPPY
    if high_adx:
        if return_eff > 0.0:
            return RANGING_DIRECTIONAL_UP
        if return_eff < 0.0:
            return RANGING_DIRECTIONAL_DOWN
    if range_eff >= float(thresholds.range_eff):
        return RANGING_VOLATILE
    return RANGING_QUIET


def regime_series(
    candles: Sequence[BarObservation],
    *,
    period: int = 21,
    adx_period_cap: int = 14,
    thresholds: RegimeThresholds = RegimeThresholds(),
) -> list[RegimeSnapshot]:
    """Return one causal snapshot for every completed aggregate candle.

    Unready observations are retained with ``ready=False`` so callers can keep
    an exact clock and fail closed rather than back-filling a future label.
    """
    if period < 2:
        raise ValueError("regime period must be at least two bars")
    if adx_period_cap < 2:
        raise ValueError("ADX period cap must be at least two bars")
    size = len(candles)
    empty = RegimeSnapshot(
        observed_time_ns=0,
        ready=False,
        label=RANGING_QUIET,
        return_eff=math.nan,
        range_eff=math.nan,
        efficiency=math.nan,
        adx=math.nan,
        plus_di=math.nan,
        minus_di=math.nan,
        atr_fraction=math.nan,
        window_net_fraction=math.nan,
    )
    output = [empty for _ in range(size)]
    if not candles:
        return output

    atr = _atr(candles, period)
    adx_period = min(int(period), int(adx_period_cap))
    plus_di, minus_di = _directional_indicators(candles, adx_period)
    _, adx = _adx_dx(plus_di, minus_di, adx_period)

    for index in range(size):
        observed = int(candles[index].ts_event)
        if index < period - 1:
            output[index] = RegimeSnapshot(
                observed, False, RANGING_QUIET,
                math.nan, math.nan, math.nan, math.nan,
                math.nan, math.nan, math.nan, math.nan,
            )
            continue
        start = index - period + 1
        close_end = float(candles[index].close)
        close_start = float(candles[start].close)
        atr_value = float(atr[index])
        adx_value = float(adx[index])
        pdi_value = float(plus_di[index])
        mdi_value = float(minus_di[index])
        values = (close_end, close_start, atr_value, adx_value, pdi_value, mdi_value)
        if not all(math.isfinite(value) for value in values) or close_end <= 0.0 or atr_value <= 0.0:
            output[index] = RegimeSnapshot(
                observed, False, RANGING_QUIET,
                math.nan, math.nan, math.nan, math.nan,
                math.nan, math.nan, math.nan, math.nan,
            )
            continue

        window = candles[start : index + 1]
        net = close_end - close_start
        high = max(float(item.high) for item in window)
        low = min(float(item.low) for item in window)
        path = sum(
            abs(float(window[offset].close) - float(window[offset - 1].close))
            for offset in range(1, len(window))
        )
        denominator = atr_value * float(period)
        return_eff = net / denominator
        range_eff = (high - low) / denominator
        efficiency = abs(net) / path if path > 0.0 else 0.0
        label = map_label(
            return_eff=return_eff,
            range_eff=range_eff,
            efficiency=efficiency,
            adx=adx_value,
            thresholds=thresholds,
        )
        output[index] = RegimeSnapshot(
            observed_time_ns=observed,
            ready=True,
            label=label,
            return_eff=float(return_eff),
            range_eff=float(range_eff),
            efficiency=float(efficiency),
            adx=float(adx_value),
            plus_di=float(pdi_value),
            minus_di=float(mdi_value),
            atr_fraction=float(atr_value / close_end),
            window_net_fraction=float(net / close_start) if close_start > 0.0 else math.nan,
        )
    return output


def latest_regime_from_minutes(
    bars: Sequence[BarObservation],
    *,
    bucket_minutes: int = 30,
    period: int = 21,
    thresholds: RegimeThresholds = RegimeThresholds(),
) -> RegimeSnapshot:
    """Classify the latest fully completed higher-timeframe auction."""
    candles = _aggregate_complete(bars, int(bucket_minutes))
    snapshots = regime_series(candles, period=int(period), thresholds=thresholds)
    if not snapshots:
        return RegimeSnapshot(
            0, False, RANGING_QUIET,
            math.nan, math.nan, math.nan, math.nan,
            math.nan, math.nan, math.nan, math.nan,
        )
    return snapshots[-1]


__all__ = [
    "RANGING_DIRECTIONAL_DOWN",
    "RANGING_DIRECTIONAL_UP",
    "RANGING_QUIET",
    "RANGING_VOLATILE",
    "RegimeSnapshot",
    "RegimeThresholds",
    "TRENDING_DOWN_CHOPPY",
    "TRENDING_DOWN_CLEAN",
    "TRENDING_UP_CHOPPY",
    "TRENDING_UP_CLEAN",
    "latest_regime_from_minutes",
    "map_label",
    "regime_series",
]

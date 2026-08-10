"""Causal adapter for the public Slope-is-Dope futures system.

Source: ``syuraj/freq-test``
``picasso_slope_is_dope_adx_1h_2Lev_dec15_3mt.py``.

The source report is a search clue, not evidence.  This module preserves the
executable one-hour entry mechanism:

* SMA(16), SMA(57), SMA(97), RSI(10), ADX(14);
* the source slope is ``(MA[t-1] - MA[t-11]) / 10``;
* long: ADX>39, price>SMA97, fast/slow slopes positive, price>price[t-6],
  RSI>55 and SMA16>SMA57;
* short: ADX>20, price<SMA97, slopes negative, price<price[t-9], RSI<55 and
  SMA16<SMA57.

The source short exit compares close to a shifted rolling *low*, which is
usually immediately true.  It is preserved as ``source_exact`` and contrasted
with a symmetric shifted rolling-high interpretation, MA-only and no-signal
management.  These are separate mechanisms, not silent corrections.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Sequence

import router_picasso as _ta

BarObservation = _ta.BarObservation
FeatureObservation = _ta.FeatureObservation
UNRESOLVED = "UNRESOLVED"
SLOPE_STATE = "PUBLIC_SLOPE_DOPE_ADX_SMA"
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

    slope_bucket_minutes: int = 60
    slope_episode_mode: str = "condition_reentry"
    slope_direction_mode: str = "dual"
    slope_adx_period: int = 14
    slope_rsi_period: int = 10
    slope_fast_period: int = 16
    slope_slow_period: int = 57
    slope_market_period: int = 97
    slope_lookback: int = 10
    slope_long_close_shift: int = 6
    slope_short_close_shift: int = 9
    slope_long_adx_min: float = 39.0
    slope_short_adx_min: float = 20.0
    slope_rsi_midline: float = 55.0
    slope_source_leverage: float = 2.0
    slope_source_stoploss_profit_ratio: float = 0.289
    slope_remote_target_fraction: float = 0.1415


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


def _state_at(
    candles: Sequence[BarObservation],
    index: int,
    config: RouteConfig,
) -> tuple[bool, bool, dict[str, float | int | str]]:
    closes = [float(candle.close) for candle in candles]
    fast = _ta._sma(closes, int(config.slope_fast_period))
    slow = _ta._sma(closes, int(config.slope_slow_period))
    market = _ta._sma(closes, int(config.slope_market_period))
    rsi = _ta._rsi(closes, int(config.slope_rsi_period))
    adx = _ta._adx(candles, int(config.slope_adx_period))
    lookback = int(config.slope_lookback)
    historical = index - (lookback + 1)
    recent = index - 1
    if historical < 0 or recent < 0:
        return False, False, {"ready": 0}
    required = (
        fast[index], slow[index], market[index], rsi[index], adx[index],
        fast[recent], fast[historical], slow[recent], slow[historical],
    )
    if not all(_finite(value) for value in required):
        return False, False, {"ready": 0}
    long_shift = int(config.slope_long_close_shift)
    short_shift = int(config.slope_short_close_shift)
    if index < max(long_shift, short_shift):
        return False, False, {"ready": 0}
    fast_slope = (float(fast[recent]) - float(fast[historical])) / max(lookback, 1)
    slow_slope = (float(slow[recent]) - float(slow[historical])) / max(lookback, 1)
    close = float(closes[index])
    long_ok = (
        float(adx[index]) > float(config.slope_long_adx_min)
        and close > float(market[index])
        and fast_slope > 0.0
        and slow_slope > 0.0
        and close > float(closes[index - long_shift])
        and float(rsi[index]) > float(config.slope_rsi_midline)
        and float(fast[index]) > float(slow[index])
    )
    short_ok = (
        float(adx[index]) > float(config.slope_short_adx_min)
        and close < float(market[index])
        and fast_slope < 0.0
        and slow_slope < 0.0
        and close < float(closes[index - short_shift])
        and float(rsi[index]) < float(config.slope_rsi_midline)
        and float(fast[index]) < float(slow[index])
    )
    diagnostics: dict[str, float | int | str] = {
        "ready": 1,
        "close": close,
        "adx": float(adx[index]),
        "rsi": float(rsi[index]),
        "fast_sma": float(fast[index]),
        "slow_sma": float(slow[index]),
        "market_sma": float(market[index]),
        "fast_slope": fast_slope,
        "slow_slope": slow_slope,
        "long_shift_close": float(closes[index - long_shift]),
        "short_shift_close": float(closes[index - short_shift]),
        "long_condition": int(long_ok),
        "short_condition": int(short_ok),
    }
    return long_ok, short_ok, diagnostics


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
    candles = _ta._aggregate_complete(bars, int(config.slope_bucket_minutes))
    minimum = max(
        int(config.slope_market_period) + 15,
        int(config.slope_slow_period) + int(config.slope_lookback) + 5,
        int(config.slope_adx_period) * 2 + 5,
    )
    if len(candles) < minimum:
        return _unresolved(
            symbol,
            "SLOPE_HISTORY_NOT_READY",
            latest_ts,
            {"hourly_candles": len(candles), "minimum": minimum},
        )
    current_long, current_short, diagnostics = _state_at(candles, len(candles) - 1, config)
    previous_long, previous_short, _ = _state_at(candles, len(candles) - 2, config)
    direction_mode = str(config.slope_direction_mode).strip().lower()
    if direction_mode == "long_only":
        current_short = False
    elif direction_mode == "short_only":
        current_long = False
    elif direction_mode != "dual":
        raise ValueError(f"unsupported slope_direction_mode={direction_mode!r}")
    diagnostics.update(
        {
            "previous_long_condition": int(previous_long),
            "previous_short_condition": int(previous_short),
            "episode_mode": str(config.slope_episode_mode),
            "direction_mode": direction_mode,
        }
    )
    if current_long and current_short:
        return _unresolved(symbol, "SLOPE_DIRECTION_AMBIGUITY", candles[-1].ts_event, diagnostics)
    if not current_long and not current_short:
        return _unresolved(symbol, "SLOPE_NO_SOURCE_ENTRY", candles[-1].ts_event, diagnostics)
    side = 1 if current_long else -1
    episode_mode = str(config.slope_episode_mode).strip().lower()
    prior = previous_long if side > 0 else previous_short
    if episode_mode == "rising_edge" and prior:
        return _unresolved(symbol, "SLOPE_CONTIGUOUS_EPISODE_ALREADY_ACTIVE", candles[-1].ts_event, diagnostics)
    if episode_mode not in {"condition_reentry", "rising_edge"}:
        raise ValueError(f"unsupported slope_episode_mode={episode_mode!r}")

    entry = float(candles[-1].close)
    leverage = max(float(config.slope_source_leverage), 1e-12)
    stop_fraction = float(config.slope_source_stoploss_profit_ratio) / leverage
    target_fraction = float(config.slope_remote_target_fraction)
    stop = entry * (1.0 - side * stop_fraction)
    target = entry * (1.0 + side * target_fraction)
    adx_margin = float(diagnostics["adx"]) - (
        float(config.slope_long_adx_min) if side > 0 else float(config.slope_short_adx_min)
    )
    slope_strength = (
        abs(float(diagnostics["fast_slope"])) + abs(float(diagnostics["slow_slope"]))
    ) / max(entry, 1e-12)
    ma_separation = abs(float(diagnostics["fast_sma"]) - float(diagnostics["slow_sma"])) / max(entry, 1e-12)
    score = (
        min(max(adx_margin, 0.0) / 10.0, 5.0)
        + min(slope_strength * 10_000.0, 5.0)
        + min(ma_separation * 10_000.0, 5.0)
        + min(abs(float(diagnostics["rsi"]) - float(config.slope_rsi_midline)) / 10.0, 5.0)
    )
    diagnostics.update(
        {
            "source_tag": "LONG" if side > 0 else "SHORT",
            "source_leverage": leverage,
            "source_stoploss_profit_ratio": float(config.slope_source_stoploss_profit_ratio),
            "underlying_stop_fraction": stop_fraction,
            "ma_separation_fraction": ma_separation,
            "slope_strength_fraction": slope_strength,
        }
    )
    return RouteDecision(
        symbol=symbol,
        state=SLOPE_STATE,
        side=side,
        score=float(score),
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=target,
        episode_ts=int(candles[-1].ts_event),
        reasons=(
            "PUBLIC_SLOPE_DOPE_ADX_SMA_RSI_ENTRY",
            "PUBLIC_SHIFTED_MA_SLOPE_SEMANTICS",
            "COMPLETED_ONE_HOUR_SIGNAL_NEXT_DATA_EXECUTION",
        ),
        diagnostics=diagnostics,
    )


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
    "BarObservation", "FeatureObservation", "RouteConfig", "RouteDecision",
    "SLOPE_STATE", "UNRESOLVED", "classify_symbol", "route_universe",
]

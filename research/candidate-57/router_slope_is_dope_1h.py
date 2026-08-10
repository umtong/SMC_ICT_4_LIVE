"""Public Slope-is-Dope one-hour trend adapter.

The source is a completed-candle level strategy.  This module preserves the
published trend, slope, momentum and literal rolling-low exit state while
providing project-compatible causal cross-symbol arbitration.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence

from router_picasso import (
    BarObservation,
    FeatureObservation,
    RouteConfig as _PicassoRouteConfig,
    RouteDecision,
    UNRESOLVED,
    _SYMBOL_PRIORITY,
    _adx,
    _aggregate_complete,
    _finite,
    _rsi,
    _sma,
)

SLOPE_STATE = "PUBLIC_SLOPE_IS_DOPE_1H"
PICASSO_STATE = SLOPE_STATE
SMA_OFFSET_STATE = SLOPE_STATE


@dataclass(frozen=True, slots=True)
class RouteConfig(_PicassoRouteConfig):
    slope_trigger_mode: str = "level"
    slope_side_mode: str = "both"
    slope_adx_period: int = 14
    slope_rsi_period: int = 10
    slope_market_ma_period: int = 97
    slope_fast_ma_period: int = 16
    slope_slow_ma_period: int = 57
    slope_adx_long: float = 39.0
    slope_adx_short: float = 20.0
    slope_close_shift_long: int = 6
    slope_close_shift_short: int = 9
    slope_source_effective_leverage: float = 2.0
    slope_source_stoploss: float = 0.289
    slope_emergency_target_fraction: float = 0.20


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


def _mode(value: str) -> tuple[str, str]:
    raw = str(value).strip().lower()
    if raw not in {"level", "edge"}:
        raise ValueError(f"unsupported slope_trigger_mode={raw!r}")
    return raw, raw


def _side_allowed(mode: str, side: int) -> bool:
    raw = str(mode).strip().lower()
    if raw == "both":
        return True
    if raw == "long":
        return side > 0
    if raw == "short":
        return side < 0
    raise ValueError(f"unsupported slope_side_mode={raw!r}")


def _slope(values: Sequence[float], index: int) -> float:
    if index - 11 < 0:
        return math.nan
    recent = float(values[index - 1])
    old = float(values[index - 11])
    if not _finite(recent) or not _finite(old):
        return math.nan
    return (recent - old) / 10.0


def _flags_at(
    candles: Sequence[BarObservation],
    market_ma: Sequence[float],
    fast_ma: Sequence[float],
    slow_ma: Sequence[float],
    rsi: Sequence[float],
    adx: Sequence[float],
    index: int,
    config: RouteConfig,
) -> tuple[bool, bool, dict[str, float]]:
    if index < 12:
        return False, False, {}
    long_shift = int(config.slope_close_shift_long)
    short_shift = int(config.slope_close_shift_short)
    if index - max(long_shift, short_shift) < 0:
        return False, False, {}
    values = (
        market_ma[index],
        fast_ma[index],
        slow_ma[index],
        rsi[index],
        adx[index],
    )
    if not all(_finite(value) for value in values):
        return False, False, {}
    fast_slope = _slope(fast_ma, index)
    slow_slope = _slope(slow_ma, index)
    if not _finite(fast_slope) or not _finite(slow_slope):
        return False, False, {}
    close = float(candles[index].close)
    volume = float(candles[index].volume)
    long_ok = (
        float(adx[index]) > float(config.slope_adx_long)
        and close > float(market_ma[index])
        and slow_slope > 0.0
        and fast_slope > 0.0
        and close > float(candles[index - long_shift].close)
        and float(rsi[index]) > 55.0
        and float(fast_ma[index]) > float(slow_ma[index])
        and volume > 0.0
    )
    short_ok = (
        float(adx[index]) > float(config.slope_adx_short)
        and close < float(market_ma[index])
        and slow_slope < 0.0
        and fast_slope < 0.0
        and close < float(candles[index - short_shift].close)
        and float(rsi[index]) < 55.0
        and float(fast_ma[index]) < float(slow_ma[index])
        and volume > 0.0
    )
    details = {
        "close": close,
        "volume": volume,
        "adx": float(adx[index]),
        "rsi": float(rsi[index]),
        "market_ma": float(market_ma[index]),
        "fast_ma": float(fast_ma[index]),
        "slow_ma": float(slow_ma[index]),
        "fast_slope": fast_slope,
        "slow_slope": slow_slope,
        "long_shift_close": float(candles[index - long_shift].close),
        "short_shift_close": float(candles[index - short_shift].close),
    }
    return bool(long_ok), bool(short_ok), details


def route_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    if not feature.ready:
        return _unresolved(symbol, "FEATURE_NOT_READY")
    candles = _aggregate_complete(bars, int(config.picasso_bucket_minutes))
    required = max(
        int(config.slope_market_ma_period),
        int(config.slope_fast_ma_period) + 12,
        int(config.slope_slow_ma_period) + 12,
        int(config.slope_rsi_period) + 2,
        int(config.slope_adx_period) * 2 + 2,
        int(config.slope_close_shift_long) + 2,
        int(config.slope_close_shift_short) + 2,
    )
    if len(candles) < required:
        return _unresolved(
            symbol,
            "INSUFFICIENT_SLOPE_WARMUP",
            int(candles[-1].ts_event) if candles else 0,
            {"completed_source_candles": len(candles), "required": required},
        )

    closes = [float(candle.close) for candle in candles]
    market_ma = _sma(closes, int(config.slope_market_ma_period))
    fast_ma = _sma(closes, int(config.slope_fast_ma_period))
    slow_ma = _sma(closes, int(config.slope_slow_ma_period))
    rsi = _rsi(closes, int(config.slope_rsi_period))
    adx = _adx(candles, int(config.slope_adx_period))
    index = len(candles) - 1
    long_now, short_now, details = _flags_at(
        candles, market_ma, fast_ma, slow_ma, rsi, adx, index, config
    )
    trigger, _ = _mode(config.slope_trigger_mode)
    if trigger == "edge":
        long_previous, short_previous, _ = _flags_at(
            candles, market_ma, fast_ma, slow_ma, rsi, adx, index - 1, config
        )
        long_now = long_now and not long_previous
        short_now = short_now and not short_previous

    if not _side_allowed(config.slope_side_mode, 1):
        long_now = False
    if not _side_allowed(config.slope_side_mode, -1):
        short_now = False
    episode_ts = int(candles[-1].ts_event)
    diagnostics: dict[str, float | int | str] = {
        **details,
        "source_signal_long": int(long_now),
        "source_signal_short": int(short_now),
        "source_signal_any": int(long_now or short_now),
        "source_trigger_mode": trigger,
        "source_side_mode": str(config.slope_side_mode),
        "source_bucket_minutes": int(config.picasso_bucket_minutes),
        "source_adx_long": float(config.slope_adx_long),
        "source_adx_short": float(config.slope_adx_short),
        "source_market_ma_period": int(config.slope_market_ma_period),
        "source_fast_ma_period": int(config.slope_fast_ma_period),
        "source_slow_ma_period": int(config.slope_slow_ma_period),
        "source_rsi_period": int(config.slope_rsi_period),
        "source_close_shift_long": int(config.slope_close_shift_long),
        "source_close_shift_short": int(config.slope_close_shift_short),
        "source_level_reentry_preserved": int(trigger == "level"),
    }
    if not long_now and not short_now:
        return _unresolved(symbol, "SLOPE_SOURCE_NO_SIGNAL", episode_ts, diagnostics)

    if long_now and short_now:
        return _unresolved(symbol, "SLOPE_SOURCE_AMBIGUOUS", episode_ts, diagnostics)
    side = 1 if long_now else -1
    entry = float(candles[-1].close)
    leverage = max(float(config.slope_source_effective_leverage), 1e-12)
    stop_fraction = float(config.slope_source_stoploss) / leverage
    stop = entry * (1.0 - side * stop_fraction)
    objective = entry * (
        1.0 + side * float(config.slope_emergency_target_fraction)
    )
    adx_value = float(details["adx"])
    fast_slope = abs(float(details["fast_slope"])) / max(entry, 1e-12)
    slow_slope = abs(float(details["slow_slope"])) / max(entry, 1e-12)
    rsi_distance = abs(float(details["rsi"]) - 55.0)
    score = (
        adx_value
        + min(25.0, fast_slope * 100_000.0)
        + min(25.0, slow_slope * 100_000.0)
        + min(10.0, rsi_distance / 2.0)
    )
    diagnostics.update(
        {
            "source_side": side,
            "source_score": score,
            "source_effective_leverage": leverage,
            "source_stoploss_profit_ratio": float(config.slope_source_stoploss),
            "source_stop_fraction": stop_fraction,
        }
    )
    return RouteDecision(
        symbol=symbol,
        state=SLOPE_STATE,
        side=side,
        score=float(score),
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=objective,
        episode_ts=episode_ts,
        reasons=(
            "PUBLIC_SLOPE_LEVEL_LONG" if side > 0 else "PUBLIC_SLOPE_LEVEL_SHORT",
        ),
        diagnostics=diagnostics,
    )


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    decisions = {
        symbol: route_symbol(
            symbol,
            bars_by_symbol[symbol],
            features_by_symbol[symbol],
            config,
        )
        for symbol in bars_by_symbol
        if symbol in features_by_symbol
    }
    actionable = [decision for decision in decisions.values() if decision.actionable]
    actionable.sort(
        key=lambda decision: (
            -float(decision.score),
            _SYMBOL_PRIORITY.get(decision.symbol, 99),
            int(decision.episode_ts),
        )
    )
    return (actionable[0] if actionable else None), decisions


__all__ = [
    "BarObservation",
    "FeatureObservation",
    "PICASSO_STATE",
    "RouteConfig",
    "RouteDecision",
    "SLOPE_STATE",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "route_symbol",
    "route_universe",
]

"""Causal adapter for the public ZaratustraV5 5m/15m/30m futures state."""
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
    _aggregate_complete,
    _atr,
    _finite,
    _rsi,
    _sma,
)

ZARA_STATE = "PUBLIC_ZARATUSTRA_V5"
PICASSO_STATE = ZARA_STATE
SMA_OFFSET_STATE = ZARA_STATE


@dataclass(frozen=True, slots=True)
class RouteConfig(_PicassoRouteConfig):
    zara_trigger_mode: str = "level"
    zara_side_mode: str = "both"
    zara_risk_mode: str = "source_fraction"
    zara_rsi_period: int = 14
    zara_di_period: int = 14
    zara_bb_period: int = 20
    zara_rsi_threshold: float = 50.0
    zara_di_threshold: float = 25.0
    zara_source_stop_fraction: float = 0.0296
    zara_target_fraction: float = 0.20
    zara_structural_lookback_5m: int = 8
    zara_atr_period_5m: int = 14
    zara_stop_atr_buffer: float = 0.25
    zara_min_stop_fraction: float = 0.0015


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


def _directional_indicators(
    candles: Sequence[BarObservation], period: int
) -> tuple[list[float], list[float]]:
    """Wilder PLUS_DI/MINUS_DI aligned to completed source candles."""
    size = len(candles)
    plus = [math.nan] * size
    minus = [math.nan] * size
    if period <= 0 or size <= period:
        return plus, minus
    tr = [0.0] * size
    plus_dm = [0.0] * size
    minus_dm = [0.0] * size
    for index in range(1, size):
        current = candles[index]
        previous = candles[index - 1]
        high = float(current.high)
        low = float(current.low)
        previous_high = float(previous.high)
        previous_low = float(previous.low)
        previous_close = float(previous.close)
        tr[index] = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )
        up_move = high - previous_high
        down_move = previous_low - low
        plus_dm[index] = up_move if up_move > down_move and up_move > 0.0 else 0.0
        minus_dm[index] = down_move if down_move > up_move and down_move > 0.0 else 0.0

    tr_sum = sum(tr[1 : period + 1])
    plus_sum = sum(plus_dm[1 : period + 1])
    minus_sum = sum(minus_dm[1 : period + 1])
    if tr_sum > 1e-12:
        plus[period] = 100.0 * plus_sum / tr_sum
        minus[period] = 100.0 * minus_sum / tr_sum
    for index in range(period + 1, size):
        tr_sum = tr_sum - tr_sum / period + tr[index]
        plus_sum = plus_sum - plus_sum / period + plus_dm[index]
        minus_sum = minus_sum - minus_sum / period + minus_dm[index]
        if tr_sum > 1e-12:
            plus[index] = 100.0 * plus_sum / tr_sum
            minus[index] = 100.0 * minus_sum / tr_sum
    return plus, minus


def _typical_middle(
    candles: Sequence[BarObservation], period: int
) -> list[float]:
    typical = [
        (float(candle.high) + float(candle.low) + float(candle.close)) / 3.0
        for candle in candles
    ]
    return _sma(typical, period)


def _timeframe_snapshot(
    bars: Sequence[BarObservation], timeframe: int, config: RouteConfig
) -> dict[str, float] | None:
    candles = _aggregate_complete(bars, timeframe)
    minimum = max(
        int(config.zara_rsi_period) + 2,
        int(config.zara_di_period) + 2,
        int(config.zara_bb_period) + 2,
    )
    if len(candles) < minimum:
        return None
    closes = [float(candle.close) for candle in candles]
    rsi = _rsi(closes, int(config.zara_rsi_period))[-1]
    plus_di, minus_di = _directional_indicators(candles, int(config.zara_di_period))
    middle = _typical_middle(candles, int(config.zara_bb_period))[-1]
    close = float(candles[-1].close)
    values = (rsi, plus_di[-1], minus_di[-1], middle, close)
    if not all(_finite(value) for value in values):
        return None
    return {
        "close": close,
        "rsi": float(rsi),
        "plus_di": float(plus_di[-1]),
        "minus_di": float(minus_di[-1]),
        "bb_middle": float(middle),
        "ts_event": float(candles[-1].ts_event),
    }


def _level(
    bars: Sequence[BarObservation], config: RouteConfig
) -> tuple[bool, bool, dict[str, float | int | str]]:
    snapshots = {
        label: _timeframe_snapshot(bars, minutes, config)
        for label, minutes in (("5m", 5), ("15m", 15), ("30m", 30))
    }
    if any(value is None for value in snapshots.values()):
        return False, False, {}
    rsi_threshold = float(config.zara_rsi_threshold)
    di_threshold = float(config.zara_di_threshold)
    long_ok = True
    short_ok = True
    diagnostics: dict[str, float | int | str] = {}
    for label, raw in snapshots.items():
        assert raw is not None
        close = float(raw["close"])
        rsi = float(raw["rsi"])
        plus_di = float(raw["plus_di"])
        minus_di = float(raw["minus_di"])
        middle = float(raw["bb_middle"])
        long_ok = (
            long_ok
            and rsi > rsi_threshold
            and plus_di > di_threshold
            and close > middle
        )
        short_ok = (
            short_ok
            and rsi < rsi_threshold
            and minus_di > di_threshold
            and close < middle
        )
        diagnostics.update(
            {
                f"close_{label}": close,
                f"rsi_{label}": rsi,
                f"plus_di_{label}": plus_di,
                f"minus_di_{label}": minus_di,
                f"bb_middle_{label}": middle,
                f"source_ts_{label}": int(raw["ts_event"]),
            }
        )
    side_mode = str(config.zara_side_mode).strip().lower()
    if side_mode == "long":
        short_ok = False
    elif side_mode == "short":
        long_ok = False
    elif side_mode != "both":
        raise ValueError(f"unsupported zara_side_mode={side_mode!r}")
    diagnostics.update(
        {
            "source_long_level": int(long_ok),
            "source_short_level": int(short_ok),
            "source_rsi_threshold": rsi_threshold,
            "source_di_threshold": di_threshold,
            "source_side_mode": side_mode,
        }
    )
    return bool(long_ok), bool(short_ok), diagnostics


def _geometry(
    bars: Sequence[BarObservation],
    side: int,
    diagnostics: Mapping[str, float | int | str],
    config: RouteConfig,
) -> tuple[float, float, dict[str, float | int | str]]:
    candles = _aggregate_complete(bars, 5)
    entry = float(candles[-1].close)
    target_fraction = float(config.zara_target_fraction)
    risk_mode = str(config.zara_risk_mode).strip().lower()
    if target_fraction <= 0.0:
        raise ValueError("zara_target_fraction must be positive")
    if risk_mode == "source_fraction":
        stop_fraction = float(config.zara_source_stop_fraction)
        if not 0.0 < stop_fraction < 1.0:
            raise ValueError("zara_source_stop_fraction must be in (0,1)")
        stop = entry * (1.0 - side * stop_fraction)
        anchor = stop
        buffer = 0.0
    elif risk_mode == "auction_structure":
        lookback = max(2, int(config.zara_structural_lookback_5m))
        recent = candles[-lookback:]
        atr = float(_atr(candles, int(config.zara_atr_period_5m))[-1])
        buffer = max(
            atr * float(config.zara_stop_atr_buffer),
            entry * 0.0002,
        )
        minimum = entry * float(config.zara_min_stop_fraction)
        middle_values = [
            float(diagnostics[f"bb_middle_{label}"])
            for label in ("5m", "15m", "30m")
        ]
        if side > 0:
            recent_extreme = min(float(candle.low) for candle in recent)
            supports = [value for value in middle_values if 0.0 < value < entry]
            supports.append(recent_extreme)
            anchor = max(supports)
            stop = min(anchor - buffer, entry - minimum)
        else:
            recent_extreme = max(float(candle.high) for candle in recent)
            resistances = [value for value in middle_values if value > entry]
            resistances.append(recent_extreme)
            anchor = min(resistances)
            stop = max(anchor + buffer, entry + minimum)
    else:
        raise ValueError(f"unsupported zara_risk_mode={risk_mode!r}")
    target = entry * (1.0 + side * target_fraction)
    return stop, target, {
        "source_risk_mode": risk_mode,
        "source_stop_anchor": anchor,
        "source_stop_buffer": buffer,
        "source_stop_fraction": abs(entry - stop) / entry,
        "source_target_fraction": target_fraction,
    }


def route_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    if not feature.ready:
        return _unresolved(symbol, "FEATURE_NOT_READY")
    long_level, short_level, diagnostics = _level(bars, config)
    if not diagnostics:
        return _unresolved(
            symbol,
            "ZARA_SOURCE_WARMUP",
            int(bars[-1].ts_event) if bars else 0,
        )
    trigger = str(config.zara_trigger_mode).strip().lower()
    long_action, short_action = long_level, short_level
    if trigger == "edge":
        if len(bars) < 6:
            return _unresolved(symbol, "ZARA_EDGE_WARMUP")
        prior_long, prior_short, _ = _level(bars[:-5], config)
        long_action = long_level and not prior_long
        short_action = short_level and not prior_short
    elif trigger != "level":
        raise ValueError(f"unsupported zara_trigger_mode={trigger!r}")
    diagnostics.update(
        {
            "source_trigger_mode": trigger,
            "source_long_action": int(long_action),
            "source_short_action": int(short_action),
        }
    )
    episode_ts = int(bars[-1].ts_event) if bars else 0
    if long_action == short_action:
        return _unresolved(
            symbol,
            "ZARA_SOURCE_NO_SIGNAL" if not long_action else "ZARA_SOURCE_AMBIGUOUS",
            episode_ts,
            diagnostics,
        )
    side = 1 if long_action else -1
    candles = _aggregate_complete(bars, 5)
    entry = float(candles[-1].close)
    stop, target, geometry = _geometry(bars, side, diagnostics, config)
    valid = (
        0.0 < stop < entry < target
        if side > 0
        else 0.0 < target < entry < stop
    )
    if not valid:
        return _unresolved(
            symbol,
            "ZARA_INVALID_GEOMETRY",
            episode_ts,
            {**diagnostics, **geometry},
        )
    rsi_distance = sum(
        abs(float(diagnostics[f"rsi_{label}"]) - float(config.zara_rsi_threshold))
        for label in ("5m", "15m", "30m")
    )
    di_excess = sum(
        max(
            0.0,
            float(
                diagnostics[
                    f"{'plus_di' if side > 0 else 'minus_di'}_{label}"
                ]
            )
            - float(config.zara_di_threshold),
        )
        for label in ("5m", "15m", "30m")
    )
    band_distance_bps = sum(
        abs(
            float(diagnostics[f"close_{label}"])
            - float(diagnostics[f"bb_middle_{label}"])
        )
        / max(float(diagnostics[f"close_{label}"]), 1e-12)
        * 10_000.0
        for label in ("5m", "15m", "30m")
    )
    score = rsi_distance + di_excess + band_distance_bps
    diagnostics.update(
        {
            **geometry,
            "source_side": side,
            "source_score": score,
            "source_effective_leverage": 10.0,
            "source_stoploss_profit_ratio": 0.296,
            "source_trailing_activation_underlying": 0.0071,
            "source_trailing_distance_underlying": 0.0013,
        }
    )
    return RouteDecision(
        symbol=symbol,
        state=ZARA_STATE,
        side=side,
        score=float(score),
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=target,
        episode_ts=episode_ts,
        reasons=(
            "PUBLIC_ZARATUSTRA_V5_LONG" if side > 0 else "PUBLIC_ZARATUSTRA_V5_SHORT",
            "COMPLETED_5M_15M_30M",
            "SOURCE_TRIGGER_" + trigger.upper(),
            "RISK_MODE_" + str(config.zara_risk_mode).upper(),
            "SOURCE_LEVERAGE_NORMALIZED_TO_UNDERLYING",
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
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "ZARA_STATE",
    "_aggregate_complete",
    "_directional_indicators",
    "_geometry",
    "_level",
    "_timeframe_snapshot",
    "route_symbol",
    "route_universe",
]

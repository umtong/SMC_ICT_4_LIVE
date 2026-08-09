"""Causal adapter for the public ``ZaratustraV5`` Freqtrade strategy.

The source is a 5-minute, long/short futures strategy whose complete entry
policy is a 5m/15m/30m agreement among RSI(14), Wilder +DI/-DI(14), and the
20-period Bollinger middle band of typical price.  Candidate 55 preserves that
policy and the source's level-entry semantics, while exposing rising-edge and
side-only interpretations as explicitly declared falsification variants.

All informative candles are complete before use.  No partial 15m/30m candle,
future row, Heikin-Ashi fill, or same-bar hindsight is introduced.
"""
from __future__ import annotations

from bisect import bisect_right
import importlib.util
import math
from pathlib import Path
import sys
from typing import Mapping, Sequence

_BASE_PATH = Path(__file__).resolve().with_name("router_picasso.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_zaratustra_reused_primitives", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load reused router primitives: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

BarObservation = _BASE.BarObservation
FeatureObservation = _BASE.FeatureObservation
RouteConfig = _BASE.RouteConfig
RouteDecision = _BASE.RouteDecision
UNRESOLVED = _BASE.UNRESOLVED
_EPS = _BASE._EPS

ZARATUSTRA_STATE = "PUBLIC_ZARATUSTRA_V5_MTF_TREND"
PICASSO_STATE = ZARATUSTRA_STATE
SMA_OFFSET_STATE = ZARATUSTRA_STATE
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}
_TIMEFRAMES = (5, 15, 30)

_aggregate_complete = _BASE._aggregate_complete
_rsi = _BASE._rsi
_sma = _BASE._sma


def _decode_mode(mode: str) -> tuple[str, str]:
    """Return (trigger_mode, side_filter) for a frozen variant name."""
    normalized = str(mode).strip().lower().replace("-", "_")
    trigger = "edge" if normalized.startswith("edge_") else "level"
    if normalized.endswith("_long"):
        side_filter = "long"
    elif normalized.endswith("_short"):
        side_filter = "short"
    elif normalized.endswith("_both"):
        side_filter = "both"
    else:
        raise ValueError(f"unsupported Candidate 55 Zaratustra mode: {mode}")
    return trigger, side_filter


def _directional_indicators(
    candles: Sequence[BarObservation], period: int
) -> tuple[list[float], list[float]]:
    """Compute causal Wilder +DI and -DI with TA-Lib-compatible geometry."""
    size = len(candles)
    plus_di = [math.nan] * size
    minus_di = [math.nan] * size
    if period <= 0 or size <= period:
        return plus_di, minus_di

    true_range = [0.0] * size
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
        true_range[index] = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )
        up_move = high - previous_high
        down_move = previous_low - low
        plus_dm[index] = (
            up_move if up_move > 0.0 and up_move > down_move else 0.0
        )
        minus_dm[index] = (
            down_move if down_move > 0.0 and down_move > up_move else 0.0
        )

    smoothed_tr = sum(true_range[1 : period + 1])
    smoothed_plus = sum(plus_dm[1 : period + 1])
    smoothed_minus = sum(minus_dm[1 : period + 1])

    def assign(index: int) -> None:
        if smoothed_tr <= _EPS:
            plus_di[index] = 0.0
            minus_di[index] = 0.0
        else:
            plus_di[index] = 100.0 * smoothed_plus / smoothed_tr
            minus_di[index] = 100.0 * smoothed_minus / smoothed_tr

    assign(period)
    for index in range(period + 1, size):
        smoothed_tr = (
            smoothed_tr - smoothed_tr / period + true_range[index]
        )
        smoothed_plus = (
            smoothed_plus - smoothed_plus / period + plus_dm[index]
        )
        smoothed_minus = (
            smoothed_minus - smoothed_minus / period + minus_dm[index]
        )
        assign(index)
    return plus_di, minus_di


def _indicator_pack(
    candles: Sequence[BarObservation],
    *,
    rsi_period: int,
    di_period: int,
    bb_period: int,
) -> dict[str, Sequence[float] | Sequence[int]]:
    closes = [float(candle.close) for candle in candles]
    typical = [
        (float(candle.high) + float(candle.low) + float(candle.close)) / 3.0
        for candle in candles
    ]
    plus_di, minus_di = _directional_indicators(candles, di_period)
    return {
        "ts": [int(candle.ts_event) for candle in candles],
        "close": closes,
        "rsi": _rsi(closes, rsi_period),
        "pdi": plus_di,
        "mdi": minus_di,
        "bbm": _sma(typical, bb_period),
    }


def _index_at(pack: Mapping[str, Sequence[float] | Sequence[int]], ts: int) -> int:
    timestamps = pack["ts"]
    return bisect_right(timestamps, int(ts)) - 1


def _source_flags_at(
    packs: Mapping[int, Mapping[str, Sequence[float] | Sequence[int]]],
    evaluation_ts: int,
) -> tuple[bool, bool, dict[str, float | int]]:
    diagnostics: dict[str, float | int] = {"evaluation_ts": int(evaluation_ts)}
    long_conditions: list[bool] = []
    short_conditions: list[bool] = []
    for timeframe in _TIMEFRAMES:
        pack = packs[timeframe]
        index = _index_at(pack, evaluation_ts)
        if index < 0:
            return False, False, {
                **diagnostics,
                "missing_timeframe": timeframe,
            }
        rsi = float(pack["rsi"][index])
        pdi = float(pack["pdi"][index])
        mdi = float(pack["mdi"][index])
        close = float(pack["close"][index])
        bbm = float(pack["bbm"][index])
        diagnostics.update(
            {
                f"ts_{timeframe}m": int(pack["ts"][index]),
                f"rsi_{timeframe}m": rsi,
                f"pdi_{timeframe}m": pdi,
                f"mdi_{timeframe}m": mdi,
                f"close_{timeframe}m": close,
                f"bbm_{timeframe}m": bbm,
            }
        )
        if not all(math.isfinite(value) for value in (rsi, pdi, mdi, close, bbm)):
            diagnostics["not_ready_timeframe"] = timeframe
            return False, False, diagnostics
        long_conditions.extend((rsi > 50.0, pdi > 25.0, close > bbm))
        short_conditions.extend((rsi < 50.0, mdi > 25.0, close < bbm))
    return all(long_conditions), all(short_conditions), diagnostics


def source_entry_flags(
    *,
    rsi_5m: float,
    rsi_15m: float,
    rsi_30m: float,
    pdi_5m: float,
    pdi_15m: float,
    pdi_30m: float,
    mdi_5m: float,
    mdi_15m: float,
    mdi_30m: float,
    close_5m: float,
    close_15m: float,
    close_30m: float,
    bbm_5m: float,
    bbm_15m: float,
    bbm_30m: float,
) -> tuple[bool, bool]:
    """Small pure contract exposing the source's exact entry inequalities."""
    values = (
        rsi_5m,
        rsi_15m,
        rsi_30m,
        pdi_5m,
        pdi_15m,
        pdi_30m,
        mdi_5m,
        mdi_15m,
        mdi_30m,
        close_5m,
        close_15m,
        close_30m,
        bbm_5m,
        bbm_15m,
        bbm_30m,
    )
    if not all(math.isfinite(float(value)) for value in values):
        return False, False
    long_signal = (
        rsi_30m > 50.0
        and rsi_15m > 50.0
        and rsi_5m > 50.0
        and pdi_30m > 25.0
        and pdi_15m > 25.0
        and pdi_5m > 25.0
        and close_30m > bbm_30m
        and close_15m > bbm_15m
        and close_5m > bbm_5m
    )
    short_signal = (
        rsi_30m < 50.0
        and rsi_15m < 50.0
        and rsi_5m < 50.0
        and mdi_30m > 25.0
        and mdi_15m > 25.0
        and mdi_5m > 25.0
        and close_30m < bbm_30m
        and close_15m < bbm_15m
        and close_5m < bbm_5m
    )
    return bool(long_signal), bool(short_signal)


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

    trigger_mode, side_filter = _decode_mode(config.picasso_precedence_mode)
    rsi_period = int(config.picasso_rsi_long_period)
    di_period = int(config.picasso_adx_period)
    bb_period = int(config.picasso_bb_long_period)
    candles_by_timeframe = {
        timeframe: _aggregate_complete(bars, timeframe)
        for timeframe in _TIMEFRAMES
    }
    minimum = max(rsi_period + 2, di_period + 2, bb_period + 1)
    for timeframe, candles in candles_by_timeframe.items():
        if len(candles) < minimum:
            return _unresolved(
                symbol,
                "ZARATUSTRA_HISTORY_NOT_READY",
                latest_ts,
                {
                    "timeframe_minutes": timeframe,
                    "candles": len(candles),
                    "minimum": minimum,
                },
            )

    packs = {
        timeframe: _indicator_pack(
            candles,
            rsi_period=rsi_period,
            di_period=di_period,
            bb_period=bb_period,
        )
        for timeframe, candles in candles_by_timeframe.items()
    }
    base_candles = candles_by_timeframe[5]
    current_ts = int(base_candles[-1].ts_event)
    previous_ts = int(base_candles[-2].ts_event)
    current_long, current_short, diagnostics = _source_flags_at(
        packs, current_ts
    )
    previous_long, previous_short, _ = _source_flags_at(packs, previous_ts)

    if side_filter == "long":
        current_short = False
        previous_short = False
    elif side_filter == "short":
        current_long = False
        previous_long = False

    long_edge = bool(current_long and not previous_long)
    short_edge = bool(current_short and not previous_short)
    if trigger_mode == "edge":
        long_action, short_action = long_edge, short_edge
    else:
        long_action, short_action = bool(current_long), bool(current_short)

    diagnostics.update(
        {
            "candidate55_declared_mode": str(config.picasso_precedence_mode),
            "source_trigger_mode": trigger_mode,
            "source_side_filter": side_filter,
            "current_long_level": int(current_long),
            "current_short_level": int(current_short),
            "previous_long_level": int(previous_long),
            "previous_short_level": int(previous_short),
            "long_rising_edge": int(long_edge),
            "short_rising_edge": int(short_edge),
            "long_action": int(long_action),
            "short_action": int(short_action),
            "complete_informative_candles_only": 1,
        }
    )
    if long_action == short_action:
        reason = (
            "ZARATUSTRA_NO_SOURCE_EDGE"
            if trigger_mode == "edge" and not long_action
            else "ZARATUSTRA_NO_SOURCE_LEVEL"
            if not long_action
            else "ZARATUSTRA_AMBIGUOUS_SOURCE_SIGNAL"
        )
        return _unresolved(symbol, reason, current_ts, diagnostics)

    side = 1 if long_action else -1
    entry = float(base_candles[-1].close)
    leverage = max(float(config.picasso_source_effective_leverage), _EPS)
    stop_fraction = float(config.picasso_source_stoploss) / leverage
    objective_fraction = float(config.picasso_emergency_target_fraction)
    stop = entry * (1.0 - side * stop_fraction)
    objective = entry * (1.0 + side * objective_fraction)

    rsi_margins = [
        side * (float(diagnostics[f"rsi_{timeframe}m"]) - 50.0)
        for timeframe in _TIMEFRAMES
    ]
    di_margins = [
        (
            float(diagnostics[f"pdi_{timeframe}m"])
            if side > 0
            else float(diagnostics[f"mdi_{timeframe}m"])
        )
        - 25.0
        for timeframe in _TIMEFRAMES
    ]
    band_margins_bps = [
        side
        * (
            float(diagnostics[f"close_{timeframe}m"])
            - float(diagnostics[f"bbm_{timeframe}m"])
        )
        / max(float(diagnostics[f"close_{timeframe}m"]), _EPS)
        * 10_000.0
        for timeframe in _TIMEFRAMES
    ]
    score = (
        1.0
        + min(5.0, max(0.0, min(rsi_margins)) / 2.0)
        + min(5.0, max(0.0, min(di_margins)) / 2.0)
        + min(5.0, max(0.0, min(band_margins_bps)) / 10.0)
    )
    diagnostics.update(
        {
            "source_tag": "Bullish trend" if side > 0 else "Bearish trend",
            "source_effective_leverage": leverage,
            "source_stoploss_profit_ratio": float(
                config.picasso_source_stoploss
            ),
            "underlying_stop_fraction": stop_fraction,
            "source_trailing_positive": float(
                config.picasso_trailing_positive
            ),
            "source_trailing_offset": float(
                config.picasso_trailing_offset
            ),
            "minimum_rsi_margin": min(rsi_margins),
            "minimum_di_margin": min(di_margins),
            "minimum_band_margin_bps": min(band_margins_bps),
            "source_level_reentry_preserved": int(trigger_mode == "level"),
        }
    )
    return RouteDecision(
        symbol=symbol,
        state=ZARATUSTRA_STATE,
        side=side,
        score=float(score),
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=objective,
        episode_ts=current_ts,
        reasons=(
            "PUBLIC_ZARATUSTRA_V5_MTF_ENTRY",
            "COMPLETE_5M_15M_30M_CANDLES",
            "SOURCE_TRIGGER_MODE_" + trigger_mode.upper(),
            "SOURCE_SIDE_FILTER_" + side_filter.upper(),
            "SOURCE_RISK_NORMALIZED_BY_10X_LEVERAGE",
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
                FeatureObservation(
                    bars[-1].ts_event if bars else 0,
                    ready=True,
                ),
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
    "PICASSO_STATE",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "ZARATUSTRA_STATE",
    "_aggregate_complete",
    "_decode_mode",
    "_directional_indicators",
    "_indicator_pack",
    "_rsi",
    "_sma",
    "classify_symbol",
    "route_universe",
    "source_entry_flags",
]

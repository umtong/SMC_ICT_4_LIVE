"""Causal Candidate 55 adapter for ``myshortingstrategiembe2``.

The public Freqtrade source reports 127k trades over 2021-2025, but its fixed
backtest overwrites executable OHLC columns with Heikin-Ashi values.  That
creates prices which never traded.  Candidate 55 deliberately does *not*
reproduce that invalid fill path.  It preserves the live-computable entry
policy and source management geometry while all fills, stops and targets use
real Binance bars in NautilusTrader.

The source's actually active v3 entries are symmetrical RSI/TEMA/Bollinger
crosses.  Its Ichimoku ``buy`` and trend ``sell`` assignments are dead v2
columns and are therefore retained only as provenance, not fabricated into the
active policy.
"""
from __future__ import annotations

from dataclasses import replace
import importlib.util
import math
from pathlib import Path
import sys
from typing import Mapping, Sequence

_BASE_PATH = Path(__file__).resolve().with_name("router_picasso.py")
_SPEC = importlib.util.spec_from_file_location("candidate55_mbe2_reused_math", _BASE_PATH)
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

MBE2_STATE = "PUBLIC_MBE2_RSI_TEMA_BB_REVERSAL"
PICASSO_STATE = MBE2_STATE
SMA_OFFSET_STATE = MBE2_STATE
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}

# Approximation of the source callback's output for Binance futures.  The
# callback collapses to min(10, max_leverage / 12) when proposed leverage is 1
# because ``atr / atr.max()`` is one for its scalar ATR.  These values affect
# only conversion of Freqtrade profit-ratio stops/ROI to underlying price
# geometry; project dollar risk remains exactly current NAV x 3%.
_SOURCE_PAIR_LEVERAGE = {
    "BTCUSDT": 10.0,
    "ETHUSDT": 8.333333333333334,
    "SOLUSDT": 6.25,
    "XRPUSDT": 4.166666666666667,
}

_aggregate_complete = _BASE._aggregate_complete
_ema = _BASE._ema
_ema_nan = _BASE._ema_nan
_rsi = _BASE._rsi
_sma = _BASE._sma


def _tema(values: Sequence[float], period: int) -> list[float]:
    """TA-Lib compatible triple exponential moving average construction."""
    first = _ema(values, period)
    second = _ema_nan(first, period)
    third = _ema_nan(second, period)
    output = [math.nan] * len(values)
    for index in range(len(values)):
        if all(math.isfinite(float(series[index])) for series in (first, second, third)):
            output[index] = 3.0 * first[index] - 3.0 * second[index] + third[index]
    return output


def _decode_mode(mode: str) -> tuple[str, str]:
    normalized = str(mode).strip().lower().replace("-", "_")
    leverage_mode = "pair" if normalized.startswith("pair_") else "average"
    if normalized.endswith("_short"):
        side_filter = "short"
    elif normalized.endswith("_long"):
        side_filter = "long"
    elif normalized.endswith("_both"):
        side_filter = "both"
    else:
        raise ValueError(f"unsupported Candidate 55 MBE2 mode: {mode}")
    return leverage_mode, side_filter


def _effective_leverage(symbol: str, config: RouteConfig, mode: str) -> float:
    leverage_mode, _ = _decode_mode(mode)
    if leverage_mode == "pair":
        return float(_SOURCE_PAIR_LEVERAGE.get(symbol, config.picasso_source_effective_leverage))
    return max(float(config.picasso_source_effective_leverage), 1.0)


def _unresolved(symbol: str, reason: str, episode_ts: int = 0,
                diagnostics: Mapping[str, float | int | str] | None = None) -> RouteDecision:
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


def source_entry_flags(
    *,
    previous_rsi: float,
    current_rsi: float,
    previous_tema: float,
    current_tema: float,
    bb_middle: float,
    volume: float,
) -> tuple[bool, bool]:
    """Return the source's active v3 long/short entry flags."""
    finite = all(
        math.isfinite(float(value))
        for value in (previous_rsi, current_rsi, previous_tema, current_tema, bb_middle, volume)
    )
    if not finite or volume <= 0.0:
        return False, False
    long_signal = (
        previous_rsi <= 30.0
        and current_rsi > 30.0
        and current_tema <= bb_middle
        and current_tema > previous_tema
    )
    short_signal = (
        previous_rsi >= 70.0
        and current_rsi < 70.0
        and current_tema > bb_middle
        and current_tema < previous_tema
    )
    return bool(long_signal), bool(short_signal)


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

    mode = str(config.picasso_precedence_mode)
    _, side_filter = _decode_mode(mode)
    bucket = int(config.picasso_bucket_minutes)
    candles = _aggregate_complete(bars, bucket)
    source_startup = 140
    if len(candles) < source_startup:
        return _unresolved(
            symbol,
            "MBE2_SOURCE_HISTORY_NOT_READY",
            latest_ts,
            {"candles": len(candles), "minimum": source_startup},
        )

    closes = [float(candle.close) for candle in candles]
    rsi_period = int(config.picasso_rsi_long_period)
    tema_period = int(config.picasso_bb_short_period)
    bb_period = int(config.picasso_bb_long_period)
    rsi = _rsi(closes, rsi_period)
    tema = _tema(closes, tema_period)
    bb_middle = _sma(closes, bb_period)
    index = len(candles) - 1
    previous = index - 1
    long_signal, short_signal = source_entry_flags(
        previous_rsi=rsi[previous],
        current_rsi=rsi[index],
        previous_tema=tema[previous],
        current_tema=tema[index],
        bb_middle=bb_middle[index],
        volume=float(candles[index].volume),
    )
    if side_filter == "short":
        long_signal = False
    elif side_filter == "long":
        short_signal = False

    diagnostics: dict[str, float | int | str] = {
        "candidate55_declared_mode": mode,
        "source_side_filter": side_filter,
        "source_timeframe_minutes": bucket,
        "source_startup_candles": source_startup,
        "previous_rsi": float(rsi[previous]),
        "current_rsi": float(rsi[index]),
        "previous_tema": float(tema[previous]),
        "current_tema": float(tema[index]),
        "bb_middle": float(bb_middle[index]),
        "volume": float(candles[index].volume),
        "long_signal": int(long_signal),
        "short_signal": int(short_signal),
        "real_ohlc_execution": 1,
        "heikin_ashi_ohlc_overwrite_rejected": 1,
        "dead_v2_buy_sell_columns_ignored": 1,
    }
    if long_signal == short_signal:
        reason = "MBE2_NO_ACTIVE_SOURCE_ENTRY" if not long_signal else "MBE2_AMBIGUOUS_ENTRY"
        return _unresolved(symbol, reason, int(candles[index].ts_event), diagnostics)

    side = 1 if long_signal else -1
    entry = float(candles[index].close)
    leverage = _effective_leverage(symbol, config, mode)
    stop_fraction = float(config.picasso_source_stoploss) / leverage
    emergency_target_fraction = float(config.picasso_emergency_target_fraction)
    stop = entry * (1.0 - side * stop_fraction)
    objective = entry * (1.0 + side * emergency_target_fraction)

    rsi_impulse = (
        max(0.0, float(rsi[index]) - 30.0)
        if side > 0
        else max(0.0, 70.0 - float(rsi[index]))
    )
    tema_slope_bps = side * (float(tema[index]) - float(tema[previous])) / entry * 10_000.0
    bb_room_bps = side * (float(bb_middle[index]) - float(tema[index])) / entry * 10_000.0
    score = 1.0 + min(5.0, rsi_impulse) + min(5.0, max(0.0, tema_slope_bps)) + min(
        5.0, max(0.0, bb_room_bps)
    )
    diagnostics.update(
        {
            "source_tag": "rsi_cross",
            "source_effective_leverage": leverage,
            "source_stoploss_profit_ratio": float(config.picasso_source_stoploss),
            "underlying_stop_fraction": stop_fraction,
            "source_trailing_positive": float(config.picasso_trailing_positive),
            "source_trailing_offset": float(config.picasso_trailing_offset),
            "rsi_impulse": rsi_impulse,
            "tema_slope_bps": tema_slope_bps,
            "bb_room_bps": bb_room_bps,
        }
    )
    return RouteDecision(
        symbol=symbol,
        state=MBE2_STATE,
        side=side,
        score=float(score),
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=objective,
        episode_ts=int(candles[index].ts_event),
        reasons=(
            "PUBLIC_MBE2_ACTIVE_V3_RSI_CROSS",
            "REAL_BINANCE_OHLC_EXECUTION",
            "SOURCE_PROFIT_RATIOS_NORMALIZED_BY_EFFECTIVE_LEVERAGE",
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
                FeatureObservation(bars[-1].ts_event if bars else 0, ready=True),
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
    "MBE2_STATE",
    "PICASSO_STATE",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "_aggregate_complete",
    "_decode_mode",
    "_ema",
    "_effective_leverage",
    "_rsi",
    "_sma",
    "_tema",
    "classify_symbol",
    "route_universe",
    "source_entry_flags",
]

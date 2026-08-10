"""Causal adapter for the public 15-minute ``TheForce`` strategy.

The public source is a long-only Freqtrade strategy. Candidate 57 preserves its
completed-candle entry inequalities exactly, including the unusual
``MACD(..., signalperiod=1)`` and ``STOCHF(..., fastd_matype=3)`` choices.
No symmetric short rule or post-outcome filter is added in this source control.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
from typing import Mapping, Sequence

_BASE_PATH = Path(__file__).resolve().with_name("router_picasso.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate57_force_reused_primitives", _BASE_PATH
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
_aggregate_complete = _BASE._aggregate_complete
_ema = _BASE._ema
_ema_nan = _BASE._ema_nan

FORCE_STATE = "PUBLIC_THEFORCE_15M_MOMENTUM"
PICASSO_STATE = FORCE_STATE
SMA_OFFSET_STATE = FORCE_STATE
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}


def _dema_nan(values: Sequence[float], period: int) -> list[float]:
    first = _ema_nan(values, period)
    second = _ema_nan(first, period)
    output = [math.nan] * len(values)
    for index, (one, two) in enumerate(zip(first, second, strict=True)):
        if math.isfinite(float(one)) and math.isfinite(float(two)):
            output[index] = 2.0 * float(one) - float(two)
    return output


def _stoch_fast(
    candles: Sequence[BarObservation],
    fastk_period: int = 5,
    fastd_period: int = 3,
) -> tuple[list[float], list[float]]:
    fastk = [math.nan] * len(candles)
    if fastk_period <= 0:
        return fastk, [math.nan] * len(candles)
    for index in range(fastk_period - 1, len(candles)):
        window = candles[index - fastk_period + 1 : index + 1]
        low = min(float(item.low) for item in window)
        high = max(float(item.high) for item in window)
        close = float(candles[index].close)
        fastk[index] = (
            0.0 if high - low <= _EPS else 100.0 * (close - low) / (high - low)
        )
    # TA-Lib MA_Type 3 is DEMA. The source passes STOCHF(5, 3, 3).
    fastd = _dema_nan(fastk, fastd_period)
    return fastk, fastd


def _macd_signal_one(
    closes: Sequence[float],
    fast_period: int = 12,
    slow_period: int = 26,
) -> tuple[list[float], list[float]]:
    fast = _ema(closes, fast_period)
    slow = _ema(closes, slow_period)
    macd = [math.nan] * len(closes)
    for index, (left, right) in enumerate(zip(fast, slow, strict=True)):
        if math.isfinite(float(left)) and math.isfinite(float(right)):
            macd[index] = float(left) - float(right)
    # EMA period one equals its input, matching signalperiod=1.
    signal = _ema_nan(macd, 1)
    return macd, signal


def source_flags_for_candles(
    candles: Sequence[BarObservation],
) -> tuple[bool, bool, dict[str, float | int | str]]:
    minimum = 30
    if len(candles) < minimum:
        return False, False, {
            "reason": "FORCE_HISTORY_NOT_READY",
            "candles": len(candles),
            "minimum": minimum,
        }
    closes = [float(item.close) for item in candles]
    opens = [float(item.open) for item in candles]
    fastk, fastd = _stoch_fast(candles, 5, 3)
    macd, signal = _macd_signal_one(closes, 12, 26)
    ema_close = _ema(closes, 5)
    ema_open = _ema(opens, 5)
    index = len(candles) - 1
    previous = index - 1
    values = (
        fastk[index],
        fastd[index],
        macd[index],
        macd[previous],
        signal[index],
        signal[previous],
        ema_close[index],
        ema_open[index],
        closes[index],
        closes[previous],
    )
    if not all(math.isfinite(float(value)) for value in values):
        return False, False, {"reason": "FORCE_INDICATORS_NOT_READY"}

    entry = bool(
        20.0 <= float(fastk[index]) <= 80.0
        and 20.0 <= float(fastd[index]) <= 80.0
        and float(macd[index]) > float(macd[previous])
        and float(signal[index]) > float(signal[previous])
        and closes[index] > closes[previous]
        and float(ema_close[index]) >= float(ema_open[index])
    )
    exit_signal = bool(
        float(fastk[index]) <= 80.0
        and float(fastd[index]) <= 80.0
        and float(macd[index]) < float(macd[previous])
        and float(signal[index]) < float(signal[previous])
        and float(ema_close[index]) < float(ema_open[index])
    )
    diagnostics: dict[str, float | int | str] = {
        "fastk": float(fastk[index]),
        "fastd": float(fastd[index]),
        "macd": float(macd[index]),
        "previous_macd": float(macd[previous]),
        "macd_signal": float(signal[index]),
        "previous_macd_signal": float(signal[previous]),
        "ema5_close": float(ema_close[index]),
        "ema5_open": float(ema_open[index]),
        "close": closes[index],
        "previous_close": closes[previous],
        "entry_flag": int(entry),
        "exit_flag": int(exit_signal),
        "stoch_fastk_period": 5,
        "stoch_fastd_period": 3,
        "stoch_fastd_matype": 3,
        "macd_fast_period": 12,
        "macd_slow_period": 26,
        "macd_signal_period": 1,
        "source_timeframe_minutes": 15,
        "complete_15m_candles_only": 1,
    }
    return entry, exit_signal, diagnostics


def source_flags_for_bars(
    bars: Sequence[BarObservation],
) -> tuple[bool, bool, dict[str, float | int | str]]:
    return source_flags_for_candles(_aggregate_complete(bars, 15))


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
    if str(config.picasso_precedence_mode) != "source_long":
        return _unresolved(
            symbol,
            "FORCE_UNFROZEN_MODE_REJECTED",
            latest_ts,
            {"mode": str(config.picasso_precedence_mode)},
        )
    candles = _aggregate_complete(bars, 15)
    entry_flag, _, diagnostics = source_flags_for_candles(candles)
    episode_ts = int(candles[-1].ts_event) if candles else latest_ts
    reason = str(diagnostics.get("reason", ""))
    if reason:
        return _unresolved(symbol, reason, episode_ts, diagnostics)
    if not entry_flag:
        return _unresolved(symbol, "FORCE_NO_SOURCE_ENTRY", episode_ts, diagnostics)

    entry = float(diagnostics["close"])
    stop_fraction = 0.015
    target_fraction = 0.012
    stop = entry * (1.0 - stop_fraction)
    objective = entry * (1.0 + target_fraction)
    close_return_bps = (
        entry / float(diagnostics["previous_close"]) - 1.0
    ) * 10_000.0
    macd_slope_bps = (
        float(diagnostics["macd"]) - float(diagnostics["previous_macd"])
    ) / max(entry, _EPS) * 10_000.0
    ema_gap_bps = (
        float(diagnostics["ema5_close"]) - float(diagnostics["ema5_open"])
    ) / max(entry, _EPS) * 10_000.0
    score = (
        1.0
        + max(0.0, close_return_bps)
        + max(0.0, macd_slope_bps)
        + max(0.0, ema_gap_bps)
    )
    diagnostics.update(
        {
            "source_side": "long",
            "source_stop_fraction": stop_fraction,
            "source_roi_0": 0.012,
            "source_roi_15": 0.010,
            "source_roi_30": 0.005,
            "close_return_bps": close_return_bps,
            "macd_slope_bps": macd_slope_bps,
            "ema_gap_bps": ema_gap_bps,
            "cross_asset_arbitration_score": score,
        }
    )
    return RouteDecision(
        symbol=symbol,
        state=FORCE_STATE,
        side=1,
        score=float(score),
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=objective,
        episode_ts=episode_ts,
        reasons=(
            "PUBLIC_THEFORCE_SOURCE_LONG_ENTRY",
            "COMPLETE_15M_CANDLE",
            "SOURCE_FIXED_STOP_AND_ROI_LADDER",
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
                    bars[-1].ts_event if bars else 0, ready=True
                ),
            ),
            config,
        )
        for symbol, bars in bars_by_symbol.items()
    }
    actionable = [item for item in decisions.values() if item.actionable]
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
    "FORCE_STATE",
    "FeatureObservation",
    "PICASSO_STATE",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "classify_symbol",
    "route_universe",
    "source_flags_for_bars",
    "source_flags_for_candles",
]

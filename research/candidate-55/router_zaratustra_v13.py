"""Causal adapter for the public ``ZaratustraV13`` futures strategy.

The source combines level-triggered 5m directional-index conditions with
Bollinger-band breakout edges, then relies exclusively on a stop and trailing
stop.  Candidate 55 preserves those asymmetric inequalities (including the
unusual ``DX > MDI`` clause on both sides) and exposes DI-only and side-only
variants for cheap causal attribution.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
from typing import Mapping, Sequence

_REUSED_PATH = Path(__file__).resolve().with_name("router_zaratustra.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_zaratustra_v13_reused_indicators", _REUSED_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load reused Zaratustra indicators: {_REUSED_PATH}")
_REUSED = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _REUSED
_SPEC.loader.exec_module(_REUSED)

BarObservation = _REUSED.BarObservation
FeatureObservation = _REUSED.FeatureObservation
RouteConfig = _REUSED.RouteConfig
RouteDecision = _REUSED.RouteDecision
UNRESOLVED = _REUSED.UNRESOLVED
_EPS = _REUSED._EPS

ZARATUSTRA_STATE = "PUBLIC_ZARATUSTRA_V13_DI_OR_BB"
PICASSO_STATE = ZARATUSTRA_STATE
SMA_OFFSET_STATE = ZARATUSTRA_STATE
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}

_aggregate_complete = _REUSED._aggregate_complete
_directional_indicators = _REUSED._directional_indicators
_rsi = _REUSED._rsi
_sma = _REUSED._sma
_BASE = _REUSED._BASE


def _decode_mode(mode: str) -> tuple[str, str]:
    """Return (component_policy, side_filter)."""
    normalized = str(mode).strip().lower().replace("-", "_")
    if normalized.startswith("di_"):
        component = "di"
    elif normalized.startswith("bb_"):
        component = "bb"
    elif normalized.startswith("source_"):
        component = "source"
    else:
        raise ValueError(f"unsupported Candidate 55 ZaratustraV13 mode: {mode}")
    if normalized.endswith("_long"):
        side_filter = "long"
    elif normalized.endswith("_short"):
        side_filter = "short"
    elif normalized.endswith("_both"):
        side_filter = "both"
    else:
        raise ValueError(f"unsupported Candidate 55 ZaratustraV13 side: {mode}")
    return component, side_filter


def _adx_dx(
    plus_di: Sequence[float], minus_di: Sequence[float], period: int
) -> tuple[list[float], list[float]]:
    """Derive causal TA-Lib-style DX and Wilder ADX from +DI/-DI."""
    size = min(len(plus_di), len(minus_di))
    dx = [math.nan] * size
    adx = [math.nan] * size
    if period <= 0 or size <= 2 * period - 1:
        return dx, adx
    for index in range(period, size):
        pdi = float(plus_di[index])
        mdi = float(minus_di[index])
        if not (math.isfinite(pdi) and math.isfinite(mdi)):
            continue
        denominator = pdi + mdi
        dx[index] = (
            0.0
            if denominator <= _EPS
            else 100.0 * abs(pdi - mdi) / denominator
        )

    seed_index = 2 * period - 1
    seed = [float(dx[index]) for index in range(period, seed_index + 1)]
    if len(seed) != period or not all(math.isfinite(value) for value in seed):
        return dx, adx
    adx[seed_index] = sum(seed) / period
    for index in range(seed_index + 1, size):
        value = float(dx[index])
        previous = float(adx[index - 1])
        if math.isfinite(value) and math.isfinite(previous):
            adx[index] = ((period - 1.0) * previous + value) / period
    return dx, adx


def _bollinger(
    candles: Sequence[BarObservation], period: int
) -> tuple[list[float], list[float], list[float]]:
    typical = [
        (float(candle.high) + float(candle.low) + float(candle.close)) / 3.0
        for candle in candles
    ]
    middle = _sma(typical, period)
    std = _BASE._rolling_std(typical, period)
    lower = [math.nan] * len(candles)
    upper = [math.nan] * len(candles)
    for index in range(len(candles)):
        if math.isfinite(float(middle[index])) and math.isfinite(float(std[index])):
            lower[index] = float(middle[index]) - 2.0 * float(std[index])
            upper[index] = float(middle[index]) + 2.0 * float(std[index])
    return lower, middle, upper


def source_entry_flags(
    *,
    previous_close: float,
    current_close: float,
    previous_lower: float,
    current_lower: float,
    previous_upper: float,
    current_upper: float,
    dx: float,
    adx: float,
    pdi: float,
    mdi: float,
) -> dict[str, bool]:
    """Expose all four source assignments before OR-combination."""
    values = (
        previous_close,
        current_close,
        previous_lower,
        current_lower,
        previous_upper,
        current_upper,
        dx,
        adx,
        pdi,
        mdi,
    )
    if not all(math.isfinite(float(value)) for value in values):
        return {
            "long_di": False,
            "long_bb": False,
            "short_di": False,
            "short_bb": False,
        }
    return {
        "long_di": bool(dx > mdi and adx > mdi and pdi > mdi),
        "long_bb": bool(
            previous_close <= previous_upper and current_close > current_upper
        ),
        "short_di": bool(dx > mdi and adx > pdi and mdi > pdi),
        "short_bb": bool(
            previous_close >= previous_lower and current_close < current_lower
        ),
    }


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

    component, side_filter = _decode_mode(config.picasso_precedence_mode)
    period = int(config.picasso_adx_period)
    bb_period = int(config.picasso_bb_long_period)
    candles = _aggregate_complete(bars, 5)
    minimum = max(2 * period + 2, bb_period + 2)
    if len(candles) < minimum:
        return _unresolved(
            symbol,
            "ZARATUSTRA_V13_HISTORY_NOT_READY",
            latest_ts,
            {"candles": len(candles), "minimum": minimum},
        )

    plus_di, minus_di = _directional_indicators(candles, period)
    dx, adx = _adx_dx(plus_di, minus_di, period)
    lower, middle, upper = _bollinger(candles, bb_period)
    index = len(candles) - 1
    previous = index - 1
    flags = source_entry_flags(
        previous_close=float(candles[previous].close),
        current_close=float(candles[index].close),
        previous_lower=float(lower[previous]),
        current_lower=float(lower[index]),
        previous_upper=float(upper[previous]),
        current_upper=float(upper[index]),
        dx=float(dx[index]),
        adx=float(adx[index]),
        pdi=float(plus_di[index]),
        mdi=float(minus_di[index]),
    )
    if component == "di":
        long_action = flags["long_di"]
        short_action = flags["short_di"]
    elif component == "bb":
        long_action = flags["long_bb"]
        short_action = flags["short_bb"]
    else:
        long_action = flags["long_di"] or flags["long_bb"]
        short_action = flags["short_di"] or flags["short_bb"]

    if side_filter == "long":
        short_action = False
    elif side_filter == "short":
        long_action = False

    diagnostics: dict[str, float | int | str] = {
        "candidate55_declared_mode": str(config.picasso_precedence_mode),
        "source_component_policy": component,
        "source_side_filter": side_filter,
        "previous_close": float(candles[previous].close),
        "current_close": float(candles[index].close),
        "previous_lower": float(lower[previous]),
        "current_lower": float(lower[index]),
        "previous_upper": float(upper[previous]),
        "current_upper": float(upper[index]),
        "bb_middle": float(middle[index]),
        "dx": float(dx[index]),
        "adx": float(adx[index]),
        "pdi": float(plus_di[index]),
        "mdi": float(minus_di[index]),
        **{name: int(value) for name, value in flags.items()},
        "long_action": int(long_action),
        "short_action": int(short_action),
        "source_asymmetric_dx_clause_preserved": 1,
        "complete_5m_candles_only": 1,
    }
    if long_action == short_action:
        reason = (
            "ZARATUSTRA_V13_NO_SOURCE_ENTRY"
            if not long_action
            else "ZARATUSTRA_V13_AMBIGUOUS_DUAL_SIDE_ENTRY"
        )
        return _unresolved(
            symbol, reason, int(candles[index].ts_event), diagnostics
        )

    side = 1 if long_action else -1
    entry = float(candles[index].close)
    leverage = max(float(config.picasso_source_effective_leverage), _EPS)
    stop_fraction = float(config.picasso_source_stoploss) / leverage
    objective_fraction = float(config.picasso_emergency_target_fraction)
    stop = entry * (1.0 - side * stop_fraction)
    objective = entry * (1.0 + side * objective_fraction)

    directional_margin = (
        min(
            float(dx[index]) - float(minus_di[index]),
            float(adx[index]) - float(minus_di[index]),
            float(plus_di[index]) - float(minus_di[index]),
        )
        if side > 0
        else min(
            float(dx[index]) - float(minus_di[index]),
            float(adx[index]) - float(plus_di[index]),
            float(minus_di[index]) - float(plus_di[index]),
        )
    )
    breakout_bps = (
        (entry - float(upper[index])) / entry * 10_000.0
        if side > 0
        else (float(lower[index]) - entry) / entry * 10_000.0
    )
    used_di = flags["long_di"] if side > 0 else flags["short_di"]
    used_bb = flags["long_bb"] if side > 0 else flags["short_bb"]
    score = (
        1.0
        + (2.0 if used_di else 0.0)
        + (2.0 if used_bb else 0.0)
        + min(6.0, max(0.0, directional_margin) / 2.0)
        + min(4.0, max(0.0, breakout_bps) / 10.0)
    )
    diagnostics.update(
        {
            "source_tag": (
                "Long Bollinger enter"
                if side > 0 and used_bb
                else "Long DI enter"
                if side > 0
                else "Short Bollinger enter"
                if used_bb
                else "Short DI enter"
            ),
            "used_di_component": int(used_di),
            "used_bb_component": int(used_bb),
            "directional_margin": directional_margin,
            "breakout_bps": breakout_bps,
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
        episode_ts=int(candles[index].ts_event),
        reasons=(
            "PUBLIC_ZARATUSTRA_V13_ENTRY",
            "SOURCE_COMPONENT_POLICY_" + component.upper(),
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
                    bars[-1].ts_event if bars else 0, ready=True
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
    "_adx_dx",
    "_aggregate_complete",
    "_bollinger",
    "_decode_mode",
    "_directional_indicators",
    "classify_symbol",
    "route_universe",
    "source_entry_flags",
]

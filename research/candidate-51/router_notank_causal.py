"""Causal extraction from the public NOTankAi_15 extrema system.

External source:
``TheoBrigitte/freqtrade`` commit
``b9feaaa2f845aed5612b3c7726a0590ee233c846``,
``strategies/notankai/NOTankAi_15.py``.

The public backtest labels extrema with ``scipy.signal.argrelextrema(order=5)``
at the pivot bar.  That label requires five future 15-minute candles and is not
available at the source entry/exit timestamp.  This adapter does not silently
copy the leak.  It exposes separately:

* ``confirmed_pivot``: at candle t, confirm that candle t-5 was the strict
  extremum of t-10..t and enter only now;
* ``confirmed_reclaim``: the same confirmed pivot, but require a causal reclaim
  and remaining reward space;
* ``rolling_reclaim``: trade a current rolling extreme rejection without a
  future-confirmed pivot.

The original RSI and DI state at the pivot are retained.  Structural
invalidation is the observed pivot/rejection extreme plus an ATR buffer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Sequence

import router_picasso as _ta

BarObservation = _ta.BarObservation
FeatureObservation = _ta.FeatureObservation
UNRESOLVED = "UNRESOLVED"
NOTANK_STATE = "PUBLIC_NOTANK_CAUSAL_EXTREMA"
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}


@dataclass(frozen=True, slots=True)
class RouteConfig:
    atr_period: int = 14
    min_impulse_atr_continuation: float = 0.75
    min_impulse_atr_reversal: float = 1.05
    min_response_atr: float = 0.12
    min_participation_ratio: float = 1.05
    min_route_score: float = 3.10
    ambiguity_score_gap: float = 0.20
    continuation_target_r: float = 2.20
    reversal_target_r: float = 1.80

    notank_bucket_minutes: int = 15
    notank_entry_mode: str = "confirmed_pivot"
    notank_direction_mode: str = "long_only"
    notank_pivot_order: int = 5
    notank_rsi_period: int = 14
    notank_long_rsi_max: float = 30.0
    notank_short_rsi_min: float = 70.0
    notank_stop_atr_buffer: float = 0.25
    notank_max_confirmation_atr: float = 2.5
    notank_min_reclaim_fraction: float = 0.0
    notank_target_r: float = 2.0
    notank_rolling_window: int = 11
    notank_min_wick_fraction: float = 0.25


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


def _strict_extremum(values: Sequence[float], index: int, order: int, side: int) -> bool:
    if index - order < 0 or index + order >= len(values):
        return False
    center = float(values[index])
    peers = [
        float(values[position])
        for position in range(index - order, index + order + 1)
        if position != index
    ]
    return center < min(peers) if side > 0 else center > max(peers)


def _di_values(candles: Sequence[BarObservation], period: int) -> list[float]:
    plus = _ta._plus_di(candles, period)
    minus = _ta._minus_di(candles, period)
    return [
        float(a) - float(b) if _finite(a) and _finite(b) else math.nan
        for a, b in zip(plus, minus)
    ]


def inspect_state(
    bars: Sequence[BarObservation],
    config: RouteConfig = RouteConfig(),
) -> dict[str, float | int | str]:
    if not bars:
        return {"ready": 0}
    candles = _ta._aggregate_complete(bars, int(config.notank_bucket_minutes))
    order = int(config.notank_pivot_order)
    minimum = max(
        int(config.notank_rsi_period) + order * 2 + 5,
        int(config.atr_period) + order * 2 + 5,
        int(config.notank_rolling_window) + 5,
    )
    if len(candles) < minimum:
        return {"ready": 0, "candles": len(candles), "minimum": minimum}
    closes = [float(item.close) for item in candles]
    rsi = _ta._rsi(closes, int(config.notank_rsi_period))
    atr = _ta._atr(candles, int(config.atr_period))
    di = _di_values(candles, int(config.notank_rsi_period))
    confirm_index = len(candles) - 1
    pivot_index = confirm_index - order
    pivot_long = _strict_extremum(closes, pivot_index, order, 1)
    pivot_short = _strict_extremum(closes, pivot_index, order, -1)
    current = candles[confirm_index]
    pivot = candles[pivot_index]
    current_atr = float(atr[confirm_index])
    pivot_rsi = float(rsi[pivot_index])
    current_rsi = float(rsi[confirm_index])
    pivot_di = float(di[pivot_index])
    current_di = float(di[confirm_index])
    return {
        "ready": int(all(_finite(value) for value in (current_atr, pivot_rsi, current_rsi, pivot_di, current_di))),
        "confirm_ts": int(current.ts_event),
        "pivot_ts": int(pivot.ts_event),
        "pivot_index": int(pivot_index),
        "pivot_long": int(pivot_long),
        "pivot_short": int(pivot_short),
        "pivot_close": float(pivot.close),
        "pivot_low": float(pivot.low),
        "pivot_high": float(pivot.high),
        "current_open": float(current.open),
        "current_high": float(current.high),
        "current_low": float(current.low),
        "current_close": float(current.close),
        "atr": current_atr,
        "pivot_rsi": pivot_rsi,
        "current_rsi": current_rsi,
        "pivot_di": pivot_di,
        "current_di": current_di,
        "confirmation_move_atr": (
            (float(current.close) - float(pivot.close)) / current_atr
            if current_atr > 0.0 else math.nan
        ),
    }


def _confirmed_candidate(
    symbol: str,
    candles: Sequence[BarObservation],
    config: RouteConfig,
) -> RouteDecision:
    state = inspect_state(candles, config)
    if not int(state.get("ready") or 0):
        return _unresolved(symbol, "NOTANK_HISTORY_NOT_READY", diagnostics=state)
    long_ok = bool(state["pivot_long"]) and float(state["pivot_rsi"]) < float(config.notank_long_rsi_max) and float(state["pivot_di"]) <= 0.0
    short_ok = bool(state["pivot_short"]) and float(state["pivot_rsi"]) > float(config.notank_short_rsi_min) and float(state["pivot_di"]) >= 0.0
    direction = str(config.notank_direction_mode).strip().lower()
    if direction == "long_only":
        short_ok = False
    elif direction == "short_only":
        long_ok = False
    elif direction != "dual":
        raise ValueError(f"unsupported notank_direction_mode={direction!r}")
    if long_ok and short_ok:
        return _unresolved(symbol, "NOTANK_DIRECTION_AMBIGUITY", int(state["pivot_ts"]), state)
    if not long_ok and not short_ok:
        return _unresolved(symbol, "NOTANK_NO_CONFIRMED_SOURCE_PIVOT", int(state["pivot_ts"]), state)
    side = 1 if long_ok else -1
    mode = str(config.notank_entry_mode).strip().lower()
    entry = float(state["current_close"])
    atr = float(state["atr"])
    pivot_close = float(state["pivot_close"])
    signed_move_atr = side * (entry - pivot_close) / max(atr, 1e-12)
    reclaim_fraction = side * (entry - pivot_close) / max(abs(pivot_close), 1e-12)
    state = dict(state)
    state.update(
        {
            "entry_mode": mode,
            "direction_mode": direction,
            "side": side,
            "signed_confirmation_move_atr": signed_move_atr,
            "reclaim_fraction": reclaim_fraction,
        }
    )
    if mode == "confirmed_reclaim":
        if reclaim_fraction < float(config.notank_min_reclaim_fraction):
            return _unresolved(symbol, "NOTANK_RECLAIM_NOT_CONFIRMED", int(state["pivot_ts"]), state)
        if signed_move_atr > float(config.notank_max_confirmation_atr):
            return _unresolved(symbol, "NOTANK_CONFIRMATION_MOVE_EXHAUSTED", int(state["pivot_ts"]), state)
    elif mode != "confirmed_pivot":
        raise ValueError(f"unsupported confirmed entry mode={mode!r}")
    stop = (
        float(state["pivot_low"]) - float(config.notank_stop_atr_buffer) * atr
        if side > 0
        else float(state["pivot_high"]) + float(config.notank_stop_atr_buffer) * atr
    )
    risk = side * (entry - stop)
    if not math.isfinite(risk) or risk <= 0.0:
        return _unresolved(symbol, "NOTANK_INVALID_STRUCTURAL_RISK", int(state["pivot_ts"]), state)
    target = entry + side * risk * float(config.notank_target_r)
    score = (
        max(0.0, (float(config.notank_long_rsi_max) - float(state["pivot_rsi"])) / 10.0)
        if side > 0
        else max(0.0, (float(state["pivot_rsi"]) - float(config.notank_short_rsi_min)) / 10.0)
    )
    score += max(0.0, 2.5 - abs(signed_move_atr))
    score += max(0.0, abs(float(state["pivot_di"])) / 10.0)
    return RouteDecision(
        symbol=symbol,
        state=NOTANK_STATE,
        side=side,
        score=float(score),
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=target,
        episode_ts=int(state["pivot_ts"]),
        reasons=(
            "PUBLIC_NOTANK_EXTREMUM_CONFIRMED_AFTER_FIVE_CANDLES",
            "SOURCE_RSI_AND_DI_STATE_AT_PIVOT",
            "STRUCTURAL_PIVOT_INVALIDATION",
        ),
        diagnostics=state,
    )


def _rolling_candidate(
    symbol: str,
    bars: Sequence[BarObservation],
    config: RouteConfig,
) -> RouteDecision:
    candles = _ta._aggregate_complete(bars, int(config.notank_bucket_minutes))
    window = int(config.notank_rolling_window)
    minimum = max(window + 2, int(config.notank_rsi_period) + 5, int(config.atr_period) + 5)
    if len(candles) < minimum:
        return _unresolved(symbol, "NOTANK_HISTORY_NOT_READY")
    closes = [float(item.close) for item in candles]
    rsi = _ta._rsi(closes, int(config.notank_rsi_period))
    atr = _ta._atr(candles, int(config.atr_period))
    di = _di_values(candles, int(config.notank_rsi_period))
    current = candles[-1]
    history = candles[-window:]
    current_atr = float(atr[-1])
    current_rsi = float(rsi[-1])
    current_di = float(di[-1])
    if not all(_finite(value) for value in (current_atr, current_rsi, current_di)) or current_atr <= 0.0:
        return _unresolved(symbol, "NOTANK_INDICATORS_NOT_READY", int(current.ts_event))
    bar_range = max(float(current.high) - float(current.low), 1e-12)
    lower_wick = min(float(current.open), float(current.close)) - float(current.low)
    upper_wick = float(current.high) - max(float(current.open), float(current.close))
    long_ok = (
        float(current.low) <= min(float(item.low) for item in history)
        and float(current.close) > float(current.low)
        and lower_wick / bar_range >= float(config.notank_min_wick_fraction)
        and current_rsi < float(config.notank_long_rsi_max)
        and current_di <= 0.0
    )
    short_ok = (
        float(current.high) >= max(float(item.high) for item in history)
        and float(current.close) < float(current.high)
        and upper_wick / bar_range >= float(config.notank_min_wick_fraction)
        and current_rsi > float(config.notank_short_rsi_min)
        and current_di >= 0.0
    )
    direction = str(config.notank_direction_mode).strip().lower()
    if direction == "long_only":
        short_ok = False
    elif direction == "short_only":
        long_ok = False
    elif direction != "dual":
        raise ValueError(f"unsupported notank_direction_mode={direction!r}")
    diagnostics = {
        "ready": 1,
        "entry_mode": "rolling_reclaim",
        "direction_mode": direction,
        "current_rsi": current_rsi,
        "current_di": current_di,
        "atr": current_atr,
        "lower_wick_fraction": lower_wick / bar_range,
        "upper_wick_fraction": upper_wick / bar_range,
        "rolling_low": min(float(item.low) for item in history),
        "rolling_high": max(float(item.high) for item in history),
        "current_open": float(current.open),
        "current_high": float(current.high),
        "current_low": float(current.low),
        "current_close": float(current.close),
    }
    if long_ok and short_ok:
        return _unresolved(symbol, "NOTANK_DIRECTION_AMBIGUITY", int(current.ts_event), diagnostics)
    if not long_ok and not short_ok:
        return _unresolved(symbol, "NOTANK_NO_ROLLING_REJECTION", int(current.ts_event), diagnostics)
    side = 1 if long_ok else -1
    entry = float(current.close)
    stop = (
        float(current.low) - float(config.notank_stop_atr_buffer) * current_atr
        if side > 0
        else float(current.high) + float(config.notank_stop_atr_buffer) * current_atr
    )
    risk = side * (entry - stop)
    if risk <= 0.0:
        return _unresolved(symbol, "NOTANK_INVALID_STRUCTURAL_RISK", int(current.ts_event), diagnostics)
    target = entry + side * risk * float(config.notank_target_r)
    wick = diagnostics["lower_wick_fraction"] if side > 0 else diagnostics["upper_wick_fraction"]
    score = float(wick) * 4.0 + abs(current_di) / 10.0 + abs(current_rsi - 50.0) / 20.0
    diagnostics.update({"side": side, "pivot_ts": int(current.ts_event)})
    return RouteDecision(
        symbol=symbol,
        state=NOTANK_STATE,
        side=side,
        score=float(score),
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=target,
        episode_ts=int(current.ts_event),
        reasons=(
            "CAUSAL_CURRENT_ROLLING_EXTREME_REJECTION",
            "SOURCE_RSI_AND_DI_STATE",
            "STRUCTURAL_REJECTION_INVALIDATION",
        ),
        diagnostics=diagnostics,
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
    mode = str(config.notank_entry_mode).strip().lower()
    if mode in {"confirmed_pivot", "confirmed_reclaim"}:
        return _confirmed_candidate(symbol, bars, config)
    if mode == "rolling_reclaim":
        return _rolling_candidate(symbol, bars, config)
    raise ValueError(f"unsupported notank_entry_mode={mode!r}")


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
    "BarObservation",
    "FeatureObservation",
    "NOTANK_STATE",
    "RouteConfig",
    "RouteDecision",
    "UNRESOLVED",
    "classify_symbol",
    "inspect_state",
    "route_universe",
]

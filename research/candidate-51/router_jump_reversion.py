"""Causal adaptation of De Nicola's intraday jump-reversion strategy.

The published rule is intentionally simple: after a large return, enter in the
opposite direction and close one equal-length period later.  The paper computes
jump size with full-sample volatility.  That is not causal, so this adaptation
uses only the immediately preceding completed returns.  A hard stop beyond the
terminal minute's auction extreme is added solely to enforce the project's 3%
planned-loss contract; the public time exit remains the main exit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Sequence

from router_picasso import BarObservation, _aggregate_complete, _atr

JUMP_REVERSION_STATE = "ACADEMIC_INTRADAY_JUMP_REVERSION"
SMA_OFFSET_STATE = JUMP_REVERSION_STATE
UNRESOLVED = "UNRESOLVED"
_EPS = 1e-12
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}


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

    jump_timeframe_minutes: int = 120
    jump_threshold_sigma: float = 2.0
    jump_volatility_window: int = 36
    jump_min_absolute_return: float = 0.0
    jump_terminal_atr_period: int = 14
    jump_stop_atr_multiple: float = 1.0
    jump_min_stop_fraction: float = 0.0015
    jump_emergency_target_fraction: float = 0.20

    sma_offset_period: int = 8
    sma_offset_low: float = 0.960
    sma_offset_high: float = 1.012
    sma_trend_fast: int = 20
    sma_trend_slow: int = 25
    sma_stop_min_fraction: float = 0.0075
    sma_stop_max_fraction: float = 0.1000
    sma_stop_atr_buffer: float = 0.50
    sma_structural_lookback: int = 6
    sma_min_reward_r: float = 1.00


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


def _unresolved(symbol: str, reason: str, episode_ts: int = 0,
                diagnostics: Mapping[str, float | int | str] | None = None) -> RouteDecision:
    return RouteDecision(symbol, UNRESOLVED, 0, 0.0, math.nan, math.nan, math.nan,
                         int(episode_ts), (reason,), dict(diagnostics or {}))


def causal_jump_zscore(returns: Sequence[float], window: int) -> tuple[float, float]:
    """Return current z-score using only *prior* completed returns."""
    if window < 2 or len(returns) < window + 1:
        return math.nan, math.nan
    current = float(returns[-1])
    sample = [float(value) for value in returns[-window - 1:-1]]
    if not all(_finite(value) for value in sample):
        return math.nan, math.nan
    mean = sum(sample) / len(sample)
    variance = sum((value - mean) ** 2 for value in sample) / max(len(sample) - 1, 1)
    sigma = math.sqrt(max(variance, 0.0))
    if sigma <= _EPS:
        return math.nan, sigma
    return (current - mean) / sigma, sigma


def classify_symbol(symbol: str, bars: Sequence[BarObservation], feature: FeatureObservation,
                    config: RouteConfig = RouteConfig()) -> RouteDecision:
    if not bars:
        return _unresolved(symbol, "NO_MINUTE_BARS")
    latest_ts = int(bars[-1].ts_event)
    if int(feature.observed_time_ns) > latest_ts:
        return _unresolved(symbol, "FUTURE_FEATURE_REJECTED", latest_ts)
    timeframe = int(config.jump_timeframe_minutes)
    candles = _aggregate_complete(bars, timeframe)
    minimum = int(config.jump_volatility_window) + 2
    if len(candles) < minimum:
        return _unresolved(symbol, "JUMP_HISTORY_NOT_READY", latest_ts,
                           {"candles": len(candles), "minimum": minimum})
    returns = []
    for index in range(1, len(candles)):
        previous = float(candles[index - 1].close)
        current = float(candles[index].close)
        if previous <= 0.0 or current <= 0.0:
            return _unresolved(symbol, "NONPOSITIVE_PRICE", latest_ts)
        returns.append(math.log(current / previous))
    zscore, sigma = causal_jump_zscore(returns, int(config.jump_volatility_window))
    current_return = float(returns[-1])
    diagnostics: dict[str, float | int | str] = {
        "timeframe_minutes": timeframe,
        "volatility_window": int(config.jump_volatility_window),
        "threshold_sigma": float(config.jump_threshold_sigma),
        "current_log_return": current_return,
        "prior_return_sigma": sigma,
        "causal_zscore": zscore,
        "absolute_return": abs(math.expm1(current_return)),
    }
    if not _finite(zscore):
        return _unresolved(symbol, "JUMP_VOLATILITY_NOT_READY", candles[-1].ts_event, diagnostics)
    if abs(zscore) < float(config.jump_threshold_sigma):
        return _unresolved(symbol, "JUMP_BELOW_SIGMA_THRESHOLD", candles[-1].ts_event, diagnostics)
    if abs(math.expm1(current_return)) < float(config.jump_min_absolute_return):
        return _unresolved(symbol, "JUMP_BELOW_ABSOLUTE_THRESHOLD", candles[-1].ts_event, diagnostics)
    side = -1 if current_return > 0.0 else 1
    entry = float(candles[-1].close)
    terminal_atr = _atr(bars, int(config.jump_terminal_atr_period))[-1]
    if not _finite(terminal_atr) or terminal_atr <= 0.0:
        return _unresolved(symbol, "TERMINAL_ATR_NOT_READY", candles[-1].ts_event, diagnostics)
    terminal = bars[-1]
    buffer = max(float(config.jump_stop_atr_multiple) * float(terminal_atr),
                 float(config.jump_min_stop_fraction) * entry)
    stop = float(terminal.low) - buffer if side > 0 else float(terminal.high) + buffer
    target_fraction = float(config.jump_emergency_target_fraction)
    objective = entry * (1.0 + side * target_fraction)
    if side > 0 and not (0.0 < stop < entry < objective):
        return _unresolved(symbol, "JUMP_LONG_GEOMETRY_INVALID", candles[-1].ts_event, diagnostics)
    if side < 0 and not (0.0 < objective < entry < stop):
        return _unresolved(symbol, "JUMP_SHORT_GEOMETRY_INVALID", candles[-1].ts_event, diagnostics)
    stop_fraction = abs(entry - stop) / entry
    diagnostics.update({
        "side": side,
        "terminal_minute_low": float(terminal.low),
        "terminal_minute_high": float(terminal.high),
        "terminal_atr": float(terminal_atr),
        "stop_buffer": buffer,
        "stop_fraction": stop_fraction,
        "source_holding_minutes": timeframe,
        "emergency_target_fraction": target_fraction,
    })
    score = abs(zscore) + min(4.0, abs(current_return) / max(sigma, _EPS))
    return RouteDecision(
        symbol=symbol,
        state=JUMP_REVERSION_STATE,
        side=side,
        score=float(score),
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=objective,
        episode_ts=int(candles[-1].ts_event),
        reasons=(
            "LARGE_COMPLETED_RETURN",
            "ENTER_OPPOSITE_DIRECTION",
            "EXIT_AFTER_ONE_EQUAL_TIME_UNIT",
            "CAUSAL_PRIOR_RETURN_VOLATILITY_ONLY",
            "TERMINAL_AUCTION_EXTREME_HARD_STOP_ADDED_FOR_RISK_CONTRACT",
        ),
        diagnostics=diagnostics,
    )


classify_sma_offset = classify_symbol


def route_universe(bars_by_symbol: Mapping[str, Sequence[BarObservation]],
                   features_by_symbol: Mapping[str, FeatureObservation],
                   config: RouteConfig = RouteConfig()) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    decisions = {symbol: classify_symbol(symbol, bars,
        features_by_symbol.get(symbol, FeatureObservation(bars[-1].ts_event if bars else 0)), config)
        for symbol, bars in bars_by_symbol.items()}
    actionable = [decision for decision in decisions.values() if decision.actionable]
    actionable.sort(key=lambda item: (-item.score, _SYMBOL_PRIORITY.get(item.symbol, 99), item.episode_ts))
    return (actionable[0] if actionable else None), decisions


__all__ = ["BarObservation", "FeatureObservation", "JUMP_REVERSION_STATE", "RouteConfig",
           "RouteDecision", "SMA_OFFSET_STATE", "UNRESOLVED", "causal_jump_zscore",
           "classify_symbol", "route_universe"]

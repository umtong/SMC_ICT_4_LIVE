"""Mechanism-oriented repair of the 4h causal jump-reversion family.

This module preserves the public rule's alpha engine (large completed return ->
trade the opposite direction for one equal time unit) while exposing two
independent repair levers:

1. stop geometry: terminal one-minute extreme vs completed impulse-candle extreme;
2. cross-sectional routing: absolute jump score vs idiosyncratic residual score.

Confirmation is handled in ``strategy_jump_repair.py`` because it is a causal
state transition after the completed source jump, not an entry-time filter.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
import statistics
from typing import Mapping, Sequence

from router_picasso import BarObservation, _aggregate_complete, _atr

JUMP_REVERSION_STATE = "C57_4H_JUMP_REVERSION"
UNRESOLVED = "UNRESOLVED"
SMA_OFFSET_STATE = JUMP_REVERSION_STATE
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
    # Compatibility fields required by the reused Candidate 35 execution shell.
    atr_period: int = 30
    min_impulse_atr_continuation: float = 0.20
    min_impulse_atr_reversal: float = 1.05
    min_response_atr: float = 0.08
    min_participation_ratio: float = 0.85
    min_route_score: float = 3.20
    ambiguity_score_gap: float = 0.10
    continuation_target_r: float = 2.00
    reversal_target_r: float = 1.65

    jump_timeframe_minutes: int = 240
    jump_threshold_sigma: float = 2.0
    jump_volatility_window: int = 18
    jump_min_absolute_return: float = 0.0
    jump_terminal_atr_period: int = 14
    jump_stop_atr_multiple: float = 1.0
    jump_min_stop_fraction: float = 0.0015
    jump_emergency_target_fraction: float = 0.20
    jump_stop_mode: str = "terminal"  # terminal | impulse
    jump_selection_mode: str = "source"  # source | residual_rank | residual_only
    jump_min_residual_share: float = 0.50
    jump_min_residual_z: float = 0.75
    jump_confirmation_minutes: int = 0
    jump_confirmation_bucket_minutes: int = 5

    # Legacy adapter compatibility.
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


def _finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


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


def causal_jump_zscore(returns: Sequence[float], window: int) -> tuple[float, float, float]:
    """Current return z-score using only prior completed returns.

    Returns ``(z, prior_sigma, prior_mean)``. The current return is never part of
    the estimator, so changing it cannot alter the denominator.
    """
    if window < 2 or len(returns) < window + 1:
        return math.nan, math.nan, math.nan
    current = float(returns[-1])
    sample = [float(value) for value in returns[-window - 1 : -1]]
    if not all(math.isfinite(value) for value in sample):
        return math.nan, math.nan, math.nan
    mean = statistics.fmean(sample)
    variance = sum((value - mean) ** 2 for value in sample) / max(len(sample) - 1, 1)
    sigma = math.sqrt(max(variance, 0.0))
    if sigma <= _EPS:
        return math.nan, sigma, mean
    return (current - mean) / sigma, sigma, mean


def _source_decision(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig,
) -> RouteDecision:
    if not bars:
        return _unresolved(symbol, "NO_MINUTE_BARS")
    latest_ts = int(bars[-1].ts_event)
    if int(feature.observed_time_ns) > latest_ts:
        return _unresolved(symbol, "FUTURE_FEATURE_REJECTED", latest_ts)

    timeframe = int(config.jump_timeframe_minutes)
    candles = _aggregate_complete(bars, timeframe)
    minimum = int(config.jump_volatility_window) + 2
    if len(candles) < minimum:
        return _unresolved(
            symbol,
            "JUMP_HISTORY_NOT_READY",
            latest_ts,
            {"candles": len(candles), "minimum": minimum},
        )

    returns: list[float] = []
    for index in range(1, len(candles)):
        previous = float(candles[index - 1].close)
        current = float(candles[index].close)
        if previous <= 0.0 or current <= 0.0:
            return _unresolved(symbol, "NONPOSITIVE_PRICE", latest_ts)
        returns.append(math.log(current / previous))

    zscore, sigma, prior_mean = causal_jump_zscore(
        returns, int(config.jump_volatility_window)
    )
    current_return = float(returns[-1])
    jump = candles[-1]
    diagnostics: dict[str, float | int | str] = {
        "timeframe_minutes": timeframe,
        "volatility_window": int(config.jump_volatility_window),
        "threshold_sigma": float(config.jump_threshold_sigma),
        "current_log_return": current_return,
        "prior_return_sigma": sigma,
        "prior_return_mean": prior_mean,
        "causal_zscore": zscore,
        "absolute_return": abs(math.expm1(current_return)),
        "jump_open": float(jump.open),
        "jump_high": float(jump.high),
        "jump_low": float(jump.low),
        "jump_close": float(jump.close),
    }
    if not _finite(zscore):
        return _unresolved(symbol, "JUMP_VOLATILITY_NOT_READY", jump.ts_event, diagnostics)
    if abs(float(zscore)) < float(config.jump_threshold_sigma):
        return _unresolved(symbol, "JUMP_BELOW_SIGMA_THRESHOLD", jump.ts_event, diagnostics)
    if abs(math.expm1(current_return)) < float(config.jump_min_absolute_return):
        return _unresolved(symbol, "JUMP_BELOW_ABSOLUTE_THRESHOLD", jump.ts_event, diagnostics)

    side = -1 if current_return > 0.0 else 1
    entry = float(jump.close)
    terminal_atr = _atr(bars, int(config.jump_terminal_atr_period))[-1]
    if not _finite(terminal_atr) or float(terminal_atr) <= 0.0:
        return _unresolved(symbol, "TERMINAL_ATR_NOT_READY", jump.ts_event, diagnostics)
    terminal = bars[-1]
    buffer = max(
        float(config.jump_stop_atr_multiple) * float(terminal_atr),
        float(config.jump_min_stop_fraction) * entry,
    )
    stop_mode = str(config.jump_stop_mode).lower()
    if stop_mode == "terminal":
        low_anchor = float(terminal.low)
        high_anchor = float(terminal.high)
    elif stop_mode == "impulse":
        low_anchor = float(jump.low)
        high_anchor = float(jump.high)
    else:
        return _unresolved(symbol, "UNKNOWN_JUMP_STOP_MODE", jump.ts_event, diagnostics)

    stop = low_anchor - buffer if side > 0 else high_anchor + buffer
    objective = entry * (1.0 + side * float(config.jump_emergency_target_fraction))
    if side > 0 and not (0.0 < stop < entry < objective):
        return _unresolved(symbol, "JUMP_LONG_GEOMETRY_INVALID", jump.ts_event, diagnostics)
    if side < 0 and not (0.0 < objective < entry < stop):
        return _unresolved(symbol, "JUMP_SHORT_GEOMETRY_INVALID", jump.ts_event, diagnostics)

    diagnostics.update(
        {
            "side": side,
            "terminal_minute_low": float(terminal.low),
            "terminal_minute_high": float(terminal.high),
            "terminal_atr": float(terminal_atr),
            "stop_buffer": buffer,
            "stop_anchor_low": low_anchor,
            "stop_anchor_high": high_anchor,
            "stop_fraction": abs(entry - stop) / entry,
            "source_holding_minutes": timeframe,
            "emergency_target_fraction": float(config.jump_emergency_target_fraction),
            "stop_mode": stop_mode,
        }
    )
    score = abs(float(zscore)) + min(
        4.0, abs(current_return - float(prior_mean)) / max(float(sigma), _EPS)
    )
    return RouteDecision(
        symbol=symbol,
        state=JUMP_REVERSION_STATE,
        side=side,
        score=score,
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=objective,
        episode_ts=int(jump.ts_event),
        reasons=(
            "LARGE_COMPLETED_RETURN",
            "ENTER_OPPOSITE_DIRECTION",
            "EXIT_AT_ORIGINAL_EQUAL_TIME_HORIZON",
            "CAUSAL_PRIOR_RETURN_VOLATILITY_ONLY",
            f"{stop_mode.upper()}_EXTREME_STOP_GEOMETRY",
        ),
        diagnostics=diagnostics,
    )


def classify_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    return _source_decision(symbol, bars, feature, config)


classify_sma_offset = classify_symbol


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    decisions = {
        symbol: _source_decision(
            symbol,
            bars,
            features_by_symbol.get(
                symbol,
                FeatureObservation(bars[-1].ts_event if bars else 0),
            ),
            config,
        )
        for symbol, bars in bars_by_symbol.items()
    }

    current_returns = {
        symbol: float(decision.diagnostics.get("current_log_return", math.nan))
        for symbol, decision in decisions.items()
    }
    sigmas = {
        symbol: float(decision.diagnostics.get("prior_return_sigma", math.nan))
        for symbol, decision in decisions.items()
    }
    mode = str(config.jump_selection_mode).lower()
    enriched: dict[str, RouteDecision] = {}
    for symbol, decision in decisions.items():
        if not decision.actionable:
            enriched[symbol] = decision
            continue
        peers = [
            value
            for peer, value in current_returns.items()
            if peer != symbol and math.isfinite(value)
        ]
        peer_sigmas = [
            value
            for peer, value in sigmas.items()
            if peer != symbol and math.isfinite(value) and value > 0.0
        ]
        peer_median = statistics.median(peers) if peers else 0.0
        peer_sigma = statistics.median(peer_sigmas) if peer_sigmas else 0.0
        current = current_returns[symbol]
        residual = current - peer_median
        pooled = math.sqrt(max(sigmas[symbol] ** 2 + peer_sigma**2, _EPS))
        residual_z = residual / pooled
        residual_share = abs(residual) / max(abs(current), _EPS)
        same_direction_peers = sum(
            1 for value in peers if value * current > 0.0
        )
        diagnostics = dict(decision.diagnostics)
        diagnostics.update(
            {
                "peer_median_log_return": peer_median,
                "cross_sectional_residual": residual,
                "cross_sectional_residual_z": residual_z,
                "residual_share_of_jump": residual_share,
                "same_direction_peer_count": same_direction_peers,
                "selection_mode": mode,
            }
        )
        if mode == "source":
            score = decision.score
        elif mode == "residual_rank":
            score = abs(residual_z) + 0.35 * abs(
                float(diagnostics.get("causal_zscore", 0.0))
            )
        elif mode == "residual_only":
            same_sign = residual * current > 0.0
            if (
                not same_sign
                or residual_share < float(config.jump_min_residual_share)
                or abs(residual_z) < float(config.jump_min_residual_z)
            ):
                enriched[symbol] = _unresolved(
                    symbol,
                    "COMMON_MODE_JUMP_NOT_REVERTED",
                    decision.episode_ts,
                    diagnostics,
                )
                continue
            score = abs(residual_z) + 0.35 * abs(
                float(diagnostics.get("causal_zscore", 0.0))
            )
        else:
            enriched[symbol] = _unresolved(
                symbol,
                "UNKNOWN_JUMP_SELECTION_MODE",
                decision.episode_ts,
                diagnostics,
            )
            continue
        enriched[symbol] = replace(decision, score=float(score), diagnostics=diagnostics)

    actionable = [decision for decision in enriched.values() if decision.actionable]
    actionable.sort(
        key=lambda item: (
            -item.score,
            _SYMBOL_PRIORITY.get(item.symbol, 99),
            item.episode_ts,
        )
    )
    return (actionable[0] if actionable else None), enriched


__all__ = [
    "BarObservation",
    "FeatureObservation",
    "JUMP_REVERSION_STATE",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "causal_jump_zscore",
    "classify_symbol",
    "route_universe",
]

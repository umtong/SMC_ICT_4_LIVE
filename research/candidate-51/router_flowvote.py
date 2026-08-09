"""External order-book imbalance × aggressor-delta vote for Candidate 51.

The public source's 3-of-4 directional vote is retained.  Project data replaces
REST snapshots with checksum-verified Binance Futures minute aggregates:
+/-1% depth imbalance, signed aggressive notional, completed-bar EMA direction,
and a 20-minute notional ratio.  Execution and accounting remain NautilusTrader.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Sequence

from router_picasso import BarObservation, _aggregate_complete, _atr, _ema

FLOWVOTE_STATE = "PUBLIC_ORDERBOOK_DELTA_VOLUME_VOTE"
SMA_OFFSET_STATE = FLOWVOTE_STATE
UNRESOLVED = "UNRESOLVED"
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}
_EPS = 1e-12


@dataclass(frozen=True, slots=True)
class FeatureObservation:
    observed_time_ns: int
    ready: bool = False
    flow_60s: float = math.nan
    flow_3m: float = math.nan
    depth_imbalance_1: float = math.nan
    notional_volume_ratio_20: float = math.nan
    efficiency_60s: float = math.nan
    depth_snapshot_age_seconds: float = math.nan
    flow_open_10s: float = math.nan
    notional_open_10s_burst: float = math.nan
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

    flowvote_bucket_minutes: int = 1
    flowvote_ema_period: int = 50
    flowvote_atr_period: int = 14
    flowvote_imbalance_threshold: float = 0.12
    flowvote_delta_threshold: float = 0.15
    flowvote_min_volume_ratio: float = 1.20
    flowvote_min_votes: int = 3
    flowvote_min_efficiency: float = 0.0
    flowvote_stop_atr_multiple: float = 1.50
    flowvote_min_stop_fraction: float = 0.003
    flowvote_target_r: float = 2.0

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


def flowvote_scores(*, depth_imbalance: float, trade_delta: float, close: float,
                    ema: float, volume_ratio: float, config: RouteConfig = RouteConfig()) -> tuple[int, int, dict[str, int]]:
    """Return the public source's long/short 3-of-4 vote counts."""
    long_score = 0
    short_score = 0
    votes = {
        "long_depth": 0, "short_depth": 0,
        "long_delta": 0, "short_delta": 0,
        "long_trend": 0, "short_trend": 0,
        "long_volume": 0, "short_volume": 0,
    }
    if depth_imbalance >= config.flowvote_imbalance_threshold:
        long_score += 1; votes["long_depth"] = 1
    if depth_imbalance <= -config.flowvote_imbalance_threshold:
        short_score += 1; votes["short_depth"] = 1
    if trade_delta >= config.flowvote_delta_threshold:
        long_score += 1; votes["long_delta"] = 1
    if trade_delta <= -config.flowvote_delta_threshold:
        short_score += 1; votes["short_delta"] = 1
    if close > ema:
        long_score += 1; votes["long_trend"] = 1
    elif close < ema:
        short_score += 1; votes["short_trend"] = 1
    if volume_ratio >= config.flowvote_min_volume_ratio:
        if trade_delta > 0.0:
            long_score += 1; votes["long_volume"] = 1
        elif trade_delta < 0.0:
            short_score += 1; votes["short_volume"] = 1
    return long_score, short_score, votes


def classify_symbol(symbol: str, bars: Sequence[BarObservation], feature: FeatureObservation,
                    config: RouteConfig = RouteConfig()) -> RouteDecision:
    if not bars:
        return _unresolved(symbol, "NO_MINUTE_BARS")
    latest_ts = int(bars[-1].ts_event)
    if int(feature.observed_time_ns) > latest_ts:
        return _unresolved(symbol, "FUTURE_FEATURE_REJECTED", latest_ts)
    if not feature.ready:
        return _unresolved(symbol, "FLOWVOTE_FEATURE_NOT_READY", latest_ts)
    candles = _aggregate_complete(bars, int(config.flowvote_bucket_minutes))
    minimum = max(int(config.flowvote_ema_period) + 2, int(config.flowvote_atr_period) + 2)
    if len(candles) < minimum:
        return _unresolved(symbol, "FLOWVOTE_HISTORY_NOT_READY", latest_ts,
                           {"candles": len(candles), "minimum": minimum})
    closes = [float(candle.close) for candle in candles]
    ema = _ema(closes, int(config.flowvote_ema_period))[-1]
    atr = _atr(candles, int(config.flowvote_atr_period))[-1]
    delta = float(feature.flow_3m if int(config.flowvote_bucket_minutes) >= 3 else feature.flow_60s)
    values = (feature.depth_imbalance_1, delta, feature.notional_volume_ratio_20,
              feature.efficiency_60s, ema, atr)
    if not all(_finite(value) for value in values):
        return _unresolved(symbol, "FLOWVOTE_NONFINITE_INPUT", latest_ts)
    if float(feature.efficiency_60s) < float(config.flowvote_min_efficiency):
        return _unresolved(symbol, "FLOWVOTE_EFFICIENCY_GATE", latest_ts, {
            "efficiency_60s": float(feature.efficiency_60s),
            "minimum": float(config.flowvote_min_efficiency),
        })
    entry = float(candles[-1].close)
    long_score, short_score, votes = flowvote_scores(
        depth_imbalance=float(feature.depth_imbalance_1), trade_delta=delta,
        close=entry, ema=float(ema), volume_ratio=float(feature.notional_volume_ratio_20),
        config=config)
    diagnostics: dict[str, float | int | str] = {
        "bucket_minutes": int(config.flowvote_bucket_minutes),
        "depth_imbalance_1": float(feature.depth_imbalance_1),
        "trade_delta": delta,
        "notional_volume_ratio_20": float(feature.notional_volume_ratio_20),
        "efficiency_60s": float(feature.efficiency_60s),
        "depth_snapshot_age_seconds": float(feature.depth_snapshot_age_seconds),
        "ema": float(ema), "atr": float(atr),
        "long_score": long_score, "short_score": short_score,
        **votes,
    }
    side = 0
    if long_score >= int(config.flowvote_min_votes) and long_score > short_score:
        side = 1
    elif short_score >= int(config.flowvote_min_votes) and short_score > long_score:
        side = -1
    if side == 0:
        return _unresolved(symbol, "FLOWVOTE_NO_DIRECTIONAL_MAJORITY", candles[-1].ts_event, diagnostics)
    stop_distance = max(float(config.flowvote_stop_atr_multiple) * float(atr),
                        float(config.flowvote_min_stop_fraction) * entry)
    stop = entry - side * stop_distance
    target = entry + side * float(config.flowvote_target_r) * stop_distance
    if side > 0 and not (0.0 < stop < entry < target):
        return _unresolved(symbol, "FLOWVOTE_LONG_GEOMETRY_INVALID", candles[-1].ts_event, diagnostics)
    if side < 0 and not (0.0 < target < entry < stop):
        return _unresolved(symbol, "FLOWVOTE_SHORT_GEOMETRY_INVALID", candles[-1].ts_event, diagnostics)
    strength = (abs(float(feature.depth_imbalance_1)) / max(config.flowvote_imbalance_threshold, _EPS)
                + abs(delta) / max(config.flowvote_delta_threshold, _EPS))
    score = float(max(long_score, short_score)) + min(4.0, strength)
    diagnostics.update({"stop_distance": stop_distance, "target_r": float(config.flowvote_target_r)})
    return RouteDecision(symbol, FLOWVOTE_STATE, side, score, entry, stop, target,
        int(candles[-1].ts_event),
        ("PUBLIC_ORDERBOOK_IMBALANCE_VOTE", "PUBLIC_AGGRESSOR_DELTA_VOTE",
         "COMPLETED_BAR_EMA_DIRECTION_VOTE", "NOTIONAL_VOLUME_BONUS_VOTE",
         "REAL_ATR_STOP_ADDED_TO_SOURCE_SIZING_DISTANCE"), diagnostics)


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


__all__ = ["BarObservation", "FeatureObservation", "FLOWVOTE_STATE", "RouteConfig",
           "RouteDecision", "SMA_OFFSET_STATE", "UNRESOLVED", "classify_symbol",
           "flowvote_scores", "route_universe"]

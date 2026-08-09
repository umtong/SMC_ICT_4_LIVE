"""Candidate 53 causal multi-mechanism router.

Three independent intraday mechanisms compete for one global slot:
1) flow/OI confirmed range release and first acceptance;
2) liquidity sweep -> failed auction reversal with absorption/peer non-confirmation;
3) cross-asset catch-up after market repricing while the local contract transitions.
All inputs are completed observations at or before the decision timestamp.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from statistics import median
from typing import Mapping, Sequence

UNRESOLVED = "UNRESOLVED"
FLOW_RELEASE_CONTINUATION = "FLOW_RELEASE_CONTINUATION"
FAILED_AUCTION_REVERSAL = "FAILED_AUCTION_REVERSAL"
CROSS_ASSET_CATCHUP = "CROSS_ASSET_CATCHUP"


@dataclass(frozen=True)
class BarObservation:
    ts_event: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class FeatureObservation:
    observed_time_ns: int
    ready: bool = True
    flow_open_10s: float = math.nan
    notional_open_10s_burst: float = math.nan
    flow_60s: float = math.nan
    efficiency_60s: float = math.nan
    oi_change_15m: float = math.nan
    premium_z: float = math.nan
    flow_15s: float = math.nan
    flow_3m: float = math.nan
    notional_burst: float = math.nan
    trade_count_burst: float = math.nan
    absorption_60s: float = math.nan
    depth_imbalance_1: float = math.nan
    depth_imbalance_2: float = math.nan
    bid_depth_change_1_1m: float = math.nan
    ask_depth_change_1_1m: float = math.nan
    bid_depth_change_1_5m: float = math.nan
    ask_depth_change_1_5m: float = math.nan
    ret_60s_bps: float = math.nan


@dataclass(frozen=True)
class RouteConfig:
    atr_period: int = 30
    min_impulse_atr_continuation: float = 0.80
    min_impulse_atr_reversal: float = 0.55
    min_response_atr: float = 0.10
    min_participation_ratio: float = 0.90
    min_route_score: float = 3.00
    ambiguity_score_gap: float = 0.15
    continuation_target_r: float = 1.80
    reversal_target_r: float = 1.55
    continuation_breakout_lookback: int = 24
    continuation_fresh_minutes: int = 4
    reversal_liquidity_lookback: int = 30
    reversal_fresh_minutes: int = 3
    peer_trend_threshold_atr: float = 0.18
    max_continuation_extension_atr: float = 1.10
    min_stop_atr: float = 0.32
    max_stop_atr: float = 2.20
    stop_buffer_atr: float = 0.12
    min_flow_3m: float = 0.08
    min_tail_flow: float = -0.08
    max_crowded_premium_z: float = 2.8
    catchup_market_atr: float = 0.55
    catchup_lag_atr: float = 0.35
    catchup_trigger_atr_3m: float = 0.10


@dataclass(frozen=True)
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
        return self.side != 0 and self.state != UNRESOLVED


def _finite(value: float) -> bool:
    return math.isfinite(float(value))


def _atr(bars: Sequence[BarObservation], period: int) -> float:
    if len(bars) < period + 1:
        return math.nan
    values: list[float] = []
    for i in range(len(bars) - period, len(bars)):
        prior = bars[i - 1].close
        bar = bars[i]
        values.append(max(bar.high - bar.low, abs(bar.high - prior), abs(bar.low - prior)))
    return sum(values) / len(values) if values else math.nan


def _return_atr(bars: Sequence[BarObservation], lookback: int, atr: float) -> float:
    if len(bars) <= lookback or not _finite(atr) or atr <= 0.0:
        return math.nan
    return (bars[-1].close - bars[-1 - lookback].close) / atr


def _side_flow(feature: FeatureObservation, side: int) -> tuple[float, float, float]:
    flow3 = feature.flow_3m if _finite(feature.flow_3m) else feature.flow_60s
    tail = feature.flow_15s if _finite(feature.flow_15s) else feature.flow_60s
    opening = feature.flow_open_10s if _finite(feature.flow_open_10s) else feature.flow_60s
    return side * flow3, side * tail, side * opening


def _burst(feature: FeatureObservation) -> float:
    values = [x for x in (feature.notional_burst, feature.notional_open_10s_burst, feature.trade_count_burst) if _finite(x)]
    return max(values) if values else math.nan


def _depth_support(feature: FeatureObservation, side: int) -> float:
    return side * feature.depth_imbalance_1 if _finite(feature.depth_imbalance_1) else 0.0


def _peer_state(bars_by_symbol: Mapping[str, Sequence[BarObservation]], config: RouteConfig) -> tuple[dict[str, float], int, float]:
    normalized: dict[str, float] = {}
    for symbol, bars in bars_by_symbol.items():
        atr = _atr(bars, config.atr_period)
        value = _return_atr(bars, 15, atr)
        if _finite(value):
            normalized[symbol] = value
    if not normalized:
        return {}, 0, 0.0
    med = float(median(normalized.values()))
    side = 1 if med > config.peer_trend_threshold_atr else (-1 if med < -config.peer_trend_threshold_atr else 0)
    return normalized, side, med


def _stop_and_target(*, side: int, entry: float, raw_stop: float, atr: float, target_r: float, config: RouteConfig) -> tuple[float, float] | None:
    if not (_finite(entry) and _finite(raw_stop) and _finite(atr) and atr > 0.0):
        return None
    risk = side * (entry - raw_stop)
    min_risk = config.min_stop_atr * atr
    max_risk = config.max_stop_atr * atr
    if risk <= 0.0:
        return None
    risk = max(risk, min_risk)
    if risk > max_risk:
        return None
    stop = entry - side * risk
    target = entry + side * target_r * risk
    if side > 0 and not (stop < entry < target):
        return None
    if side < 0 and not (target < entry < stop):
        return None
    return stop, target


def _continuation(symbol: str, bars: Sequence[BarObservation], feature: FeatureObservation, normalized: Mapping[str, float], market_side: int, config: RouteConfig) -> RouteDecision | None:
    if len(bars) < config.continuation_breakout_lookback + config.continuation_fresh_minutes + 5:
        return None
    atr = _atr(bars, config.atr_period)
    if not _finite(atr) or atr <= 0.0:
        return None
    trend15 = _return_atr(bars, 15, atr)
    trend5 = _return_atr(bars, 5, atr)
    if not (_finite(trend15) and _finite(trend5)):
        return None
    candidates: list[RouteDecision] = []
    for side in (1, -1):
        if side * trend15 < config.min_impulse_atr_continuation or side * trend5 < 0.12:
            continue
        breakout_index: int | None = None
        boundary = math.nan
        for age in range(config.continuation_fresh_minutes - 1, -1, -1):
            idx = len(bars) - 1 - age
            prior_start = idx - config.continuation_breakout_lookback
            if prior_start < 0:
                continue
            prior = bars[prior_start:idx]
            bound = max(x.high for x in prior) if side > 0 else min(x.low for x in prior)
            crossed = bars[idx].close > bound if side > 0 else bars[idx].close < bound
            prior_close_ok = bars[idx - 1].close <= bound if side > 0 else bars[idx - 1].close >= bound
            if crossed and prior_close_ok:
                breakout_index, boundary = idx, bound
        if breakout_index is None:
            continue
        current = bars[-1].close
        acceptance = side * (current - boundary) / atr
        if acceptance < -0.05 or acceptance > config.max_continuation_extension_atr:
            continue
        flow3, tail, opening = _side_flow(feature, side)
        if not _finite(flow3) or flow3 < config.min_flow_3m:
            continue
        if _finite(tail) and tail < config.min_tail_flow:
            continue
        burst = _burst(feature)
        if _finite(burst) and burst < config.min_participation_ratio:
            continue
        efficiency = feature.efficiency_60s if _finite(feature.efficiency_60s) else 0.0
        if efficiency < 0.015:
            continue
        breadth = sum(1 for value in normalized.values() if side * value >= config.peer_trend_threshold_atr)
        if breadth < 2:
            continue
        if market_side and market_side != side and abs(float(median(normalized.values()))) >= 0.5:
            continue
        if _finite(feature.premium_z) and side * feature.premium_z > config.max_crowded_premium_z:
            continue
        post_break = bars[max(0, breakout_index - 2):]
        raw_stop = min(x.low for x in post_break) - config.stop_buffer_atr * atr if side > 0 else max(x.high for x in post_break) + config.stop_buffer_atr * atr
        geometry = _stop_and_target(side=side, entry=current, raw_stop=raw_stop, atr=atr, target_r=config.continuation_target_r, config=config)
        if geometry is None:
            continue
        stop, target = geometry
        oi_build = feature.oi_change_15m if _finite(feature.oi_change_15m) else 0.0
        depth = _depth_support(feature, side)
        score = (1.15 * min(2.0, side * trend15) + 0.45 * min(1.5, max(0.0, side * trend5)) + 0.75 * min(1.0, max(0.0, flow3)) + 0.20 * min(2.0, max(0.0, burst if _finite(burst) else 1.0)) + 0.12 * max(-1.0, min(1.0, depth)) + 0.18 * (breadth - 1) + (0.25 if oi_build > 0.0 else -0.08 if oi_build < -0.002 else 0.0) - 0.18 * max(0.0, acceptance - 0.55))
        if score < config.min_route_score:
            continue
        candidates.append(RouteDecision(symbol, FLOW_RELEASE_CONTINUATION, side, score, current, stop, target, bars[breakout_index].ts_event, ("FRESH_RANGE_RELEASE", "FLOW_ACCEPTANCE", "CROSS_ASSET_PARTICIPATION"), {"atr": atr, "trend15_atr": trend15, "trend5_atr": trend5, "boundary": boundary, "acceptance_atr": acceptance, "flow3_side": flow3, "tail_side": tail, "opening_side": opening, "burst": burst, "efficiency": efficiency, "breadth": breadth, "oi_change_15m": oi_build, "premium_z": feature.premium_z, "depth_support": depth}))
    return max(candidates, key=lambda x: x.score, default=None)


def _reversal(symbol: str, bars: Sequence[BarObservation], feature: FeatureObservation, normalized: Mapping[str, float], config: RouteConfig) -> RouteDecision | None:
    lookback = config.reversal_liquidity_lookback
    if len(bars) < lookback + config.reversal_fresh_minutes + 5:
        return None
    atr = _atr(bars, config.atr_period)
    if not _finite(atr) or atr <= 0.0:
        return None
    current = bars[-1].close
    candidates: list[RouteDecision] = []
    for side in (1, -1):
        sweep_idx: int | None = None
        boundary = math.nan
        extreme = math.nan
        for age in range(config.reversal_fresh_minutes - 1, -1, -1):
            idx = len(bars) - 1 - age
            prior_start = idx - lookback
            if prior_start < 0:
                continue
            prior = bars[prior_start:idx]
            if side > 0:
                bound = min(x.low for x in prior)
                raided = bars[idx].low < bound - 0.03 * atr and bars[idx].close > bound
                ext = bars[idx].low
            else:
                bound = max(x.high for x in prior)
                raided = bars[idx].high > bound + 0.03 * atr and bars[idx].close < bound
                ext = bars[idx].high
            if raided:
                sweep_idx, boundary, extreme = idx, bound, ext
        if sweep_idx is None:
            continue
        response = side * (current - boundary) / atr
        if response < config.min_response_atr:
            continue
        flow3, tail, _ = _side_flow(feature, side)
        efficiency = feature.efficiency_60s if _finite(feature.efficiency_60s) else math.nan
        absorption = feature.absorption_60s if _finite(feature.absorption_60s) else math.nan
        absorption_turn = _finite(absorption) and absorption >= 0.20 and (not _finite(efficiency) or efficiency <= 0.20)
        flow_turn = _finite(flow3) and flow3 >= config.min_flow_3m
        if not (flow_turn or absorption_turn):
            continue
        if _finite(tail) and tail < -0.18:
            continue
        sweep_dir = -side
        peer_confirm = sum(1 for other, value in normalized.items() if other != symbol and sweep_dir * value >= 0.35)
        if peer_confirm >= 2:
            continue
        oi = feature.oi_change_15m if _finite(feature.oi_change_15m) else 0.0
        premium = feature.premium_z if _finite(feature.premium_z) else 0.0
        depth = _depth_support(feature, side)
        burst = _burst(feature)
        stress = ((abs(premium) >= 0.8 and sweep_dir * premium > 0.0) or oi <= 0.0 or absorption_turn or (_finite(burst) and burst >= 1.35))
        if not stress:
            continue
        raw_stop = extreme - side * config.stop_buffer_atr * atr
        geometry = _stop_and_target(side=side, entry=current, raw_stop=raw_stop, atr=atr, target_r=config.reversal_target_r, config=config)
        if geometry is None:
            continue
        stop, target = geometry
        prior = bars[sweep_idx - lookback:sweep_idx]
        opposite = max(x.high for x in prior) if side > 0 else min(x.low for x in prior)
        capped_target = min(target, opposite) if side > 0 else max(target, opposite)
        achieved_r = side * (capped_target - current) / max(1e-12, side * (current - stop))
        if achieved_r < 1.05:
            continue
        target = capped_target
        score = (1.25 + 0.75 * min(1.5, response) + 0.65 * min(1.0, max(0.0, flow3 if _finite(flow3) else 0.0)) + (0.45 if absorption_turn else 0.0) + (0.25 if oi <= 0.0 else 0.0) + 0.18 * min(2.0, abs(premium)) + 0.15 * max(-1.0, min(1.0, depth)) + 0.20 * (2 - peer_confirm))
        if score < config.min_route_score:
            continue
        candidates.append(RouteDecision(symbol, FAILED_AUCTION_REVERSAL, side, score, current, stop, target, bars[sweep_idx].ts_event, ("LIQUIDITY_RAID", "FAILED_ACCEPTANCE", "FLOW_OR_ABSORPTION_TURN"), {"atr": atr, "boundary": boundary, "sweep_extreme": extreme, "response_atr": response, "flow3_side": flow3, "tail_side": tail, "absorption": absorption, "efficiency": efficiency, "peer_confirm": peer_confirm, "oi_change_15m": oi, "premium_z": premium, "depth_support": depth, "achieved_r": achieved_r}))
    return max(candidates, key=lambda x: x.score, default=None)


def _catchup(symbol: str, bars: Sequence[BarObservation], feature: FeatureObservation, normalized: Mapping[str, float], market_side: int, market_median: float, config: RouteConfig) -> RouteDecision | None:
    if market_side == 0 or len(bars) < 35 or symbol not in normalized:
        return None
    side = market_side
    local15 = normalized[symbol]
    if side * market_median < config.catchup_market_atr:
        return None
    lag = side * (market_median - local15)
    if lag < config.catchup_lag_atr:
        return None
    atr = _atr(bars, config.atr_period)
    trigger3 = _return_atr(bars, 3, atr)
    trigger1 = _return_atr(bars, 1, atr)
    if not (_finite(trigger3) and side * trigger3 >= config.catchup_trigger_atr_3m):
        return None
    if _finite(trigger1) and side * trigger1 < -0.08:
        return None
    flow3, tail, opening = _side_flow(feature, side)
    if not _finite(flow3) or flow3 < max(config.min_flow_3m, 0.10):
        return None
    if _finite(tail) and tail < -0.05:
        return None
    burst = _burst(feature)
    if _finite(burst) and burst < config.min_participation_ratio:
        return None
    if _finite(feature.premium_z) and side * feature.premium_z > config.max_crowded_premium_z:
        return None
    current = bars[-1].close
    recent = bars[-7:]
    raw_stop = min(x.low for x in recent) - config.stop_buffer_atr * atr if side > 0 else max(x.high for x in recent) + config.stop_buffer_atr * atr
    geometry = _stop_and_target(side=side, entry=current, raw_stop=raw_stop, atr=atr, target_r=max(1.35, config.continuation_target_r - 0.20), config=config)
    if geometry is None:
        return None
    stop, target = geometry
    breadth = sum(1 for value in normalized.values() if side * value >= config.peer_trend_threshold_atr)
    if breadth < 2:
        return None
    oi = feature.oi_change_15m if _finite(feature.oi_change_15m) else 0.0
    score = (1.10 + 0.85 * min(1.5, side * market_median) + 0.65 * min(1.2, lag) + 0.60 * min(1.0, max(0.0, flow3)) + 0.35 * min(1.0, max(0.0, side * trigger3)) + 0.15 * (breadth - 1) + (0.20 if oi > 0.0 else 0.0))
    if score < config.min_route_score:
        return None
    return RouteDecision(symbol, CROSS_ASSET_CATCHUP, side, score, current, stop, target, bars[-3].ts_event, ("MARKET_REPRICED", "LOCAL_LAG", "LOCAL_FLOW_TRANSITION"), {"atr": atr, "market_median_atr": market_median, "local15_atr": local15, "lag_atr": lag, "trigger3_atr": trigger3, "flow3_side": flow3, "tail_side": tail, "opening_side": opening, "burst": burst, "breadth": breadth, "oi_change_15m": oi, "premium_z": feature.premium_z})


def classify_symbol(symbol: str, bars: Sequence[BarObservation], feature: FeatureObservation, bars_by_symbol: Mapping[str, Sequence[BarObservation]], normalized: Mapping[str, float], market_side: int, market_median: float, config: RouteConfig) -> RouteDecision:
    if not feature.ready:
        return RouteDecision(symbol, UNRESOLVED, 0, 0.0, math.nan, math.nan, math.nan, 0, ("FEATURE_NOT_READY",))
    if len(bars) < max(config.atr_period + 16, config.reversal_liquidity_lookback + 5):
        return RouteDecision(symbol, UNRESOLVED, 0, 0.0, math.nan, math.nan, math.nan, 0, ("INSUFFICIENT_HISTORY",))
    options = [_continuation(symbol, bars, feature, normalized, market_side, config), _reversal(symbol, bars, feature, normalized, config), _catchup(symbol, bars, feature, normalized, market_side, market_median, config)]
    options = [item for item in options if item is not None]
    if not options:
        return RouteDecision(symbol, UNRESOLVED, 0, 0.0, bars[-1].close, math.nan, math.nan, bars[-1].ts_event, ("NO_CAUSAL_SCENARIO",))
    options.sort(key=lambda item: (-item.score, item.state))
    if len(options) >= 2 and options[0].side != options[1].side and options[0].score - options[1].score < config.ambiguity_score_gap:
        return RouteDecision(symbol, UNRESOLVED, 0, 0.0, bars[-1].close, math.nan, math.nan, bars[-1].ts_event, ("LOCAL_SCENARIO_CONFLICT",), {"top_score": options[0].score, "second_score": options[1].score})
    return options[0]


def route_universe(*, bars_by_symbol: Mapping[str, Sequence[BarObservation]], features_by_symbol: Mapping[str, FeatureObservation], config: RouteConfig) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    normalized, market_side, market_median = _peer_state(bars_by_symbol, config)
    decisions: dict[str, RouteDecision] = {}
    for symbol in sorted(bars_by_symbol):
        decisions[symbol] = classify_symbol(symbol, bars_by_symbol[symbol], features_by_symbol[symbol], bars_by_symbol, normalized, market_side, market_median, config)
    actionable = [item for item in decisions.values() if item.actionable]
    actionable.sort(key=lambda item: (-item.score, item.symbol, item.state))
    if not actionable:
        return None, decisions
    if len(actionable) >= 2 and actionable[0].side != actionable[1].side and actionable[0].score - actionable[1].score < config.ambiguity_score_gap:
        return None, decisions
    return actionable[0], decisions


def thesis_invalidated(*, state: str, side: int, bars: Sequence[BarObservation], feature: FeatureObservation, diagnostics: Mapping[str, float | int | str], config: RouteConfig) -> tuple[bool, str]:
    atr = _atr(bars, config.atr_period)
    if not _finite(atr) or len(bars) < 6:
        return False, "NOT_READY"
    ret5 = _return_atr(bars, 5, atr)
    flow3, tail, _ = _side_flow(feature, side)
    if state in (FLOW_RELEASE_CONTINUATION, CROSS_ASSET_CATCHUP):
        price_failed = _finite(ret5) and side * ret5 < -0.45
        flow_failed = _finite(flow3) and flow3 < -0.14
        tail_failed = _finite(tail) and tail < -0.25
        if price_failed and (flow_failed or tail_failed):
            return True, "PROSPECTIVE_CONTINUATION_FAILED"
        boundary = diagnostics.get("boundary")
        if isinstance(boundary, (int, float)) and _finite(float(boundary)) and side * (bars[-1].close - float(boundary)) < -0.22 * atr and flow_failed:
            return True, "RELEASE_LOST"
    elif state == FAILED_AUCTION_REVERSAL:
        boundary = diagnostics.get("boundary")
        if isinstance(boundary, (int, float)) and _finite(float(boundary)):
            crossed_back = side * (bars[-1].close - float(boundary)) < -0.12 * atr
            if crossed_back and _finite(flow3) and flow3 < -0.12:
                return True, "FAILED_AUCTION_REACCEPTED"
    return False, "HEALTHY"


__all__ = ["BarObservation", "FeatureObservation", "RouteConfig", "RouteDecision", "UNRESOLVED", "FLOW_RELEASE_CONTINUATION", "FAILED_AUCTION_REVERSAL", "CROSS_ASSET_CATCHUP", "route_universe", "thesis_invalidated"]

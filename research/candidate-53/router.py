"""Candidate 53 sparse causal router.

The v1 micro-signal router was deliberately retired after it produced 578 trades
in one development week with ~8% wins.  v2 trades only completed auction chains:

1. external range release -> material impulse -> real pullback -> renewed flow;
2. release -> apparent failure back inside -> later reacceptance in the original
   direction -> renewed initiative.

Targets are solved for *net* R after configured adverse entry/exit slippage,
fees and funding reserve.  All observations are completed at or before decision
time.  No result-dependent symbol thresholds exist.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from statistics import median
from typing import Mapping, Sequence

UNRESOLVED = "UNRESOLVED"
IMPULSE_RETRACE_CONTINUATION = "IMPULSE_RETRACE_CONTINUATION"
FAILED_REVERSAL_CONTINUATION = "FAILED_REVERSAL_CONTINUATION"


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
    min_impulse_atr_continuation: float = 1.25
    min_impulse_atr_reversal: float = 1.05
    min_response_atr: float = 0.12
    min_participation_ratio: float = 1.00
    min_route_score: float = 3.00
    ambiguity_score_gap: float = 0.20
    continuation_target_r: float = 1.50
    reversal_target_r: float = 1.50
    external_lookback: int = 45
    breakout_max_age: int = 22
    min_breakout_age: int = 3
    impulse_origin_lookback: int = 12
    retrace_min_fraction: float = 0.24
    retrace_max_fraction: float = 0.78
    min_recovery_atr: float = 0.10
    min_flow_3m: float = 0.10
    min_tail_flow: float = -0.02
    min_efficiency: float = 0.025
    max_premium_z: float = 3.0
    min_stop_atr: float = 0.38
    max_stop_atr: float = 1.65
    stop_buffer_atr: float = 0.08
    max_target_atr: float = 2.75
    failure_inside_atr: float = 0.08
    reaccept_atr: float = 0.08
    fee_rate_each_side: float = 0.00075
    slippage_rate_each_side: float = 0.00025
    funding_reserve_rate: float = 0.00010


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


def _finite(x: float) -> bool:
    return math.isfinite(float(x))


def _atr(bars: Sequence[BarObservation], period: int) -> float:
    if len(bars) < period + 1:
        return math.nan
    vals: list[float] = []
    for i in range(len(bars) - period, len(bars)):
        b, p = bars[i], bars[i - 1].close
        vals.append(max(b.high - b.low, abs(b.high - p), abs(b.low - p)))
    return sum(vals) / len(vals) if vals else math.nan


def _return_atr(bars: Sequence[BarObservation], lookback: int, atr: float) -> float:
    if len(bars) <= lookback or not _finite(atr) or atr <= 0:
        return math.nan
    return (bars[-1].close - bars[-1 - lookback].close) / atr


def _flow(feature: FeatureObservation, side: int) -> tuple[float, float]:
    f3 = feature.flow_3m if _finite(feature.flow_3m) else feature.flow_60s
    tail = feature.flow_15s if _finite(feature.flow_15s) else feature.flow_60s
    return side * f3, side * tail


def _burst(feature: FeatureObservation) -> float:
    vals = [x for x in (feature.notional_burst, feature.notional_open_10s_burst, feature.trade_count_burst) if _finite(x)]
    return max(vals) if vals else math.nan


def _peer_state(bars_by_symbol: Mapping[str, Sequence[BarObservation]], cfg: RouteConfig) -> tuple[dict[str, float], int, float]:
    values: dict[str, float] = {}
    for symbol, bars in bars_by_symbol.items():
        atr = _atr(bars, cfg.atr_period)
        r = _return_atr(bars, 15, atr)
        if _finite(r):
            values[symbol] = r
    if not values:
        return {}, 0, 0.0
    med = float(median(values.values()))
    side = 1 if med >= 0.25 else (-1 if med <= -0.25 else 0)
    return values, side, med


def _cost_aware_geometry(*, side: int, entry: float, raw_stop: float, atr: float, net_target_r: float, cfg: RouteConfig) -> tuple[float, float, float] | None:
    """Return (stop_reference, target_reference, conservative planned_loss/unit).

    Geometry mirrors the execution shell's adverse entry/stop assumptions and
    additionally solves target distance so the requested R remains after an
    adverse exit fill, both-side fees and funding reserve.
    """
    if not (_finite(entry) and _finite(raw_stop) and _finite(atr)) or atr <= 0:
        return None
    raw_risk = side * (entry - raw_stop)
    if raw_risk <= 0:
        return None
    risk = max(raw_risk, cfg.min_stop_atr * atr)
    if risk > cfg.max_stop_atr * atr:
        return None
    stop = entry - side * risk
    f, s, u = cfg.fee_rate_each_side, cfg.slippage_rate_each_side, cfg.funding_reserve_rate
    if side > 0:
        entry_exec = entry * (1 + s)
        stop_exec = stop * (1 - s)
        loss = (entry_exec - stop_exec) + f * (entry_exec + stop_exec) + u * entry
        target_exec = (entry_exec * (1 + f) + net_target_r * loss + u * entry) / (1 - f)
        target = target_exec / (1 - s)
    else:
        entry_exec = entry * (1 - s)
        stop_exec = stop * (1 + s)
        loss = (stop_exec - entry_exec) + f * (entry_exec + stop_exec) + u * entry
        target_exec = (entry_exec * (1 - f) - net_target_r * loss - u * entry) / (1 + f)
        target = target_exec / (1 + s)
    if not (_finite(loss) and loss > 0 and _finite(target)):
        return None
    if side * (target - entry) <= 0 or abs(target - entry) > cfg.max_target_atr * atr:
        return None
    return stop, target, loss


def _find_breakouts(bars: Sequence[BarObservation], cfg: RouteConfig, side: int, atr: float):
    last = len(bars) - 1
    for age in range(cfg.min_breakout_age, cfg.breakout_max_age + 1):
        idx = last - age
        if idx <= cfg.external_lookback + cfg.impulse_origin_lookback:
            continue
        prior = bars[idx - cfg.external_lookback:idx]
        boundary = max(b.high for b in prior) if side > 0 else min(b.low for b in prior)
        crossed = bars[idx].close > boundary + 0.05 * atr if side > 0 else bars[idx].close < boundary - 0.05 * atr
        prior_inside = bars[idx - 1].close <= boundary if side > 0 else bars[idx - 1].close >= boundary
        if not (crossed and prior_inside):
            continue
        origin_slice = bars[idx - cfg.impulse_origin_lookback:idx + 1]
        origin = min(b.low for b in origin_slice) if side > 0 else max(b.high for b in origin_slice)
        impulse = side * (bars[idx].close - origin)
        if impulse < cfg.min_impulse_atr_continuation * atr:
            continue
        yield idx, boundary, origin, impulse


def _impulse_retrace(symbol: str, bars: Sequence[BarObservation], feature: FeatureObservation, peers: Mapping[str, float], market_side: int, cfg: RouteConfig) -> RouteDecision | None:
    atr = _atr(bars, cfg.atr_period)
    if not _finite(atr) or atr <= 0:
        return None
    current = bars[-1]
    best: RouteDecision | None = None
    for side in (1, -1):
        for idx, boundary, origin, impulse in _find_breakouts(bars, cfg, side, atr):
            after = bars[idx + 1:]
            if len(after) < 2:
                continue
            retrace_extreme = min(b.low for b in after) if side > 0 else max(b.high for b in after)
            retrace = side * (bars[idx].close - retrace_extreme)
            frac = retrace / impulse if impulse > 0 else math.inf
            if frac < cfg.retrace_min_fraction or frac > cfg.retrace_max_fraction:
                continue
            # Pullback must preserve the old external boundary; this avoids trading
            # an already failed breakout as if it were a routine retest.
            if side * (retrace_extreme - boundary) < -0.08 * atr:
                continue
            recovery = side * (current.close - retrace_extreme) / atr
            if recovery < cfg.min_recovery_atr or side * (current.close - current.open) <= 0:
                continue
            f3, tail = _flow(feature, side)
            if not _finite(f3) or f3 < cfg.min_flow_3m or (_finite(tail) and tail < cfg.min_tail_flow):
                continue
            eff = feature.efficiency_60s if _finite(feature.efficiency_60s) else 0.0
            if eff < cfg.min_efficiency:
                continue
            burst = _burst(feature)
            if _finite(burst) and burst < cfg.min_participation_ratio:
                continue
            breadth = sum(1 for x in peers.values() if side * x >= 0.20)
            if breadth < 2:
                continue
            if market_side and market_side != side:
                continue
            if _finite(feature.premium_z) and side * feature.premium_z > cfg.max_premium_z:
                continue
            raw_stop = retrace_extreme - side * cfg.stop_buffer_atr * atr
            geometry = _cost_aware_geometry(side=side, entry=current.close, raw_stop=raw_stop, atr=atr, net_target_r=cfg.continuation_target_r, cfg=cfg)
            if geometry is None:
                continue
            stop, target, planned_loss = geometry
            extension = side * (current.close - bars[idx].close) / atr
            if extension > 0.75:
                continue
            oi = feature.oi_change_15m if _finite(feature.oi_change_15m) else 0.0
            score = 1.2 + min(2.0, impulse / atr) * 0.75 + min(1.5, recovery) * 0.55 + min(1.0, max(0.0, f3)) * 0.65 + 0.18 * (breadth - 1) + (0.18 if oi > 0 else 0.0) - max(0.0, extension) * 0.2
            if score < cfg.min_route_score:
                continue
            d = RouteDecision(symbol, IMPULSE_RETRACE_CONTINUATION, side, score, current.close, stop, target, bars[idx].ts_event, ("EXTERNAL_RELEASE", "REAL_PULLBACK", "RENEWED_INITIATIVE"), {"atr": atr, "boundary": boundary, "origin": origin, "impulse_atr": impulse / atr, "retrace_fraction": frac, "retrace_extreme": retrace_extreme, "recovery_atr": recovery, "flow3_side": f3, "tail_side": tail, "efficiency": eff, "burst": burst, "breadth": breadth, "oi_change_15m": oi, "planned_loss_per_unit": planned_loss})
            if best is None or d.score > best.score:
                best = d
    return best


def _failed_reversal_continuation(symbol: str, bars: Sequence[BarObservation], feature: FeatureObservation, peers: Mapping[str, float], cfg: RouteConfig) -> RouteDecision | None:
    atr = _atr(bars, cfg.atr_period)
    if not _finite(atr) or atr <= 0:
        return None
    current = bars[-1]
    best: RouteDecision | None = None
    for side in (1, -1):
        for idx, boundary, origin, impulse in _find_breakouts(bars, cfg, side, atr):
            after = list(bars[idx + 1:])
            if len(after) < 4:
                continue
            failed_indices = [j for j, b in enumerate(after[:-1]) if side * (b.close - boundary) <= -cfg.failure_inside_atr * atr]
            if not failed_indices:
                continue
            failure_j = failed_indices[0]
            # Original direction must be reaccepted only after a visible failure.
            if side * (current.close - boundary) < cfg.reaccept_atr * atr:
                continue
            since_failure = after[failure_j + 1:]
            if len(since_failure) < 2:
                continue
            if not any(side * (b.close - boundary) >= cfg.reaccept_atr * atr for b in since_failure[:-1]):
                continue
            # Current minute is a defended outside close, not the first bar back out.
            prev = bars[-2]
            if side * (prev.close - boundary) < -0.03 * atr or side * (current.close - current.open) <= 0:
                continue
            f3, tail = _flow(feature, side)
            if not _finite(f3) or f3 < max(cfg.min_flow_3m, 0.12) or (_finite(tail) and tail < 0.0):
                continue
            eff = feature.efficiency_60s if _finite(feature.efficiency_60s) else 0.0
            if eff < max(cfg.min_efficiency, 0.04):
                continue
            burst = _burst(feature)
            if _finite(burst) and burst < cfg.min_participation_ratio:
                continue
            breadth = sum(1 for x in peers.values() if side * x >= 0.15)
            if breadth < 2:
                continue
            failure_slice = after[failure_j:max(failure_j + 1, len(after) - 1)]
            failure_extreme = min(b.low for b in failure_slice) if side > 0 else max(b.high for b in failure_slice)
            raw_stop = failure_extreme - side * cfg.stop_buffer_atr * atr
            geometry = _cost_aware_geometry(side=side, entry=current.close, raw_stop=raw_stop, atr=atr, net_target_r=cfg.reversal_target_r, cfg=cfg)
            if geometry is None:
                continue
            stop, target, planned_loss = geometry
            oi = feature.oi_change_15m if _finite(feature.oi_change_15m) else 0.0
            score = 1.25 + min(2.2, impulse / atr) * 0.65 + min(1.0, max(0.0, f3)) * 0.75 + min(0.5, eff) * 0.8 + 0.18 * (breadth - 1) + (0.22 if oi > 0 else 0.0)
            if score < cfg.min_route_score:
                continue
            d = RouteDecision(symbol, FAILED_REVERSAL_CONTINUATION, side, score, current.close, stop, target, bars[idx].ts_event, ("EXTERNAL_RELEASE", "REVERSAL_ATTEMPT_FAILED", "ORIGINAL_DIRECTION_REACCEPTED"), {"atr": atr, "boundary": boundary, "origin": origin, "impulse_atr": impulse / atr, "failure_extreme": failure_extreme, "flow3_side": f3, "tail_side": tail, "efficiency": eff, "burst": burst, "breadth": breadth, "oi_change_15m": oi, "planned_loss_per_unit": planned_loss})
            if best is None or d.score > best.score:
                best = d
    return best


def classify_symbol(symbol: str, bars: Sequence[BarObservation], feature: FeatureObservation, bars_by_symbol: Mapping[str, Sequence[BarObservation]], peers: Mapping[str, float], market_side: int, market_median: float, cfg: RouteConfig) -> RouteDecision:
    if not feature.ready or len(bars) < cfg.external_lookback + cfg.breakout_max_age + cfg.impulse_origin_lookback + 5:
        return RouteDecision(symbol, UNRESOLVED, 0, 0.0, bars[-1].close if bars else math.nan, math.nan, math.nan, 0, ("NOT_READY",))
    options = [x for x in (_impulse_retrace(symbol, bars, feature, peers, market_side, cfg), _failed_reversal_continuation(symbol, bars, feature, peers, cfg)) if x is not None]
    if not options:
        return RouteDecision(symbol, UNRESOLVED, 0, 0.0, bars[-1].close, math.nan, math.nan, bars[-1].ts_event, ("NO_COMPLETE_AUCTION_CHAIN",))
    options.sort(key=lambda x: (-x.score, x.state))
    if len(options) > 1 and options[0].side != options[1].side and options[0].score - options[1].score < cfg.ambiguity_score_gap:
        return RouteDecision(symbol, UNRESOLVED, 0, 0.0, bars[-1].close, math.nan, math.nan, bars[-1].ts_event, ("LOCAL_CONFLICT",))
    return options[0]


def route_universe(*, bars_by_symbol: Mapping[str, Sequence[BarObservation]], features_by_symbol: Mapping[str, FeatureObservation], config: RouteConfig) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    peers, market_side, market_median = _peer_state(bars_by_symbol, config)
    decisions = {s: classify_symbol(s, bars_by_symbol[s], features_by_symbol[s], bars_by_symbol, peers, market_side, market_median, config) for s in sorted(bars_by_symbol)}
    actionable = sorted((x for x in decisions.values() if x.actionable), key=lambda x: (-x.score, x.symbol, x.state))
    if not actionable:
        return None, decisions
    if len(actionable) > 1 and actionable[0].side != actionable[1].side and actionable[0].score - actionable[1].score < config.ambiguity_score_gap:
        return None, decisions
    return actionable[0], decisions


def thesis_invalidated(*, state: str, side: int, bars: Sequence[BarObservation], feature: FeatureObservation, diagnostics: Mapping[str, float | int | str], config: RouteConfig) -> tuple[bool, str]:
    """Only a hard causal reacceptance closes early; micro-flow noise does not."""
    atr = _atr(bars, config.atr_period)
    if not _finite(atr) or len(bars) < 3:
        return False, "NOT_READY"
    boundary = diagnostics.get("boundary")
    if isinstance(boundary, (int, float)) and _finite(float(boundary)):
        if side * (bars[-1].close - float(boundary)) < -0.28 * atr:
            f3, _ = _flow(feature, side)
            if _finite(f3) and f3 < -0.15:
                return True, "EXTERNAL_RELEASE_REJECTED"
    return False, "HEALTHY"


__all__ = ["BarObservation", "FeatureObservation", "RouteConfig", "RouteDecision", "UNRESOLVED", "IMPULSE_RETRACE_CONTINUATION", "FAILED_REVERSAL_CONTINUATION", "route_universe", "thesis_invalidated"]

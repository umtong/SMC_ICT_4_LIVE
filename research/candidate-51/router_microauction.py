"""Real-data micro-auction states mined from the public Smallfish system.

The public Smallfish backtest manufactures order books and trade tapes from a
completed candle.  Its reported microstructure performance is therefore not
used as evidence.  This module reuses only two causal ideas and measures them on
checksum-verified Binance Futures aggTrades + bookDepth observations:

1. Efficient liquidity consumption: aggressive flow, trade/notional urgency,
   directional price efficiency, and non-opposing depth produce an accepted
   break from a completed balance.
2. Absorption: aggressive flow and urgency fail to move price, a visible
   liquidity extreme is swept and reclaimed, and the defending side remains in
   the depth snapshot.

The two states are mutually distinct.  They are not combined as an indicator
vote.  Every decision has an auction objective and a structural invalidation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Sequence

from router_picasso import BarObservation, _atr

CONTINUATION_STATE = "REAL_FLOW_EFFICIENT_BALANCE_BREAK"
ABSORPTION_STATE = "REAL_FLOW_ABSORPTION_RECLAIM"
SMA_OFFSET_STATE = CONTINUATION_STATE
UNRESOLVED = "UNRESOLVED"
_EPS = 1e-12
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}


@dataclass(frozen=True, slots=True)
class FeatureObservation:
    observed_time_ns: int
    ready: bool = False
    flow_15s: float = math.nan
    flow_60s: float = math.nan
    flow_3m: float = math.nan
    notional_burst: float = math.nan
    trade_count_burst: float = math.nan
    ret_60s_bps: float = math.nan
    path_60s_bps: float = math.nan
    efficiency_60s: float = math.nan
    flow_price_alignment_60s: float = math.nan
    absorption_60s: float = math.nan
    depth_imbalance_1: float = math.nan
    depth_imbalance_2: float = math.nan
    bid_depth_change_1_1m: float = math.nan
    ask_depth_change_1_1m: float = math.nan
    bid_depth_change_1_5m: float = math.nan
    ask_depth_change_1_5m: float = math.nan
    depth_snapshot_age_seconds: float = math.nan


@dataclass(frozen=True, slots=True)
class RouteConfig:
    # Fields required by the reused Candidate 35 execution shell.
    atr_period: int = 30
    min_impulse_atr_continuation: float = 0.20
    min_impulse_atr_reversal: float = 1.05
    min_response_atr: float = 0.08
    min_participation_ratio: float = 0.85
    min_route_score: float = 3.20
    ambiguity_score_gap: float = 0.10
    continuation_target_r: float = 2.00
    reversal_target_r: float = 1.65

    microauction_mode: str = "continuation"  # continuation | absorption | unified
    microauction_atr_period: int = 30
    microauction_balance_lookback: int = 10
    microauction_stop_buffer_atr: float = 0.15
    microauction_min_reward_r: float = 1.25

    # Efficient liquidity-consumption state.
    continuation_flow_min: float = 0.20
    continuation_flow_3m_min: float = 0.10
    continuation_notional_burst_min: float = 1.50
    continuation_trade_burst_min: float = 1.20
    continuation_efficiency_min: float = 0.35
    continuation_displacement_atr_min: float = 0.20
    continuation_depth_support_min: float = -0.10
    continuation_opposite_depth_thinning_min: float = 0.05
    continuation_same_depth_growth_min: float = 0.05
    continuation_measured_move_multiple: float = 1.00

    # Failed-auction absorption state.
    absorption_flow_min: float = 0.25
    absorption_flow_15s_min: float = 0.15
    absorption_flow_3m_min: float = 0.05
    absorption_notional_burst_min: float = 1.50
    absorption_trade_burst_min: float = 1.20
    absorption_efficiency_max: float = 0.25
    absorption_strength_min: float = 0.15
    absorption_alignment_max_bps: float = 0.50
    absorption_sweep_buffer_atr: float = 0.05
    absorption_depth_defense_min: float = 0.00
    absorption_same_depth_growth_min: float = 0.00

    # Legacy fields retained only so generic config materializers can discard or
    # ignore them without constructor collisions.
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


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(value)) for value in values)


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


def _geometry(
    *,
    symbol: str,
    state: str,
    side: int,
    entry: float,
    stop: float,
    objective: float,
    episode_ts: int,
    score: float,
    reasons: tuple[str, ...],
    diagnostics: dict[str, float | int | str],
    min_reward_r: float,
) -> RouteDecision:
    if side > 0:
        valid = 0.0 < stop < entry < objective
        reward = objective - entry
    else:
        valid = 0.0 < objective < entry < stop
        reward = entry - objective
    risk = abs(entry - stop)
    reward_r = reward / risk if risk > _EPS else math.nan
    diagnostics.update({
        "risk_distance": risk,
        "reward_distance": reward,
        "reward_r": reward_r,
    })
    if not valid or not math.isfinite(reward_r):
        return _unresolved(symbol, f"{state}_GEOMETRY_INVALID", episode_ts, diagnostics)
    if reward_r < float(min_reward_r):
        return _unresolved(symbol, f"{state}_REWARD_SPACE_EXHAUSTED", episode_ts, diagnostics)
    return RouteDecision(
        symbol=symbol,
        state=state,
        side=side,
        score=float(score),
        entry_reference=float(entry),
        stop_reference=float(stop),
        objective_reference=float(objective),
        episode_ts=int(episode_ts),
        reasons=reasons,
        diagnostics=diagnostics,
    )


def _base_inputs(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig,
) -> tuple[BarObservation, Sequence[BarObservation], float] | RouteDecision:
    if not bars:
        return _unresolved(symbol, "MICROAUCTION_NO_MINUTE_BARS")
    latest_ts = int(bars[-1].ts_event)
    if int(feature.observed_time_ns) > latest_ts:
        return _unresolved(symbol, "MICROAUCTION_FUTURE_FEATURE_REJECTED", latest_ts)
    if not feature.ready:
        return _unresolved(symbol, "MICROAUCTION_FEATURE_NOT_READY", latest_ts)
    lookback = int(config.microauction_balance_lookback)
    minimum = max(lookback + 1, int(config.microauction_atr_period) + 2)
    if len(bars) < minimum:
        return _unresolved(symbol, "MICROAUCTION_HISTORY_NOT_READY", latest_ts, {
            "bars": len(bars), "minimum": minimum,
        })
    atr_values = _atr(bars, int(config.microauction_atr_period))
    atr = float(atr_values[-1]) if atr_values else math.nan
    if not math.isfinite(atr) or atr <= 0.0:
        return _unresolved(symbol, "MICROAUCTION_ATR_NOT_READY", latest_ts)
    return bars[-1], bars[-lookback - 1:-1], atr


def classify_continuation(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    prepared = _base_inputs(symbol, bars, feature, config)
    if isinstance(prepared, RouteDecision):
        return prepared
    event, prior, atr = prepared
    latest_ts = int(event.ts_event)
    values = (
        feature.flow_60s,
        feature.flow_3m,
        feature.notional_burst,
        feature.trade_count_burst,
        feature.ret_60s_bps,
        feature.efficiency_60s,
        feature.depth_imbalance_1,
        feature.bid_depth_change_1_1m,
        feature.ask_depth_change_1_1m,
    )
    if not _finite(*values):
        return _unresolved(symbol, "CONTINUATION_NONFINITE_INPUT", latest_ts)

    flow = float(feature.flow_60s)
    if abs(flow) < float(config.continuation_flow_min):
        return _unresolved(symbol, "CONTINUATION_FLOW_TOO_WEAK", latest_ts)
    side = 1 if flow > 0.0 else -1
    diagnostics: dict[str, float | int | str] = {
        "family": CONTINUATION_STATE,
        "side": side,
        "flow_60s": flow,
        "flow_3m": float(feature.flow_3m),
        "notional_burst": float(feature.notional_burst),
        "trade_count_burst": float(feature.trade_count_burst),
        "ret_60s_bps": float(feature.ret_60s_bps),
        "efficiency_60s": float(feature.efficiency_60s),
        "depth_imbalance_1": float(feature.depth_imbalance_1),
        "bid_depth_change_1_1m": float(feature.bid_depth_change_1_1m),
        "ask_depth_change_1_1m": float(feature.ask_depth_change_1_1m),
        "depth_snapshot_age_seconds": float(feature.depth_snapshot_age_seconds),
        "atr": atr,
    }
    if side * float(feature.flow_3m) < float(config.continuation_flow_3m_min):
        return _unresolved(symbol, "CONTINUATION_FLOW_NOT_PERSISTENT", latest_ts, diagnostics)
    if float(feature.notional_burst) < float(config.continuation_notional_burst_min):
        return _unresolved(symbol, "CONTINUATION_NOTIONAL_NOT_URGENT", latest_ts, diagnostics)
    if float(feature.trade_count_burst) < float(config.continuation_trade_burst_min):
        return _unresolved(symbol, "CONTINUATION_TRADE_RATE_NOT_URGENT", latest_ts, diagnostics)
    if float(feature.efficiency_60s) < float(config.continuation_efficiency_min):
        return _unresolved(symbol, "CONTINUATION_PRICE_NOT_EFFICIENT", latest_ts, diagnostics)
    if side * float(feature.ret_60s_bps) <= 0.0:
        return _unresolved(symbol, "CONTINUATION_FLOW_PRICE_DISAGREE", latest_ts, diagnostics)

    displacement_atr = side * (float(event.close) - float(event.open)) / atr
    diagnostics["displacement_atr"] = displacement_atr
    if displacement_atr < float(config.continuation_displacement_atr_min):
        return _unresolved(symbol, "CONTINUATION_DISPLACEMENT_TOO_SMALL", latest_ts, diagnostics)

    prior_high = max(float(bar.high) for bar in prior)
    prior_low = min(float(bar.low) for bar in prior)
    balance_width = prior_high - prior_low
    diagnostics.update({
        "prior_balance_high": prior_high,
        "prior_balance_low": prior_low,
        "balance_width": balance_width,
    })
    accepted = float(event.close) > prior_high if side > 0 else float(event.close) < prior_low
    if not accepted:
        return _unresolved(symbol, "CONTINUATION_BALANCE_NOT_ACCEPTED", latest_ts, diagnostics)

    depth_support = side * float(feature.depth_imbalance_1)
    same_depth_change = (
        float(feature.bid_depth_change_1_1m)
        if side > 0 else float(feature.ask_depth_change_1_1m)
    )
    opposite_depth_change = (
        float(feature.ask_depth_change_1_1m)
        if side > 0 else float(feature.bid_depth_change_1_1m)
    )
    diagnostics.update({
        "depth_support": depth_support,
        "same_depth_change": same_depth_change,
        "opposite_depth_change": opposite_depth_change,
    })
    if depth_support < float(config.continuation_depth_support_min):
        return _unresolved(symbol, "CONTINUATION_DEPTH_STRONGLY_OPPOSES", latest_ts, diagnostics)
    depth_transition = (
        opposite_depth_change <= -float(config.continuation_opposite_depth_thinning_min)
        or same_depth_change >= float(config.continuation_same_depth_growth_min)
    )
    if not depth_transition:
        return _unresolved(symbol, "CONTINUATION_NO_DEPTH_TRANSITION", latest_ts, diagnostics)

    entry = float(event.close)
    buffer = float(config.microauction_stop_buffer_atr) * atr
    stop = float(event.low) - buffer if side > 0 else float(event.high) + buffer
    objective = (
        prior_high + float(config.continuation_measured_move_multiple) * balance_width
        if side > 0
        else prior_low - float(config.continuation_measured_move_multiple) * balance_width
    )
    score = (
        abs(flow) / max(float(config.continuation_flow_min), _EPS)
        + abs(float(feature.flow_3m)) / max(float(config.continuation_flow_3m_min), _EPS)
        + min(3.0, float(feature.notional_burst) / max(float(config.continuation_notional_burst_min), _EPS))
        + min(2.0, float(feature.trade_count_burst) / max(float(config.continuation_trade_burst_min), _EPS))
        + float(feature.efficiency_60s) / max(float(config.continuation_efficiency_min), _EPS)
        + min(3.0, max(0.0, displacement_atr))
    )
    return _geometry(
        symbol=symbol,
        state=CONTINUATION_STATE,
        side=side,
        entry=entry,
        stop=stop,
        objective=objective,
        episode_ts=latest_ts,
        score=score,
        reasons=(
            "ACTUAL_AGGRESSOR_FLOW_PERSISTENCE",
            "ACTUAL_NOTIONAL_AND_TRADE_URGENCY",
            "HIGH_PRICE_PATH_EFFICIENCY",
            "COMPLETED_BALANCE_ACCEPTANCE",
            "NON_OPPOSING_DEPTH_TRANSITION",
            "MEASURED_MOVE_AUCTION_OBJECTIVE",
        ),
        diagnostics=diagnostics,
        min_reward_r=float(config.microauction_min_reward_r),
    )


def classify_absorption(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    prepared = _base_inputs(symbol, bars, feature, config)
    if isinstance(prepared, RouteDecision):
        return prepared
    event, prior, atr = prepared
    latest_ts = int(event.ts_event)
    values = (
        feature.flow_15s,
        feature.flow_60s,
        feature.flow_3m,
        feature.notional_burst,
        feature.trade_count_burst,
        feature.ret_60s_bps,
        feature.efficiency_60s,
        feature.flow_price_alignment_60s,
        feature.absorption_60s,
        feature.depth_imbalance_1,
        feature.bid_depth_change_1_1m,
        feature.ask_depth_change_1_1m,
    )
    if not _finite(*values):
        return _unresolved(symbol, "ABSORPTION_NONFINITE_INPUT", latest_ts)

    aggression = float(feature.flow_60s)
    if abs(aggression) < float(config.absorption_flow_min):
        return _unresolved(symbol, "ABSORPTION_FLOW_TOO_WEAK", latest_ts)
    side = -1 if aggression > 0.0 else 1
    diagnostics: dict[str, float | int | str] = {
        "family": ABSORPTION_STATE,
        "side": side,
        "aggression_side": -side,
        "flow_15s": float(feature.flow_15s),
        "flow_60s": aggression,
        "flow_3m": float(feature.flow_3m),
        "notional_burst": float(feature.notional_burst),
        "trade_count_burst": float(feature.trade_count_burst),
        "ret_60s_bps": float(feature.ret_60s_bps),
        "efficiency_60s": float(feature.efficiency_60s),
        "flow_price_alignment_60s": float(feature.flow_price_alignment_60s),
        "absorption_60s": float(feature.absorption_60s),
        "depth_imbalance_1": float(feature.depth_imbalance_1),
        "bid_depth_change_1_1m": float(feature.bid_depth_change_1_1m),
        "ask_depth_change_1_1m": float(feature.ask_depth_change_1_1m),
        "depth_snapshot_age_seconds": float(feature.depth_snapshot_age_seconds),
        "atr": atr,
    }
    if (-side) * float(feature.flow_15s) < float(config.absorption_flow_15s_min):
        return _unresolved(symbol, "ABSORPTION_TERMINAL_FLOW_NOT_URGENT", latest_ts, diagnostics)
    if (-side) * float(feature.flow_3m) < float(config.absorption_flow_3m_min):
        return _unresolved(symbol, "ABSORPTION_FLOW_NOT_CROWDED", latest_ts, diagnostics)
    if float(feature.notional_burst) < float(config.absorption_notional_burst_min):
        return _unresolved(symbol, "ABSORPTION_NOTIONAL_NOT_URGENT", latest_ts, diagnostics)
    if float(feature.trade_count_burst) < float(config.absorption_trade_burst_min):
        return _unresolved(symbol, "ABSORPTION_TRADE_RATE_NOT_URGENT", latest_ts, diagnostics)
    if float(feature.efficiency_60s) > float(config.absorption_efficiency_max):
        return _unresolved(symbol, "ABSORPTION_PRICE_TOO_EFFICIENT", latest_ts, diagnostics)
    if float(feature.absorption_60s) < float(config.absorption_strength_min):
        return _unresolved(symbol, "ABSORPTION_STRENGTH_TOO_LOW", latest_ts, diagnostics)
    if float(feature.flow_price_alignment_60s) > float(config.absorption_alignment_max_bps):
        return _unresolved(symbol, "ABSORPTION_AGGRESSION_STILL_ADVANCING", latest_ts, diagnostics)

    prior_high = max(float(bar.high) for bar in prior)
    prior_low = min(float(bar.low) for bar in prior)
    midpoint = 0.5 * (prior_high + prior_low)
    sweep_buffer = float(config.absorption_sweep_buffer_atr) * atr
    if side > 0:
        swept = float(event.low) < prior_low - sweep_buffer
        reclaimed = float(event.close) > prior_low
        same_depth_change = float(feature.bid_depth_change_1_1m)
    else:
        swept = float(event.high) > prior_high + sweep_buffer
        reclaimed = float(event.close) < prior_high
        same_depth_change = float(feature.ask_depth_change_1_1m)
    diagnostics.update({
        "prior_balance_high": prior_high,
        "prior_balance_low": prior_low,
        "prior_balance_midpoint": midpoint,
        "sweep_buffer": sweep_buffer,
        "swept": int(swept),
        "reclaimed": int(reclaimed),
        "same_depth_change": same_depth_change,
        "depth_defense": side * float(feature.depth_imbalance_1),
    })
    if not swept:
        return _unresolved(symbol, "ABSORPTION_NO_LIQUIDITY_SWEEP", latest_ts, diagnostics)
    if not reclaimed:
        return _unresolved(symbol, "ABSORPTION_NO_RANGE_RECLAIM", latest_ts, diagnostics)
    depth_defended = (
        side * float(feature.depth_imbalance_1) >= float(config.absorption_depth_defense_min)
        or same_depth_change >= float(config.absorption_same_depth_growth_min)
    )
    if not depth_defended:
        return _unresolved(symbol, "ABSORPTION_DEFENDING_DEPTH_ABSENT", latest_ts, diagnostics)

    entry = float(event.close)
    buffer = float(config.microauction_stop_buffer_atr) * atr
    stop = float(event.low) - buffer if side > 0 else float(event.high) + buffer
    objective = midpoint
    sweep_distance_atr = (
        (prior_low - float(event.low)) / atr
        if side > 0
        else (float(event.high) - prior_high) / atr
    )
    diagnostics["sweep_distance_atr"] = sweep_distance_atr
    score = (
        abs(aggression) / max(float(config.absorption_flow_min), _EPS)
        + abs(float(feature.flow_15s)) / max(float(config.absorption_flow_15s_min), _EPS)
        + min(3.0, float(feature.notional_burst) / max(float(config.absorption_notional_burst_min), _EPS))
        + min(2.0, float(feature.trade_count_burst) / max(float(config.absorption_trade_burst_min), _EPS))
        + float(feature.absorption_60s) / max(float(config.absorption_strength_min), _EPS)
        + min(3.0, max(0.0, sweep_distance_atr))
    )
    return _geometry(
        symbol=symbol,
        state=ABSORPTION_STATE,
        side=side,
        entry=entry,
        stop=stop,
        objective=objective,
        episode_ts=latest_ts,
        score=score,
        reasons=(
            "ACTUAL_AGGRESSOR_FLOW_CROWDING",
            "LOW_PRICE_PATH_EFFICIENCY",
            "ACTUAL_ABSORPTION",
            "COMPLETED_LIQUIDITY_SWEEP_AND_RECLAIM",
            "DEFENDING_DEPTH_PRESENT",
            "PRIOR_BALANCE_MIDPOINT_OBJECTIVE",
        ),
        diagnostics=diagnostics,
        min_reward_r=float(config.microauction_min_reward_r),
    )


def classify_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    mode = str(config.microauction_mode).strip().lower()
    if mode == "continuation":
        return classify_continuation(symbol, bars, feature, config)
    if mode == "absorption":
        return classify_absorption(symbol, bars, feature, config)
    if mode != "unified":
        return _unresolved(symbol, "MICROAUCTION_UNKNOWN_MODE", bars[-1].ts_event if bars else 0, {
            "mode": mode,
        })
    continuation = classify_continuation(symbol, bars, feature, config)
    absorption = classify_absorption(symbol, bars, feature, config)
    candidates = [decision for decision in (continuation, absorption) if decision.actionable]
    if not candidates:
        reasons = tuple(continuation.reasons) + tuple(absorption.reasons)
        return _unresolved(symbol, "MICROAUCTION_NO_ACTIONABLE_STATE", bars[-1].ts_event if bars else 0, {
            "continuation_reason": reasons[0] if reasons else "",
            "absorption_reason": reasons[-1] if reasons else "",
        })
    if len(candidates) == 2 and candidates[0].side != candidates[1].side:
        return _unresolved(symbol, "MICROAUCTION_STATE_CONFLICT", bars[-1].ts_event if bars else 0, {
            "continuation_score": continuation.score,
            "absorption_score": absorption.score,
        })
    candidates.sort(key=lambda decision: (
        0 if decision.state == ABSORPTION_STATE else 1,
        -decision.score,
    ))
    return candidates[0]


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
                FeatureObservation(bars[-1].ts_event if bars else 0),
            ),
            config,
        )
        for symbol, bars in bars_by_symbol.items()
    }
    actionable = [decision for decision in decisions.values() if decision.actionable]
    actionable.sort(key=lambda decision: (
        0 if decision.state == ABSORPTION_STATE else 1,
        -decision.score,
        _SYMBOL_PRIORITY.get(decision.symbol, 99),
        decision.episode_ts,
    ))
    return (actionable[0] if actionable else None), decisions


__all__ = [
    "ABSORPTION_STATE",
    "BarObservation",
    "CONTINUATION_STATE",
    "FeatureObservation",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "classify_absorption",
    "classify_continuation",
    "classify_symbol",
    "route_universe",
]

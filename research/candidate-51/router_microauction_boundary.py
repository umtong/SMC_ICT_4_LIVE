"""Boundary-invalidation refinement of the real-data micro-auction router.

Only continuation invalidation changes: once a completed balance break is
accepted, re-entry through that balance edge invalidates the auction leg.  The
public-data state filters and measured-move objective remain unchanged.
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence

import router_microauction as _base

ABSORPTION_STATE = _base.ABSORPTION_STATE
CONTINUATION_STATE = _base.CONTINUATION_STATE
BarObservation = _base.BarObservation
FeatureObservation = _base.FeatureObservation
RouteConfig = _base.RouteConfig
RouteDecision = _base.RouteDecision
SMA_OFFSET_STATE = _base.SMA_OFFSET_STATE
UNRESOLVED = _base.UNRESOLVED


def classify_continuation(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    prepared = _base._base_inputs(symbol, bars, feature, config)
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
    if not _base._finite(*values):
        return _base._unresolved(symbol, "CONTINUATION_NONFINITE_INPUT", latest_ts)

    flow = float(feature.flow_60s)
    if abs(flow) < float(config.continuation_flow_min):
        return _base._unresolved(symbol, "CONTINUATION_FLOW_TOO_WEAK", latest_ts)
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
        return _base._unresolved(symbol, "CONTINUATION_FLOW_NOT_PERSISTENT", latest_ts, diagnostics)
    if float(feature.notional_burst) < float(config.continuation_notional_burst_min):
        return _base._unresolved(symbol, "CONTINUATION_NOTIONAL_NOT_URGENT", latest_ts, diagnostics)
    if float(feature.trade_count_burst) < float(config.continuation_trade_burst_min):
        return _base._unresolved(symbol, "CONTINUATION_TRADE_RATE_NOT_URGENT", latest_ts, diagnostics)
    if float(feature.efficiency_60s) < float(config.continuation_efficiency_min):
        return _base._unresolved(symbol, "CONTINUATION_PRICE_NOT_EFFICIENT", latest_ts, diagnostics)
    if side * float(feature.ret_60s_bps) <= 0.0:
        return _base._unresolved(symbol, "CONTINUATION_FLOW_PRICE_DISAGREE", latest_ts, diagnostics)

    displacement_atr = side * (float(event.close) - float(event.open)) / atr
    diagnostics["displacement_atr"] = displacement_atr
    if displacement_atr < float(config.continuation_displacement_atr_min):
        return _base._unresolved(symbol, "CONTINUATION_DISPLACEMENT_TOO_SMALL", latest_ts, diagnostics)

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
        return _base._unresolved(symbol, "CONTINUATION_BALANCE_NOT_ACCEPTED", latest_ts, diagnostics)

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
        return _base._unresolved(symbol, "CONTINUATION_DEPTH_STRONGLY_OPPOSES", latest_ts, diagnostics)
    depth_transition = (
        opposite_depth_change <= -float(config.continuation_opposite_depth_thinning_min)
        or same_depth_change >= float(config.continuation_same_depth_growth_min)
    )
    if not depth_transition:
        return _base._unresolved(symbol, "CONTINUATION_NO_DEPTH_TRANSITION", latest_ts, diagnostics)

    entry = float(event.close)
    buffer = float(config.microauction_stop_buffer_atr) * atr
    stop = prior_high - buffer if side > 0 else prior_low + buffer
    objective = (
        prior_high + float(config.continuation_measured_move_multiple) * balance_width
        if side > 0
        else prior_low - float(config.continuation_measured_move_multiple) * balance_width
    )
    score = (
        abs(flow) / max(float(config.continuation_flow_min), _base._EPS)
        + abs(float(feature.flow_3m)) / max(float(config.continuation_flow_3m_min), _base._EPS)
        + min(3.0, float(feature.notional_burst) / max(float(config.continuation_notional_burst_min), _base._EPS))
        + min(2.0, float(feature.trade_count_burst) / max(float(config.continuation_trade_burst_min), _base._EPS))
        + float(feature.efficiency_60s) / max(float(config.continuation_efficiency_min), _base._EPS)
        + min(3.0, max(0.0, displacement_atr))
    )
    diagnostics["invalidation"] = "accepted-balance-boundary-plus-atr-buffer"
    return _base._geometry(
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
            "BALANCE_EDGE_INVALIDATION",
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
    return _base.classify_absorption(symbol, bars, feature, config)


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
        return _base._unresolved(symbol, "MICROAUCTION_UNKNOWN_MODE", bars[-1].ts_event if bars else 0, {"mode": mode})
    continuation = classify_continuation(symbol, bars, feature, config)
    absorption = classify_absorption(symbol, bars, feature, config)
    candidates = [decision for decision in (continuation, absorption) if decision.actionable]
    if not candidates:
        return _base._unresolved(symbol, "MICROAUCTION_NO_ACTIONABLE_STATE", bars[-1].ts_event if bars else 0, {
            "continuation_reason": continuation.reasons[0] if continuation.reasons else "",
            "absorption_reason": absorption.reasons[0] if absorption.reasons else "",
        })
    if len(candidates) == 2 and candidates[0].side != candidates[1].side:
        return _base._unresolved(symbol, "MICROAUCTION_STATE_CONFLICT", bars[-1].ts_event if bars else 0, {
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
        _base._SYMBOL_PRIORITY.get(decision.symbol, 99),
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

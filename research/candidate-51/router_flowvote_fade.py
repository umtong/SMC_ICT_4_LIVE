"""Auction-fade reinterpretation of the failed public flow-vote continuation.

The public 3-of-4 observation is unchanged.  Candidate 51's continuation tests
were catastrophically and consistently wrong across eight management/risk
variants, which is evidence that the observation may identify *exhaustion*
rather than continuation in these perpetual markets.  This module performs one
predeclared inversion test:

    strong buy-side vote  -> short only above the recent balance midpoint;
    strong sell-side vote -> long only below the recent balance midpoint.

Invalidation sits beyond the completed recent balance extreme plus an ATR
buffer; the objective is the same balance midpoint.  Signals without enough
cost-after target space or reward/risk are rejected.  No continuation parameter
is re-fitted from outcomes.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Mapping, Sequence

import router_flowvote as _source

BarObservation = _source.BarObservation
FeatureObservation = _source.FeatureObservation
SMA_OFFSET_STATE = "PUBLIC_FLOWVOTE_AUCTION_FADE"
FLOWVOTE_FADE_STATE = SMA_OFFSET_STATE
UNRESOLVED = _source.UNRESOLVED
RouteDecision = _source.RouteDecision
_EPS = 1e-12


@dataclass(frozen=True, slots=True)
class RouteConfig(_source.RouteConfig):
    flowvote_fade_lookback_minutes: int = 30
    flowvote_fade_atr_buffer: float = 0.50
    flowvote_fade_min_reward_r: float = 0.75
    flowvote_fade_min_target_fraction: float = 0.0040
    flowvote_fade_min_displacement_atr: float = 0.35


def flowvote_scores(**kwargs):
    return _source.flowvote_scores(**kwargs)


def _reject(
    decision: RouteDecision,
    reason: str,
    diagnostics: Mapping[str, float | int | str] | None = None,
) -> RouteDecision:
    merged = dict(decision.diagnostics)
    merged.update(dict(diagnostics or {}))
    return replace(
        decision,
        state=UNRESOLVED,
        side=0,
        score=0.0,
        entry_reference=math.nan,
        stop_reference=math.nan,
        objective_reference=math.nan,
        reasons=(reason,),
        diagnostics=merged,
    )


def classify_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    raw = _source.classify_symbol(symbol, bars, feature, config)
    if not raw.actionable:
        return raw

    lookback = max(3, int(config.flowvote_fade_lookback_minutes))
    minimum = max(lookback, int(config.flowvote_atr_period) + 2)
    if len(bars) < minimum:
        return _reject(
            raw,
            "FLOWVOTE_FADE_HISTORY_NOT_READY",
            {"available_minutes": len(bars), "required_minutes": minimum},
        )
    atr = _source._atr(bars, int(config.flowvote_atr_period))[-1]
    if not math.isfinite(float(atr)) or float(atr) <= _EPS:
        return _reject(raw, "FLOWVOTE_FADE_ATR_NOT_READY")

    sample = bars[-lookback:]
    balance_high = max(float(bar.high) for bar in sample)
    balance_low = min(float(bar.low) for bar in sample)
    midpoint = 0.5 * (balance_high + balance_low)
    entry = float(bars[-1].close)
    fade_side = -int(raw.side)
    buffer = float(config.flowvote_fade_atr_buffer) * float(atr)

    if fade_side > 0:
        stop = balance_low - buffer
        objective = midpoint
        favorable_space = objective - entry
    else:
        stop = balance_high + buffer
        objective = midpoint
        favorable_space = entry - objective

    risk = abs(entry - stop)
    target_fraction = favorable_space / max(entry, _EPS)
    reward_risk = favorable_space / max(risk, _EPS)
    displacement_atr = favorable_space / max(float(atr), _EPS)
    geometry = {
        "raw_continuation_side": int(raw.side),
        "fade_side": fade_side,
        "fade_lookback_minutes": lookback,
        "balance_high": balance_high,
        "balance_low": balance_low,
        "balance_midpoint": midpoint,
        "fade_atr": float(atr),
        "fade_atr_buffer": buffer,
        "fade_stop_fraction": risk / max(entry, _EPS),
        "fade_target_fraction": target_fraction,
        "fade_reward_risk": reward_risk,
        "fade_displacement_atr": displacement_atr,
    }
    valid = (
        0.0 < stop < entry < objective
        if fade_side > 0
        else 0.0 < objective < entry < stop
    )
    if not valid:
        return _reject(raw, "FLOWVOTE_FADE_NOT_OUTSIDE_BALANCE_MIDPOINT", geometry)
    if displacement_atr < float(config.flowvote_fade_min_displacement_atr):
        return _reject(raw, "FLOWVOTE_FADE_DISPLACEMENT_TOO_SMALL", geometry)
    if target_fraction < float(config.flowvote_fade_min_target_fraction):
        return _reject(raw, "FLOWVOTE_FADE_TARGET_SPACE_TOO_SMALL", geometry)
    if reward_risk < float(config.flowvote_fade_min_reward_r):
        return _reject(raw, "FLOWVOTE_FADE_REWARD_RISK_TOO_SMALL", geometry)

    diagnostics = dict(raw.diagnostics)
    diagnostics.update(
        {
            **geometry,
            "raw_state": raw.state,
            "risk_geometry": "recent-balance-extreme-plus-atr-buffer",
            "objective_geometry": "recent-balance-midpoint",
            "management_policy": "opposite-fade-vote-or-midpoint-or-timeout",
            "continuation_policy_rejected_before_inversion": 1,
        }
    )
    score = (
        float(raw.score)
        + min(4.0, reward_risk)
        + min(4.0, displacement_atr)
    )
    return replace(
        raw,
        state=FLOWVOTE_FADE_STATE,
        side=fade_side,
        score=score,
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=objective,
        reasons=(
            "PUBLIC_STRONG_FLOW_VOTE_OBSERVED",
            "CONTINUATION_DIRECTION_REJECTED_IN_PRIOR_EXPERIMENT",
            "FADE_ONLY_OUTSIDE_RECENT_BALANCE_MIDPOINT",
            "RECENT_BALANCE_EXTREME_INVALIDATION",
            "RECENT_BALANCE_MIDPOINT_OBJECTIVE",
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
                FeatureObservation(bars[-1].ts_event if bars else 0),
            ),
            config,
        )
        for symbol, bars in bars_by_symbol.items()
    }
    priority = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}
    actionable = [decision for decision in decisions.values() if decision.actionable]
    actionable.sort(
        key=lambda item: (
            -float(item.score),
            priority.get(item.symbol, 99),
            int(item.episode_ts),
        )
    )
    return (actionable[0] if actionable else None), decisions


__all__ = [
    "BarObservation",
    "FeatureObservation",
    "FLOWVOTE_FADE_STATE",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "classify_symbol",
    "flowvote_scores",
    "route_universe",
]

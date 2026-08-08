"""State-specific route construction for Candidate 37."""
from __future__ import annotations

import math
from statistics import median
from typing import Mapping, Sequence

from burst_features import snapshot
from model import BarObservation, RouteConfig, RouteDecision, Snapshot, SYMBOLS


def valid_geometry(side: int, entry: float, stop: float, objective: float) -> tuple[bool, float]:
    risk = side * (entry - stop)
    reward = side * (objective - entry)
    valid = side in (-1, 1) and math.isfinite(risk) and math.isfinite(reward) and risk > 0 and reward > 0
    return valid, reward / risk if valid else math.nan


def common_candidate(
    *, symbol: str, bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    snapshots: Mapping[str, Snapshot], anchor_index: int, current_index: int,
    config: RouteConfig,
) -> RouteDecision | None:
    active = [
        item for item, state in snapshots.items()
        if state.direction and state.tr_atr >= config.min_impulse_atr_continuation
        and state.volume_ratio >= config.min_participation_ratio and state.efficiency >= 0.35
    ]
    if len(active) < config.common_breadth:
        return None
    positive = sum(snapshots[item].direction > 0 for item in active)
    negative = sum(snapshots[item].direction < 0 for item in active)
    side = 1 if positive > negative else -1 if negative > positive else 0
    if not side:
        return None
    aligned = [item for item in active if snapshots[item].direction == side]
    agreement = len(aligned) / len(active)
    if agreement < config.min_common_agreement or len(aligned) < config.common_breadth:
        return None
    previous_active = 0
    for item in SYMBOLS:
        prior = snapshot(bars_by_symbol[item], anchor_index - 1, config)
        if prior and prior.tr_atr >= config.min_impulse_atr_continuation and prior.volume_ratio >= config.min_participation_ratio:
            previous_active += 1
    if previous_active >= config.common_breadth:
        return None
    common_move = float(median([side * snapshots[item].net_atr for item in aligned]))
    common_abruptness = float(median([snapshots[item].abruptness for item in aligned]))
    if common_move <= 0 or common_abruptness < 1.15:
        return None
    retentions: list[float] = []
    for item in aligned:
        bars = bars_by_symbol[item]
        pre = bars[anchor_index - 1].close
        shock = side * (bars[anchor_index].close - pre)
        retained = side * (bars[current_index].close - pre)
        if shock > 0:
            retentions.append(retained / shock)
    if len(retentions) < config.common_breadth:
        return None
    retention = float(median(retentions))
    if retention < 1.0 - config.max_common_retrace:
        return None

    bars = bars_by_symbol[symbol]
    state = snapshots[symbol]
    pre = bars[anchor_index - 1].close
    shock_signed = side * state.net_atr
    laggard_gap = common_move - shock_signed
    if laggard_gap < config.min_laggard_gap_atr:
        return None
    current = bars[current_index]
    previous = bars[current_index - 1]
    acceptance = side * (current.close - previous.close) / state.atr
    if acceptance < config.min_response_atr or side * (current.close - pre) <= 0:
        return None
    if side * (current.close - current.open) <= 0:
        return None
    entry = current.close
    post = bars[anchor_index + 1 : current_index + 1] or [current]
    stop = (
        min(item.low for item in post) - config.stop_buffer_atr * state.atr
        if side > 0 else
        max(item.high for item in post) + config.stop_buffer_atr * state.atr
    )
    risk = side * (entry - stop)
    if not config.min_risk_atr * state.atr <= risk <= config.max_risk_atr * state.atr:
        return None
    objective = entry + side * config.continuation_target_r * risk
    valid, rr = valid_geometry(side, entry, stop, objective)
    if not valid:
        return None
    score = (
        1.00 + 0.45 * len(aligned) + 0.70 * min(1.5, common_move)
        + 0.55 * min(1.5, laggard_gap) + 0.55 * min(1.5, acceptance)
        + 0.40 * min(1.5, retention) + 0.25 * min(2.0, common_abruptness)
    )
    return RouteDecision(
        symbol=symbol, state="SYNC_PROPAGATION", side=side, score=score,
        expected_target_r=rr, entry_reference=entry, stop_reference=stop,
        objective_reference=objective, episode_ts=bars[anchor_index].ts_event,
        reasons=("ABRUPT_SYNCHRONOUS_COMMON_SHOCK_WITH_LAGGARD_ACCEPTANCE",),
        diagnostics={
            "anchor_age_bars": current_index - anchor_index,
            "active_breadth": len(active), "aligned_breadth": len(aligned),
            "agreement": agreement, "common_move_atr": common_move,
            "common_abruptness": common_abruptness, "cohort_retention": retention,
            "symbol_shock_atr": shock_signed, "laggard_gap_atr": laggard_gap,
            "current_acceptance_atr": acceptance,
        },
    )


def endogenous_candidate(
    *, symbol: str, bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    snapshots: Mapping[str, Snapshot], anchor_index: int, current_index: int,
    config: RouteConfig,
) -> RouteDecision | None:
    state = snapshots[symbol]
    if (
        not state.direction or state.tr_atr < config.min_impulse_atr_reversal
        or state.volume_ratio < config.min_participation_ratio
        or state.ramp_score < config.min_endogenous_ramp_score
        or state.ramp_direction_share < 0.50 or state.abruptness > 4.5
    ):
        return None
    broad = [
        item for item, other in snapshots.items()
        if other.tr_atr >= config.min_impulse_atr_continuation
        and other.volume_ratio >= config.min_participation_ratio
    ]
    same = [item for item in broad if snapshots[item].direction == state.direction]
    if len(broad) >= config.common_breadth and len(same) >= config.common_breadth:
        return None
    bars = bars_by_symbol[symbol]
    direction = state.direction
    side = -direction
    pre = bars[anchor_index - 1].close
    shock = bars[anchor_index]
    current = bars[current_index]
    extent = shock.high - pre if direction > 0 else pre - shock.low
    if extent <= 0:
        return None
    reclaim = (shock.high - current.close) / extent if direction > 0 else (current.close - shock.low) / extent
    response = side * (current.close - shock.close) / state.atr
    if reclaim < config.reversal_reclaim_fraction or response < config.min_response_atr:
        return None
    if side * (current.close - current.open) <= 0:
        return None
    entry = current.close
    stop = shock.high + config.stop_buffer_atr * state.atr if side < 0 else shock.low - config.stop_buffer_atr * state.atr
    risk = side * (entry - stop)
    if not config.min_risk_atr * state.atr <= risk <= config.max_risk_atr * state.atr:
        return None
    balance = bars[max(0, anchor_index - 12) : max(1, anchor_index - 3)]
    if len(balance) < 5:
        return None
    pre_balance = float(median([item.close for item in balance]))
    required = config.reversal_target_r * risk
    if side * (pre_balance - entry) < required:
        return None
    objective = entry + side * required
    valid, rr = valid_geometry(side, entry, stop, objective)
    if not valid:
        return None
    isolation = 1.0 - min(1.0, len(same) / max(1, config.common_breadth))
    score = (
        1.10 + 0.75 * min(2.0, state.tr_atr)
        + 0.50 * min(2.0, state.volume_ratio / config.min_participation_ratio)
        + 0.85 * min(1.5, state.ramp_score) + 0.55 * min(1.5, reclaim)
        + 0.35 * isolation + 0.25 * min(1.5, response)
    )
    return RouteDecision(
        symbol=symbol, state="ENDOGENOUS_EXHAUSTION", side=side, score=score,
        expected_target_r=rr, entry_reference=entry, stop_reference=stop,
        objective_reference=objective, episode_ts=shock.ts_event,
        reasons=("ISOLATED_ACTIVITY_RAMP_FAILED_AND_RECLAIMED",),
        diagnostics={
            "anchor_age_bars": current_index - anchor_index,
            "shock_tr_atr": state.tr_atr, "volume_ratio": state.volume_ratio,
            "ramp_score": state.ramp_score,
            "ramp_direction_share": state.ramp_direction_share,
            "abruptness": state.abruptness, "broad_active": len(broad),
            "same_direction_active": len(same), "reclaim_fraction": reclaim,
            "response_atr": response, "pre_balance": pre_balance,
        },
    )

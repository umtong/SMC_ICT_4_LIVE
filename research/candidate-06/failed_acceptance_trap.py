"""Pure failed-acceptance trap scenario construction."""

from __future__ import annotations

from typing import Any, Mapping

from lrb_types import PrimitiveSnapshot, ScenarioSignal


def build_failed_acceptance_trap(
    signal: ScenarioSignal,
    snapshot: PrimitiveSnapshot,
    params: Mapping[str, Any],
) -> ScenarioSignal | None:
    """Convert a failed SAC defense into a range-reentry trap reversal.

    The reversal is armed only when the next completed bar closes back inside
    the completed auction and has an opposite-direction body.  An optional flow
    variant additionally requires opposite signed taker-flow imbalance.
    """
    if signal.family != "SAC":
        return None
    action = str(params.get("sac_failed_defense_action", "ABSTAIN")).upper()
    if action not in {"TRAP_RECLAIM_BODY", "TRAP_RECLAIM_BODY_FLOW"}:
        return None

    observation = snapshot.observation
    boundary = float(signal.liquidity_level)
    require_flow = action == "TRAP_RECLAIM_BODY_FLOW"
    details = dict(signal.details)
    range_high_raw = details.get("auction_range_high")
    range_low_raw = details.get("auction_range_low")
    extreme_raw = details.get("episode_extreme")
    if range_high_raw is None or range_low_raw is None or extreme_raw is None:
        return None
    range_high = float(range_high_raw)
    range_low = float(range_low_raw)
    episode_extreme = float(extreme_raw)
    range_mid = (range_high + range_low) / 2.0
    buffer_value = float(params.get("stop_buffer_atr", 0.10)) * snapshot.atr

    if signal.direction == "LONG":
        reclaimed = observation.close < boundary
        opposite_body = observation.close < observation.open
        opposite_flow = snapshot.flow_ratio < 0.0
        if not (reclaimed and opposite_body and (opposite_flow or not require_flow)):
            return None
        direction = "SHORT"
        stop = max(episode_extreme, observation.high) + buffer_value
        candidates = [
            (range_mid, "FAILED_ACCEPTANCE_RANGE_EQUILIBRIUM"),
            (range_low, "FAILED_ACCEPTANCE_OPPOSITE_BOUNDARY"),
        ]
    elif signal.direction == "SHORT":
        reclaimed = observation.close > boundary
        opposite_body = observation.close > observation.open
        opposite_flow = snapshot.flow_ratio > 0.0
        if not (reclaimed and opposite_body and (opposite_flow or not require_flow)):
            return None
        direction = "LONG"
        stop = min(episode_extreme, observation.low) - buffer_value
        candidates = [
            (range_mid, "FAILED_ACCEPTANCE_RANGE_EQUILIBRIUM"),
            (range_high, "FAILED_ACCEPTANCE_OPPOSITE_BOUNDARY"),
        ]
    else:
        return None

    entry = float(observation.close)
    risk = abs(entry - stop)
    if risk <= 0.0:
        return None
    minimum_rr = float(params.get("minimum_structural_rr", 0.75))
    valid: list[tuple[float, str]] = []
    for price, reason in candidates:
        reward = price - entry if direction == "LONG" else entry - price
        if reward > 0.0 and reward / risk >= minimum_rr:
            valid.append((float(price), reason))
    valid.sort(key=lambda item: abs(item[0] - entry))
    if not valid:
        return None
    target, target_reason = valid[0]
    return ScenarioSignal(
        scenario_id=signal.scenario_id,
        family="FAT",
        direction=direction,
        observed_ts_ns=observation.ts_ns,
        reference_entry=entry,
        stop_price=stop,
        target_price=target,
        target_reason=target_reason,
        atr=snapshot.atr,
        liquidity_level=boundary,
        details={
            **details,
            "source_family": signal.family,
            "source_direction": signal.direction,
            "trap_action": action,
            "trap_reclaim_close": entry,
            "trap_reclaim_flow_ratio": snapshot.flow_ratio,
            "trap_stop_anchor": episode_extreme,
            "trap_target_reason": target_reason,
        },
    )

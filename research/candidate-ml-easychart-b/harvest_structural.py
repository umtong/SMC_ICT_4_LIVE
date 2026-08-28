#!/usr/bin/env python3
"""EasyChart-B structural-completion harvester.

A fixed R number never chooses the take-profit.  Each immutable first-return plan uses
one of two causal structures already visible when the order is armed:

* the current liquidity-release/impulse extreme, reclaimed after the first return; or
* the nearest still-live opposing semantic-liquidity or causal volume frontier beyond
  that impulse.

The nearest structural completion which can pay the user's pre-cost 1R minimum is used.
No maximum R exists.  A naturally open 2R/3R route remains 2R/3R.  The order is armed
only after an observable auction-state change; future bars merely label the already
fixed entry, stop and structural target.
"""
from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any, Iterator

import numpy as np
import pandas as pd

import candidate_1k_harvest as source

core = source.core
policy = source.policy
EPS = source.EPS
POLICY = (
    "ML_EASYCHART_B_CAUSAL_SWEEP_RELEASE_FIRST_RETURN_"
    "NEAREST_CAUSAL_STRUCTURAL_COMPLETION_NO_FIXED_R_CAP"
)


def _sign(side: str) -> float:
    return 1.0 if side == "LONG" else -1.0


def _quantize_inside(price: float, side: str, tick: float) -> float:
    """Place the limit one tick inside a known extreme without moving it outward."""
    if side == "LONG":
        return math.floor((float(price) - float(tick)) / float(tick) + 1e-12) * float(tick)
    return math.ceil((float(price) + float(tick)) / float(tick) - 1e-12) * float(tick)


def _candidate_economics(
    side: str,
    entry: float,
    stop: float,
    target: float,
    tick: float,
) -> tuple[float, dict[str, float]] | None:
    valid = stop < entry < target if side == "LONG" else target < entry < stop
    if not valid:
        return None
    risk = abs(float(entry) - float(stop))
    if risk <= EPS:
        return None
    gross_rr = abs(float(target) - float(entry)) / risk
    if gross_rr + 1e-12 < 1.0:
        return None
    economics = core._raw_economics(side, entry, stop, target, tick)
    if economics is None or float(economics["target_net_r"]) <= 0.0:
        return None
    return float(gross_rr), economics


def _phase(progress_r: float, retrace_fraction: float, outside_ratio: float) -> str:
    if progress_r <= 0.0:
        return "FAILED_REENTRY"
    if retrace_fraction >= 0.72:
        return "DEEP_RETEST"
    if retrace_fraction >= 0.20:
        return "FIRST_RETEST_FORMING"
    if outside_ratio >= 0.60:
        return "ACCEPTED_EXPANSION"
    return "EARLY_RESPONSE"


def _pre_arm_alive_without_target(
    data: pd.DataFrame,
    candidate: Any,
    arm: int,
    entry: float,
    stop: float,
    tick: float,
) -> bool:
    """The pre-arm impulse may create the future reclaim target; do not call it spent."""
    setup = candidate.setup
    departure = int(candidate.departure_index)
    side = str(setup.side)
    for position in range(departure + 1, arm + 1):
        row = data.iloc[position]
        invalidated = float(row.low) <= stop if side == "LONG" else float(row.high) >= stop
        traded = (
            float(row.low) <= entry - core.LIMIT_TRADE_THROUGH_TICKS * tick
            if side == "LONG"
            else float(row.high) >= entry + core.LIMIT_TRADE_THROUGH_TICKS * tick
        )
        overlaps = float(row.low) <= float(setup.upper) and float(row.high) >= float(setup.lower)
        if invalidated or traded or overlaps:
            return False
    return True


def structural_arm_positions(
    data: pd.DataFrame,
    candidate: Any,
    source_level: Any,
    entry: float,
    stop: float,
    _target_unused: float,
    tick: float,
) -> Iterator[int]:
    """Emit causal state changes until the first-return opportunity ends."""
    departure = int(candidate.departure_index)
    expiry = min(len(data) - 1, core._pending_expiry(candidate, source_level))
    side = str(candidate.setup.side)
    sign = _sign(side)
    risk = max(abs(float(entry) - float(stop)), EPS)
    best = sign * (float(data.iloc[departure].close) - float(entry))
    last_phase: str | None = None
    milestones = {1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377}
    for arm in range(departure + 1, expiry + 1):
        if not _pre_arm_alive_without_target(data, candidate, arm, entry, stop, tick):
            break
        value = sign * (float(data.iloc[arm].close) - float(entry))
        new_extreme = value > best + tick
        if new_extreme:
            best = value
        progress_r = value / risk
        retrace = max(0.0, best - value) / max(abs(best), tick)
        segment = data.iloc[departure : arm + 1]
        boundary = float(candidate.setup.upper if side == "LONG" else candidate.setup.lower)
        outside = (
            segment.close.astype(float) > boundary
            if side == "LONG"
            else segment.close.astype(float) < boundary
        )
        phase = _phase(progress_r, retrace, float(outside.mean()))
        changed = phase != last_phase
        if new_extreme or changed or (arm - departure) in milestones:
            yield arm
            last_phase = phase


def _structural_target_at_arm(
    data: pd.DataFrame,
    levels: Any,
    metadata: dict[str, Any],
    candidate: Any,
    arm: int,
    entry: float,
    stop: float,
    tick: float,
):
    """Choose the nearest qualifying causal structure, never a numerical RR cap."""
    side = str(candidate.setup.side)
    sign = _sign(side)
    departure = int(candidate.departure_index)
    segment = data.iloc[departure : arm + 1]
    extreme = float(segment.high.max()) if side == "LONG" else float(segment.low.min())
    impulse_target = _quantize_inside(extreme, side, tick)
    impulse = _candidate_economics(side, entry, stop, impulse_target, tick)

    obstacle, route_features = core.v4._first_obstacle(
        data,
        levels,
        metadata,
        int(arm),
        float(entry),
        side,
        float(tick),
    )
    route_target = float(obstacle.order_price) if obstacle is not None else float("nan")
    # A route behind the already-observed impulse is not a still-live destination.
    route_beyond_impulse = (
        obstacle is not None
        and (route_target > extreme + tick if side == "LONG" else route_target < extreme - tick)
    )
    route = (
        _candidate_economics(side, entry, stop, route_target, tick)
        if route_beyond_impulse
        else None
    )

    candidates: list[tuple[float, str, Any, float, dict[str, float]]] = []
    if impulse is not None:
        gross_rr, economics = impulse
        impulse_obstacle = core.v4.Obstacle(
            obstacle_id=(
                f"ARM_IMPULSE_RECLAIM:{int(data.index[arm].value)}:"
                f"{core._stable_id(candidate.setup.setup_kind, side, impulse_target)}"
            ),
            kind="ARM_CAUSAL_IMPULSE_RECLAIM",
            timeframe_minutes=1,
            structure_price=extreme,
            order_price=impulse_target,
            strength=1.0,
            source_level_id=None,
        )
        candidates.append((gross_rr, "IMPULSE_RECLAIM", impulse_obstacle, impulse_target, economics))
    if route is not None and obstacle is not None:
        gross_rr, economics = route
        candidates.append((gross_rr, "OPPOSING_LIVE_FRONTIER", obstacle, route_target, economics))
    if not candidates:
        return None

    # Distance only orders already meaningful structures; R never invents a target.
    gross_rr, provenance, chosen, target, economics = min(
        candidates,
        key=lambda item: (item[0], 0 if item[1] == "IMPULSE_RECLAIM" else 1, item[2].obstacle_id),
    )
    risk = max(abs(float(entry) - float(stop)), EPS)
    chosen_features = dict(route_features)
    chosen_features.update(
        {
            "target_is_impulse_reclaim": float(provenance == "IMPULSE_RECLAIM"),
            "target_is_opposing_live_frontier": float(provenance == "OPPOSING_LIVE_FRONTIER"),
            "impulse_reclaim_structure_price": extreme,
            "impulse_reclaim_target_price": impulse_target,
            "impulse_reclaim_rr": abs(impulse_target - entry) / risk,
            "opposing_live_frontier_price": route_target,
            "opposing_live_frontier_rr": (
                abs(route_target - entry) / risk if math.isfinite(route_target) else float("nan")
            ),
            "opposing_live_frontier_kind": obstacle.kind if obstacle is not None else "NONE",
            "structural_target_provenance": provenance,
            "structural_target_price": float(target),
            "structural_target_rr": float(gross_rr),
        }
    )
    return chosen, chosen_features, float(target), float(gross_rr), economics, provenance


def label_from_structural_arm(
    data: pd.DataFrame,
    candidate: Any,
    arm: int,
    entry: float,
    stop: float,
    target: float,
    tick: float,
):
    """Ignore the pre-arm creation of the reclaim target; enforce it after arming."""
    setup = candidate.setup
    side = str(setup.side)
    expiry = min(len(data) - 1, core._pending_expiry(candidate, candidate.source))
    if arm >= expiry or not _pre_arm_alive_without_target(data, candidate, arm, entry, stop, tick):
        return policy._empty_label("ARM_NOT_AVAILABLE", data, min(max(arm, 0), len(data) - 1))
    touch_index = None
    for position in range(arm + 1, expiry + 1):
        row = data.iloc[position]
        invalidated = float(row.low) <= stop if side == "LONG" else float(row.high) >= stop
        target_spent = float(row.high) >= target if side == "LONG" else float(row.low) <= target
        traded = (
            float(row.low) <= entry - core.LIMIT_TRADE_THROUGH_TICKS * tick
            if side == "LONG"
            else float(row.high) >= entry + core.LIMIT_TRADE_THROUGH_TICKS * tick
        )
        overlaps = float(row.low) <= float(setup.upper) and float(row.high) >= float(setup.lower)
        if traded:
            if invalidated or target_spent:
                return policy._copy_label(
                    core._same_bar_stop_label(data, position, arm, entry, stop, target, side, tick)
                )
            return policy._copy_label(
                core._resolve_after_fill(data, position, arm, entry, stop, target, side, tick)
            )
        if invalidated:
            return policy._empty_label("CANCELED_PRE_FILL_INVALIDATED", data, position)
        if target_spent:
            return policy._empty_label("CANCELED_PRE_FILL_TARGET_SPENT", data, position)
        if touch_index is None and overlaps:
            touch_index = position
        elif touch_index is not None:
            close_away = (
                float(row.close) >= float(setup.upper)
                if side == "LONG"
                else float(row.close) <= float(setup.lower)
            )
            if close_away or position - touch_index > core.MAX_RESPONSE_BARS:
                return policy._empty_label("CANCELED_FIRST_RETURN_PASSED", data, position)
    return policy._empty_label("EXPIRED_UNFILLED", data, expiry)


def generate_symbol(symbol, data, levels, metadata, trading_start):
    tick = core.CONTRACTS[symbol].tick_size
    start_ns = int(pd.Timestamp(trading_start, tz="UTC").value)
    sources = source.direction_sources(levels, metadata, source.MINIMUM_SOURCE_TIMEFRAME)
    sources = sorted(
        sources,
        key=lambda level: (
            int(level.first_penetration_index),
            level.side,
            -float(metadata[level.level_id].semantic_weight),
            level.level_id,
        ),
    )
    records: list[dict[str, Any]] = []
    active_until = {"HIGH": -1, "LOW": -1}
    seen: set[tuple[int, str]] = set()
    counts = {
        "semantic_sources": len(sources),
        "source_interactions": 0,
        "causal_departures": 0,
        "arm_states": 0,
        "plans": 0,
        "no_structural_completion_over_one_r": 0,
    }

    for source_level in sources:
        interaction = int(source_level.first_penetration_index)
        if (
            interaction >= len(data)
            or int(data.index[interaction].value) < start_ns
            or interaction <= active_until[source_level.side]
        ):
            continue
        clock = (interaction, str(source_level.side))
        if clock in seen:
            continue
        peers = [
            level
            for level in sources
            if level.side == source_level.side
            and int(level.first_penetration_index) == interaction
        ]
        owner = max(
            peers,
            key=lambda level: (
                float(metadata[level.level_id].semantic_weight),
                int(level.timeframe_minutes),
                int(level.defense_count),
                float(level.strength_ratio),
            ),
        )
        seen.add(clock)
        if owner.level_id != source_level.level_id:
            continue
        counts["source_interactions"] += 1
        candidates = core._departure_candidates(data, owner, tick)
        if not candidates:
            continue
        candidate = candidates[0]
        active_until[source_level.side] = max(
            active_until[source_level.side], int(candidate.departure_index)
        )
        counts["causal_departures"] += 1
        try:
            object.__setattr__(candidate, "source", owner)
        except (AttributeError, TypeError):
            pass

        stop = float(core._causal_stop(data, candidate, owner, tick))
        event_ns = int(data.index[candidate.setup.interaction_index].value)
        episode_id = f"ECB3:{symbol}:{event_ns}:{core._stable_id(owner.level_id)}"

        for entry_name, entry_value in core._entry_variants(data, candidate, tick):
            entry = float(entry_value)
            if not (stop < entry if candidate.setup.side == "LONG" else stop > entry):
                continue
            risk = abs(entry - stop)
            # The placeholder target is unused by structural_arm_positions.
            for arm in structural_arm_positions(
                data, candidate, owner, entry, stop, entry, tick
            ):
                target_plan = _structural_target_at_arm(
                    data, levels, metadata, candidate, arm, entry, stop, tick
                )
                if target_plan is None:
                    counts["no_structural_completion_over_one_r"] += 1
                    continue
                obstacle, route_features, target, gross_rr, economics, provenance = target_plan
                label = label_from_structural_arm(
                    data, candidate, arm, entry, stop, target, tick
                )
                if label.fill_state == "ARM_NOT_AVAILABLE":
                    continue
                base_features = core._plan_features(
                    data,
                    levels,
                    metadata,
                    owner,
                    candidate,
                    obstacle,
                    route_features,
                    entry,
                    stop,
                )
                arm_features = dict(policy._arm_metrics(data, candidate, arm, entry, stop))
                arm_progress = float(arm_features.get("arm_progress_r", 0.0))
                arm_features.update(
                    {
                        "arm_structural_target_headroom_r": float(gross_rr - arm_progress),
                        "arm_structural_target_consumed_fraction": float(
                            arm_progress / max(gross_rr, EPS)
                        ),
                        "auction_route_headroom_r": float(gross_rr),
                        "route_utilization": 1.0,
                        "exact_route_target": 1.0,
                    }
                )
                state_id = (
                    f"ECB3STATE:{symbol}:{event_ns}:"
                    f"{candidate.event_meta['narrative_branch']}:{arm}:"
                    f"{core._stable_id(owner.level_id, candidate.setup.setup_kind)}"
                )
                action_id = (
                    f"{episode_id}:{arm}:{entry_name}:{provenance}:"
                    f"{core._stable_id(obstacle.obstacle_id, target)}"
                )
                records.append(
                    {
                        "action_id": action_id,
                        "state_id": state_id,
                        "episode_id": episode_id,
                        "symbol": symbol,
                        "side": candidate.setup.side,
                        "family": candidate.event_meta["narrative_branch"],
                        "departure_time_ns": int(data.index[candidate.departure_index].value),
                        "order_time_ns": int(data.index[arm].value),
                        "arm_index": int(arm),
                        "entry_geometry": entry_name,
                        "entry": entry,
                        "stop": stop,
                        "target": target,
                        "gross_rr": float(gross_rr),
                        "risk_bps": risk / max(abs(entry), EPS) * 10_000.0,
                        "route_kind": obstacle.kind,
                        "route_price": float(target),
                        "route_rr": float(gross_rr),
                        "planned_target_net_r": float(economics["target_net_r"]),
                        **base_features,
                        **arm_features,
                        **asdict(label),
                    }
                )
                counts["plans"] += 1

    frame = pd.DataFrame(records)
    counts["arm_states"] = int(frame.state_id.nunique()) if not frame.empty else 0
    return frame, counts


core.direction_sources = source.direction_sources
core.MINIMUM_SOURCE_TIMEFRAME = source.MINIMUM_SOURCE_TIMEFRAME
core._atr_price = source.atr_price
core.generate_symbol = generate_symbol
core.POLICY = POLICY

if __name__ == "__main__":
    core.main()

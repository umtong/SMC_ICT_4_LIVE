#!/usr/bin/env python3
"""Candidate 1k causal liquidity-auction harvester.

This module keeps the useful causal episode machinery from the latest research lineage
but changes the executable plan itself:

* a semantic liquidity interaction owns the direction decision;
* failed-auction reversal and accepted-auction continuation share one event grammar;
* OB/FVG/boundary geometry only refines the first-return limit entry;
* the stop invalidates the observed event;
* the full take-profit is the nearest still-unconsumed opposing liquidity/volume
  obstacle, rather than an arbitrary RR grid point;
* only plans whose actual structural route pays at least 1R before costs are emitted;
* future bars label an already immutable order and never create the candidate.

The latest episode branch contained import-contract errors: its v6 generator requested
``direction_sources(..., minimum_timeframe=...)`` and ``core._atr_price`` although the
inherited v5 core exposed neither.  The adapters below repair those contracts without
changing candidate causality.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

import numpy as np
import pandas as pd

import auction_episode_harvest as episode
import semantic_liquidity_v4 as semantic

policy = episode.policy
core = episode.core
EPS = policy.EPS
MINIMUM_SOURCE_TIMEFRAME = 15
POLICY = (
    "CANDIDATE_1K_SEMANTIC_LIQUIDITY_AUCTION_EPISODE_"
    "FIRST_RETURN_LIMIT_EVENT_INVALIDATION_EXACT_FIRST_OPPOSING_ROUTE_TP"
)


def direction_sources(
    levels: Any,
    metadata: dict[str, Any],
    minimum_timeframe: int | None = None,
):
    """Compatibility adapter plus the intended direction-scale floor."""
    floor = MINIMUM_SOURCE_TIMEFRAME if minimum_timeframe is None else int(minimum_timeframe)
    return [
        level
        for level in semantic.direction_sources(levels, metadata)
        if int(level.timeframe_minutes) >= floor
    ]


def atr_price(data: pd.DataFrame, position: int, lookback: int = 30) -> float:
    """Causal robust one-minute ATR used only to normalize event-state features."""
    position = min(max(int(position), 0), len(data) - 1)
    start = max(0, position - max(int(lookback), 2) + 1)
    frame = data.iloc[start:position + 1]
    high = pd.to_numeric(frame.high, errors="coerce").to_numpy(float)
    low = pd.to_numeric(frame.low, errors="coerce").to_numpy(float)
    close = pd.to_numeric(frame.close, errors="coerce").to_numpy(float)
    previous = np.empty_like(close)
    if start > 0:
        previous[0] = float(data.close.iloc[start - 1])
    else:
        previous[0] = close[0]
    if len(close) > 1:
        previous[1:] = close[:-1]
    true_range = np.maximum.reduce(
        [high - low, np.abs(high - previous), np.abs(low - previous)]
    )
    finite = true_range[np.isfinite(true_range) & (true_range > 0.0)]
    if finite.size:
        return float(np.median(finite))
    fallback = abs(float(data.high.iloc[position]) - float(data.low.iloc[position]))
    return max(fallback, EPS)


def _sign(side: str) -> float:
    return 1.0 if side == "LONG" else -1.0


def _target_plan(
    data: pd.DataFrame,
    levels: Any,
    metadata: dict[str, Any],
    candidate: Any,
    entry: float,
    stop: float,
    tick: float,
):
    """Return the first meaningful pre-existing obstacle as the one full-position TP."""
    obstacle, route_features = core.v4._first_obstacle(
        data,
        levels,
        metadata,
        int(candidate.departure_index),
        float(entry),
        str(candidate.setup.side),
        float(tick),
    )
    if obstacle is None:
        return None
    side = str(candidate.setup.side)
    target = float(obstacle.order_price)
    valid = stop < entry < target if side == "LONG" else target < entry < stop
    if not valid:
        return None
    risk = abs(float(entry) - float(stop))
    if risk <= EPS:
        return None
    gross_rr = abs(target - float(entry)) / risk
    # The user rule is pre-cost planned RR >= 1R. Costs are still included in labels.
    if gross_rr + 1e-12 < 1.0:
        return None
    economics = core._raw_economics(side, float(entry), float(stop), target, float(tick))
    if economics is None or float(economics["target_net_r"]) <= 0.0:
        return None
    return obstacle, route_features, target, gross_rr, economics


def generate_symbol(symbol, data, levels, metadata, trading_start):
    """Enumerate exact-route immutable plans at causal episode states."""
    tick = core.CONTRACTS[symbol].tick_size
    start_ns = int(pd.Timestamp(trading_start, tz="UTC").value)
    sources = direction_sources(levels, metadata, MINIMUM_SOURCE_TIMEFRAME)
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
        "route_below_one_r": 0,
        "missing_route": 0,
    }

    for source in sources:
        interaction = int(source.first_penetration_index)
        if (
            interaction >= len(data)
            or int(data.index[interaction].value) < start_ns
            or interaction <= active_until[source.side]
        ):
            continue
        clock = (interaction, str(source.side))
        if clock in seen:
            continue
        peers = [
            level
            for level in sources
            if level.side == source.side
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
        if owner.level_id != source.level_id:
            continue

        counts["source_interactions"] += 1
        candidates = core._departure_candidates(data, owner, tick)
        if not candidates:
            continue
        candidate = candidates[0]
        active_until[source.side] = max(active_until[source.side], int(candidate.departure_index))
        counts["causal_departures"] += 1
        # Fixed wrapper candidates already carry source; keep compatibility with older rows.
        try:
            object.__setattr__(candidate, "source", owner)
        except (AttributeError, TypeError):
            pass

        stop = float(core._causal_stop(data, candidate, owner, tick))
        event_ns = int(data.index[candidate.setup.interaction_index].value)
        episode_id = f"C1K:{symbol}:{event_ns}:{core._stable_id(owner.level_id)}"

        for entry_name, entry_value in core._entry_variants(data, candidate, tick):
            entry = float(entry_value)
            if not (stop < entry if candidate.setup.side == "LONG" else stop > entry):
                continue
            target_plan = _target_plan(data, levels, metadata, candidate, entry, stop, tick)
            if target_plan is None:
                # Separate missing route from a route that exists but cannot pay 1R.
                obstacle, _ = core.v4._first_obstacle(
                    data,
                    levels,
                    metadata,
                    int(candidate.departure_index),
                    entry,
                    str(candidate.setup.side),
                    tick,
                )
                if obstacle is None:
                    counts["missing_route"] += 1
                else:
                    counts["route_below_one_r"] += 1
                continue
            obstacle, route_features, target, gross_rr, economics = target_plan
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
            risk = abs(entry - stop)
            route_rr = abs(float(obstacle.order_price) - entry) / max(risk, EPS)

            for arm in policy._arm_positions(
                data,
                candidate,
                owner,
                entry,
                stop,
                target,
                tick,
            ):
                label = policy.label_from_arm(
                    data,
                    candidate,
                    arm,
                    entry,
                    stop,
                    target,
                    tick,
                )
                if label.fill_state == "ARM_NOT_AVAILABLE":
                    continue
                state_id = (
                    f"C1KSTATE:{symbol}:{event_ns}:"
                    f"{candidate.event_meta['narrative_branch']}:{arm}:"
                    f"{core._stable_id(owner.level_id, candidate.setup.setup_kind)}"
                )
                action_id = (
                    f"{episode_id}:{arm}:{entry_name}:"
                    f"{core._stable_id(obstacle.obstacle_id)}"
                )
                arm_features = dict(policy._arm_metrics(data, candidate, arm, entry, stop))
                arm_features.update(
                    {
                        "auction_route_headroom_r": float(route_rr),
                        "route_utilization": 1.0,
                        "exact_route_target": 1.0,
                    }
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
                        "route_price": float(obstacle.order_price),
                        "route_rr": float(route_rr),
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


# Patch the import contracts and generator used by inherited run_research/main.
core.direction_sources = direction_sources
core.MINIMUM_SOURCE_TIMEFRAME = MINIMUM_SOURCE_TIMEFRAME
core._atr_price = atr_price
core.POLICY = POLICY
core.generate_symbol = generate_symbol

if __name__ == "__main__":
    core.main()

#!/usr/bin/env python3
"""One coherent directional-liquidity decision policy.

This module keeps the mature point-in-time source/episode discovery code, but
replaces the plan lattice and fitted admission gate with one causal decision:

    direction/objective -> completed liquidity event -> one first-return price
    -> structural invalidation -> nearest fresh opposing liquidity.

Every accepted episode creates at most one predeclared entry/stop/target plan.
"""
from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any, Sequence

import numpy as np
import pandas as pd

import episode_policy as restored
from directional_context import (
    build_directional_snapshot,
    build_objective_snapshot,
    mechanism_coherence,
)
from world_model_common import EPS, MEDIUM_SCALE, EpisodeSignal, sign, stable

base_policy = restored.base_policy
geometry = restored.geometry


def _last_opposite_body(
    data: pd.DataFrame,
    start: int,
    end: int,
    side: str,
) -> tuple[float, float, str, int, float] | None:
    start = max(0, start)
    segment = data.iloc[start : end + 1]
    opposite = segment.close < segment.open if side == "LONG" else segment.close > segment.open
    indices = np.flatnonzero(opposite.to_numpy())
    if not len(indices):
        return None
    index = start + int(indices[-1])
    row = data.iloc[index]
    lower, upper = sorted((float(row.open), float(row.close)))
    body = max(upper - lower, EPS)
    return lower, upper, "ORIGIN_BODY", index, body


def _last_fvg(
    data: pd.DataFrame,
    start: int,
    end: int,
    side: str,
    tick: float,
) -> tuple[float, float, str, int, float] | None:
    candidates: list[tuple[float, float, str, int, float]] = []
    bodies = (data.close - data.open).abs()
    for index in range(max(2, start + 2), end + 1):
        prior = bodies.iloc[max(0, index - 32) : index]
        normal = max(float(prior.median()) if len(prior) else 0.0, tick)
        middle_body = float(bodies.iloc[index - 1])
        displacement = middle_body / normal
        if side == "LONG":
            lower, upper = float(data.high.iloc[index - 2]), float(data.low.iloc[index])
            kind = "BULLISH_FVG"
        else:
            lower, upper = float(data.high.iloc[index]), float(data.low.iloc[index - 2])
            kind = "BEARISH_FVG"
        lower, upper = sorted((lower, upper))
        if upper - lower <= 2.0 * tick or displacement <= 1.0:
            continue
        candidates.append((lower, upper, kind, index, displacement))
    return candidates[-1] if candidates else None


def _overlap(
    left: tuple[float, float, str, int, float] | None,
    right: tuple[float, float, str, int, float] | None,
    kind: str,
) -> tuple[float, float, str, int, float] | None:
    if left is None or right is None:
        return None
    lower, upper = max(left[0], right[0]), min(left[1], right[1])
    if upper <= lower:
        return None
    return lower, upper, kind, max(left[3], right[3]), left[4] + right[4]


def _source_zone(signal: EpisodeSignal) -> tuple[float, float, str, int, float] | None:
    source = signal.source
    if source is None:
        return None
    return (
        float(source.lower),
        float(source.upper),
        "TRANSFERRED_SOURCE_SR",
        int(source.observed_index),
        float(max(source.strength, 0.0)),
    )


def _one_entry_zone(
    data: pd.DataFrame,
    signal: EpisodeSignal,
    atr_price: float,
    tick: float,
) -> tuple[float, float, str] | None:
    body = _last_opposite_body(
        data, signal.impulse_start_index, signal.decision_index, signal.side
    )
    gap = _last_fvg(
        data, signal.impulse_start_index, signal.decision_index, signal.side, tick
    )
    source = _source_zone(signal)
    body_gap = _overlap(body, gap, "ORIGIN_BODY_FVG_OVERLAP")
    source_body = _overlap(source, body, "SOURCE_ORIGIN_BODY_OVERLAP")
    source_gap = _overlap(source, gap, "SOURCE_FVG_OVERLAP")

    if signal.family == "FAILED_AUCTION_REVERSAL":
        ordered = [source_gap, source_body, source]
    elif signal.family == "ACCEPTED_AUCTION_CONTINUATION":
        ordered = [source_gap, source_body, source, body_gap, gap, body]
    elif signal.family == "INITIATIVE_MITIGATION_CONTINUATION":
        ordered = [body_gap, gap, body]
    else:
        return None

    decision_price = float(data.close.iloc[signal.decision_index])
    valid: list[tuple[int, float, int, tuple[float, float, str, int, float]]] = []
    for priority, item in enumerate(ordered):
        if item is None:
            continue
        lower, upper = sorted((float(item[0]), float(item[1])))
        favorable = upper < decision_price - tick if signal.side == "LONG" else lower > decision_price + tick
        if not favorable:
            continue
        distance = decision_price - upper if signal.side == "LONG" else lower - decision_price
        valid.append((priority, distance / max(atr_price, EPS), -item[3], item))
    if not valid:
        return None
    _, _, _, selected = min(valid, key=lambda item: (item[0], item[1], item[2]))
    return float(selected[0]), float(selected[1]), str(selected[2])


def _entry_price(
    lower: float,
    upper: float,
    side: str,
    signal: EpisodeSignal,
) -> float:
    lower, upper = sorted((float(lower), float(upper)))
    source = signal.source
    if source is not None and lower <= float(source.price) <= upper:
        return float(source.price)
    width = max(upper - lower, EPS)
    return float(upper - 0.25 * width if side == "LONG" else lower + 0.25 * width)


def directional_plan_from_signal(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[Any],
    metadata: dict[str, Any],
    nodes_by_scale: dict[float, list[Any]],
    small_nodes: Sequence[Any],
    signal: EpisodeSignal,
    atr: np.ndarray,
    tick: float,
) -> tuple[dict[str, Any] | None, str]:
    decision = int(signal.decision_index)
    atr_price = float(atr[decision])
    decision_price = float(data.close.iloc[decision])

    long_destination = geometry.choose_destination(
        data, levels, metadata, nodes_by_scale, decision, decision_price,
        "LONG", atr, tick,
    )
    short_destination = geometry.choose_destination(
        data, levels, metadata, nodes_by_scale, decision, decision_price,
        "SHORT", atr, tick,
    )
    directional = build_directional_snapshot(data, decision, signal.side, atr_price)
    objective = build_objective_snapshot(
        side=signal.side,
        price=decision_price,
        atr_price=atr_price,
        long_destination=long_destination,
        short_destination=short_destination,
    )
    source_strength = float(signal.source.strength) if signal.source else 1.0 + MEDIUM_SCALE
    source_confluence = int(signal.source.confluence_count) if signal.source else 1
    coherence = mechanism_coherence(
        signal.family,
        signal.evidence,
        directional,
        objective,
        source_strength=source_strength,
        source_confluence=source_confluence,
    )
    if not math.isfinite(coherence) or coherence <= 0.0:
        return None, "DIRECTION_EVENT_OBJECTIVE_CONTRADICTION"

    zone = _one_entry_zone(data, signal, atr_price, tick)
    if zone is None:
        return None, "NO_FAMILY_SPECIFIC_FIRST_RETURN_LOCATION"
    zone_lower, zone_upper, zone_kind = zone
    entry = _entry_price(zone_lower, zone_upper, signal.side, signal)
    favorable = entry < decision_price - tick if signal.side == "LONG" else entry > decision_price + tick
    if not favorable:
        return None, "ENTRY_NOT_BEHIND_COMPLETED_CONTROL"

    stop = geometry.stop_price(data, signal, zone_lower, zone_upper, tick)
    if not (stop < entry if signal.side == "LONG" else stop > entry):
        return None, "INVALID_STRUCTURAL_INVALIDATION"

    destination = geometry.choose_destination(
        data, levels, metadata, nodes_by_scale, decision, entry,
        signal.side, atr, tick,
    )
    if destination is None:
        return None, "NO_FRESH_OPPOSING_LIQUIDITY"
    target = (
        float(destination.lower) - tick
        if signal.side == "LONG"
        else float(destination.upper) + tick
    )
    risk = abs(entry - stop)
    gross_rr = sign(signal.side) * (target - entry) / max(risk, EPS)
    if gross_rr < 1.0:
        return None, "FRESH_DESTINATION_PAYS_LESS_THAN_1R"
    estimate = geometry.economics(signal.side, entry, stop, target, tick)
    if estimate is None or float(estimate["target_net_r"]) <= 0.0:
        return None, "NON_POSITIVE_POST_COST_DESTINATION"

    expiry = geometry.pending_expiry(signal, small_nodes, len(data))
    label = geometry.resolve_order(data, signal, entry, stop, target, tick, expiry)
    episode_id = (
        f"DLP2:{symbol}:{int(data.index[signal.interaction_index].value)}:"
        f"{signal.family}:{stable(signal.source.source_id if signal.source else signal.context_scale, decision)}"
    )
    route_quality = math.log1p(max(float(destination.strength), 0.0))
    opportunity_score = (
        coherence
        + 0.16 * min(gross_rr, 4.0)
        + 0.10 * route_quality
        + 0.08 * math.log1p(max(objective.route_room_atr, 0.0))
    )
    return {
        "order_exists": True,
        "action_id": f"{episode_id}:ONE_PLAN",
        "state_id": f"DLP2STATE:{stable(episode_id, decision)}",
        "episode_id": episode_id,
        "symbol": symbol,
        "side": signal.side,
        "family": signal.family,
        "interaction_time_ns": int(data.index[signal.interaction_index].value),
        "order_time_ns": int(data.index[decision].value),
        "entry_geometry": zone_kind,
        "entry": float(entry),
        "stop": float(stop),
        "target": float(target),
        "gross_rr": float(gross_rr),
        "risk_bps": float(risk / max(abs(entry), EPS) * 10_000.0),
        "planned_target_net_r": float(estimate["target_net_r"]),
        "route_kind": str(destination.kind),
        "route_price": float(destination.price),
        "route_strength": float(destination.strength),
        "route_scale": float(destination.scale),
        "source_kind": signal.source.kind if signal.source else "LIVE_MEDIUM_AUCTION_LEG",
        "source_strength": source_strength,
        "source_scale": float(signal.source.scale) if signal.source else float(MEDIUM_SCALE * 60.0),
        "source_confluence_count": source_confluence,
        "zone_lower": float(zone_lower),
        "zone_upper": float(zone_upper),
        "event_extreme": float(signal.event_extreme),
        "pullback_extreme": float(signal.pullback_extreme),
        "mechanism_coherence": float(coherence),
        "opportunity_score": float(opportunity_score),
        **{f"direction_{key}": value for key, value in directional.to_dict().items()},
        **{f"objective_{key}": value for key, value in objective.to_dict().items()},
        **signal.evidence,
        **asdict(label),
    }, "PLAN_CREATED"


base_policy.plan_from_signal = directional_plan_from_signal


def generate_symbol(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[Any],
    metadata: dict[str, Any],
    trading_start: Any,
) -> tuple[pd.DataFrame, dict[str, int]]:
    combined_levels = list(levels)
    combined_metadata = dict(metadata)
    dynamic_count = 0
    dynamic_channel_count = 0
    data.attrs["symbol"] = symbol
    dynamic_boundaries = restored.dynamic_boundaries
    if dynamic_boundaries is not None:
        tick = base_policy.core.CONTRACTS[symbol].tick_size
        models = dynamic_boundaries.build_dynamic_boundaries(
            symbol, data, combined_levels, tick
        )
        dynamic_levels, dynamic_metadata = dynamic_boundaries.source_levels(
            symbol, data, models, tick, combined_levels
        )
        combined_levels.extend(dynamic_levels)
        combined_metadata.update(dynamic_metadata)
        dynamic_count = len(dynamic_levels)
        dynamic_channel_count = sum(
            "CHANNEL" in str(level.source_kind) for level in dynamic_levels
        )

    frame, counts = base_policy.generate_symbol(
        symbol, data, combined_levels, combined_metadata, trading_start
    )
    frame = frame.copy()
    frame["policy_version"] = "directional-liquidity-policy-v2"
    frame["symbol_identity_used_for_decision"] = False
    frame["fitted_admission_model_used"] = False
    frame["one_plan_per_episode"] = True
    frame["target_selected_before_rr"] = True
    has_order = (
        frame.get("order_exists", pd.Series(False, index=frame.index))
        .astype(str).str.lower().isin({"true", "1", "yes"})
    )
    counts = dict(counts)
    counts.update(
        {
            "directional_policy_rows": int(len(frame)),
            "directional_policy_orders": int(has_order.sum()),
            "uses_outcome_in_generation": 0,
            "one_plan_per_episode": 1,
            "fixed_rr_target_lattice": 0,
            "fitted_admission_model": 0,
            "symbol_identity_feature": 0,
            "dynamic_liquidity_sources": int(dynamic_count),
            "dynamic_channel_sources": int(dynamic_channel_count),
            "dynamic_geometry_available": int(dynamic_boundaries is not None),
        }
    )
    return frame, counts


__all__ = ["directional_plan_from_signal", "generate_symbol"]

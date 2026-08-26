#!/usr/bin/env python3
"""Liquidity episode policy with exact risk-aware structural destination choice.

The earlier local-frontier policy chose the nearest destination before knowing
whether that destination paid the required 1R.  A too-close obstacle could then
reject an otherwise valid episode even when the next public structural frontier
was tradeable.  This module computes the structural stop first and selects the
nearest still-live destination whose exact geometry pays at least 1R after the
same cost model.  It never substitutes a fixed-R target.
"""
from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any, Sequence

import numpy as np
import pandas as pd

import episode_policy as episode
import dynamic_boundaries
import plan_geometry as geometry
import world_model_policy as base_policy
from world_model_common import EPS, MEDIUM_SCALE, Destination, finite, sign, stable


def destination_candidates(
    data: pd.DataFrame,
    levels: Sequence[Any],
    metadata: dict[str, Any],
    nodes_by_scale: dict[float, list[Any]],
    decision: int,
    entry: float,
    side: str,
    atr: np.ndarray,
    tick: float,
) -> list[Destination]:
    candidates: list[Destination] = []
    static = episode._BASE_CHOOSE_DESTINATION(
        data, levels, metadata, nodes_by_scale, decision, entry, side, atr, tick
    )
    if static is not None:
        candidates.append(static)
    target_side = "HIGH" if side == "LONG" else "LOW"
    for model, price in dynamic_boundaries.active_route_boundaries(
        str(data.attrs.get("symbol", "")), data, decision, entry, side, tick
    ):
        width = max(
            2.0 * tick,
            1.5 * float(model.residual_price),
            0.04 * float(atr[decision]),
        )
        candidates.append(
            Destination(
                destination_id=f"DYNAMIC_ROUTE:{model.boundary_id}:{decision}",
                side=target_side,
                lower=float(price - width),
                upper=float(price + width),
                price=float(price),
                observed_index=int(model.observed_index),
                scale=float(model.timeframe_minutes),
                strength=float(
                    max(model.quality, 0.0)
                    * (1.0 + 0.60 * max(model.channel_quality, 0.0))
                ),
                kind=(
                    f"DYNAMIC_CHANNEL_ROUTE_{model.timeframe_minutes}M"
                    if model.is_channel_edge
                    else f"DYNAMIC_TRENDLINE_ROUTE_{model.timeframe_minutes}M"
                ),
            )
        )
    candidates.extend(
        episode._local_completion_frontiers(
            data, decision, entry, side, atr, tick
        )
    )
    direction = sign(side)
    ahead = [
        item
        for item in episode._cluster_destinations(candidates, decision, atr, tick)
        if item.side == target_side
        and direction * (item.price - entry) > tick
    ]
    ahead.sort(
        key=lambda item: (
            direction * (item.price - entry),
            -item.strength,
            -item.scale,
            item.destination_id,
        )
    )
    return ahead


def risk_aware_plan_from_signal(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[Any],
    metadata: dict[str, Any],
    nodes_by_scale: dict[float, list[Any]],
    small_nodes: Sequence[Any],
    signal: Any,
    atr: np.ndarray,
    tick: float,
) -> tuple[dict[str, Any] | None, str]:
    control = signal.evidence
    if (
        finite(control.get("control_move_atr")) <= 0.0
        or finite(control.get("control_path_efficiency")) <= 0.0
    ):
        return None, "NO_DIRECTIONAL_CONTROL"
    decision_price = float(data.close.iloc[signal.decision_index])
    zone_lower, zone_upper, zone_kind = geometry.entry_zone(
        data, signal, float(atr[signal.decision_index]), tick
    )
    if zone_kind == "CAUSAL_DEPARTURE_BAND":
        return None, "NO_CAUSAL_ENTRY_ORIGIN"
    entry = geometry.entry_price(
        zone_lower, zone_upper, signal.side, signal.source
    )
    favorable = (
        entry < decision_price - tick
        if signal.side == "LONG"
        else entry > decision_price + tick
    )
    if not favorable:
        return None, "ENTRY_NOT_A_FIRST_RETURN_PRICE"
    stop = geometry.stop_price(
        data, signal, zone_lower, zone_upper, tick
    )
    if not (stop < entry if signal.side == "LONG" else stop > entry):
        return None, "INVALID_CAUSAL_STOP"
    risk = abs(entry - stop)
    if risk <= EPS:
        return None, "INVALID_CAUSAL_STOP"

    candidates = destination_candidates(
        data, levels, metadata, nodes_by_scale,
        signal.decision_index, entry, signal.side, atr, tick,
    )
    if not candidates:
        return None, "NO_FRESH_DESTINATION"
    chosen: Destination | None = None
    chosen_target = 0.0
    chosen_rr = 0.0
    chosen_economics: dict[str, float] | None = None
    chosen_rank = 0
    rejected_too_close = 0
    for rank, destination in enumerate(candidates, start=1):
        target = (
            destination.lower - tick
            if signal.side == "LONG"
            else destination.upper + tick
        )
        gross_rr = sign(signal.side) * (target - entry) / max(risk, EPS)
        if gross_rr < 1.0:
            rejected_too_close += 1
            continue
        estimate = geometry.economics(
            signal.side, entry, stop, target, tick
        )
        if estimate is None or estimate["target_net_r"] <= 0.0:
            continue
        chosen = destination
        chosen_target = float(target)
        chosen_rr = float(gross_rr)
        chosen_economics = estimate
        chosen_rank = rank
        break
    if chosen is None or chosen_economics is None:
        return None, "NO_STRUCTURAL_DESTINATION_PAYING_ONE_R"

    expiry = geometry.pending_expiry(signal, small_nodes, len(data))
    label = geometry.resolve_order(
        data, signal, entry, stop, chosen_target, tick, expiry
    )
    episode_id = (
        f"WM:{symbol}:{int(data.index[signal.interaction_index].value)}:"
        f"{signal.family}:"
        f"{stable(signal.source.source_id if signal.source else signal.context_scale, signal.decision_index)}"
    )
    decision_quality = (
        1.20 * finite(control.get("control_path_efficiency"))
        + 0.35 * finite(control.get("control_flow_share_signed"))
        + 0.20 * math.log1p(max(finite(control.get("control_activity_ratio")), 0.0))
        + 0.12 * min(chosen_rr, 4.0)
        + 0.10 * math.log1p(max(chosen.strength, 0.0))
        + 0.08 * finite(control.get("common_breadth_signed"))
    )
    return {
        "order_exists": True,
        "action_id": f"{episode_id}:ONE_PLAN",
        "state_id": f"WMSTATE:{stable(episode_id, signal.decision_index)}",
        "episode_id": episode_id,
        "symbol": symbol,
        "side": signal.side,
        "family": signal.family,
        "interaction_time_ns": int(data.index[signal.interaction_index].value),
        "order_time_ns": int(data.index[signal.decision_index].value),
        "entry_geometry": zone_kind,
        "entry": float(entry),
        "stop": float(stop),
        "target": float(chosen_target),
        "gross_rr": float(chosen_rr),
        "risk_bps": risk / max(abs(entry), EPS) * 10_000.0,
        "planned_target_net_r": float(chosen_economics["target_net_r"]),
        "route_kind": chosen.kind,
        "route_price": float(chosen.price),
        "route_strength": float(chosen.strength),
        "route_scale": float(chosen.scale),
        "completion_frontier_rank": int(chosen_rank),
        "nearer_frontiers_below_one_r": int(rejected_too_close),
        "uses_local_completion_frontier": int("LOCAL_" in chosen.kind),
        "source_kind": (
            signal.source.kind if signal.source else "LIVE_MEDIUM_AUCTION_LEG"
        ),
        "source_strength": (
            float(signal.source.strength)
            if signal.source else float(1.0 + MEDIUM_SCALE)
        ),
        "source_scale": (
            float(signal.source.scale)
            if signal.source else float(MEDIUM_SCALE * 60.0)
        ),
        "source_confluence_count": (
            int(signal.source.confluence_count) if signal.source else 1
        ),
        "zone_lower": float(zone_lower),
        "zone_upper": float(zone_upper),
        "event_extreme": float(signal.event_extreme),
        "pullback_extreme": float(signal.pullback_extreme),
        "decision_quality": float(decision_quality),
        **control,
        **asdict(label),
    }, "PLAN_CREATED"


base_policy.plan_from_signal = risk_aware_plan_from_signal


def generate_symbol(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[Any],
    metadata: dict[str, Any],
    trading_start: Any,
):
    frame, counts = episode.generate_symbol(
        symbol, data, levels, metadata, trading_start
    )
    counts = dict(counts)
    counts["risk_aware_structural_destination"] = 1
    counts["fixed_rr_target_lattice"] = 0
    return frame, counts


__all__ = ["generate_symbol", "risk_aware_plan_from_signal"]

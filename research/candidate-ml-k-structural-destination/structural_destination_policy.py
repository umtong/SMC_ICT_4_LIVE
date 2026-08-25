#!/usr/bin/env python3
"""Exact counterfactual structural-destination generator for candidate ML-k.

The causal auction engine still owns direction, source, response, entry and
structural invalidation.  The previous policy collapsed every live opposing
liquidity map to one nearest destination before the decision model could reason
about reachability.  This module keeps the causally live structural frontiers,
resolves each immutable TP/SL alternative exactly, and leaves one pre-entry
choice to the account router.

Multiple rows are research counterfactuals, not multiple orders.  Every sibling
shares one causal episode, entry and stop; the router may execute at most one.
"""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
CANDIDATE_DIR = Path(__file__).resolve().parent
DEPENDENCY_DIRS = (
    CANDIDATE_DIR,
    REPO_ROOT / "research/candidate-liquidity-episode-policy-v1",
    REPO_ROOT / "research/candidate-liquidity-world-model-v1",
    REPO_ROOT / "research/candidate-liquidity-auction-v2",
    REPO_ROOT / "research/candidate-liquidity-auction-v7",
    REPO_ROOT / "research/candidate-liquidity-auction-v6",
    REPO_ROOT / "research/candidate-liquidity-auction-v5",
    REPO_ROOT / "research/candidate-coherent-auction-system-v4",
    REPO_ROOT / "research/candidate-coherent-auction-system-v3",
    REPO_ROOT / "research/candidate-coherent-liquidity-policy-v2",
    REPO_ROOT / "research/candidate-coherent-liquidity-policy-v1",
    REPO_ROOT / "research/candidate-hierarchical-liquidity-bpr-v2",
    REPO_ROOT / "research/candidate-hierarchical-liquidity-bpr-v1",
    REPO_ROOT / "research/candidate-liquidity-displacement-v1",
    REPO_ROOT / "research/candidate-auction-dislocation-confluence-v1",
    REPO_ROOT / "research/candidate-derivatives-dislocation-v1",
    REPO_ROOT / "research/candidate-auction-episode-policy",
    REPO_ROOT / "research/candidate-auction-event-v2",
    REPO_ROOT / "research/candidate-direct-auction-policy",
    REPO_ROOT / "research/candidate-easychart_re1",
    REPO_ROOT / "research/candidate-easychart-v5",
    REPO_ROOT / "research/candidate-easychart-v3",
)
for dependency in reversed([path for path in DEPENDENCY_DIRS if path.exists()]):
    value = str(dependency)
    if value not in sys.path:
        sys.path.insert(0, value)

import dynamic_boundaries  # noqa: E402
import liquidity_world  # noqa: E402
import plan_geometry as geometry  # noqa: E402
import world_model_policy as base_policy  # noqa: E402
from episode_detection import (  # noqa: E402
    accepted_signal,
    dedupe_signals,
    failed_signal,
    mitigation_signals,
)
from episode_policy_features import FEATURE_COLUMNS, enrich_episode_frame  # noqa: E402
from liquidity_world import (  # noqa: E402
    dc_source_events,
    merge_source_events,
    semantic_source_events,
)
from world_model_common import (  # noqa: E402
    EVENT_SCALE,
    LARGE_SCALE,
    MEDIUM_SCALE,
    Destination,
    EpisodeSignal,
    atr_array,
    core,
    dc,
    finite,
    fixed,
    sign,
    stable,
)

POLICY_VERSION = "candidate-ml-k-structural-destination-v1"
_BASE_PLAN_FROM_SIGNAL = geometry.plan_from_signal


def _route_family(kind: str) -> str:
    text = str(kind).upper()
    if "DYNAMIC_CHANNEL" in text:
        return "DYNAMIC_CHANNEL"
    if "DYNAMIC_TRENDLINE" in text:
        return "DYNAMIC_TRENDLINE"
    if "PREVIOUS_DAY" in text:
        return "PREVIOUS_DAY"
    if "DIRECTIONAL_CHANGE" in text:
        return "DIRECTIONAL_CHANGE"
    if "SEMANTIC" in text:
        return "SEMANTIC"
    return "OTHER_STRUCTURAL"




def _scenario_family(signal: EpisodeSignal, destination: Destination) -> str:
    source = str(signal.source.kind if signal.source is not None else "").upper()
    route = _route_family(destination.kind)
    if signal.family == "FAILED_AUCTION_REVERSAL":
        if "CHANNEL" in source or route == "DYNAMIC_CHANNEL":
            return "CHANNEL_TRAP_RECLAIM"
        if "TRENDLINE" in source or route == "DYNAMIC_TRENDLINE":
            return "TRENDLINE_TRAP_RECLAIM"
        return "HORIZONTAL_LIQUIDITY_RECLAIM"
    if signal.family == "ACCEPTED_AUCTION_CONTINUATION":
        if route in {"DYNAMIC_CHANNEL", "DYNAMIC_TRENDLINE"}:
            return "STRUCTURAL_FLIP_FIRST_RETEST"
        return "ACCEPTED_AUCTION_FIRST_RETEST"
    return "INITIATIVE_SOURCE_MITIGATION"


def _scale_bucket(scale: float) -> str:
    value = max(float(scale), 1.0)
    if value < 60.0:
        return "LOCAL"
    if value < 240.0:
        return "INTRADAY"
    if value < 1440.0:
        return "MESO"
    return "DAILY_PLUS"


def _dynamic_destinations(
    symbol: str,
    data: pd.DataFrame,
    decision: int,
    entry: float,
    side: str,
    atr: np.ndarray,
    tick: float,
) -> list[Destination]:
    target_side = "HIGH" if side == "LONG" else "LOW"
    output: list[Destination] = []
    for model, price in dynamic_boundaries.active_route_boundaries(
        symbol, data, decision, entry, side, tick
    ):
        width = max(
            2.0 * tick,
            1.5 * float(model.residual_price),
            0.04 * float(atr[decision]),
        )
        output.append(
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
    return output


def structural_frontiers(
    *,
    symbol: str,
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
    """Return first-live frontiers by structural mechanism and scale.

    A farther level of the same mechanism and scale cannot be reached without
    first clearing its nearer sibling.  Keeping the nearest member of each
    mechanism/scale pair therefore preserves distinct causal destinations
    without turning every historical line into a target lattice.
    """
    target_side = "HIGH" if side == "LONG" else "LOW"
    candidates: list[Destination] = [
        *liquidity_world._semantic_destinations(levels, metadata, decision),
        *liquidity_world._dc_destinations(data, nodes_by_scale, decision, atr),
        *_dynamic_destinations(symbol, data, decision, entry, side, atr, tick),
    ]
    previous = liquidity_world._previous_day(data, decision, target_side)
    if previous is not None:
        candidates.append(previous)

    direction = sign(side)
    clustered = liquidity_world._cluster(candidates, decision, atr, tick)
    ahead = [
        item
        for item in clustered
        if item.side == target_side
        and direction
        * ((item.lower if side == "LONG" else item.upper) - entry)
        > tick
    ]
    ahead.sort(
        key=lambda item: (
            direction * (item.price - entry),
            -item.strength,
            -item.scale,
            item.destination_id,
        )
    )
    if not ahead:
        return []

    first_by_mechanism_scale: dict[tuple[str, str], Destination] = {}
    for item in ahead:
        key = (_route_family(item.kind), _scale_bucket(item.scale))
        first_by_mechanism_scale.setdefault(key, item)

    selected = list(first_by_mechanism_scale.values())
    nearest = ahead[0]
    if all(item.destination_id != nearest.destination_id for item in selected):
        selected.append(nearest)
    selected.sort(
        key=lambda item: (
            direction * (item.price - entry),
            -item.strength,
            -item.scale,
            item.destination_id,
        )
    )
    return selected


def _entry_for_signal(
    data: pd.DataFrame,
    signal: EpisodeSignal,
    atr: np.ndarray,
    tick: float,
) -> tuple[float | None, str]:
    control = signal.evidence
    if (
        finite(control.get("control_move_atr")) <= 0.0
        or finite(control.get("control_path_efficiency")) <= 0.0
    ):
        return None, "NO_DIRECTIONAL_CONTROL"
    lower, upper, kind = geometry.entry_zone(
        data, signal, float(atr[signal.decision_index]), tick
    )
    if kind == "CAUSAL_DEPARTURE_BAND":
        return None, "NO_CAUSAL_ENTRY_ORIGIN"
    entry = geometry.entry_price(lower, upper, signal.side, signal.source)
    decision_price = float(data.close.iloc[signal.decision_index])
    favorable = (
        entry < decision_price - tick
        if signal.side == "LONG"
        else entry > decision_price + tick
    )
    if not favorable:
        return None, "ENTRY_NOT_A_FIRST_RETURN_PRICE"
    return float(entry), "ENTRY_READY"


def _plan_for_destination(
    *,
    destination: Destination,
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
    original = geometry.choose_destination
    geometry.choose_destination = lambda *args, **kwargs: destination
    try:
        plan, reason = _BASE_PLAN_FROM_SIGNAL(
            symbol,
            data,
            levels,
            metadata,
            nodes_by_scale,
            small_nodes,
            signal,
            atr,
            tick,
        )
    finally:
        geometry.choose_destination = original
    if plan is not None and str(plan.get("entry_geometry")) == "CAUSAL_DEPARTURE_BAND":
        return None, "NO_CAUSAL_ENTRY_ORIGIN"
    return plan, reason


def _complete_no_order(
    symbol: str,
    signal: EpisodeSignal,
    data: pd.DataFrame,
    reason: str,
    atr: np.ndarray,
) -> dict[str, Any]:
    row = geometry.no_plan_record(symbol, signal, data, reason, atr)
    return base_policy._complete_no_order_row(row, signal, data)


def _signals(
    data: pd.DataFrame,
    levels: Sequence[Any],
    metadata: dict[str, Any],
    trading_start: Any,
    tick: float,
) -> tuple[list[EpisodeSignal], dict[float, list[Any]], np.ndarray, int]:
    start = pd.Timestamp(trading_start)
    start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    start_index = int(data.index.searchsorted(start))
    decision_end_ns = getattr(fixed, "_DECISION_END_NS", None)
    end_index = (
        len(data)
        if decision_end_ns is None
        else int(
            data.index.searchsorted(
                pd.Timestamp(decision_end_ns, unit="ns", tz="UTC")
            )
        )
    )
    atr = atr_array(data)
    nodes_by_scale = {
        scale: dc.directional_change(data, scale, atr)
        for scale in (EVENT_SCALE, MEDIUM_SCALE, LARGE_SCALE)
    }
    source_events = merge_source_events(
        [
            *semantic_source_events(levels, metadata, data),
            *dc_source_events(data, nodes_by_scale, atr),
        ],
        atr,
        tick,
    )
    small_nodes = nodes_by_scale[EVENT_SCALE]
    signals: list[EpisodeSignal] = []
    for source in source_events:
        if source.interaction_index < start_index or source.interaction_index >= end_index:
            continue
        failed = failed_signal(data, source, small_nodes, atr)
        accepted = accepted_signal(data, source, small_nodes, atr)
        if failed is not None and accepted is not None:
            signals.append(min((failed, accepted), key=lambda item: item.decision_index))
        elif failed is not None:
            signals.append(failed)
        elif accepted is not None:
            signals.append(accepted)
    signals.extend(
        mitigation_signals(
            data,
            small_nodes,
            nodes_by_scale[MEDIUM_SCALE],
            atr,
            start_index,
            end_index,
        )
    )
    return dedupe_signals(signals), nodes_by_scale, atr, end_index


def _assert_counterfactual_invariants(frame: pd.DataFrame) -> None:
    orders = frame[
        frame.get("order_exists", pd.Series(False, index=frame.index))
        .astype(str)
        .str.lower()
        .isin({"true", "1", "yes"})
    ].copy()
    if orders.empty:
        return
    if orders.action_id.astype(str).duplicated().any():
        raise RuntimeError("Counterfactual destination action_id is not unique")
    if orders.entry_geometry.astype(str).eq("CAUSAL_DEPARTURE_BAND").any():
        raise RuntimeError("Synthetic entry fallback leaked into destination candidates")
    gross = pd.to_numeric(orders.gross_rr, errors="coerce")
    if gross.isna().any() or gross.lt(1.0 - 1e-12).any():
        raise RuntimeError("Destination candidate violates gross RR >= 1.0")
    for episode_id, group in orders.groupby("episode_id", sort=False):
        for column in ("entry", "stop", "side", "order_time_ns"):
            if group[column].astype(str).nunique(dropna=False) != 1:
                raise RuntimeError(
                    f"Sibling destinations changed {column} in episode {episode_id}"
                )
        if pd.to_numeric(group.target, errors="coerce").duplicated().any():
            raise RuntimeError(f"Duplicate target frontier in episode {episode_id}")


def generate_symbol(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[Any],
    metadata: dict[str, Any],
    trading_start: Any,
) -> tuple[pd.DataFrame, dict[str, int]]:
    tick = core.CONTRACTS[symbol].tick_size
    data.attrs["symbol"] = symbol

    combined_levels = list(levels)
    combined_metadata = dict(metadata)
    models = dynamic_boundaries.build_dynamic_boundaries(
        symbol, data, combined_levels, tick
    )
    dynamic_levels, dynamic_metadata = dynamic_boundaries.source_levels(
        symbol, data, models, tick, combined_levels
    )
    combined_levels.extend(dynamic_levels)
    combined_metadata.update(dynamic_metadata)

    signals, nodes_by_scale, atr, end_index = _signals(
        data, combined_levels, combined_metadata, trading_start, tick
    )
    small_nodes = nodes_by_scale[EVENT_SCALE]
    records: list[dict[str, Any]] = []
    episode_candidates = 0
    no_trade_episodes = 0

    for signal in signals:
        if signal.decision_index >= end_index:
            continue
        entry, prerequisite = _entry_for_signal(data, signal, atr, tick)
        if entry is None:
            records.append(
                _complete_no_order(symbol, signal, data, prerequisite, atr)
            )
            no_trade_episodes += 1
            continue

        destinations = structural_frontiers(
            symbol=symbol,
            data=data,
            levels=combined_levels,
            metadata=combined_metadata,
            nodes_by_scale=nodes_by_scale,
            decision=signal.decision_index,
            entry=entry,
            side=signal.side,
            atr=atr,
            tick=tick,
        )
        plans: list[tuple[dict[str, Any], Destination]] = []
        last_reason = "NO_FRESH_STRUCTURAL_FRONTIER"
        for destination in destinations:
            plan, reason = _plan_for_destination(
                destination=destination,
                symbol=symbol,
                data=data,
                levels=combined_levels,
                metadata=combined_metadata,
                nodes_by_scale=nodes_by_scale,
                small_nodes=small_nodes,
                signal=signal,
                atr=atr,
                tick=tick,
            )
            last_reason = reason
            if plan is not None:
                plans.append((plan, destination))

        if not plans:
            records.append(
                _complete_no_order(symbol, signal, data, last_reason, atr)
            )
            no_trade_episodes += 1
            continue

        plans.sort(
            key=lambda item: (
                float(item[0]["gross_rr"]),
                -float(item[0]["route_strength"]),
                str(item[1].destination_id),
            )
        )
        valid_count = len(plans)
        previous_distance_atr = 0.0
        for rank, (plan, destination) in enumerate(plans):
            target_distance = abs(float(plan["target"]) - float(plan["entry"]))
            target_distance_atr = target_distance / max(
                float(atr[signal.decision_index]), 1e-12
            )
            plan["destination_id"] = destination.destination_id
            plan["target_candidate_rank"] = rank
            plan["target_candidate_count"] = valid_count
            plan["target_frontier_percentile"] = (
                float(rank / (valid_count - 1)) if valid_count > 1 else 0.0
            )
            plan["target_distance_atr"] = float(target_distance_atr)
            plan["frontier_spacing_atr"] = float(
                max(0.0, target_distance_atr - previous_distance_atr)
            )
            plan["route_family"] = _route_family(destination.kind)
            plan["route_scale_bucket"] = _scale_bucket(destination.scale)
            plan["scenario_family"] = _scenario_family(signal, destination)
            plan["counterfactual_target_candidate"] = True
            plan["candidate_policy_version"] = POLICY_VERSION
            plan["action_id"] = (
                f"{plan['episode_id']}:TARGET:"
                f"{stable(destination.destination_id, round(float(plan['target']) / tick))}"
            )
            previous_distance_atr = target_distance_atr
            records.append(plan)
        episode_candidates += 1

    frame = pd.DataFrame(records)
    if frame.empty:
        return frame, {
            "causal_episode_signals": int(len(signals)),
            "episodes_with_destination_candidates": 0,
            "counterfactual_target_rows": 0,
            "no_trade_episodes": int(no_trade_episodes),
        }

    order_mask = (
        frame.get("order_exists", pd.Series(False, index=frame.index))
        .astype(str)
        .str.lower()
        .isin({"true", "1", "yes"})
    )
    if order_mask.any():
        enriched = enrich_episode_frame(frame, data)
    else:
        enriched = frame.copy()
        for column in FEATURE_COLUMNS:
            if column not in enriched:
                enriched[column] = 0.0
    enriched["episode_policy_version"] = POLICY_VERSION
    _assert_counterfactual_invariants(enriched)

    return enriched, {
        "causal_episode_signals": int(len(signals)),
        "episodes_with_destination_candidates": int(episode_candidates),
        "counterfactual_target_rows": int(order_mask.sum()),
        "no_trade_episodes": int(no_trade_episodes),
        "dynamic_liquidity_sources": int(len(dynamic_levels)),
        "dynamic_channel_sources": int(
            sum("CHANNEL" in str(level.source_kind) for level in dynamic_levels)
        ),
        "one_executed_plan_per_episode_after_routing": 1,
        "uses_outcome_in_generation": 0,
        "synthetic_entry_fallback_enabled": 0,
    }


__all__ = [
    "POLICY_VERSION",
    "generate_symbol",
    "structural_frontiers",
]

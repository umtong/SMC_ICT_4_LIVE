#!/usr/bin/env python3
"""One causal skilled-liquidity-response trading policy.

The policy asks one question at a public liquidity boundary: after the attempted
break, where did the auction settle and how much price response did its effort buy?
A settled response determines direction.  OB/FVG/source geometry then determines the
first-return entry, the event determines invalidation, and live opposing liquidity
determines the target.  The same code is used for all four markets.
"""
from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any, Sequence

import pandas as pd

# Reuse the mature dynamic boundary, entry geometry and causal order resolver from
# the preceding branch.  Importing this module installs trend-line/channel routes.
import directional_liquidity_policy as dlp2
from directional_context import build_directional_snapshot, build_objective_snapshot
from liquidity_world import dc_source_events, merge_source_events, semantic_source_events
from response_event_detection import (
    accepted_signal,
    dedupe_signals,
    failed_signal,
    mitigation_signals,
)
from world_model_common import (
    EVENT_SCALE,
    LARGE_SCALE,
    MEDIUM_SCALE,
    EpisodeSignal,
    atr_array,
    core,
    dc,
    finite,
    sign,
    stable,
)

restored = dlp2.restored
geometry = dlp2.geometry
EPS = 1e-12


def _complete_no_order_row(
    row: dict[str, Any],
    signal: EpisodeSignal,
    data: pd.DataFrame,
) -> dict[str, Any]:
    row.update(
        {
            "fill_state": "NO_ORDER",
            "outcome": "NO_TRADE",
            "fill_index": None,
            "fill_time_ns": None,
            "resolution_index": None,
            "resolution_time_ns": None,
            "order_terminal_index": int(signal.decision_index),
            "order_terminal_time_ns": int(data.index[signal.decision_index].value),
            "entry_wait_minutes": None,
            "holding_minutes": None,
            "actual_entry": None,
            "actual_target_net_r": None,
            "actual_stop_net_r": None,
            "actual_gross_rr": None,
            "net_r": None,
            "mfe_r": None,
            "mae_r": None,
        }
    )
    return row


def _decision_alignment(
    signal: EpisodeSignal,
    directional: Any,
    objective: Any,
) -> float:
    """Join event response, broader direction and live objective by mechanism."""
    response = finite(signal.evidence.get("auction_response_score"), float("-inf"))
    if not math.isfinite(response):
        return float("-inf")
    relative = math.tanh(float(directional.relative_strength_alignment))
    common = math.tanh(float(directional.common_factor_alignment))
    if signal.family == "FAILED_AUCTION_REVERSAL":
        # Reversal quality comes from an energetic overshoot which settled inside.
        # A mature opposing move is potential fuel, not an automatic veto.
        exhaustion = math.tanh(max(-float(directional.long_move_atr), 0.0))
        return float(
            response
            + 0.30 * float(objective.objective_alignment)
            + 0.18 * exhaustion
            + 0.10 * relative
            + 0.06 * common
        )
    if signal.family == "ACCEPTED_AUCTION_CONTINUATION":
        return float(
            response
            + 0.42 * float(directional.trend_alignment)
            + 0.18 * float(directional.trend_consensus)
            + 0.28 * float(objective.objective_alignment)
            + 0.10 * relative
        )
    if signal.family == "INITIATIVE_MITIGATION_CONTINUATION":
        return float(
            response
            + 0.36 * float(directional.trend_alignment)
            + 0.14 * float(directional.trend_consensus)
            + 0.25 * float(objective.objective_alignment)
            + 0.12 * relative
        )
    return float("-inf")


def plan_from_signal(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[Any],
    metadata: dict[str, Any],
    nodes_by_scale: dict[float, list[Any]],
    small_nodes: Sequence[Any],
    signal: EpisodeSignal,
    atr: Any,
    tick: float,
) -> tuple[dict[str, Any] | None, str]:
    decision = int(signal.decision_index)
    atr_price = float(atr[decision])
    decision_price = float(data.close.iloc[decision])
    event_response = finite(signal.evidence.get("auction_response_score"), float("-inf"))
    if not math.isfinite(event_response) or event_response <= 0.0:
        return None, "AUCTION_RESPONSE_DID_NOT_DOMINATE_ORDINARY_NOISE"

    long_destination = geometry.choose_destination(
        data,
        levels,
        metadata,
        nodes_by_scale,
        decision,
        decision_price,
        "LONG",
        atr,
        tick,
    )
    short_destination = geometry.choose_destination(
        data,
        levels,
        metadata,
        nodes_by_scale,
        decision,
        decision_price,
        "SHORT",
        atr,
        tick,
    )
    directional = build_directional_snapshot(data, decision, signal.side, atr_price)
    objective = build_objective_snapshot(
        side=signal.side,
        price=decision_price,
        atr_price=atr_price,
        long_destination=long_destination,
        short_destination=short_destination,
    )
    alignment = _decision_alignment(signal, directional, objective)
    if not math.isfinite(alignment) or alignment <= 0.0:
        return None, "SETTLED_RESPONSE_DIRECTION_AND_OBJECTIVE_DISAGREE"

    zone = dlp2._one_entry_zone(data, signal, atr_price, tick)
    if zone is None:
        return None, "NO_CAUSAL_FIRST_RETURN_LOCATION"
    zone_lower, zone_upper, zone_kind = zone
    entry = dlp2._entry_price(zone_lower, zone_upper, signal.side, signal)
    favorable = (
        entry < decision_price - tick
        if signal.side == "LONG"
        else entry > decision_price + tick
    )
    if not favorable:
        return None, "FIRST_RETURN_PRICE_NOT_BEHIND_COMPLETED_RESPONSE"

    stop = geometry.stop_price(data, signal, zone_lower, zone_upper, tick)
    if not (stop < entry if signal.side == "LONG" else stop > entry):
        return None, "INVALID_EVENT_INVALIDATION"

    destination = geometry.choose_destination(
        data,
        levels,
        metadata,
        nodes_by_scale,
        decision,
        entry,
        signal.side,
        atr,
        tick,
    )
    if destination is None:
        return None, "NO_LIVE_OPPOSING_LIQUIDITY"
    target = (
        float(destination.lower) - tick
        if signal.side == "LONG"
        else float(destination.upper) + tick
    )
    risk = abs(entry - stop)
    gross_rr = sign(signal.side) * (target - entry) / max(risk, EPS)
    if gross_rr < 1.0:
        return None, "REAL_LIQUIDITY_DESTINATION_PAYS_LESS_THAN_1R"
    economics = geometry.economics(signal.side, entry, stop, target, tick)
    if economics is None or float(economics["target_net_r"]) <= 0.0:
        return None, "NON_POSITIVE_POST_COST_DESTINATION"

    expiry = geometry.pending_expiry(signal, small_nodes, len(data))
    label = geometry.resolve_order(data, signal, entry, stop, target, tick, expiry)
    episode_id = (
        f"SLR1:{symbol}:{int(data.index[signal.interaction_index].value)}:"
        f"{signal.family}:"
        f"{stable(signal.source.source_id if signal.source else signal.context_scale, decision)}"
    )
    route_quality = math.log1p(max(float(destination.strength), 0.0))
    opportunity_score = (
        alignment
        + 0.14 * min(gross_rr, 4.0)
        + 0.09 * route_quality
        + 0.06 * math.log1p(max(float(objective.route_room_atr), 0.0))
    )
    source_strength = (
        float(signal.source.strength)
        if signal.source is not None
        else float(1.0 + MEDIUM_SCALE)
    )
    source_confluence = (
        int(signal.source.confluence_count) if signal.source is not None else 1
    )
    return {
        "order_exists": True,
        "action_id": f"{episode_id}:ONE_PLAN",
        "state_id": f"SLR1STATE:{stable(episode_id, decision)}",
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
        "planned_target_net_r": float(economics["target_net_r"]),
        "route_kind": str(destination.kind),
        "route_price": float(destination.price),
        "route_strength": float(destination.strength),
        "route_scale": float(destination.scale),
        "source_kind": (
            signal.source.kind
            if signal.source is not None
            else "LIVE_MEDIUM_AUCTION_LEG"
        ),
        "source_strength": source_strength,
        "source_scale": (
            float(signal.source.scale)
            if signal.source is not None
            else float(MEDIUM_SCALE * 60.0)
        ),
        "source_confluence_count": source_confluence,
        "zone_lower": float(zone_lower),
        "zone_upper": float(zone_upper),
        "event_extreme": float(signal.event_extreme),
        "pullback_extreme": float(signal.pullback_extreme),
        "auction_response_score": float(event_response),
        "decision_alignment": float(alignment),
        "opportunity_score": float(opportunity_score),
        **{f"direction_{key}": value for key, value in directional.to_dict().items()},
        **{f"objective_{key}": value for key, value in objective.to_dict().items()},
        **signal.evidence,
        **asdict(label),
    }, "PLAN_CREATED"


def generate_symbol(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[Any],
    metadata: dict[str, Any],
    trading_start: Any,
) -> tuple[pd.DataFrame, dict[str, int]]:
    tick = core.CONTRACTS[symbol].tick_size
    start = pd.Timestamp(trading_start)
    start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    start_index = int(data.index.searchsorted(start))
    decision_end_ns = getattr(dlp2.base_policy.fixed, "_DECISION_END_NS", None)
    end_index = (
        len(data)
        if decision_end_ns is None
        else int(data.index.searchsorted(pd.Timestamp(decision_end_ns, unit="ns", tz="UTC")))
    )

    combined_levels = list(levels)
    combined_metadata = dict(metadata)
    dynamic_count = 0
    dynamic_channel_count = 0
    data.attrs["symbol"] = symbol
    dynamic_boundaries = restored.dynamic_boundaries
    if dynamic_boundaries is not None:
        models = dynamic_boundaries.build_dynamic_boundaries(
            symbol,
            data,
            combined_levels,
            tick,
        )
        dynamic_levels, dynamic_metadata = dynamic_boundaries.source_levels(
            symbol,
            data,
            models,
            tick,
            combined_levels,
        )
        combined_levels.extend(dynamic_levels)
        combined_metadata.update(dynamic_metadata)
        dynamic_count = len(dynamic_levels)
        dynamic_channel_count = sum(
            "CHANNEL" in str(level.source_kind) for level in dynamic_levels
        )

    atr = atr_array(data)
    nodes_by_scale = {
        scale: dc.directional_change(data, scale, atr)
        for scale in (EVENT_SCALE, MEDIUM_SCALE, LARGE_SCALE)
    }
    source_events = merge_source_events(
        [
            *semantic_source_events(combined_levels, combined_metadata, data),
            *dc_source_events(data, nodes_by_scale, atr),
        ],
        atr,
        tick,
    )
    small_nodes = nodes_by_scale[EVENT_SCALE]
    signals: list[EpisodeSignal] = []
    external_events = 0
    for source in source_events:
        if int(source.interaction_index) < start_index or int(source.interaction_index) >= end_index:
            continue
        external_events += 1
        failed = failed_signal(data, source, small_nodes, atr)
        accepted = accepted_signal(data, source, small_nodes, atr)
        available = [item for item in (failed, accepted) if item is not None]
        if available:
            available.sort(
                key=lambda item: (
                    int(item.decision_index),
                    -finite(item.evidence.get("auction_response_score")),
                )
            )
            signals.append(available[0])
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
    signals = dedupe_signals(signals)

    records: list[dict[str, Any]] = []
    plan_count = 0
    for signal in signals:
        if int(signal.decision_index) >= end_index:
            continue
        plan, reason = plan_from_signal(
            symbol,
            data,
            combined_levels,
            combined_metadata,
            nodes_by_scale,
            small_nodes,
            signal,
            atr,
            tick,
        )
        if plan is None:
            row = geometry.no_plan_record(symbol, signal, data, reason, atr)
            row.update(signal.evidence)
            row["policy_version"] = "skilled-liquidity-response-v1"
            records.append(_complete_no_order_row(row, signal, data))
        else:
            plan["policy_version"] = "skilled-liquidity-response-v1"
            plan["symbol_identity_used_for_decision"] = False
            plan["fitted_admission_model_used"] = False
            plan["one_plan_per_episode"] = True
            plan["target_selected_before_rr"] = True
            records.append(plan)
            plan_count += 1
    frame = pd.DataFrame(records)
    return frame, {
        "semantic_and_dc_source_events": int(len(source_events)),
        "external_events_in_window": int(external_events),
        "causal_response_signals": int(len(signals)),
        "one_plan_episodes": int(plan_count),
        "no_trade_episodes": int(len(records) - plan_count),
        "plans": int(len(records)),
        "uses_outcome_in_generation": 0,
        "one_plan_per_episode": 1,
        "fixed_rr_target_lattice": 0,
        "fitted_admission_model": 0,
        "symbol_identity_feature": 0,
        "dynamic_liquidity_sources": int(dynamic_count),
        "dynamic_channel_sources": int(dynamic_channel_count),
        "dynamic_geometry_available": int(dynamic_boundaries is not None),
        "auction_response_is_causal": 1,
    }


__all__ = ["generate_symbol", "plan_from_signal"]

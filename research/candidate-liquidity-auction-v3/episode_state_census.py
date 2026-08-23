"""Episode-conditioned state/action census without a legacy response gate.

A public liquidity interaction starts an episode.  From then on, the market can be in
one of three observable states relative to that same boundary: accepted outside,
rejected back inside, or unresolved in the boundary zone.  The previous systems chose a
single response with hand-written boolean rules; if that response was wrong, ML could
only reject the finished bad plan.  This census instead exposes the evolving causal path
at sparse online decision times.  Each state owns exactly one honest market-entry plan:

* rejected inside -> trade away from the swept boundary, stop beyond the episode extreme;
* accepted outside -> trade with the break, stop beyond the transferred boundary;
* target -> nearest still-unconsumed static, volume, trendline, or channel obstacle.

Every candidate is immutable and is labelled only after emission.  One episode may have
several decision-time candidates for learning, but the account router may execute at
most one of them.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Sequence
import json
import math

import numpy as np
import pandas as pd

import coherent_policy as core
import coherent_policy_v2 as rich
import coherent_system as semantic
import coherent_system_v4 as execution
import hierarchical_liquidity_bpr as hl
import liquidity_auction_dynamic as dynamic_policy
from auction_episode_research import ActionSpec, CONTRACTS, _economics, _stable_id
from derivatives_dislocation import prepare_market_state
from dynamic_boundaries import boundary_for_source
from liquidity_displacement import _add_common_state
from market_data import load_range as load_reference_range
from metrics_state import load_range_metrics
from semantic_liquidity_full import PoolMeta


POLICY = (
    "PUBLIC_HORIZONTAL_OR_DYNAMIC_LIQUIDITY_INTERACTION_THEN_ONLINE_ACCEPTED_"
    "OUTSIDE_OR_REJECTED_INSIDE_STATE_PATH_THEN_NEXT_OPEN_MARKET_ENTRY_THEN_"
    "EPISODE_EXTREME_OR_TRANSFERRED_BOUNDARY_INVALIDATION_THEN_NEAREST_"
    "UNCONSUMED_STATIC_VOLUME_TRENDLINE_OR_CHANNEL_OBSTACLE"
)
MAX_EPISODE_MINUTES = 45
DECISION_INTERVAL_MINUTES = 3
ENTRY_SLIPPAGE_TICKS = execution.ENTRY_SLIPPAGE_TICKS
STOP_SLIPPAGE_TICKS = execution.STOP_SLIPPAGE_TICKS
EPS = 1e-12
RESOLVED_OUTCOMES = {
    "TARGET_FIRST",
    "STOP_FIRST",
    "AMBIGUOUS_SAME_MINUTE",
    "AMBIGUOUS_FILL_BARRIER_SAME_MINUTE",
    "TIME_EXIT",
}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _sign(side: str) -> float:
    return 1.0 if side == "LONG" else -1.0


def _source_geometry(
    source: hl.LiquidityLevel,
    index: int,
) -> tuple[float, float, float]:
    model = boundary_for_source(source.level_id)
    if model is None:
        center = float(source.price)
        lower = float(source.lower)
        upper = float(source.upper)
        return center, lower, upper
    center = float(model.value_at(index))
    interaction_half = max(
        0.5 * abs(float(source.upper) - float(source.lower)),
        1.5 * float(model.residual_price),
    )
    return center, center - interaction_half, center + interaction_half


def _observable_state(
    source: hl.LiquidityLevel,
    data: pd.DataFrame,
    index: int,
) -> tuple[str | None, str | None, float, float, float]:
    center, lower, upper = _source_geometry(source, index)
    close = float(data.iloc[index].close)
    if source.side == "LOW":
        if close > upper:
            return "REJECTED_INSIDE", "LONG", center, lower, upper
        if close < lower:
            return "ACCEPTED_OUTSIDE", "SHORT", center, lower, upper
    else:
        if close < lower:
            return "REJECTED_INSIDE", "SHORT", center, lower, upper
        if close > upper:
            return "ACCEPTED_OUTSIDE", "LONG", center, lower, upper
    return None, None, center, lower, upper


def _prior_wick_noise(
    data: pd.DataFrame,
    index: int,
    side: str,
    tick: float,
) -> float:
    frame = data.iloc[max(0, index - 120):index]
    if frame.empty:
        return 2.0 * tick
    if side == "LONG":
        wick = (
            np.minimum(frame.open.to_numpy(float), frame.close.to_numpy(float))
            - frame.low.to_numpy(float)
        )
    else:
        wick = (
            frame.high.to_numpy(float)
            - np.maximum(frame.open.to_numpy(float), frame.close.to_numpy(float))
        )
    wick = wick[np.isfinite(wick) & (wick >= 0.0)]
    value = float(np.median(wick)) if len(wick) else 0.0
    if value <= 0.0:
        value = _finite((frame.high - frame.low).median(), 0.0) / 2.0
    return max(2.0 * tick, value)


def _episode_stop(
    data: pd.DataFrame,
    source: hl.LiquidityLevel,
    interaction: int,
    decision: int,
    state: str,
    side: str,
    lower: float,
    upper: float,
    tick: float,
) -> tuple[str, float]:
    noise = _prior_wick_noise(data, decision, side, tick)
    frame = data.iloc[interaction:decision + 1]
    if state == "REJECTED_INSIDE":
        if side == "LONG":
            reference = float(frame.low.min())
            return "EPISODE_EXTREME_WICK_NOISE_INVALIDATION", reference - noise
        reference = float(frame.high.max())
        return "EPISODE_EXTREME_WICK_NOISE_INVALIDATION", reference + noise
    if side == "LONG":
        return "TRANSFERRED_BOUNDARY_WICK_NOISE_INVALIDATION", lower - noise
    return "TRANSFERRED_BOUNDARY_WICK_NOISE_INVALIDATION", upper + noise


def _branch_state_features(
    data: pd.DataFrame,
    source: hl.LiquidityLevel,
    interaction: int,
    decision: int,
    state: str,
    side: str,
) -> dict[str, float]:
    frame = data.iloc[interaction:decision + 1]
    sign = _sign(side)
    atr = max(core._atr_price(data, decision), EPS)
    centers: list[float] = []
    lowers: list[float] = []
    uppers: list[float] = []
    states: list[int] = []
    distances: list[float] = []
    for offset, (_, row) in enumerate(frame.iterrows()):
        index = interaction + offset
        center, lower, upper = _source_geometry(source, index)
        centers.append(center)
        lowers.append(lower)
        uppers.append(upper)
        close = float(row.close)
        if source.side == "LOW":
            raw_state = 1 if close > upper else -1 if close < lower else 0
        else:
            raw_state = 1 if close < lower else -1 if close > upper else 0
        # +1 means rejected inside, -1 accepted outside, independent of side.
        states.append(raw_state)
        distances.append(sign * (close - center) / atr)
    closes = frame.close.to_numpy(float)
    minute_returns = np.diff(closes, prepend=closes[0])
    signed_returns = sign * minute_returns / np.maximum(np.abs(closes), EPS) * 10_000.0
    signed_quote = 2.0 * frame.taker_buy_quote_volume.to_numpy(float) - frame.quote_volume.to_numpy(float)
    quote = np.maximum(frame.quote_volume.to_numpy(float), EPS)
    signed_delta = sign * signed_quote / quote
    path = float(np.abs(np.diff(closes)).sum())
    net = float(closes[-1] - closes[0]) if len(closes) > 1 else 0.0
    state_array = np.asarray(states, dtype=int)
    transition_count = int(np.sum(state_array[1:] != state_array[:-1])) if len(state_array) > 1 else 0
    desired_raw_state = 1 if state == "REJECTED_INSIDE" else -1
    dwell = 0
    for value in state_array[::-1]:
        if value != desired_raw_state:
            break
        dwell += 1
    common = np.asarray(
        [
            _finite(value, 0.0)
            for value in frame.get("common_return_1m", pd.Series(0.0, index=frame.index))
        ],
        dtype=float,
    )
    residual = np.asarray(
        [
            _finite(value, 0.0)
            for value in frame.get("residual_return_1m", pd.Series(0.0, index=frame.index))
        ],
        dtype=float,
    )
    progress = signed_returns > 0.0
    flow_with = signed_delta > 0.0
    output = {
        "episode_age_minutes": float(decision - interaction),
        "episode_inside_fraction": float(np.mean(state_array == 1)),
        "episode_outside_fraction": float(np.mean(state_array == -1)),
        "episode_zone_fraction": float(np.mean(state_array == 0)),
        "episode_state_transition_count": float(transition_count),
        "episode_current_state_dwell": float(dwell),
        "episode_current_boundary_distance_atr_signed": float(distances[-1]),
        "episode_max_boundary_distance_atr_signed": float(max(distances)),
        "episode_min_boundary_distance_atr_signed": float(min(distances)),
        "episode_net_return_bps_signed": sign * net / max(abs(closes[-1]), EPS) * 10_000.0,
        "episode_path_efficiency_signed": sign * net / max(path, EPS),
        "episode_cumulative_delta_share_signed": float(signed_quote.sum() * sign / max(quote.sum(), EPS)),
        "episode_initiative_fraction": float(np.mean(progress & flow_with)),
        "episode_absorption_fraction": float(np.mean(progress & ~flow_with)),
        "episode_failed_initiative_fraction": float(np.mean(~progress & flow_with)),
        "episode_mean_activity_ratio": _finite(frame.activity_ratio.mean(), 0.0),
        "episode_max_activity_ratio": _finite(frame.activity_ratio.max(), 0.0),
        "episode_mean_range_ratio": _finite(frame.range_ratio.mean(), 0.0),
        "episode_max_range_ratio": _finite(frame.range_ratio.max(), 0.0),
        "episode_common_return_signed": sign * float(np.nansum(common)),
        "episode_residual_return_signed": sign * float(np.nansum(residual)),
        "episode_residual_minus_common_signed": sign * float(np.nansum(residual - common)),
        "episode_boundary_drift_bps": abs(centers[-1] - centers[0])
        / max(abs(centers[0]), EPS)
        * 10_000.0,
        "episode_zone_width_bps": (uppers[-1] - lowers[-1])
        / max(abs(centers[-1]), EPS)
        * 10_000.0,
    }
    # Preserve the most recent twelve one-minute observations rather than only
    # aggregate blocks.  Missing early lags are explicit zeros.
    for lag in range(12):
        index = len(frame) - 1 - lag
        prefix = f"episode_lag_{lag}"
        if index < 0:
            output.update(
                {
                    f"{prefix}_return_bps_signed": 0.0,
                    f"{prefix}_delta_share_signed": 0.0,
                    f"{prefix}_activity_ratio": 0.0,
                    f"{prefix}_range_ratio": 0.0,
                    f"{prefix}_close_location_signed": 0.0,
                    f"{prefix}_boundary_distance_atr_signed": 0.0,
                }
            )
            continue
        row = frame.iloc[index]
        output.update(
            {
                f"{prefix}_return_bps_signed": float(signed_returns[index]),
                f"{prefix}_delta_share_signed": float(signed_delta[index]),
                f"{prefix}_activity_ratio": _finite(row.get("activity_ratio"), 0.0),
                f"{prefix}_range_ratio": _finite(row.get("range_ratio"), 0.0),
                f"{prefix}_close_location_signed": sign
                * (2.0 * _finite(row.get("close_location"), 0.5) - 1.0),
                f"{prefix}_boundary_distance_atr_signed": float(distances[index]),
            }
        )
    return output


def _dynamic_source_features(
    source: hl.LiquidityLevel,
    interaction: int,
    decision: int,
) -> dict[str, float]:
    model = boundary_for_source(source.level_id)
    if model is None:
        return {
            "dynamic_source_present": 0.0,
            "dynamic_source_is_channel": 0.0,
            "dynamic_source_quality": 0.0,
            "dynamic_source_channel_quality": 0.0,
            "dynamic_source_slope_atr_per_hour": 0.0,
            "dynamic_source_anchor_count": 0.0,
            "dynamic_source_drift_to_decision_bps": 0.0,
        }
    initial = float(model.value_at(interaction))
    current = float(model.value_at(decision))
    return {
        "dynamic_source_present": 1.0,
        "dynamic_source_is_channel": float(model.is_channel_edge),
        "dynamic_source_quality": float(model.quality),
        "dynamic_source_channel_quality": float(model.channel_quality),
        "dynamic_source_slope_atr_per_hour": float(model.normalized_slope),
        "dynamic_source_anchor_count": float(model.anchor_count),
        "dynamic_source_drift_to_decision_bps": abs(current - initial)
        / max(abs(initial), EPS)
        * 10_000.0,
    }


def _common_features(
    data: pd.DataFrame,
    levels: Sequence[hl.LiquidityLevel],
    metadata: dict[str, PoolMeta],
    source: hl.LiquidityLevel,
    interaction: int,
    decision: int,
    state: str,
    side: str,
    obstacle: execution.Obstacle,
    route_features: dict[str, float],
    entry: float,
    stop: float,
) -> dict[str, Any]:
    source_meta = metadata[source.level_id]
    target_meta = (
        metadata.get(obstacle.source_level_id)
        if obstacle.source_level_id is not None
        else None
    )
    tick = CONTRACTS[source.symbol].tick_size
    economics = _economics(
        side=side,
        entry=entry,
        stop=stop,
        target=obstacle.order_price,
        tick_size=tick,
        entry_style="MARKET",
    )
    planned_target_r = economics["target_net_r"] / max(
        abs(economics["stop_net_r"]),
        EPS,
    )
    features: dict[str, Any] = {
        **economics,
        "planned_account_target_r": planned_target_r,
        "planned_account_stop_r": -1.0,
        **core._liquidity_map_features(data, levels, decision),
        **semantic._semantic_map_features(data, levels, metadata, decision),
        **core._active_structure_features(data, levels, decision),
        **core._approach_features(data, interaction, source),
        **core._row_state_features(data, interaction, side, "event"),
        **core._row_state_features(data, decision, side, "decision"),
        **core._clock_features(pd.Timestamp(data.index[decision])),
        **rich._anchored_vwap_features(data, interaction, decision, side),
        **rich._sequence_features(data, decision, side),
        **rich._source_accumulation_features(data, source, interaction),
        **rich._volume_route_features(data, decision, entry, obstacle.order_price),
        **route_features,
        **_branch_state_features(
            data,
            source,
            interaction,
            decision,
            state,
            side,
        ),
        **_dynamic_source_features(source, interaction, decision),
        "narrative_branch": (
            "FAILED_AUCTION_REVERSAL"
            if state == "REJECTED_INSIDE"
            else "ACCEPTED_AUCTION_CONTINUATION"
        ),
        "setup_kind": "ONLINE_EPISODE_STATE",
        "location_kind": source_meta.pool_kind,
        "response_kind": state,
        "source_pool_kind": source_meta.pool_kind,
        "source_pool_members": float(source_meta.member_count),
        "source_pool_accumulated": float(source_meta.accumulated),
        "source_semantic_weight": float(source_meta.semantic_weight),
        "source_scale_minutes": float(source.timeframe_minutes),
        "source_strength_ratio": _finite(source.strength_ratio),
        "source_defense_count": float(source.defense_count),
        "source_age_minutes": float(decision - source.observed_index_1m),
        "target_pool_kind": target_meta.pool_kind if target_meta else obstacle.kind,
        "target_pool_members": float(target_meta.member_count) if target_meta else 0.0,
        "target_pool_accumulated": float(target_meta.accumulated) if target_meta else 0.0,
        "target_semantic_weight": float(target_meta.semantic_weight)
        if target_meta
        else float(obstacle.strength),
        "target_scale_minutes": float(obstacle.timeframe_minutes),
        "target_strength_ratio": float(obstacle.strength),
        "event_penetration_bps": abs(
            float(data.iloc[interaction].low if source.side == "LOW" else data.iloc[interaction].high)
            - float(source.price)
        )
        / max(abs(float(source.price)), EPS)
        * 10_000.0,
        "event_to_decision_minutes": float(decision - interaction),
        "diagnostic_event_time_ns": int(data.index[interaction].value),
        "diagnostic_decision_time_ns": int(data.index[decision].value),
        "diagnostic_target_level_id": obstacle.obstacle_id,
        "diagnostic_target_structure_price": obstacle.structure_price,
    }
    return features


def _owned_sources(
    levels: Sequence[hl.LiquidityLevel],
    metadata: dict[str, PoolMeta],
) -> list[hl.LiquidityLevel]:
    sources = sorted(
        dynamic_policy.direction_sources(levels, metadata),
        key=lambda level: (
            int(level.first_penetration_index),
            level.side,
            -metadata[level.level_id].semantic_weight,
            -level.timeframe_minutes,
            level.level_id,
        ),
    )
    output: list[hl.LiquidityLevel] = []
    seen: set[tuple[int, str]] = set()
    for source in sources:
        interaction = int(source.first_penetration_index)
        key = (interaction, source.side)
        if key in seen:
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
                metadata[level.level_id].semantic_weight,
                level.timeframe_minutes,
                level.defense_count,
                level.strength_ratio,
                level.level_id,
            ),
        )
        seen.add(key)
        output.append(owner)
    return output


def generate_symbol(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[hl.LiquidityLevel],
    metadata: dict[str, PoolMeta],
    trading_start: date,
    trading_end: date,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    tick = CONTRACTS[symbol].tick_size
    start_ns = int(pd.Timestamp(trading_start, tz="UTC").value)
    cutoff_ns = int(pd.Timestamp(trading_end, tz="UTC").value)
    sources = _owned_sources(levels, metadata)
    records: list[dict[str, Any]] = []
    state_rows: list[dict[str, Any]] = []
    counts = {
        "semantic_sources": len(sources),
        "source_interactions": 0,
        "observable_state_points": 0,
        "executable_actions": 0,
    }
    for source in sources:
        interaction = int(source.first_penetration_index)
        if interaction < 0 or interaction >= len(data) - 2:
            continue
        interaction_ns = int(data.index[interaction].value)
        if interaction_ns < start_ns or interaction_ns >= cutoff_ns:
            continue
        counts["source_interactions"] += 1
        episode_id = f"LA3:{symbol}:{interaction_ns}:{_stable_id(source.level_id)}"
        previous_state: str | None = None
        state_start = interaction
        end = min(
            len(data) - 2,
            interaction + MAX_EPISODE_MINUTES,
            int(data.index.searchsorted(pd.Timestamp(trading_end, tz="UTC"), side="left")) - 1,
        )
        for decision in range(interaction + 1, end + 1):
            state, side, center, lower, upper = _observable_state(source, data, decision)
            if state is None or side is None:
                previous_state = None
                continue
            if state != previous_state:
                state_start = decision
                should_emit = True
            else:
                should_emit = (decision - state_start) % DECISION_INTERVAL_MINUTES == 0
            previous_state = state
            if not should_emit:
                continue
            counts["observable_state_points"] += 1
            entry = float(data.iloc[decision].close)
            stop_name, stop = _episode_stop(
                data,
                source,
                interaction,
                decision,
                state,
                side,
                lower,
                upper,
                tick,
            )
            obstacle, route_features = dynamic_policy._first_obstacle(
                data,
                levels,
                metadata,
                decision,
                entry,
                side,
                tick,
            )
            if obstacle is None:
                continue
            valid = (
                stop < entry < obstacle.order_price
                if side == "LONG"
                else obstacle.order_price < entry < stop
            )
            if not valid:
                continue
            economics = _economics(
                side=side,
                entry=entry,
                stop=stop,
                target=obstacle.order_price,
                tick_size=tick,
                entry_style="MARKET",
            )
            if (
                not economics
                or economics["gross_rr"] < 1.0
                or economics["target_net_r"] <= 0.0
                or economics["stop_net_r"] >= 0.0
            ):
                continue
            features = _common_features(
                data,
                levels,
                metadata,
                source,
                interaction,
                decision,
                state,
                side,
                obstacle,
                route_features,
                entry,
                stop,
            )
            branch = str(features["narrative_branch"])
            entry_geometry = "ONLINE_EPISODE_STATE_MARKET"
            features.update(
                {
                    "state_id": f"LA3STATE:{episode_id}:{decision}:{branch}",
                    "entry_geometry": entry_geometry,
                    "stop_geometry": stop_name,
                }
            )
            action_id = (
                f"{episode_id}:{decision}:{branch}:{entry_geometry}:{stop_name}:"
                f"{obstacle.kind}"
            )
            action = ActionSpec(
                action_id=action_id,
                episode_id=episode_id,
                symbol=symbol,
                event_type=branch,
                decision_stage="ONLINE_EPISODE_STATE",
                side=side,
                emission_index=decision,
                emission_time_ns=int(data.index[decision].value),
                entry_style="MARKET",
                entry=entry,
                stop=float(stop),
                target=float(obstacle.order_price),
                entry_expiry_minutes=1,
                source_level_id=source.level_id,
                source_kind=source.source_kind,
                source_timeframe_minutes=source.timeframe_minutes,
                source_span=source.span,
                source_price=source.price,
                source_lower=source.lower,
                source_upper=source.upper,
                source_strength_ratio=source.strength_ratio,
                source_defense_count=source.defense_count,
                source_age_minutes=float(decision - source.observed_index_1m),
                objective_id=obstacle.obstacle_id,
                objective_kind=obstacle.kind,
                objective_timeframe_minutes=obstacle.timeframe_minutes,
                objective_strength_ratio=obstacle.strength,
                interaction_time_ns=interaction_ns,
                feature_values=features,
            )
            label = execution.label_action(data, action, tick)
            records.append(
                {
                    **{
                        key: value
                        for key, value in asdict(action).items()
                        if key != "feature_values"
                    },
                    **features,
                    **asdict(label),
                }
            )
            state_rows.append(
                {
                    "state_id": features["state_id"],
                    "symbol": symbol,
                    "episode_id": episode_id,
                    "emission_index": decision,
                    "emission_time_ns": int(data.index[decision].value),
                    "action_side": side,
                    **features,
                }
            )
            counts["executable_actions"] += 1
    frame = pd.DataFrame(records)
    states = pd.DataFrame(state_rows)
    if not frame.empty and frame.action_id.duplicated().any():
        raise RuntimeError(f"duplicate episode-state action {symbol}")
    summary = {
        "symbol": symbol,
        "bars": len(data),
        "semantic_levels": len(levels),
        **counts,
        "episodes_with_actions": int(frame.episode_id.nunique()) if not frame.empty else 0,
        "outcomes": frame.outcome.value_counts().to_dict() if not frame.empty else {},
        "branches": frame.narrative_branch.value_counts().to_dict() if not frame.empty else {},
        "source_kinds": frame.source_kind.value_counts().to_dict() if not frame.empty else {},
    }
    return frame, states, summary


def run_research(
    *,
    start: date,
    end: date,
    warmup_days: int,
    symbols: Sequence[str],
    cache: Path,
    output: Path,
) -> dict[str, Any]:
    from data_re1_flow import load_range_flow

    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    load_start = start - timedelta(days=warmup_days)
    label_end = end + timedelta(days=1)
    prepared: dict[str, pd.DataFrame] = {}
    levels_by: dict[str, list[hl.LiquidityLevel]] = {}
    metadata_by: dict[str, dict[str, PoolMeta]] = {}
    for symbol in symbols:
        tick = CONTRACTS[symbol].tick_size
        raw = load_range_flow(symbol, load_start, label_end, cache)
        index_price = load_reference_range(
            "indexPriceKlines",
            symbol,
            load_start,
            label_end,
            cache,
        )
        mark_price = load_reference_range(
            "markPriceKlines",
            symbol,
            load_start,
            label_end,
            cache,
        )
        metrics = load_range_metrics(symbol, load_start, label_end, cache)
        state = prepare_market_state(
            raw,
            index_price,
            mark_price,
            metrics,
            tick,
        )
        levels, metadata = dynamic_policy.build_semantic_liquidity(
            symbol,
            state,
            raw,
            tick,
        )
        prepared[symbol] = state
        levels_by[symbol] = levels
        metadata_by[symbol] = metadata
    prepared = _add_common_state(prepared)
    actions_all: list[pd.DataFrame] = []
    states_all: list[pd.DataFrame] = []
    by_symbol: dict[str, Any] = {}
    for symbol in symbols:
        actions, states, summary = generate_symbol(
            symbol,
            prepared[symbol],
            levels_by[symbol],
            metadata_by[symbol],
            start,
            end,
        )
        by_symbol[symbol] = summary
        if not actions.empty:
            actions.to_csv(output / f"{symbol}_episode_state_actions.csv", index=False)
            actions_all.append(actions)
        if not states.empty:
            states.to_csv(output / f"{symbol}_episode_states.csv", index=False)
            states_all.append(states)
    actions = (
        pd.concat(actions_all, ignore_index=True, sort=False)
        if actions_all
        else pd.DataFrame()
    )
    states = (
        pd.concat(states_all, ignore_index=True, sort=False)
        if states_all
        else pd.DataFrame()
    )
    actions.to_csv(output / "coherent_actions.csv", index=False)
    states.to_csv(output / "destination_states.csv", index=False)
    resolved = (
        actions[actions.outcome.astype(str).isin(RESOLVED_OUTCOMES)]
        if not actions.empty
        else actions
    )
    summary = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "label_data_end": label_end.isoformat(),
        "symbols": list(symbols),
        "actions": int(len(actions)),
        "episodes": int(actions.episode_id.nunique()) if not actions.empty else 0,
        "resolved_actions": int(len(resolved)),
        "wins": int(resolved.outcome.astype(str).eq("TARGET_FIRST").sum())
        if len(resolved)
        else 0,
        "win_rate": float(
            resolved.outcome.astype(str).eq("TARGET_FIRST").mean()
        )
        if len(resolved)
        else None,
        "mean_account_r": float(pd.to_numeric(resolved.net_r, errors="coerce").mean())
        if len(resolved)
        else None,
        "by_symbol": by_symbol,
        "policy": POLICY,
        "future_information_in_features": False,
        "future_information_in_labels_only": True,
        "one_episode_multiple_learning_states": True,
        "runtime_account_may_execute_one_state_per_episode": True,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


__all__ = ["POLICY", "generate_symbol", "run_research"]

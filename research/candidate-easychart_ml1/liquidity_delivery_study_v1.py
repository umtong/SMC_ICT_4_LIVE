#!/usr/bin/env python3
"""Direction-first liquidity-delivery study for the EasyChart ML branch.

The module deliberately does not create separate OB, FVG, trendline, channel or
fakeout strategies.  A candidate exists only after a top-down delivery state and
one meaningful liquidity interaction agree.  Structure describes the path,
price/volume describes the event, and OB/FVG/BPR are alternative execution
locations inside the same causal episode.

Every feature is observable no later than ``decision_ts``.  Barrier and trade
outcomes are attached afterwards as research labels.  Overlapping pool aliases
remain one episode and downstream policy may select at most one action.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
import json
import math
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

import bpr_transfer_study as bpr
import liquidity_event_graph as graph
from auction_transition_study import Pivot, aggregate, add_cross_features, make_features
from data_derivatives import join_metrics_causally, load_metrics_range
from data_funding import load_funding_range
from data_re1_flow import load_range_flow
from data_spot_flow import load_spot_range_flow
from liquidity_narrative_features import liquidity_map_features, narrative_phase_features
from spot_perp_context import add_spot_perp_context

SYMBOLS = bpr.SYMBOLS
TICKS = bpr.TICKS
DELIVERY_TIMEFRAMES = (5, 15, 60)
DESTINATION_HORIZON_MINUTES = 480
LABEL_EXTENSION_DAYS = 1


@dataclass(frozen=True)
class Barrier:
    side: str
    level: float
    identity: str
    timeframe: int
    kind: str
    strength: float
    observed_ts: pd.Timestamp


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _pool_live(pool: Any, asof: pd.Timestamp) -> bool:
    return (
        pool.observed_ts < asof
        and (pool.first_interaction_ts is None or pool.first_interaction_ts > asof)
    )


def _barrier_candidates(
    frame: pd.DataFrame,
    pools: Sequence[Any],
    pivots: Sequence[Pivot],
    asof: pd.Timestamp,
    price: float,
    min_timeframe: int,
    excluded_pool_ids: set[str] | None = None,
) -> tuple[list[Barrier], list[Barrier]]:
    excluded = excluded_pool_ids or set()
    highs: list[Barrier] = []
    lows: list[Barrier] = []
    for pool in pools:
        if (
            pool.pool_id in excluded
            or pool.timeframe < min_timeframe
            or not _pool_live(pool, asof)
        ):
            continue
        barrier = Barrier(
            side=pool.side,
            level=float(pool.inner),
            identity=pool.pool_id,
            timeframe=int(pool.timeframe),
            kind="POOL",
            strength=float(pool.strength) / (1.0 + max(float(pool.equality_atr), 0.0)),
            observed_ts=pool.observed_ts,
        )
        if pool.side == "HIGH" and barrier.level > price:
            highs.append(barrier)
        elif pool.side == "LOW" and barrier.level < price:
            lows.append(barrier)

    # Pivots are fallback destinations, not equal-liquidity aliases.  Keep only
    # those still untouched as of the decision.
    for pivot in pivots:
        if (
            pivot.timeframe < min_timeframe
            or pivot.observed_ts >= asof
            or not graph.pivot_is_live(frame, pivot, asof)
        ):
            continue
        barrier = Barrier(
            side=pivot.side,
            level=float(pivot.price),
            identity=pivot.pivot_id,
            timeframe=int(pivot.timeframe),
            kind="PIVOT",
            strength=float(pivot.strength),
            observed_ts=pivot.observed_ts,
        )
        if pivot.side == "HIGH" and barrier.level > price:
            highs.append(barrier)
        elif pivot.side == "LOW" and barrier.level < price:
            lows.append(barrier)
    highs.sort(key=lambda item: (item.level, -item.timeframe, -item.strength, item.identity))
    lows.sort(key=lambda item: (-item.level, -item.timeframe, -item.strength, item.identity))
    return highs, lows


def _nearest_barriers(
    frame: pd.DataFrame,
    pools: Sequence[Any],
    pivots: Sequence[Pivot],
    asof: pd.Timestamp,
    price: float,
    min_timeframe: int,
    excluded_pool_ids: set[str] | None = None,
) -> tuple[Barrier | None, Barrier | None]:
    highs, lows = _barrier_candidates(
        frame,
        pools,
        pivots,
        asof,
        price,
        min_timeframe,
        excluded_pool_ids,
    )
    return (highs[0] if highs else None, lows[0] if lows else None)


def _regression_channel_features(
    frame: pd.DataFrame,
    asof: pd.Timestamp,
    timeframe: int,
    bars: int,
) -> dict[str, float]:
    sampled = aggregate(frame.loc[frame.index <= asof], timeframe)
    sampled = sampled.loc[sampled.index <= asof].tail(bars)
    prefix = f"state_{timeframe}m"
    if len(sampled) < max(8, bars // 3):
        return {}
    close = sampled.close.astype(float).clip(lower=1e-12)
    y = np.log(close.to_numpy())
    x = np.arange(len(y), dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    fitted = intercept + slope * x
    residual = y - fitted
    residual_scale = max(float(np.quantile(np.abs(residual), 0.80)), 1e-9)
    total = float(np.sum((y - y.mean()) ** 2))
    unexplained = float(np.sum(residual**2))
    r2 = 0.0 if total <= 1e-18 else max(0.0, 1.0 - unexplained / total)
    step_scale = max(float(np.median(np.abs(np.diff(y)))), 1e-9)
    high = sampled.high.astype(float)
    low = sampled.low.astype(float)
    tr = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = max(float(tr.median()), 1e-12)
    width = float(high.max() - low.min())
    recent_n = max(4, len(sampled) // 4)
    recent_width = float(high.tail(recent_n).max() - low.tail(recent_n).min())
    earlier = sampled.iloc[:-recent_n]
    earlier_width = (
        float(earlier.high.max() - earlier.low.min())
        if not earlier.empty
        else width
    )
    path = float(np.abs(np.diff(y)).sum())
    net = float(y[-1] - y[0])
    return {
        f"{prefix}_slope_step": float(slope / step_scale),
        f"{prefix}_trend_r2": r2,
        f"{prefix}_channel_location": float(np.clip(residual[-1] / residual_scale, -4.0, 4.0)),
        f"{prefix}_channel_halfwidth_log": residual_scale,
        f"{prefix}_path_efficiency": net / max(path, 1e-12),
        f"{prefix}_range_atr": width / atr,
        f"{prefix}_compression_ratio": recent_width / max(earlier_width, atr),
        f"{prefix}_close_range_location": (
            (float(close.iloc[-1]) - float(low.min())) / max(width, 1e-12)
        ),
    }


def _last_completed_row(frame: pd.DataFrame, asof: pd.Timestamp) -> pd.Series | None:
    before = frame.loc[frame.index <= asof]
    return None if before.empty else before.iloc[-1]


def _recent_episode_features(
    episodes: Sequence[graph.InteractionEpisode],
    asof: pd.Timestamp,
) -> dict[str, float]:
    output: dict[str, float] = {}
    prior = [episode for episode in episodes if episode.confirm_ts < asof]
    for scale in DELIVERY_TIMEFRAMES:
        choices = [
            episode
            for episode in prior
            if episode.source_max_timeframe >= scale
        ]
        prefix = f"state_recent_{scale}m"
        if not choices:
            output[f"{prefix}_side"] = 0.0
            output[f"{prefix}_age_minutes"] = math.nan
            output[f"{prefix}_accepted"] = 0.0
            output[f"{prefix}_sweep_reclaim"] = 0.0
            continue
        episode = max(choices, key=lambda item: (item.confirm_ts, item.episode_id))
        output[f"{prefix}_side"] = float(episode.side)
        output[f"{prefix}_age_minutes"] = float(
            (asof - episode.confirm_ts) / pd.Timedelta(minutes=1)
        )
        output[f"{prefix}_accepted"] = float(episode.state == "ACCEPTED_BREAK")
        output[f"{prefix}_sweep_reclaim"] = float(episode.state == "SWEEP_RECLAIM")
        output[f"{prefix}_source_strength"] = float(episode.source_strength_max)
    return output


def _pool_inventory_features(
    pools: Sequence[Any],
    asof: pd.Timestamp,
    price: float,
    sigma_price: float,
) -> dict[str, float]:
    output: dict[str, float] = {}
    for scale in DELIVERY_TIMEFRAMES:
        known = [
            pool
            for pool in pools
            if pool.timeframe >= scale and pool.observed_ts < asof
        ]
        live = [pool for pool in known if _pool_live(pool, asof)]
        consumed = [
            pool
            for pool in known
            if pool.first_interaction_ts is not None
            and pool.first_interaction_ts <= asof
        ]
        prefix = f"state_liquidity_{scale}m"
        for side, token in (("HIGH", "above"), ("LOW", "below")):
            relevant = [pool for pool in live if pool.side == side]
            ahead = [
                pool
                for pool in relevant
                if (pool.inner > price if side == "HIGH" else pool.inner < price)
            ]
            masses: list[float] = []
            distances: list[float] = []
            for pool in ahead:
                distance = abs(float(pool.inner) - price) / sigma_price
                quality = (
                    math.sqrt(max(float(pool.timeframe), 1.0) / scale)
                    * max(float(pool.strength), 0.0)
                    / (1.0 + max(float(pool.equality_atr), 0.0))
                )
                masses.append(quality / (1.0 + distance))
                distances.append(distance)
            output[f"{prefix}_{token}_count"] = float(len(ahead))
            output[f"{prefix}_{token}_nearest_sigma"] = min(distances) if distances else math.nan
            output[f"{prefix}_{token}_mass"] = float(sum(masses))
            recently_consumed = [
                pool
                for pool in consumed
                if pool.side == side
                and pool.first_interaction_ts is not None
                and asof - pd.Timedelta(hours=24) < pool.first_interaction_ts <= asof
            ]
            output[f"{prefix}_{token}_consumed_24h"] = float(len(recently_consumed))
        upper = output[f"{prefix}_above_mass"]
        lower = output[f"{prefix}_below_mass"]
        output[f"{prefix}_mass_skew"] = (upper - lower) / max(upper + lower, 1e-12)
        output[f"{prefix}_consumption_skew_24h"] = (
            output[f"{prefix}_below_consumed_24h"]
            - output[f"{prefix}_above_consumed_24h"]
        )
    return output


def delivery_state_features(
    frame: pd.DataFrame,
    pools: Sequence[Any],
    pivots: Sequence[Pivot],
    episodes: Sequence[graph.InteractionEpisode],
    episode: graph.InteractionEpisode,
    asof: pd.Timestamp,
    entry: float,
) -> tuple[dict[str, float], Barrier | None, Barrier | None]:
    row = _last_completed_row(frame, asof)
    if row is None:
        return {}, None, None
    prior_sigma = max(float(row.get("prior_sigma", 1e-8)), 1e-8)
    sigma_price = max(entry * prior_sigma, 1e-12)
    excluded = set(episode.touched_pool_ids)
    high, low = _nearest_barriers(
        frame,
        pools,
        pivots,
        asof,
        entry,
        episode.source_max_timeframe,
        excluded,
    )
    output: dict[str, float] = {
        "state_source_side": float(-1 if episode.primary_pool.side == "HIGH" else 1),
        "state_event_side": float(episode.side),
        "state_event_sweep_reclaim": float(episode.state == "SWEEP_RECLAIM"),
        "state_event_accepted_break": float(episode.state == "ACCEPTED_BREAK"),
        "state_source_scale": float(episode.source_max_timeframe),
        "state_source_pool_count": float(episode.source_pool_count),
        "state_source_strength": float(episode.source_strength_max),
        "state_source_equality": float(episode.source_equality_min),
    }
    if high is not None:
        output.update(
            {
                "state_upper_distance_sigma": (high.level - entry) / sigma_price,
                "state_upper_timeframe": float(high.timeframe),
                "state_upper_strength": float(high.strength),
                "state_upper_age_minutes": float((asof - high.observed_ts) / pd.Timedelta(minutes=1)),
                "state_upper_is_pool": float(high.kind == "POOL"),
            }
        )
    if low is not None:
        output.update(
            {
                "state_lower_distance_sigma": (entry - low.level) / sigma_price,
                "state_lower_timeframe": float(low.timeframe),
                "state_lower_strength": float(low.strength),
                "state_lower_age_minutes": float((asof - low.observed_ts) / pd.Timedelta(minutes=1)),
                "state_lower_is_pool": float(low.kind == "POOL"),
            }
        )
    if high is not None and low is not None and high.level > low.level:
        width = high.level - low.level
        output["state_dealing_range_location"] = (entry - low.level) / width
        output["state_dealing_range_width_sigma"] = width / sigma_price
        upper_pull = high.strength / (1.0 + (high.level - entry) / sigma_price)
        lower_pull = low.strength / (1.0 + (entry - low.level) / sigma_price)
        output["state_destination_pull_skew"] = (
            upper_pull - lower_pull
        ) / max(upper_pull + lower_pull, 1e-12)
    output.update(_pool_inventory_features(pools, asof, entry, sigma_price))
    output.update(_recent_episode_features(episodes, asof))
    output.update(_regression_channel_features(frame, asof, 5, 48))
    output.update(_regression_channel_features(frame, asof, 15, 48))
    output.update(_regression_channel_features(frame, asof, 60, 36))

    # Preserve unaligned price/volume, cross-market, basis and derivative state
    # for the direction model.  Action-level code later adds aligned copies.
    numeric_prefixes = (
        "ret_z_", "path_eff_", "delta_share_", "flow_progress_",
        "common_z_", "residual_z_", "breadth_pos_", "breadth_neg_",
        "dispersion_", "oi_", "metric_", "global_", "top_",
        "perp_spot_", "spot_", "basis_",
    )
    for key, value in row.items():
        if not isinstance(key, str) or not key.startswith(numeric_prefixes):
            continue
        number = _finite(value)
        if number is not None:
            output[f"state_raw_{key}"] = number
    return output, high, low


def _destination_label(
    frame: pd.DataFrame,
    decision_ts: pd.Timestamp,
    high: Barrier | None,
    low: Barrier | None,
) -> dict[str, Any]:
    output: dict[str, Any] = {
        "destination_up_label": math.nan,
        "destination_side": 0,
        "destination_resolution_ts": None,
        "destination_minutes": None,
    }
    if high is None or low is None:
        return output
    future = frame.loc[frame.index > decision_ts].head(DESTINATION_HORIZON_MINUTES)
    for ts, bar in future.iterrows():
        high_hit = float(bar.high) >= high.level
        low_hit = float(bar.low) <= low.level
        if high_hit and low_hit:
            return output
        if high_hit or low_hit:
            side = 1 if high_hit else -1
            output.update(
                {
                    "destination_up_label": float(side > 0),
                    "destination_side": side,
                    "destination_resolution_ts": ts.isoformat(),
                    "destination_minutes": int((ts - decision_ts) / pd.Timedelta(minutes=1)),
                }
            )
            return output
    return output


def _response_ob_zone(
    frame: pd.DataFrame,
    episode: graph.InteractionEpisode,
    tick: float,
) -> list[graph.EntryZone]:
    """Build one response OB only after completed directional displacement."""
    index = frame.index
    interaction_i = int(index.searchsorted(episode.interaction_ts, side="left"))
    confirm_i = int(index.searchsorted(episode.confirm_ts, side="left"))
    end_i = min(len(frame) - 1, confirm_i + 30)
    displacement_i: int | None = None
    for i in range(confirm_i, end_i + 1):
        bar = frame.iloc[i]
        range_value = max(float(bar.high - bar.low), tick)
        body = episode.side * float(bar.close - bar.open) / range_value
        ratio = _finite(bar.get("range_ratio"))
        delta = _finite(bar.get("delta_share_1"))
        if (
            body >= 0.52
            and (ratio is None or ratio >= 1.10)
            and (delta is None or episode.side * delta >= -0.10)
        ):
            displacement_i = i
            break
    if displacement_i is None:
        return []
    start = max(interaction_i, displacement_i - 7)
    for i in range(displacement_i - 1, start - 1, -1):
        bar = frame.iloc[i]
        opposite = (
            float(bar.close) < float(bar.open)
            if episode.side > 0
            else float(bar.close) > float(bar.open)
        )
        if not opposite:
            continue
        lower = min(float(bar.open), float(bar.close))
        upper = max(float(bar.open), float(bar.close))
        if upper - lower < tick:
            continue
        formed_ts = index[displacement_i]
        return [
            graph.EntryZone(
                action_family="RESPONSE_OB_FIRST_RETEST",
                action_id=f"OB:{episode.episode_id}:{int(index[i].value)}:{int(formed_ts.value)}",
                formed_ts=max(episode.confirm_ts, formed_ts),
                lower=lower,
                upper=upper,
                adverse_gap_id=None,
                aligned_gap_id=None,
                mss_ts=None,
                mss_level=None,
                bpr_overlap_fraction=0.0,
                ifvg_close_through_sigma=0.0,
            )
        ]
    return []


def _confluence_zones(
    zones: Sequence[graph.EntryZone],
    tick: float,
) -> list[graph.EntryZone]:
    obs = [zone for zone in zones if zone.action_family == "RESPONSE_OB_FIRST_RETEST"]
    footprints = [zone for zone in zones if zone.action_family != "RESPONSE_OB_FIRST_RETEST"]
    output: list[graph.EntryZone] = []
    for ob in obs:
        for footprint in footprints:
            lower = max(ob.lower, footprint.lower)
            upper = min(ob.upper, footprint.upper)
            if upper - lower < tick:
                continue
            output.append(
                graph.EntryZone(
                    action_family="OB_FOOTPRINT_CONFLUENCE_FIRST_RETEST",
                    action_id=f"CONF:{ob.action_id}|{footprint.action_id}",
                    formed_ts=max(ob.formed_ts, footprint.formed_ts),
                    lower=lower,
                    upper=upper,
                    adverse_gap_id=footprint.adverse_gap_id,
                    aligned_gap_id=footprint.aligned_gap_id,
                    mss_ts=footprint.mss_ts,
                    mss_level=footprint.mss_level,
                    bpr_overlap_fraction=footprint.bpr_overlap_fraction,
                    ifvg_close_through_sigma=footprint.ifvg_close_through_sigma,
                )
            )
    return output


def _entry_zones(
    frame: pd.DataFrame,
    episode: graph.InteractionEpisode,
    tick: float,
    pivots1: Sequence[Pivot],
    pivots5: Sequence[Pivot],
) -> list[graph.EntryZone]:
    if episode.state == "SWEEP_RECLAIM":
        zones = graph.build_reversal_entry_zones(frame, episode, tick, pivots1, pivots5)
    else:
        zones = graph.build_continuation_entry_zones(frame, episode, tick)
    zones = list(zones) + _response_ob_zone(frame, episode, tick)
    zones += _confluence_zones(zones, tick)
    unique: dict[tuple[str, int, int], graph.EntryZone] = {}
    for zone in zones:
        key = (
            zone.action_family,
            int(round(zone.lower / tick)),
            int(round(zone.upper / tick)),
        )
        existing = unique.get(key)
        if existing is None or zone.formed_ts < existing.formed_ts:
            unique[key] = zone
    return list(unique.values())


def _enrich_action(
    row: dict[str, Any],
    frame: pd.DataFrame,
    pools: Sequence[Any],
    pivots: Sequence[Pivot],
    episodes: Sequence[graph.InteractionEpisode],
    episode: graph.InteractionEpisode,
    zone: graph.EntryZone,
) -> dict[str, Any]:
    decision_ts = pd.Timestamp(row["decision_ts"])
    entry = float(row["entry"])
    stop = float(row["stop"])
    target = float(row["structural_target"])
    row.update(
        liquidity_map_features(
            frame, pools, pivots, episode, decision_ts, entry, stop, target
        )
    )
    row.update(
        narrative_phase_features(
            frame, episode, zone.formed_ts, decision_ts
        )
    )
    state, high, low = delivery_state_features(
        frame, pools, pivots, episodes, episode, decision_ts, entry
    )
    row.update(state)
    destination = _destination_label(frame, decision_ts, high, low)
    row.update(destination)
    row["destination_aligned_label"] = (
        float(int(destination["destination_side"]) == int(episode.side))
        if int(destination["destination_side"]) != 0
        else math.nan
    )
    if high is not None:
        row.update(
            {
                "destination_upper": high.level,
                "destination_upper_id": high.identity,
                "destination_upper_timeframe": high.timeframe,
                "destination_upper_kind": high.kind,
            }
        )
    if low is not None:
        row.update(
            {
                "destination_lower": low.level,
                "destination_lower_id": low.identity,
                "destination_lower_timeframe": low.timeframe,
                "destination_lower_kind": low.kind,
            }
        )
    # Aligned state copies are for execution quality; raw state remains for the
    # independent up/down destination model.
    for key, value in list(state.items()):
        if not key.startswith("state_raw_"):
            continue
        number = _finite(value)
        if number is None:
            continue
        directional_tokens = (
            "ret_z_", "path_eff_", "delta_share_", "flow_progress_",
            "common_z_", "residual_z_", "basis_", "metric_taker_",
            "global_account_", "top_account_", "top_position_",
        )
        if any(token in key for token in directional_tokens):
            row[f"aligned_{key}"] = episode.side * number
    family = str(row.get("action_family", ""))
    row["execution_zone_bpr"] = float(family == "BPR_FIRST_RETEST")
    row["execution_zone_ifvg"] = float(family == "IFVG_FIRST_RETEST")
    row["execution_zone_mss_fvg"] = float(family == "MSS_FVG_FIRST_RETEST")
    row["execution_zone_acceptance_fvg"] = float(family == "ACCEPTANCE_FVG_FIRST_RETEST")
    row["execution_zone_ob"] = float(family == "RESPONSE_OB_FIRST_RETEST")
    row["execution_zone_confluence"] = float(family == "OB_FOOTPRINT_CONFLUENCE_FIRST_RETEST")
    row["delivery_state_version"] = "DIRECTION_LIQUIDITY_STRUCTURE_EVENT_EXECUTION_V1"
    row["entry_role"] = "PRICE_REFINEMENT_AFTER_DIRECTION_AND_LIQUIDITY_EVENT"
    return row


def harvest_symbol(
    symbol: str,
    period: str,
    frame: pd.DataFrame,
    funding: pd.DataFrame,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    episodes, pools, pivots = graph.build_interaction_episodes(
        symbol, frame, timeframes=DELIVERY_TIMEFRAMES
    )
    pivots1, pivots5 = graph.internal_pivots(frame)
    rows: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {
        "episodes": len(episodes),
        "episodes_in_window": 0,
        "entry_zones": 0,
        "plans": 0,
        "by_state": {},
        "by_action": {},
        "timeframes": list(DELIVERY_TIMEFRAMES),
    }
    for episode in episodes:
        if not (start_ts <= episode.interaction_ts <= end_ts):
            continue
        diagnostics["episodes_in_window"] += 1
        diagnostics["by_state"][episode.state] = diagnostics["by_state"].get(episode.state, 0) + 1
        zones = _entry_zones(frame, episode, TICKS[symbol], pivots1, pivots5)
        diagnostics["entry_zones"] += len(zones)
        for zone in zones:
            diagnostics["by_action"][zone.action_family] = diagnostics["by_action"].get(zone.action_family, 0) + 1
            row = bpr.build_action_row(
                symbol,
                period,
                frame,
                funding,
                pools,
                pivots,
                episode,
                zone,
                start_ts,
                end_ts,
            )
            if row is None:
                continue
            enriched = _enrich_action(row, frame, pools, pivots, episodes, episode, zone)
            enriched["evaluation_start"] = start_ts.date().isoformat()
            enriched["evaluation_end"] = end_ts.date().isoformat()
            enriched["evaluation_calendar_days"] = int((end_ts.date() - start_ts.date()).days + 1)
            rows.append(enriched)
            diagnostics["plans"] += 1
    return rows, diagnostics


def _summary(
    actions: pd.DataFrame,
    start: date,
    end: date,
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    days = (end - start).days + 1
    output: dict[str, Any] = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "calendar_days": days,
        "actions": int(len(actions)),
        "episodes": int(actions.episode_id.nunique()) if not actions.empty else 0,
        "episodes_per_day": float(actions.episode_id.nunique() / days) if not actions.empty else 0.0,
        "diagnostics": diagnostics,
        "information_order": [
            "DIRECTION_FROM_LIVE_AND_SECURED_LIQUIDITY",
            "STRUCTURAL_PATH_AND_DEALING_RANGE",
            "PRICE_VOLUME_LIQUIDITY_EVENT",
            "OB_FVG_BPR_FIRST_RETEST_EXECUTION",
            "STRUCTURAL_INVALIDATION_AND_OPPOSING_LIQUIDITY_TARGET",
        ],
        "causality": "FEATURES_AT_DECISION_THEN_DESTINATION_AND_TRADE_LABELS",
    }
    if actions.empty:
        return output
    output["destination_resolved_rate"] = float(pd.to_numeric(actions.destination_up_label, errors="coerce").notna().mean())
    output["structural_target_first_rate"] = float(pd.to_numeric(actions.structural_target_first, errors="coerce").mean())
    output["structural_mean_realized_r"] = float(pd.to_numeric(actions.structural_realized_r, errors="coerce").mean())
    output["by_action"] = {
        str(name): {
            "actions": int(len(group)),
            "episodes": int(group.episode_id.nunique()),
            "target_first_rate": float(pd.to_numeric(group.structural_target_first, errors="coerce").mean()),
            "mean_r": float(pd.to_numeric(group.structural_realized_r, errors="coerce").mean()),
        }
        for name, group in actions.groupby("action_family")
    }
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--warmup-days", type=int, default=35)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    warmup_start = args.start - timedelta(days=args.warmup_days)
    data_end = args.end + timedelta(days=LABEL_EXTENSION_DAYS)
    start_ts = pd.Timestamp(args.start, tz="UTC")
    end_ts = pd.Timestamp(args.end + timedelta(days=1), tz="UTC") - pd.Timedelta(minutes=1)

    futures_raw = {
        symbol: load_range_flow(symbol, warmup_start, data_end, args.cache / "futures")
        for symbol in SYMBOLS
    }
    spot_raw = {
        symbol: load_spot_range_flow(symbol, warmup_start, data_end, args.cache)
        for symbol in SYMBOLS
    }
    frames = {symbol: make_features(symbol, raw) for symbol, raw in futures_raw.items()}
    frames = add_cross_features(frames)
    funding: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOLS:
        frames[symbol] = add_spot_perp_context(frames[symbol], symbol, spot_raw[symbol])
        metrics = load_metrics_range(symbol, warmup_start, data_end, args.cache)
        frames[symbol] = join_metrics_causally(frames[symbol], metrics)
        funding[symbol] = load_funding_range(symbol, warmup_start, data_end, args.cache)

    rows: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {}
    for symbol in SYMBOLS:
        symbol_rows, symbol_diagnostics = harvest_symbol(
            symbol,
            args.period,
            frames[symbol],
            funding[symbol],
            start_ts,
            end_ts,
        )
        rows.extend(symbol_rows)
        diagnostics[symbol] = symbol_diagnostics
    actions = pd.DataFrame(rows)
    if not actions.empty:
        actions = actions.sort_values(["entry_ts", "episode_id", "action_family", "plan_id"]).reset_index(drop=True)
        if actions.plan_id.duplicated().any():
            raise RuntimeError(f"duplicate plan ids: {actions.loc[actions.plan_id.duplicated(), 'plan_id'].head().tolist()}")
        alternatives = actions.groupby("episode_id").size()
        actions["episode_alternative_count"] = actions.episode_id.map(alternatives).astype(int)
        actions["episode_weight"] = 1.0 / actions.episode_alternative_count

    args.output.mkdir(parents=True, exist_ok=True)
    actions.to_csv(args.output / "actions.csv", index=False)
    (args.output / "summary.json").write_text(
        json.dumps(_summary(actions, args.start, args.end, diagnostics), indent=2, sort_keys=True),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

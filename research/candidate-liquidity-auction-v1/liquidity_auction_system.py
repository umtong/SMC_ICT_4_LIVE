"""Episode-conditioned liquidity auction system.

This is not a filter on a legacy policy.  Strong semantic liquidity still owns event
creation and direction, while local structure, volume-at-price and OB/FVG footprints
serve their narrower roles as route obstacles and entry locations.  The first meaningful
obstacle is never skipped to manufacture RR.  Stops sit beyond the causal invalidation
plus ordinary prior wick noise, and the only entry alternatives are completed-response
market entry or the public proximal boundary on the first return.
"""
from __future__ import annotations

from typing import Any
import hashlib
import math

import numpy as np
import pandas as pd

import coherent_system_v4 as v4
import coherent_system_v5 as v5  # installs prior/post-event direction features into v4
from semantic_liquidity_full import (
    PoolMeta,
    build_semantic_liquidity,
    direction_sources,
    route_levels,
)


POLICY = (
    "SEMANTIC_LIQUIDITY_DIRECTION_THEN_MUTUALLY_EXCLUSIVE_FAILED_OR_ACCEPTED_"
    "AUCTION_THEN_PRICE_VOLUME_CONTROL_TRANSFER_THEN_MARKET_OR_PUBLIC_PROXIMAL_"
    "FIRST_RETURN_ENTRY_THEN_EVENT_PLUS_PRIOR_WICK_NOISE_INVALIDATION_THEN_"
    "NEAREST_LOCAL_SEMANTIC_OR_MULTISCALE_VOLUME_ROUTE_OBSTACLE"
)
PROFILE_WINDOWS = (120, 360, 1440)
PROFILE_MINIMUM_BARS = 80
_ORIGINAL_COMMON_FEATURES = v4._common_features


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _sign(side: str) -> float:
    return 1.0 if side == "LONG" else -1.0


def _profile_obstacle(
    data: pd.DataFrame,
    index: int,
    entry: float,
    side: str,
    tick: float,
    window: int,
) -> tuple[v4.Obstacle | None, dict[str, float]]:
    frame = data.iloc[max(0, index - window):index]
    prefix = f"route_profile_{window}m"
    if len(frame) < min(PROFILE_MINIMUM_BARS, window // 2):
        return None, {f"{prefix}_bars": float(len(frame)), f"{prefix}_nodes": 0.0}
    typical = ((frame.high + frame.low + frame.close) / 3.0).to_numpy(float)
    weights = pd.to_numeric(frame.quote_volume, errors="coerce").to_numpy(float)
    finite = np.isfinite(typical) & np.isfinite(weights) & (weights > 0.0)
    typical, weights = typical[finite], weights[finite]
    if len(typical) < min(PROFILE_MINIMUM_BARS, window // 2):
        return None, {f"{prefix}_bars": float(len(typical)), f"{prefix}_nodes": 0.0}
    lower, upper = np.quantile(typical, [0.01, 0.99])
    if not np.isfinite(lower) or not np.isfinite(upper) or upper <= lower + tick:
        return None, {f"{prefix}_bars": float(len(typical)), f"{prefix}_nodes": 0.0}
    # Freedman-Diaconis adapts the profile resolution to observed dispersion; the
    # bounds only prevent degenerate one-bin or one-tick profiles.
    edges = np.histogram_bin_edges(typical, bins="fd", range=(lower, upper))
    bin_count = int(np.clip(len(edges) - 1, 16, 64))
    edges = np.linspace(lower, upper, bin_count + 1)
    histogram, _ = np.histogram(typical, bins=edges, weights=weights)
    positive = histogram[histogram > 0.0]
    if len(positive) < 6:
        return None, {f"{prefix}_bars": float(len(typical)), f"{prefix}_nodes": 0.0}
    threshold = float(np.median(positive))
    peaks: list[int] = []
    for position, value in enumerate(histogram):
        left = histogram[position - 1] if position > 0 else -np.inf
        right = histogram[position + 1] if position + 1 < len(histogram) else -np.inf
        if value >= threshold and value >= left and value >= right:
            peaks.append(position)
    candidates: list[tuple[float, int, float]] = []
    for position in peaks:
        zone_lower = float(edges[position])
        zone_upper = float(edges[position + 1])
        if side == "LONG" and zone_lower > entry + tick:
            target = zone_lower - v4.TARGET_INSIDE_TICKS * tick
        elif side == "SHORT" and zone_upper < entry - tick:
            target = zone_upper + v4.TARGET_INSIDE_TICKS * tick
        else:
            continue
        candidates.append((abs(target - entry), position, target))
    features = {
        f"{prefix}_bars": float(len(typical)),
        f"{prefix}_nodes": float(len(peaks)),
        f"{prefix}_range_bps": (upper - lower) / max(abs(entry), v4.EPS) * 10_000.0,
    }
    if not candidates:
        return None, features
    _, position, target = min(candidates)
    share = float(histogram[position] / max(histogram.sum(), v4.EPS))
    zone_lower, zone_upper = float(edges[position]), float(edges[position + 1])
    identity = hashlib.sha1(
        f"{window}|{data.index[index].value}|{zone_lower}|{zone_upper}".encode()
    ).hexdigest()[:12]
    obstacle = v4.Obstacle(
        obstacle_id=f"VOLUME_NODE:{window}M:{identity}",
        kind=f"CAUSAL_VOLUME_NODE_{window}M",
        timeframe_minutes=window,
        structure_price=(zone_lower + zone_upper) / 2.0,
        order_price=float(target),
        strength=share,
        source_level_id=None,
    )
    features.update(
        {
            f"{prefix}_nearest_share": share,
            f"{prefix}_nearest_distance_bps": abs(target - entry)
            / max(abs(entry), v4.EPS)
            * 10_000.0,
            f"{prefix}_nearest_width_bps": (zone_upper - zone_lower)
            / max(abs(entry), v4.EPS)
            * 10_000.0,
        }
    )
    return obstacle, features


def _first_obstacle(
    data,
    levels,
    metadata,
    index,
    entry,
    side,
    tick,
):
    semantic = v4._nearest_semantic_obstacle(
        levels,
        metadata,
        index,
        entry,
        side,
        tick,
    )
    candidates = [semantic] if semantic is not None else []
    features: dict[str, float] = {}
    for window in PROFILE_WINDOWS:
        obstacle, profile_features = _profile_obstacle(
            data,
            index,
            entry,
            side,
            tick,
            window,
        )
        features.update(profile_features)
        if obstacle is not None:
            candidates.append(obstacle)
    if not candidates:
        return None, features
    chosen = min(
        candidates,
        key=lambda item: (
            abs(item.order_price - entry),
            -item.strength,
            item.obstacle_id,
        ),
    )
    features.update(
        {
            "route_obstacle_is_local_or_semantic": float(chosen.source_level_id is not None),
            "route_obstacle_is_multiscale_volume": float(chosen.kind.startswith("CAUSAL_VOLUME_NODE_")),
            "route_obstacle_distance_bps": abs(chosen.order_price - entry)
            / max(abs(entry), v4.EPS)
            * 10_000.0,
            "route_obstacle_strength": float(chosen.strength),
        }
    )
    return chosen, features


def _entry_variants(data, setup, response, event_meta, source, tick):
    side = setup.side
    decision = float(data.iloc[int(response["response_index"])].close)
    output = [("COMPLETED_RESPONSE_MARKET", "MARKET", decision)]
    proximal = float(setup.upper if side == "LONG" else setup.lower)
    favorable = proximal <= decision - tick if side == "LONG" else proximal >= decision + tick
    if favorable:
        output.append(("PUBLIC_PROXIMAL_LIMIT", "LIMIT", proximal))
    return output


def _prior_wick_noise(data: pd.DataFrame, index: int, side: str, tick: float) -> float:
    frame = data.iloc[max(0, index - 120):index]
    if frame.empty:
        return 2.0 * tick
    if side == "LONG":
        wick = np.minimum(frame.open.to_numpy(float), frame.close.to_numpy(float)) - frame.low.to_numpy(float)
    else:
        wick = frame.high.to_numpy(float) - np.maximum(frame.open.to_numpy(float), frame.close.to_numpy(float))
    wick = wick[np.isfinite(wick) & (wick >= 0.0)]
    if len(wick):
        value = float(np.median(wick))
    else:
        value = 0.0
    if value <= 0.0:
        value = _finite((frame.high - frame.low).median(), 0.0) / 2.0
    return max(2.0 * tick, value)


def _stop_variants(data, setup, response, source, event_meta, tick):
    branch = str(event_meta["narrative_branch"])
    index = int(response["response_index"])
    noise = _prior_wick_noise(data, index, setup.side, tick)
    if branch == "FAILED_AUCTION_REVERSAL":
        parent_reference = float(setup.event_extreme)
        parent_name = "EVENT_WICK_NOISE_INVALIDATION"
    else:
        retest = float(response["retest_extreme"])
        parent_reference = min(retest, source.lower) if setup.side == "LONG" else max(retest, source.upper)
        parent_name = "TRANSFERRED_BOUNDARY_WICK_NOISE_INVALIDATION"
    parent = parent_reference - noise if setup.side == "LONG" else parent_reference + noise
    output = [(parent_name, parent)]
    if branch == "FAILED_AUCTION_REVERSAL":
        retest_reference = float(response["retest_extreme"])
        retest = retest_reference - noise if setup.side == "LONG" else retest_reference + noise
        decision = float(data.iloc[index].close)
        valid = retest < decision if setup.side == "LONG" else retest > decision
        if valid and abs(retest - parent) > tick:
            output.append(("FIRST_RETEST_WICK_NOISE_INVALIDATION", retest))
    return output


def _common_features(*args, **kwargs):
    features = _ORIGINAL_COMMON_FEATURES(*args, **kwargs)
    data = args[0]
    setup = args[4]
    response = args[5]
    tick = v4.CONTRACTS[args[3].symbol].tick_size
    noise = _prior_wick_noise(data, int(response["response_index"]), setup.side, tick)
    entry = float(args[9])
    features["prior_wick_noise_bps"] = noise / max(abs(entry), v4.EPS) * 10_000.0
    features["prior_wick_noise_to_zone_width"] = noise / max(abs(setup.upper - setup.lower), tick)
    return features


# Patch the reusable v4 action engine.  Direction sources remain strong; only route
# representation, entry alternatives and invalidation geometry change.
v4.PoolMeta = PoolMeta
v4.build_semantic_liquidity = build_semantic_liquidity
v4.direction_sources = direction_sources
v4.route_levels = route_levels
v4._first_obstacle = _first_obstacle
v4._entry_variants = _entry_variants
v4._stop_variants = _stop_variants
v4._common_features = _common_features
v4.POLICY = POLICY
v5.POLICY = POLICY

run_research = v4.run_research
generate_symbol = v4.generate_symbol
label_action = v4.label_action
MAX_HOLD_MINUTES = v4.MAX_HOLD_MINUTES
LIMIT_EXPIRY_MINUTES = v4.LIMIT_EXPIRY_MINUTES

__all__ = [
    "POLICY",
    "MAX_HOLD_MINUTES",
    "LIMIT_EXPIRY_MINUTES",
    "run_research",
    "generate_symbol",
    "label_action",
]

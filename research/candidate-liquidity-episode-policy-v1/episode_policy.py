#!/usr/bin/env python3
"""Causal liquidity episode policy.

The mature liquidity-world-model generator remains responsible for point-in-time
source detection, episode formation and destination-first plan geometry.  This
module adds dynamic trend/channel liquidity and causal local completion
frontiers, then requires a real OB/FVG/source-origin entry object.  The local
frontiers repair a recurrent branch failure: a valid intraday control transfer
was often forced to target a remote high-timeframe pool even though a skilled
trader would first plan against the nearest still-live opposing swing or range
boundary.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np
import pandas as pd

import dynamic_boundaries
import plan_geometry as geometry
import world_model_policy as base_policy
from episode_policy_features import FEATURE_COLUMNS, enrich_episode_frame
from world_model_common import Destination, sign, stable

_BASE_PLAN_FROM_SIGNAL = base_policy.plan_from_signal
_BASE_CHOOSE_DESTINATION = geometry.choose_destination


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _local_completion_frontiers(
    data: pd.DataFrame,
    decision: int,
    entry: float,
    side: str,
    atr: np.ndarray,
    tick: float,
) -> list[Destination]:
    """Return public, unspent local swing liquidity in the route direction.

    A pivot is usable only after its right-hand confirmation bars have closed.
    Freshness is checked only through ``decision``.  Several horizons represent
    the same market idea at different intraday scales; nearby duplicates are
    clustered by the caller.  This is structure, not a fixed-R target lattice.
    """
    if decision < 8:
        return []
    target_side = "HIGH" if side == "LONG" else "LOW"
    direction = sign(side)
    start = max(2, decision - 480)
    output: list[Destination] = []
    highs = data.high.to_numpy(float)
    lows = data.low.to_numpy(float)

    for half_window, scale in ((2, 5.0), (4, 15.0), (8, 30.0), (15, 60.0)):
        earliest = max(start, half_window)
        latest = decision - half_window - 1
        for pivot in range(earliest, latest + 1):
            observed = pivot + half_window
            if observed >= decision:
                continue
            left = pivot - half_window
            right = pivot + half_window + 1
            if target_side == "HIGH":
                price = float(highs[pivot])
                if price < float(np.max(highs[left:right])) - EPSILON:
                    continue
                # A target already traded through after confirmation is spent.
                if observed + 1 <= decision and float(np.max(highs[observed + 1 : decision + 1])) >= price:
                    continue
            else:
                price = float(lows[pivot])
                if price > float(np.min(lows[left:right])) + EPSILON:
                    continue
                if observed + 1 <= decision and float(np.min(lows[observed + 1 : decision + 1])) <= price:
                    continue
            if direction * (price - entry) <= tick:
                continue

            local_atr = max(float(atr[min(decision, len(atr) - 1)]), tick)
            tolerance = max(3.0 * tick, 0.05 * local_atr)
            prior = data.iloc[max(0, pivot - 180) : observed + 1]
            if target_side == "HIGH":
                touches = int(((prior.high - price).abs() <= tolerance).sum())
            else:
                touches = int(((prior.low - price).abs() <= tolerance).sum())
            age = max(1, decision - observed)
            recency = math.exp(-age / max(90.0, 4.0 * scale))
            strength = (
                1.0
                + 0.22 * math.log1p(max(touches, 1))
                + 0.16 * math.log1p(scale)
                + 0.25 * recency
            )
            width = max(2.0 * tick, 0.025 * local_atr)
            output.append(
                Destination(
                    destination_id=(
                        f"LOCAL_COMPLETION:{target_side}:{scale:.0f}:"
                        f"{pivot}:{stable(round(price / max(tick, 1e-12)))}"
                    ),
                    side=target_side,
                    lower=float(price - width),
                    upper=float(price + width),
                    price=price,
                    observed_index=int(observed),
                    scale=scale,
                    strength=float(strength),
                    kind=f"LOCAL_CONFIRMED_SWING_{scale:.0f}M",
                )
            )

    # Public rolling range edges capture repeated equal-high/equal-low liquidity
    # even when no single candle is a clean fractal pivot.
    for horizon, scale in ((30, 15.0), (60, 30.0), (120, 60.0), (240, 120.0)):
        left = max(0, decision - horizon)
        prior = data.iloc[left:decision]
        if len(prior) < max(12, horizon // 3):
            continue
        if target_side == "HIGH":
            offset = int(np.argmax(prior.high.to_numpy(float)))
            price = float(prior.high.iloc[offset])
        else:
            offset = int(np.argmin(prior.low.to_numpy(float)))
            price = float(prior.low.iloc[offset])
        pivot = left + offset
        if direction * (price - entry) <= tick:
            continue
        local_atr = max(float(atr[min(decision, len(atr) - 1)]), tick)
        width = max(2.0 * tick, 0.03 * local_atr)
        output.append(
            Destination(
                destination_id=f"LOCAL_RANGE:{target_side}:{horizon}:{pivot}:{stable(price)}",
                side=target_side,
                lower=float(price - width),
                upper=float(price + width),
                price=price,
                observed_index=int(decision - 1),
                scale=scale,
                strength=float(1.15 + 0.18 * math.log1p(scale)),
                kind=f"LOCAL_UNSPENT_RANGE_EDGE_{horizon}M",
            )
        )
    return output


EPSILON = 1e-12


def _cluster_destinations(
    candidates: list[Destination],
    decision: int,
    atr: np.ndarray,
    tick: float,
) -> list[Destination]:
    if not candidates:
        return []
    threshold = max(4.0 * tick, 0.10 * float(atr[decision]))
    groups: list[list[Destination]] = []
    for item in sorted(candidates, key=lambda value: value.price):
        if groups and abs(item.price - groups[-1][-1].price) <= threshold:
            groups[-1].append(item)
        else:
            groups.append([item])
    output: list[Destination] = []
    for group in groups:
        weights = np.asarray([max(item.strength, EPSILON) for item in group])
        owner = max(group, key=lambda item: (item.strength, item.scale, -item.observed_index))
        output.append(
            Destination(
                destination_id="EPDEST:" + stable(*(item.destination_id for item in group)),
                side=owner.side,
                lower=min(item.lower for item in group),
                upper=max(item.upper for item in group),
                price=float(np.average([item.price for item in group], weights=weights)),
                observed_index=max(item.observed_index for item in group),
                scale=max(item.scale for item in group),
                strength=float(sum(item.strength for item in group)),
                kind="+".join(sorted({item.kind for item in group})),
            )
        )
    return output


def _choose_destination_with_dynamic(
    data,
    levels,
    metadata,
    nodes_by_scale,
    decision,
    entry,
    side,
    atr,
    tick,
):
    """Select the nearest live opposing structural completion frontier."""
    candidates: list[Destination] = []
    static = _BASE_CHOOSE_DESTINATION(
        data, levels, metadata, nodes_by_scale, decision, entry, side, atr, tick
    )
    if static is not None:
        candidates.append(static)

    target_side = "HIGH" if side == "LONG" else "LOW"
    for model, price in dynamic_boundaries.active_route_boundaries(
        str(data.attrs.get("symbol", "")), data, decision, entry, side, tick
    ):
        width = max(2.0 * tick, 1.5 * float(model.residual_price), 0.04 * float(atr[decision]))
        candidates.append(
            Destination(
                destination_id=f"DYNAMIC_ROUTE:{model.boundary_id}:{decision}",
                side=target_side,
                lower=float(price - width),
                upper=float(price + width),
                price=float(price),
                observed_index=int(model.observed_index),
                scale=float(model.timeframe_minutes),
                strength=float(max(model.quality, 0.0) * (1.0 + 0.60 * max(model.channel_quality, 0.0))),
                kind=(
                    f"DYNAMIC_CHANNEL_ROUTE_{model.timeframe_minutes}M"
                    if model.is_channel_edge
                    else f"DYNAMIC_TRENDLINE_ROUTE_{model.timeframe_minutes}M"
                ),
            )
        )

    candidates.extend(
        _local_completion_frontiers(data, decision, entry, side, atr, tick)
    )
    direction = sign(side)
    ahead = [
        item for item in _cluster_destinations(candidates, decision, atr, tick)
        if item.side == target_side and direction * (item.price - entry) > tick
    ]
    if not ahead:
        return None
    ahead.sort(
        key=lambda item: (
            direction * (item.price - entry), -item.strength,
            -item.scale, item.destination_id,
        )
    )
    nearest = direction * (ahead[0].price - entry)
    near = [
        item for item in ahead
        if direction * (item.price - entry) <= nearest + 0.14 * float(atr[decision])
    ]
    return max(near, key=lambda item: (item.strength, item.scale, -abs(item.price - entry)))


def _strict_plan_from_signal(*args, **kwargs):
    plan, reason = _BASE_PLAN_FROM_SIGNAL(*args, **kwargs)
    if plan is not None and str(plan.get("entry_geometry")) == "CAUSAL_DEPARTURE_BAND":
        return None, "NO_CAUSAL_ENTRY_ORIGIN"
    if plan is not None:
        plan["uses_local_completion_frontier"] = int(
            "LOCAL_" in str(plan.get("route_kind", ""))
        )
    return plan, reason


geometry.choose_destination = _choose_destination_with_dynamic
base_policy.plan_from_signal = _strict_plan_from_signal


def _order_mask(frame: pd.DataFrame) -> pd.Series:
    return frame.get("order_exists", pd.Series(False, index=frame.index)).astype(str).str.lower().isin({"true", "1", "yes"})


def _assert_policy_invariants(frame: pd.DataFrame) -> None:
    orders = frame[_order_mask(frame)].copy()
    if orders.empty:
        return
    if "episode_id" not in orders:
        raise RuntimeError("Episode policy output is missing episode_id")
    duplicate = orders.episode_id.astype(str).duplicated(keep=False)
    if duplicate.any():
        examples = orders.loc[duplicate, "episode_id"].astype(str).head(10).tolist()
        raise RuntimeError(f"One-plan-per-episode invariant violated: {examples}")
    if orders.get("entry_geometry", pd.Series(index=orders.index, dtype=object)).astype(str).eq("CAUSAL_DEPARTURE_BAND").any():
        raise RuntimeError("Synthetic entry fallback leaked into executable orders")
    gross_rr = pd.to_numeric(orders.get("gross_rr"), errors="coerce")
    if gross_rr.isna().any() or gross_rr.lt(1.0 - 1e-12).any():
        raise RuntimeError("Executable order violates gross planned RR >= 1.0")


def generate_symbol(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[Any],
    metadata: dict[str, Any],
    trading_start: Any,
) -> tuple[pd.DataFrame, dict[str, int]]:
    combined_levels = list(levels)
    combined_metadata = dict(metadata)
    data.attrs["symbol"] = symbol
    tick = base_policy.core.CONTRACTS[symbol].tick_size
    models = dynamic_boundaries.build_dynamic_boundaries(symbol, data, combined_levels, tick)
    dynamic_levels, dynamic_metadata = dynamic_boundaries.source_levels(
        symbol, data, models, tick, combined_levels
    )
    combined_levels.extend(dynamic_levels)
    combined_metadata.update(dynamic_metadata)
    frame, counts = base_policy.generate_symbol(
        symbol, data, combined_levels, combined_metadata, trading_start
    )
    if _order_mask(frame).any():
        enriched = enrich_episode_frame(frame, data)
    else:
        enriched = frame.copy()
        for column in FEATURE_COLUMNS:
            if column not in enriched:
                enriched[column] = 0.0
        enriched["episode_policy_version"] = "liquidity-episode-policy-v2-local-completion"
    _assert_policy_invariants(enriched)
    counts = dict(counts)
    counts.update(
        {
            "episode_policy_rows": int(len(enriched)),
            "episode_policy_orders": int(_order_mask(enriched).sum()),
            "uses_outcome_in_generation": 0,
            "one_plan_per_episode": 1,
            "synthetic_entry_fallback_enabled": 0,
            "dynamic_liquidity_sources": int(len(dynamic_levels)),
            "dynamic_channel_sources": int(sum("CHANNEL" in str(level.source_kind) for level in dynamic_levels)),
            "dynamic_geometry_available": 1,
            "local_completion_frontiers_enabled": 1,
        }
    )
    return enriched, counts


__all__ = ["generate_symbol"]

"""Causal liquidity-map and phase-transition features for complete narratives.

The functions here describe *why* a source interaction should transfer price to
an opposing liquidity pool. They do not decide whether a trade wins and they
never inspect bars after the decision timestamp.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np
import pandas as pd

from auction_transition_study import Pivot
from liquidity_event_graph import InteractionEpisode, pivot_is_live
from liquidity_transfer_study import LiquidityPool


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _pool_live(pool: LiquidityPool, asof: pd.Timestamp) -> bool:
    return (
        pool.observed_ts < asof
        and (pool.first_interaction_ts is None or pool.first_interaction_ts > asof)
    )


def _level_ahead(side: int, level: float, price: float) -> bool:
    return side * (level - price) > 0.0


def _pool_mass(
    pool: LiquidityPool,
    distance_atr: float,
    source_timeframe: int,
) -> float:
    scale = math.sqrt(
        max(float(pool.timeframe), 1.0)
        / max(float(source_timeframe), 1.0)
    )
    quality = max(float(pool.strength), 0.0) / (
        1.0 + max(float(pool.equality_atr), 0.0)
    )
    return scale * quality / (1.0 + max(distance_atr, 0.0))


def liquidity_map_features(
    frame: pd.DataFrame,
    pools: Sequence[LiquidityPool],
    pivots: Sequence[Pivot],
    episode: InteractionEpisode,
    decision_ts: pd.Timestamp,
    entry: float,
    stop: float,
    target: float,
) -> dict[str, float]:
    """Describe live liquidity ahead of and behind one frozen action.

    A clean reversal has consumed meaningful source-side liquidity without
    leaving another comparable pool immediately behind it, while a plausible
    destination remains ahead. The features preserve this continuum rather
    than turning it into a hand-picked gate.
    """
    side = episode.side
    source = episode.primary_pool
    source_atr = max(float(source.atr), abs(entry - stop), 1e-12)
    risk = max(abs(entry - stop), 1e-12)
    sigma_price = max(
        float(frame.loc[:decision_ts, "prior_sigma"].iloc[-1]) * entry,
        1e-12,
    )
    touched = set(episode.touched_pool_ids)

    live_pools = [
        pool
        for pool in pools
        if pool.pool_id not in touched and _pool_live(pool, decision_ts)
    ]
    ahead: list[tuple[LiquidityPool, float]] = []
    behind: list[tuple[LiquidityPool, float]] = []
    for pool in live_pools:
        level = float(pool.inner)
        signed = side * (level - entry)
        if signed > 0.0:
            ahead.append((pool, signed))
        elif signed < 0.0:
            behind.append((pool, -signed))

    output: dict[str, float] = {
        "live_ahead_pool_count": float(len(ahead)),
        "live_behind_pool_count": float(len(behind)),
        "live_ahead_comparable_pool_count": float(
            sum(
                pool.timeframe >= episode.source_max_timeframe
                for pool, _ in ahead
            )
        ),
        "live_behind_comparable_pool_count": float(
            sum(
                pool.timeframe >= episode.source_max_timeframe
                for pool, _ in behind
            )
        ),
    }
    for name, collection in (("ahead", ahead), ("behind", behind)):
        distances_atr = [distance / source_atr for _, distance in collection]
        distances_r = [distance / risk for _, distance in collection]
        comparable = [
            (pool, distance)
            for pool, distance in collection
            if pool.timeframe >= episode.source_max_timeframe
        ]
        output[f"nearest_{name}_pool_atr"] = (
            min(distances_atr) if distances_atr else math.nan
        )
        output[f"nearest_{name}_pool_r"] = (
            min(distances_r) if distances_r else math.nan
        )
        output[f"nearest_{name}_comparable_pool_atr"] = (
            min(distance / source_atr for _, distance in comparable)
            if comparable
            else math.nan
        )
        for radius in (0.5, 1.0, 2.0, 4.0):
            tag = str(radius).replace(".", "p")
            output[f"{name}_pool_count_{tag}atr"] = float(
                sum(
                    distance <= radius * source_atr
                    for _, distance in collection
                )
            )
            output[f"{name}_comparable_pool_count_{tag}atr"] = float(
                sum(
                    pool.timeframe >= episode.source_max_timeframe
                    and distance <= radius * source_atr
                    for pool, distance in collection
                )
            )
        output[f"{name}_pool_mass"] = float(
            sum(
                _pool_mass(
                    pool,
                    distance / source_atr,
                    episode.source_max_timeframe,
                )
                for pool, distance in collection
            )
        )
        output[f"{name}_comparable_pool_mass"] = float(
            sum(
                _pool_mass(
                    pool,
                    distance / source_atr,
                    episode.source_max_timeframe,
                )
                for pool, distance in comparable
            )
        )

    total_mass = output["ahead_pool_mass"] + output["behind_pool_mass"]
    output["liquidity_mass_imbalance"] = (
        output["ahead_pool_mass"] - output["behind_pool_mass"]
    ) / max(total_mass, 1e-12)
    comparable_mass = (
        output["ahead_comparable_pool_mass"]
        + output["behind_comparable_pool_mass"]
    )
    output["comparable_liquidity_mass_imbalance"] = (
        output["ahead_comparable_pool_mass"]
        - output["behind_comparable_pool_mass"]
    ) / max(comparable_mass, 1e-12)

    live_pivots = [
        pivot
        for pivot in pivots
        if pivot.observed_ts < decision_ts
        and pivot_is_live(frame, pivot, decision_ts)
    ]
    ahead_pivots = [
        (pivot, side * (pivot.price - entry))
        for pivot in live_pivots
        if _level_ahead(side, pivot.price, entry)
    ]
    behind_pivots = [
        (pivot, -side * (pivot.price - entry))
        for pivot in live_pivots
        if not _level_ahead(side, pivot.price, entry)
        and abs(pivot.price - entry) > 1e-12
    ]
    for name, collection in (
        ("ahead", ahead_pivots),
        ("behind", behind_pivots),
    ):
        output[f"live_{name}_pivot_count"] = float(len(collection))
        output[f"nearest_{name}_pivot_atr"] = (
            min(distance / source_atr for _, distance in collection)
            if collection
            else math.nan
        )
        output[f"live_{name}_comparable_pivot_count"] = float(
            sum(
                pivot.timeframe >= episode.source_max_timeframe
                for pivot, _ in collection
            )
        )

    target_distance = side * (target - entry)
    output["target_distance_r"] = target_distance / risk
    output["target_distance_source_atr"] = target_distance / source_atr
    output["target_distance_sigma"] = target_distance / sigma_price
    output["ahead_pool_obstacles_before_target"] = float(
        sum(0.0 < distance < target_distance for _, distance in ahead)
    )
    output["ahead_comparable_pool_obstacles_before_target"] = float(
        sum(
            pool.timeframe >= episode.source_max_timeframe
            and 0.0 < distance < target_distance
            for pool, distance in ahead
        )
    )
    output["ahead_pivot_obstacles_before_target"] = float(
        sum(
            0.0 < distance < target_distance
            for _, distance in ahead_pivots
        )
    )

    interaction_ts = episode.interaction_ts
    for hours in (6, 24):
        lower = interaction_ts - pd.Timedelta(hours=hours)
        prior = frame.loc[
            (frame.index >= lower) & (frame.index < interaction_ts)
        ]
        if prior.empty:
            continue
        lo = float(prior.low.min())
        hi = float(prior.high.max())
        width = max(hi - lo, 1e-12)
        source_edge = (
            (float(source.outer) - lo) / width
            if source.side == "LOW"
            else (hi - float(source.outer)) / width
        )
        sweep_edge = (
            (float(episode.sweep_extreme) - lo) / width
            if source.side == "LOW"
            else (hi - float(episode.sweep_extreme)) / width
        )
        output[f"source_edge_distance_{hours}h"] = source_edge
        output[f"sweep_edge_distance_{hours}h"] = sweep_edge
        output[f"sweep_extension_{hours}h"] = (
            max(lo - episode.sweep_extreme, 0.0) / width
            if source.side == "LOW"
            else max(episode.sweep_extreme - hi, 0.0) / width
        )

    observed_source_side = [
        pool
        for pool in pools
        if pool.side == source.side
        and pool.observed_ts < interaction_ts
        and pool.pool_id not in touched
        and (
            pool.first_interaction_ts is None
            or pool.first_interaction_ts >= interaction_ts
        )
    ]
    if source.side == "LOW":
        more_extreme = [
            pool
            for pool in observed_source_side
            if pool.outer < source.outer
        ]
    else:
        more_extreme = [
            pool
            for pool in observed_source_side
            if pool.outer > source.outer
        ]
    output["source_more_extreme_pool_count"] = float(len(more_extreme))
    output["source_more_extreme_comparable_pool_count"] = float(
        sum(
            pool.timeframe >= episode.source_max_timeframe
            for pool in more_extreme
        )
    )
    output["source_more_extreme_pool_count_2atr"] = float(
        sum(
            abs(pool.outer - source.outer) <= 2.0 * source_atr
            for pool in more_extreme
        )
    )

    for hours in (6, 24):
        lower = decision_ts - pd.Timedelta(hours=hours)
        consumed = [
            pool
            for pool in pools
            if pool.side == source.side
            and pool.first_interaction_ts is not None
            and lower < pool.first_interaction_ts <= decision_ts
        ]
        output[f"source_side_pools_consumed_{hours}h"] = float(
            len(consumed)
        )
        output[f"source_side_comparable_pools_consumed_{hours}h"] = float(
            sum(
                pool.timeframe >= episode.source_max_timeframe
                for pool in consumed
            )
        )

    return output


def _segment_features(
    frame: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    side: int,
    prefix: str,
) -> dict[str, float]:
    window = frame.loc[(frame.index >= start) & (frame.index <= end)]
    if window.empty:
        return {}
    output: dict[str, float] = {f"{prefix}_minutes": float(len(window))}
    perp_open = float(window.open.iloc[0])
    perp_close = float(window.close.iloc[-1])
    aligned_return = side * math.log(
        max(perp_close, 1e-12) / max(perp_open, 1e-12)
    )
    minute_return = np.log(
        window.close.astype(float).clip(lower=1e-12)
    ).diff().dropna()
    output[f"{prefix}_perp_return_aligned"] = aligned_return
    output[f"{prefix}_path_efficiency_aligned"] = (
        aligned_return / max(float(minute_return.abs().sum()), 1e-12)
    )
    output[f"{prefix}_range_fraction"] = (
        float(window.high.max() - window.low.min())
        / max(perp_open, 1e-12)
    )
    output[f"{prefix}_activity_mean"] = float(
        window.activity_ratio.mean()
    )
    output[f"{prefix}_activity_max"] = float(
        window.activity_ratio.max()
    )
    output[f"{prefix}_range_ratio_mean"] = float(
        window.range_ratio.mean()
    )

    quote = window.quote_volume.astype(float).clip(lower=0.0)
    signed = (
        2.0 * window.taker_buy_quote_volume.astype(float) - quote
    )
    perp_delta = (
        side * float(signed.sum()) / max(float(quote.sum()), 1e-12)
    )
    output[f"{prefix}_perp_delta_aligned"] = perp_delta
    output[f"{prefix}_perp_flow_price_agreement"] = (
        perp_delta * aligned_return
    )

    spot = (
        window.spot_close.dropna()
        if "spot_close" in window.columns
        else pd.Series(dtype=float)
    )
    if len(spot) >= 2:
        spot_return = side * math.log(
            float(spot.iloc[-1]) / float(spot.iloc[0])
        )
        output[f"{prefix}_spot_return_aligned"] = spot_return
        output[f"{prefix}_venue_return_gap_aligned"] = (
            aligned_return - spot_return
        )
        output[f"{prefix}_spot_perp_price_agreement"] = (
            aligned_return * spot_return
        )
    if "spot_bar_available" in window.columns:
        output[f"{prefix}_spot_coverage"] = float(
            window.spot_bar_available.mean()
        )

    if "perp_spot_basis_log" in window.columns:
        basis = window.perp_spot_basis_log.dropna()
        if len(basis) >= 2:
            output[f"{prefix}_basis_change_aligned"] = (
                side * float(basis.iloc[-1] - basis.iloc[0])
            )

    if "oi_value_log" in window.columns:
        oi = window.oi_value_log.dropna()
        if len(oi) >= 2:
            oi_change = float(oi.iloc[-1] - oi.iloc[0])
            output[f"{prefix}_oi_value_change"] = oi_change
            output[f"{prefix}_price_oi_interaction"] = (
                aligned_return * oi_change
            )

    if {
        "spot_activity_share_1",
        "perp_activity_share_1",
        "spot_delta_share_context_1",
    }.issubset(window.columns):
        reconstructed_spot_quote = (
            window.spot_activity_share_1.astype(float)
            * quote
            / window.perp_activity_share_1.astype(float).replace(0.0, np.nan)
        )
        if "spot_bar_available" in window.columns:
            reconstructed_spot_quote = reconstructed_spot_quote.where(
                window.spot_bar_available.astype(float) > 0.5
            )
        spot_signed = (
            reconstructed_spot_quote
            * window.spot_delta_share_context_1.astype(float)
        )
        spot_q = float(reconstructed_spot_quote.sum(min_count=1))
        spot_s = float(spot_signed.sum(min_count=1))
        if (
            math.isfinite(spot_q)
            and math.isfinite(spot_s)
            and spot_q > 0.0
        ):
            spot_delta = side * spot_s / spot_q
            output[f"{prefix}_spot_delta_aligned"] = spot_delta
            output[f"{prefix}_venue_delta_gap_aligned"] = (
                perp_delta - spot_delta
            )
            output[f"{prefix}_venue_delta_agreement"] = (
                perp_delta * spot_delta
            )

    return output


def narrative_phase_features(
    frame: pd.DataFrame,
    episode: InteractionEpisode,
    zone_formed_ts: pd.Timestamp,
    decision_ts: pd.Timestamp,
) -> dict[str, float]:
    """Decompose the causal episode into approach, reclaim, impulse and return."""
    side = episode.side
    output: dict[str, float] = {}
    for minutes in (5, 15, 30):
        output.update(
            _segment_features(
                frame,
                episode.interaction_ts
                - pd.Timedelta(minutes=minutes - 1),
                episode.interaction_ts,
                side,
                f"approach_{minutes}",
            )
        )
    output.update(
        _segment_features(
            frame,
            episode.interaction_ts,
            episode.confirm_ts,
            side,
            "reclaim",
        )
    )
    output.update(
        _segment_features(
            frame,
            episode.confirm_ts,
            zone_formed_ts,
            side,
            "displacement",
        )
    )
    output.update(
        _segment_features(
            frame,
            zone_formed_ts,
            decision_ts,
            side,
            "mitigation",
        )
    )

    for metric in (
        "perp_return_aligned",
        "spot_return_aligned",
        "perp_delta_aligned",
        "spot_delta_aligned",
        "basis_change_aligned",
        "oi_value_change",
        "path_efficiency_aligned",
    ):
        approach = output.get(f"approach_15_{metric}")
        reclaim = output.get(f"reclaim_{metric}")
        displacement = output.get(f"displacement_{metric}")
        if approach is not None and reclaim is not None:
            output[f"reclaim_minus_approach_{metric}"] = (
                reclaim - approach
            )
        if approach is not None and displacement is not None:
            output[f"displacement_minus_approach_{metric}"] = (
                displacement - approach
            )
    return output

"""Causal liquidity-pool event graph and BPR/IFVG footprint construction.

The graph deliberately collapses every overlapping pool interpretation touched by
one physical five-minute interaction into one episode. An episode can expose
several alternative entries (BPR, IFVG, post-MSS FVG), but downstream routing
must choose at most one action and count the episode once.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

from auction_transition_study import Pivot, aggregate, confirmed_pivots
from liquidity_transfer_study import LiquidityPool, build_liquidity_pools


@dataclass(frozen=True)
class InteractionEpisode:
    episode_id: str
    side: int
    state: str
    interaction_ts: pd.Timestamp
    confirm_ts: pd.Timestamp
    primary_pool: LiquidityPool
    touched_pool_ids: tuple[str, ...]
    source_timeframes: tuple[int, ...]
    source_pool_count: int
    source_max_timeframe: int
    source_min_timeframe: int
    source_strength_max: float
    source_strength_mean: float
    source_equality_min: float
    source_equality_mean: float
    sweep_extreme: float
    sequence_low: float
    sequence_high: float


@dataclass(frozen=True)
class Gap:
    gap_id: str
    side: int
    completion_i: int
    completion_ts: pd.Timestamp
    middle_ts: pd.Timestamp
    lower: float
    upper: float
    gap_bps: float
    middle_range_ratio: float
    middle_body_aligned: float
    progress_sigma: float


@dataclass(frozen=True)
class EntryZone:
    action_family: str
    action_id: str
    formed_ts: pd.Timestamp
    lower: float
    upper: float
    adverse_gap_id: str | None
    aligned_gap_id: str | None
    mss_ts: pd.Timestamp | None
    mss_level: float | None
    bpr_overlap_fraction: float
    ifvg_close_through_sigma: float


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def choose_primary_pool(pools: Sequence[LiquidityPool]) -> LiquidityPool:
    """Choose the most information-rich source without creating extra episodes."""
    if not pools:
        raise ValueError("at least one pool is required")
    return max(
        pools,
        key=lambda pool: (
            pool.timeframe,
            pool.strength,
            -pool.equality_atr,
            pool.separation_bars,
            pool.observed_ts,
            pool.pool_id,
        ),
    )


def build_interaction_episodes(
    symbol: str,
    frame: pd.DataFrame,
    timeframes: Iterable[int] = (15, 60),
) -> tuple[list[InteractionEpisode], list[LiquidityPool], list[Pivot]]:
    """Collapse pool aliases and classify one physical first interaction once."""
    pools: list[LiquidityPool] = []
    pivots: list[Pivot] = []
    for timeframe in timeframes:
        timeframe_pools, timeframe_pivots = build_liquidity_pools(frame, timeframe)
        pools.extend(timeframe_pools)
        pivots.extend(timeframe_pivots)
    sentinel = pd.Timestamp("2262-04-01", tz="UTC")
    pools = sorted(
        pools,
        key=lambda p: (p.first_interaction_ts or sentinel, p.side, p.pool_id),
    )
    pivots = sorted(pivots, key=lambda p: (p.observed_ts, p.timeframe, p.pivot_id))
    bars5 = aggregate(frame, 5)
    grouped: dict[tuple[pd.Timestamp, str], list[LiquidityPool]] = {}
    for pool in pools:
        if pool.first_interaction_ts is None:
            continue
        grouped.setdefault((pool.first_interaction_ts, pool.side), []).append(pool)

    episodes: list[InteractionEpisode] = []
    for (interaction_ts, pool_side), touched in sorted(
        grouped.items(),
        key=lambda item: (item[0][0], item[0][1]),
    ):
        if interaction_ts not in bars5.index:
            continue
        loc = bars5.index.get_loc(interaction_ts)
        if isinstance(loc, slice) or int(loc) >= len(bars5) - 2:
            continue
        i = int(loc)
        primary = choose_primary_pool(touched)
        sequence = bars5.iloc[i : min(i + 4, len(bars5))]
        if sequence.empty:
            continue

        state: str | None = None
        side = 0
        confirm_ts: pd.Timestamp | None = None
        if pool_side == "LOW":
            sweep_extreme = float(sequence.low.min())
            if float(sequence.low.iloc[0]) >= primary.outer:
                continue
            for offset, (_, bar) in enumerate(sequence.iloc[:3].iterrows()):
                if float(bar.close) > primary.inner:
                    state = "SWEEP_RECLAIM"
                    side = 1
                    confirm_ts = sequence.index[offset]
                    break
            if state is None and len(sequence) >= 2:
                first, second = sequence.iloc[0], sequence.iloc[1]
                if (
                    float(first.close) < primary.outer
                    and float(second.open) < primary.inner
                    and float(second.close) < primary.outer
                ):
                    state = "ACCEPTED_BREAK"
                    side = -1
                    confirm_ts = sequence.index[1]
        else:
            sweep_extreme = float(sequence.high.max())
            if float(sequence.high.iloc[0]) <= primary.outer:
                continue
            for offset, (_, bar) in enumerate(sequence.iloc[:3].iterrows()):
                if float(bar.close) < primary.inner:
                    state = "SWEEP_RECLAIM"
                    side = -1
                    confirm_ts = sequence.index[offset]
                    break
            if state is None and len(sequence) >= 2:
                first, second = sequence.iloc[0], sequence.iloc[1]
                if (
                    float(first.close) > primary.outer
                    and float(second.open) > primary.inner
                    and float(second.close) > primary.outer
                ):
                    state = "ACCEPTED_BREAK"
                    side = 1
                    confirm_ts = sequence.index[1]
        if state is None or confirm_ts is None:
            continue

        source_timeframes = tuple(sorted({pool.timeframe for pool in touched}))
        strengths = np.array([pool.strength for pool in touched], dtype=float)
        equalities = np.array([pool.equality_atr for pool in touched], dtype=float)
        touched_ids = tuple(sorted(pool.pool_id for pool in touched))
        episodes.append(
            InteractionEpisode(
                episode_id=f"{symbol}:{pool_side}:{int(interaction_ts.value)}",
                side=side,
                state=state,
                interaction_ts=interaction_ts,
                confirm_ts=confirm_ts,
                primary_pool=primary,
                touched_pool_ids=touched_ids,
                source_timeframes=source_timeframes,
                source_pool_count=len(touched),
                source_max_timeframe=max(source_timeframes),
                source_min_timeframe=min(source_timeframes),
                source_strength_max=float(strengths.max()),
                source_strength_mean=float(strengths.mean()),
                source_equality_min=float(equalities.min()),
                source_equality_mean=float(equalities.mean()),
                sweep_extreme=sweep_extreme,
                sequence_low=float(sequence.loc[:confirm_ts, "low"].min()),
                sequence_high=float(sequence.loc[:confirm_ts, "high"].max()),
            )
        )
    return episodes, pools, pivots


def detect_gaps(
    frame: pd.DataFrame,
    side: int,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
    tick: float,
    reference_ts: pd.Timestamp,
) -> list[Gap]:
    """Detect completed one-minute FVGs using each gap's own causal anchor.

    ``reference_ts`` bounds the narrative but must not become the price anchor
    for an older gap. Comparing a pre-sweep manipulation FVG with the later
    sweep price reverses its displacement sign and silently destroys genuine
    BPR geometry. Every gap is therefore measured from the last completed
    close immediately before its three-candle sequence, using volatility that
    was already available at the first candle.
    """
    index = frame.index
    start_i = max(2, int(index.searchsorted(start_ts, side="left")))
    end_i = min(len(frame) - 1, int(index.searchsorted(end_ts, side="right")))
    if reference_ts < start_ts or start_i > end_i:
        return []
    output: list[Gap] = []
    for i in range(start_i, end_i + 1):
        first = frame.iloc[i - 2]
        middle = frame.iloc[i - 1]
        third = frame.iloc[i]
        anchor = (
            float(frame.iloc[i - 3].close)
            if i >= 3
            else float(first.open)
        )
        sigma = max(float(first.prior_sigma), 1e-12)
        middle_range = max(float(middle.high - middle.low), tick)
        range_ratio = _finite(middle.range_ratio)
        if range_ratio is None or not math.isfinite(anchor) or anchor <= 0.0:
            continue
        body_aligned = side * float(middle.close - middle.open) / middle_range
        if side > 0:
            lower = float(first.high)
            upper = float(third.low)
        else:
            lower = float(third.high)
            upper = float(first.low)
        width = upper - lower
        if width < tick:
            continue
        progress_sigma = (
            side
            * math.log(max(float(third.close), 1e-12) / anchor)
            / sigma
        )
        if progress_sigma <= 0.0:
            continue
        output.append(
            Gap(
                gap_id=f"FVG:{side}:{int(index[i].value)}:{lower:.12g}:{upper:.12g}",
                side=side,
                completion_i=i,
                completion_ts=index[i],
                middle_ts=index[i - 1],
                lower=lower,
                upper=upper,
                gap_bps=width / float(third.close) * 1e4,
                middle_range_ratio=range_ratio,
                middle_body_aligned=body_aligned,
                progress_sigma=progress_sigma,
            )
        )
    return output


def _internal_mss(
    frame: pd.DataFrame,
    side: int,
    interaction_ts: pd.Timestamp,
    confirm_ts: pd.Timestamp,
    pivots1: Sequence[Pivot],
    pivots5: Sequence[Pivot],
    max_minutes: int = 30,
) -> tuple[pd.Timestamp, float, str] | None:
    wanted = "HIGH" if side > 0 else "LOW"
    lower_bound = interaction_ts - pd.Timedelta(minutes=90)
    candidates = [
        pivot
        for pivot in (*pivots1, *pivots5)
        if pivot.side == wanted
        and pivot.observed_ts <= interaction_ts
        and lower_bound <= pivot.event_ts < interaction_ts
    ]
    if not candidates:
        return None
    pivot = max(
        candidates,
        key=lambda p: (p.event_ts, p.timeframe, p.span, p.strength, p.pivot_id),
    )
    future = frame.loc[
        (frame.index >= interaction_ts)
        & (frame.index <= confirm_ts + pd.Timedelta(minutes=max_minutes))
    ]
    if side > 0:
        hits = future.index[future.close.astype(float) > pivot.price]
    else:
        hits = future.index[future.close.astype(float) < pivot.price]
    if len(hits) == 0:
        return None
    return hits[0], pivot.price, pivot.pivot_id


def build_reversal_entry_zones(
    frame: pd.DataFrame,
    episode: InteractionEpisode,
    tick: float,
    pivots1: Sequence[Pivot],
    pivots5: Sequence[Pivot],
) -> list[EntryZone]:
    """Build BPR, IFVG and post-MSS-FVG alternatives for one sweep episode."""
    if episode.state != "SWEEP_RECLAIM":
        return []
    side = episode.side
    adverse = detect_gaps(
        frame,
        -side,
        episode.interaction_ts - pd.Timedelta(minutes=45),
        episode.confirm_ts,
        tick,
        episode.interaction_ts,
    )
    aligned = detect_gaps(
        frame,
        side,
        episode.interaction_ts - pd.Timedelta(minutes=4),
        episode.confirm_ts + pd.Timedelta(minutes=24),
        tick,
        episode.interaction_ts,
    )
    adverse = [
        gap
        for gap in adverse
        if gap.middle_range_ratio >= 1.00
        and gap.middle_body_aligned >= 0.35
        and gap.progress_sigma >= 0.50
    ]
    aligned = [
        gap
        for gap in aligned
        if gap.middle_range_ratio >= 1.25
        and gap.middle_body_aligned >= 0.45
        and gap.progress_sigma >= 0.75
    ]
    if not aligned:
        return []
    mss = _internal_mss(
        frame,
        side,
        episode.interaction_ts,
        episode.confirm_ts,
        pivots1,
        pivots5,
    )
    mss_ts = None if mss is None else mss[0]
    mss_level = None if mss is None else mss[1]
    zones: list[EntryZone] = []

    bpr_candidates: list[tuple[float, Gap, Gap, float, float]] = []
    for new in aligned:
        for old in adverse:
            if old.completion_ts > new.completion_ts:
                continue
            lower = max(old.lower, new.lower)
            upper = min(old.upper, new.upper)
            width = upper - lower
            if width < tick:
                continue
            overlap_fraction = width / max(
                min(old.upper - old.lower, new.upper - new.lower),
                tick,
            )
            score = (
                overlap_fraction
                + 0.15 * min(new.middle_range_ratio, 6.0)
                + 0.10 * min(new.progress_sigma, 6.0)
                - 0.002
                * max(
                    (new.completion_ts - episode.interaction_ts)
                    / pd.Timedelta(minutes=1),
                    0.0,
                )
            )
            bpr_candidates.append((score, old, new, lower, upper))
    if bpr_candidates:
        _, old, new, lower, upper = max(
            bpr_candidates,
            key=lambda item: (item[0], item[2].completion_ts),
        )
        overlap_fraction = (upper - lower) / max(
            min(old.upper - old.lower, new.upper - new.lower),
            tick,
        )
        zones.append(
            EntryZone(
                action_family="BPR_FIRST_RETEST",
                action_id=f"BPR:{old.gap_id}|{new.gap_id}",
                formed_ts=max(episode.confirm_ts, new.completion_ts),
                lower=lower,
                upper=upper,
                adverse_gap_id=old.gap_id,
                aligned_gap_id=new.gap_id,
                mss_ts=mss_ts,
                mss_level=mss_level,
                bpr_overlap_fraction=overlap_fraction,
                ifvg_close_through_sigma=0.0,
            )
        )

    ifvg_candidates: list[tuple[float, Gap, pd.Timestamp, float]] = []
    for old in adverse:
        future = frame.loc[
            (frame.index >= episode.interaction_ts)
            & (frame.index <= episode.confirm_ts + pd.Timedelta(minutes=24))
        ]
        if side > 0:
            hits = future.index[future.close.astype(float) > old.upper]
            penetration = (
                None
                if len(hits) == 0
                else float(future.loc[hits[0], "close"] - old.upper)
            )
        else:
            hits = future.index[future.close.astype(float) < old.lower]
            penetration = (
                None
                if len(hits) == 0
                else float(old.lower - future.loc[hits[0], "close"])
            )
        if len(hits) == 0 or penetration is None:
            continue
        ts = hits[0]
        sigma = max(float(frame.loc[ts, "prior_sigma"]), 1e-12)
        close_price = float(frame.loc[ts, "close"])
        penetration_sigma = penetration / close_price / sigma
        score = penetration_sigma - 0.002 * max(
            (ts - episode.interaction_ts) / pd.Timedelta(minutes=1),
            0.0,
        )
        ifvg_candidates.append((score, old, ts, penetration_sigma))
    if ifvg_candidates:
        _, old, flip_ts, penetration_sigma = max(
            ifvg_candidates,
            key=lambda item: (item[0], item[2]),
        )
        zones.append(
            EntryZone(
                action_family="IFVG_FIRST_RETEST",
                action_id=f"IFVG:{old.gap_id}:{int(flip_ts.value)}",
                formed_ts=max(episode.confirm_ts, flip_ts),
                lower=old.lower,
                upper=old.upper,
                adverse_gap_id=old.gap_id,
                aligned_gap_id=None,
                mss_ts=mss_ts,
                mss_level=mss_level,
                bpr_overlap_fraction=0.0,
                ifvg_close_through_sigma=penetration_sigma,
            )
        )

    if mss is not None:
        mss_gaps = [gap for gap in aligned if gap.completion_ts >= mss_ts]
        if mss_gaps:
            new = min(
                mss_gaps,
                key=lambda gap: (
                    gap.completion_ts,
                    -gap.middle_range_ratio,
                    gap.gap_id,
                ),
            )
            zones.append(
                EntryZone(
                    action_family="MSS_FVG_FIRST_RETEST",
                    action_id=f"MSS:{mss[2]}|{new.gap_id}",
                    formed_ts=max(episode.confirm_ts, new.completion_ts, mss_ts),
                    lower=new.lower,
                    upper=new.upper,
                    adverse_gap_id=None,
                    aligned_gap_id=new.gap_id,
                    mss_ts=mss_ts,
                    mss_level=mss_level,
                    bpr_overlap_fraction=0.0,
                    ifvg_close_through_sigma=0.0,
                )
            )

    unique: dict[tuple[str, int, int], EntryZone] = {}
    for zone in zones:
        key = (
            zone.action_family,
            int(round(zone.lower / tick)),
            int(round(zone.upper / tick)),
        )
        unique.setdefault(key, zone)
    return list(unique.values())


def build_continuation_entry_zones(
    frame: pd.DataFrame,
    episode: InteractionEpisode,
    tick: float,
) -> list[EntryZone]:
    if episode.state != "ACCEPTED_BREAK":
        return []
    gaps = detect_gaps(
        frame,
        episode.side,
        episode.confirm_ts - pd.Timedelta(minutes=4),
        episode.confirm_ts + pd.Timedelta(minutes=30),
        tick,
        episode.confirm_ts,
    )
    gaps = [
        gap
        for gap in gaps
        if gap.middle_range_ratio >= 1.25
        and gap.middle_body_aligned >= 0.45
        and gap.progress_sigma >= 0.75
    ]
    if not gaps:
        return []
    gap = min(
        gaps,
        key=lambda item: (
            item.completion_ts,
            -item.middle_range_ratio,
            item.gap_id,
        ),
    )
    return [
        EntryZone(
            action_family="ACCEPTANCE_FVG_FIRST_RETEST",
            action_id=f"ACCEPT:{gap.gap_id}",
            formed_ts=max(episode.confirm_ts, gap.completion_ts),
            lower=gap.lower,
            upper=gap.upper,
            adverse_gap_id=None,
            aligned_gap_id=gap.gap_id,
            mss_ts=None,
            mss_level=None,
            bpr_overlap_fraction=0.0,
            ifvg_close_through_sigma=0.0,
        )
    ]


def find_first_zone_retest(
    frame: pd.DataFrame,
    side: int,
    zone: EntryZone,
    hard_invalidation: float,
    tick: float,
    max_minutes: int = 120,
) -> int | None:
    """Return the true first mitigation after completed causal detachment.

    BPR/IFVG formation often *is* the detachment bar. Starting the scan with
    ``detached=False`` skips the next-bar first retest and turns a first-touch
    strategy into a materially worse second-touch strategy. Detachment is
    therefore initialized from the completed formation bar; no intrabar order
    is inferred when a later bar both detaches and touches.
    """
    index = frame.index
    formed_i = int(index.searchsorted(zone.formed_ts, side="right")) - 1
    if formed_i < 0 or formed_i >= len(frame) - 1:
        return None
    width = max(zone.upper - zone.lower, tick)
    midpoint = 0.5 * (zone.lower + zone.upper)
    formation = frame.iloc[formed_i]
    if side > 0 and float(formation.low) <= hard_invalidation:
        return None
    if side < 0 and float(formation.high) >= hard_invalidation:
        return None
    detached = (
        float(formation.close) >= zone.upper + 0.25 * width
        if side > 0
        else float(formation.close) <= zone.lower - 0.25 * width
    )
    start = formed_i + 1
    end = min(start + max_minutes, len(frame) - 1)
    for i in range(start, end):
        bar = frame.iloc[i]
        if side > 0 and float(bar.low) <= hard_invalidation:
            return None
        if side < 0 and float(bar.high) >= hard_invalidation:
            return None
        if not detached:
            detached = (
                float(bar.close) >= zone.upper + 0.25 * width
                if side > 0
                else float(bar.close) <= zone.lower - 0.25 * width
            )
            continue
        touched = (
            float(bar.low) <= zone.upper
            if side > 0
            else float(bar.high) >= zone.lower
        )
        held = (
            float(bar.close) >= midpoint
            if side > 0
            else float(bar.close) <= midpoint
        )
        if touched and held:
            return i
    return None


def pivot_is_live(
    frame: pd.DataFrame,
    pivot: Pivot,
    asof: pd.Timestamp,
) -> bool:
    after = frame.loc[(frame.index > pivot.observed_ts) & (frame.index <= asof)]
    if after.empty:
        return True
    if pivot.side == "HIGH":
        return not bool((after.high.astype(float) >= pivot.price).any())
    return not bool((after.low.astype(float) <= pivot.price).any())


def comparable_opposing_target(
    frame: pd.DataFrame,
    pools: Sequence[LiquidityPool],
    pivots: Sequence[Pivot],
    episode: InteractionEpisode,
    entry: float,
    decision_ts: pd.Timestamp,
) -> tuple[float, str, int, str] | None:
    """Choose unswept opposite liquidity at the source scale or higher."""
    wanted = "HIGH" if episode.side > 0 else "LOW"
    min_timeframe = episode.source_max_timeframe
    pool_choices: list[tuple[float, LiquidityPool]] = []
    for pool in pools:
        if (
            pool.side != wanted
            or pool.timeframe < min_timeframe
            or pool.observed_ts >= decision_ts
            or pool.pool_id in episode.touched_pool_ids
        ):
            continue
        if (
            pool.first_interaction_ts is not None
            and pool.first_interaction_ts <= decision_ts
        ):
            continue
        target = pool.inner
        if (
            (episode.side > 0 and target > entry)
            or (episode.side < 0 and target < entry)
        ):
            pool_choices.append((target, pool))
    if pool_choices:
        if episode.side > 0:
            target, pool = min(
                pool_choices,
                key=lambda item: (
                    item[0],
                    -item[1].timeframe,
                    item[1].pool_id,
                ),
            )
        else:
            target, pool = max(
                pool_choices,
                key=lambda item: (
                    item[0],
                    item[1].timeframe,
                    item[1].pool_id,
                ),
            )
        return float(target), pool.pool_id, pool.timeframe, "POOL"

    pivot_choices = [
        pivot
        for pivot in pivots
        if pivot.side == wanted
        and pivot.timeframe >= min_timeframe
        and pivot.observed_ts < decision_ts
        and pivot_is_live(frame, pivot, decision_ts)
        and (
            (episode.side > 0 and pivot.price > entry)
            or (episode.side < 0 and pivot.price < entry)
        )
    ]
    if not pivot_choices:
        return None
    if episode.side > 0:
        pivot = min(
            pivot_choices,
            key=lambda p: (
                p.price,
                -p.timeframe,
                -p.span,
                p.pivot_id,
            ),
        )
    else:
        pivot = max(
            pivot_choices,
            key=lambda p: (
                p.price,
                p.timeframe,
                p.span,
                p.pivot_id,
            ),
        )
    return float(pivot.price), pivot.pivot_id, pivot.timeframe, "PIVOT"


def internal_pivots(
    frame: pd.DataFrame,
) -> tuple[list[Pivot], list[Pivot]]:
    return (
        confirmed_pivots(frame, 1, (2, 3)),
        confirmed_pivots(frame, 5, (2, 3)),
    )

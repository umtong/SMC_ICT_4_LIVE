"""Causal nested intraday liquidity helpers for candidate-07."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

import diagnose_impact_resilience_1s as impact
from run_aggtrade_resilience_second_safe import (
    first_touch_after_complete_confirmation_second,
)


NS_PER_SECOND = 1_000_000_000
NS_PER_THIRTY_SECONDS = 30 * NS_PER_SECOND
_SOURCE_PRIORITY = {"15S": 0, "30S": 1, "1M": 2}


def aggregate_thirty_seconds(bars: pd.DataFrame) -> pd.DataFrame:
    """Aggregate two complete Unix-aligned 15-second bars into one 30-second bar."""
    work = bars.copy().sort_values("timestamp_ns", kind="stable").reset_index(drop=True)
    work["bucket"] = work["timestamp_ns"].astype("int64") // NS_PER_THIRTY_SECONDS
    grouped = work.groupby("bucket", sort=True)
    result = grouped.agg(
        timestamp_ns=("timestamp_ns", "last"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        count=("timestamp_ns", "count"),
    ).reset_index(drop=True)
    return result[result["count"] == 2].drop(columns=["count"]).reset_index(drop=True)


def source_first_touches(
    bars: pd.DataFrame,
    pools: Iterable[impact.Pool],
) -> tuple[list[tuple[int, impact.Pool]], dict[str, Any]]:
    """Select one non-ambiguous source pool at every literal first-touch bar.

    When same-side liquidity from several clocks is touched in one bar, the
    highest structural timeframe wins; the nearest level breaks ties inside that
    timeframe. Opposite-side collisions are consumed without a scenario.
    """
    pool_list = list(pools)
    timestamps = bars["timestamp_ns"].astype("int64").to_numpy()
    highs = bars["high"].astype(float).to_numpy()
    lows = bars["low"].astype(float).to_numpy()
    closes = bars["close"].astype(float).to_numpy()
    previous_close = np.empty_like(closes)
    previous_close[0] = closes[0]
    previous_close[1:] = closes[:-1]

    by_index: dict[int, list[impact.Pool]] = defaultdict(list)
    never: Counter[str] = Counter()
    source_counts: Counter[str] = Counter(item.timeframe for item in pool_list)
    for pool in pool_list:
        touch = first_touch_after_complete_confirmation_second(
            pool,
            timestamps=timestamps,
            previous_close=previous_close,
            highs=highs,
            lows=lows,
        )
        if touch is None:
            never[pool.timeframe] += 1
        else:
            by_index[int(touch)].append(pool)

    selected: list[tuple[int, impact.Pool]] = []
    selected_counts: Counter[str] = Counter()
    counters: Counter[str] = Counter()
    for index, touched in sorted(by_index.items()):
        if len({item.side for item in touched}) > 1:
            counters["opposite_side_ambiguous_touch_bars"] += 1
            counters["opposite_side_pools_consumed"] += len(touched)
            continue
        highest = max(_SOURCE_PRIORITY.get(item.timeframe, -1) for item in touched)
        finalists = [
            item
            for item in touched
            if _SOURCE_PRIORITY.get(item.timeframe, -1) == highest
        ]
        if len(touched) > 1:
            counters["same_side_multiscale_collision_bars"] += 1
            counters["same_side_extra_pools_consumed"] += len(touched) - 1
        anchor = float(previous_close[index])
        chosen = min(finalists, key=lambda item: abs(item.level - anchor))
        selected.append((index, chosen))
        selected_counts[chosen.timeframe] += 1

    return selected, {
        "source_pool_counts": dict(sorted(source_counts.items())),
        "never_touched_counts": dict(sorted(never.items())),
        "raw_first_touch_bars": len(by_index),
        "selected_first_touch_events": len(selected),
        "selected_source_counts": dict(sorted(selected_counts.items())),
        **dict(sorted(counters.items())),
    }


def independent_boundary(
    source: impact.Pool,
    boundary: impact.Pool | None,
) -> bool:
    """Require source sweep and protected MSS swing to be different pivots."""
    return boundary is not None and int(source.pivot_ts_ns) != int(boundary.pivot_ts_ns)


def actual_timeframe_target(
    pools_by_timeframe: Mapping[str, Iterable[impact.Pool]],
    *,
    direction: str,
    entry: float,
    stop: float,
    entry_index: int,
    timestamps: np.ndarray,
    previous_close: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    touch_cache: dict[str, int | None],
    minimum_rr: float,
) -> tuple[impact.Pool, float] | None:
    """Choose nearest unconsumed target in the 15S→30S→1M→5M hierarchy."""
    risk = entry - stop if direction == "LONG" else stop - entry
    if risk <= 0.0:
        return None
    unique: dict[str, impact.Pool] = {}
    for values in pools_by_timeframe.values():
        for pool in values:
            unique[pool.pool_id] = pool
    grouped: dict[str, list[impact.Pool]] = defaultdict(list)
    for pool in unique.values():
        grouped[pool.timeframe].append(pool)

    side = "UPPER" if direction == "LONG" else "LOWER"
    entry_second = int(timestamps[entry_index]) // NS_PER_SECOND
    for timeframe in ("15S", "30S", "1M", "5M"):
        candidates = [
            pool
            for pool in grouped.get(timeframe, ())
            if pool.side == side
            and int(pool.confirmed_ts_ns) // NS_PER_SECOND < entry_second
            and (pool.level > entry if direction == "LONG" else pool.level < entry)
        ]
        candidates.sort(key=lambda pool: abs(pool.level - entry))
        for pool in candidates:
            rr = abs(pool.level - entry) / risk
            if rr < minimum_rr:
                continue
            if pool.pool_id not in touch_cache:
                touch_cache[pool.pool_id] = first_touch_after_complete_confirmation_second(
                    pool,
                    timestamps=timestamps,
                    previous_close=previous_close,
                    highs=highs,
                    lows=lows,
                )
            first_touch = touch_cache[pool.pool_id]
            if first_touch is None or first_touch > entry_index:
                return pool, rr
    return None


def source_timeframe(pool_id: str) -> str:
    if pool_id.startswith("30S"):
        return "30S"
    if pool_id.startswith("1M"):
        return "1M"
    return "15S"


# Stable public names used by the contract tests.
_aggregate_thirty_seconds = aggregate_thirty_seconds
_source_first_touches = source_first_touches
_independent_boundary = independent_boundary

#!/usr/bin/env python3
"""Run aggregate-trade resilience with implementation-safe event causality.

This wrapper changes no hypothesis, threshold, stop, target hierarchy or frozen
period.  It closes four representation ambiguities before the result is read:

1. minute/five-minute bars end at ``...999 ms`` while aggregate-trade seconds
   end at ``...999999999 ns``; the confirmation second itself is never eligible;
2. a target pool must be confirmed in an earlier wall-clock second than entry;
3. the first pool contact owns the complete fixed fifteen-second observation
   episode, so later contacts inside that episode are consumed rather than
   double-counted;
4. an unmatched OI context is explicitly invalid, never truthy ``NaN``.

All alpha screening still occurs before any NautilusTrader strategy is created.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

import diagnose_aggtrade_resilience as aggregate_candidate
import diagnose_impact_resilience_1s as impact
import diagnose_impact_resilience_1s_v2 as impact_v2


def first_touch_after_complete_confirmation_second(
    pool: impact.Pool,
    *,
    timestamps: np.ndarray,
    previous_close: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    stop_index: int | None = None,
) -> int | None:
    """Return literal first touch strictly after the confirmation second."""
    confirmation_second = int(pool.confirmed_ts_ns) // impact.NS_PER_SECOND
    first_eligible_end_ns = (
        (confirmation_second + 1) * impact.NS_PER_SECOND
        + impact.NS_PER_SECOND
        - 1
    )
    start = int(np.searchsorted(timestamps, first_eligible_end_ns, side="left"))
    end = len(timestamps) if stop_index is None else min(len(timestamps), stop_index + 1)
    if start >= end:
        return None
    if pool.side == "UPPER":
        mask = (
            (previous_close[start:end] <= pool.level)
            & (highs[start:end] >= pool.level)
        )
    else:
        mask = (
            (previous_close[start:end] >= pool.level)
            & (lows[start:end] <= pool.level)
        )
    hits = np.flatnonzero(mask)
    return None if len(hits) == 0 else start + int(hits[0])


def target_pool_after_complete_confirmation_second(
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
    """Select only unconsumed pools known before the entry wall-clock second."""
    risk = entry - stop if direction == "LONG" else stop - entry
    if risk <= 0.0:
        return None
    side = "UPPER" if direction == "LONG" else "LOWER"
    entry_second = int(timestamps[entry_index]) // impact.NS_PER_SECOND
    for timeframe in ("1M", "5M"):
        candidates = [
            pool
            for pool in pools_by_timeframe.get(timeframe, ())
            if int(pool.confirmed_ts_ns) // impact.NS_PER_SECOND < entry_second
            and pool.side == side
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


def deduplicate_contact_pools_event_safe(
    bars: pd.DataFrame,
    pools: Iterable[impact.Pool],
) -> tuple[list[impact.Pool], dict[str, int]]:
    """Create one source episode per non-overlapping fifteen-second window."""
    pool_list = list(pools)
    timestamps = bars["timestamp_ns"].astype("int64").to_numpy()
    highs = bars["high"].astype(float).to_numpy()
    lows = bars["low"].astype(float).to_numpy()
    closes = bars["close"].astype(float).to_numpy()
    previous_close = np.empty_like(closes)
    previous_close[0] = closes[0]
    previous_close[1:] = closes[:-1]

    by_touch: dict[int, list[impact.Pool]] = defaultdict(list)
    never_touched = 0
    for pool in pool_list:
        touch = first_touch_after_complete_confirmation_second(
            pool,
            timestamps=timestamps,
            previous_close=previous_close,
            highs=highs,
            lows=lows,
        )
        if touch is None:
            never_touched += 1
        else:
            by_touch[int(touch)].append(pool)

    selected: list[impact.Pool] = []
    counters: Counter[str] = Counter()
    observation_end = -1
    event_seconds = impact.ImpactLogic().event_seconds
    for touch, touched in sorted(by_touch.items()):
        if touch <= observation_end:
            counters["pools_consumed_inside_prior_event"] += len(touched)
            counters["touch_seconds_consumed_inside_prior_event"] += 1
            continue
        observation_end = touch + event_seconds - 1
        sides = {pool.side for pool in touched}
        if len(sides) > 1:
            counters["opposite_side_ambiguous_seconds"] += 1
            counters["opposite_side_pools_consumed"] += len(touched)
            continue
        if len(touched) > 1:
            counters["same_side_collision_seconds"] += 1
            counters["same_side_extra_pools_consumed"] += len(touched) - 1
        anchor = float(previous_close[touch])
        selected.append(min(touched, key=lambda pool: abs(pool.level - anchor)))

    return selected, {
        "source_pools": len(pool_list),
        "source_pools_never_touched": never_touched,
        "raw_touch_seconds": len(by_touch),
        "selected_contact_episodes": len(selected),
        **dict(sorted(counters.items())),
    }


_original_diagnose = aggregate_candidate.diagnose


def diagnose_with_explicit_context_validity(
    bars: pd.DataFrame,
    *args: object,
    **kwargs: object,
) -> dict[str, object]:
    """Normalize nullable as-of context before any branch can inspect it."""
    work = bars.copy()
    if "positioning_valid" in work.columns:
        work["positioning_valid"] = work["positioning_valid"].fillna(False).astype(bool)
    if "inventory_state" in work.columns:
        work["inventory_state"] = work["inventory_state"].fillna("INVALID").astype(str)
    return _original_diagnose(work, *args, **kwargs)


# The imported diagnostic modules resolve these attributes at call time.  Patch
# before importing the staged wrapper so the implementation correction is
# centralized, testable and does not duplicate the market logic.
impact._first_touch_index = first_touch_after_complete_confirmation_second
impact._target_pool = target_pool_after_complete_confirmation_second
impact_v2.deduplicate_contact_pools = deduplicate_contact_pools_event_safe
aggregate_candidate.diagnose = diagnose_with_explicit_context_validity

from diagnose_aggtrade_resilience_v2 import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

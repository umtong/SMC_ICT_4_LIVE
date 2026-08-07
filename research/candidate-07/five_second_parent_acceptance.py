"""Confirm a five-second retest in the next completed fifteen-second auction."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

import diagnose_impact_resilience_1s as impact
import run_local_liquidity_sweep_mss_retest as local
from five_second_sweep_execution import diagnose_five_second_execution


NS_PER_SECOND = 1_000_000_000


def parent_accepts(
    row: Mapping[str, Any],
    *,
    direction: str,
    boundary_level: float,
) -> bool:
    """Require price and net aggressor flow to accept beyond the broken level."""
    close = float(row["close"])
    signed_quote = float(row["signed_quote"])
    if direction == "LONG":
        return close > boundary_level and signed_quote > 0.0
    if direction == "SHORT":
        return close < boundary_level and signed_quote < 0.0
    raise ValueError(f"unsupported direction: {direction}")


def _parent_bar_index(timestamps: np.ndarray, observed_ns: int) -> int | None:
    """Return the first completed parent bar at or after the retest instant."""
    seconds = timestamps.astype("int64") // NS_PER_SECOND
    observed_second = int(observed_ns) // NS_PER_SECOND
    index = int(np.searchsorted(seconds, observed_second, side="left"))
    return None if index >= len(seconds) else index


def diagnose_parent_acceptance(
    seconds: pd.DataFrame,
    *,
    one_pools: Iterable[impact.Pool],
    five_pools: Iterable[impact.Pool],
    trade_start_ns: int,
    trade_end_ns: int,
    logic: local.LocalSweepMSSLogic,
) -> dict[str, Any]:
    """Delay each valid 5S retest until its parent 15S auction accepts it."""
    one_pool_list = list(one_pools)
    five_pool_list = list(five_pools)
    raw = diagnose_five_second_execution(
        seconds,
        one_pools=one_pool_list,
        five_pools=five_pool_list,
        trade_start_ns=trade_start_ns,
        trade_end_ns=trade_end_ns,
        logic=logic,
        require_retest=True,
    )

    bars_15 = local._prepare_local_bars(seconds, logic)
    bars_15["timestamp_ns"] = bars_15["timestamp_ns"].astype(object)
    parent_timestamps = bars_15["timestamp_ns"].astype("int64").to_numpy()
    source_pools = impact._pool_confirmations(
        bars_15,
        timeframe="15S",
        radius=logic.source_pivot_radius,
    )

    second_work = (
        seconds.copy()
        .sort_values("timestamp_ns", kind="stable")
        .reset_index(drop=True)
    )
    timestamps = second_work["timestamp_ns"].astype("int64").to_numpy()
    highs = second_work["high"].astype(float).to_numpy()
    lows = second_work["low"].astype(float).to_numpy()
    closes = second_work["close"].astype(float).to_numpy()
    previous_close = np.empty_like(closes)
    previous_close[0] = closes[0]
    previous_close[1:] = closes[:-1]
    target_touch_cache: dict[str, int | None] = {}
    target_pools = {
        "15S": source_pools,
        "1M": one_pool_list,
        "5M": five_pool_list,
    }

    counters: Counter[str] = Counter()
    scenarios: list[dict[str, Any]] = []
    for raw_item in raw.get("scenarios", ()):
        item = deepcopy(dict(raw_item))
        retest_ns = int(item["observed_time_ns"])
        parent_index = _parent_bar_index(parent_timestamps, retest_ns)
        if parent_index is None:
            counters["NO_COMPLETED_PARENT_AUCTION"] += 1
            continue
        parent = bars_15.iloc[parent_index]
        parent_ns = int(parent["timestamp_ns"])
        if not trade_start_ns <= parent_ns < trade_end_ns:
            counters["PARENT_AUCTION_OUTSIDE_TRADE_WINDOW"] += 1
            continue

        stop = float(item["stop"])
        after_retest = (timestamps > retest_ns) & (timestamps <= parent_ns)
        if item["direction"] == "LONG":
            invalidated = bool((lows[after_retest] <= stop).any())
        else:
            invalidated = bool((highs[after_retest] >= stop).any())
        if invalidated:
            counters["SOURCE_INVALIDATED_BEFORE_PARENT_ACCEPTANCE"] += 1
            continue

        boundary = float(item["mss"]["boundary_level"])
        if not parent_accepts(
            parent,
            direction=str(item["direction"]),
            boundary_level=boundary,
        ):
            counters["PARENT_AUCTION_DID_NOT_ACCEPT"] += 1
            continue
        counters["PARENT_AUCTION_ACCEPTED"] += 1

        entry_index = local._entry_second_index(timestamps, parent_ns)
        if entry_index is None:
            counters["NO_EXECUTION_SECOND"] += 1
            continue
        entry = float(parent["close"])
        risk = (
            entry - stop
            if item["direction"] == "LONG"
            else stop - entry
        )
        if risk <= 0.0:
            counters["NONPOSITIVE_RISK_AFTER_PARENT_ACCEPTANCE"] += 1
            continue
        target = local._target_pool(
            target_pools,
            direction=str(item["direction"]),
            entry=entry,
            stop=stop,
            entry_index=entry_index,
            timestamps=timestamps,
            previous_close=previous_close,
            highs=highs,
            lows=lows,
            touch_cache=target_touch_cache,
            minimum_rr=logic.minimum_rr,
        )
        if target is None:
            counters["NO_CAUSAL_TARGET_AFTER_PARENT_ACCEPTANCE"] += 1
            continue
        target_pool, expected_rr = target
        counters["ENTRY_READY"] += 1

        item.update(
            {
                "scenario_id": f"{item['scenario_id']}-PARENT-15S",
                "entry": entry,
                "target": float(target_pool.level),
                "expected_rr": float(expected_rr),
                "observed_time_ns": parent_ns,
                "execution_timeframe": "5S_RETEST_15S_ACCEPTANCE",
                "parent_acceptance": {
                    "timestamp_ns": parent_ns,
                    "boundary_level": boundary,
                    "open": float(parent["open"]),
                    "high": float(parent["high"]),
                    "low": float(parent["low"]),
                    "close": float(parent["close"]),
                    "signed_quote": float(parent["signed_quote"]),
                    "imbalance": float(parent["imbalance"]),
                    "price_acceptance": True,
                    "flow_acceptance": True,
                },
                "target_pool": {
                    "pool_id": target_pool.pool_id,
                    "timeframe": target_pool.timeframe,
                    "level": float(target_pool.level),
                    "confirmed_ts_ns": int(target_pool.confirmed_ts_ns),
                },
            }
        )
        scenarios.append(item)

    scenarios.sort(
        key=lambda item: (
            int(item["observed_time_ns"]),
            str(item["scenario_id"]),
        )
    )
    active_days = sorted(
        {
            pd.to_datetime(int(item["observed_time_ns"]), unit="ns", utc=True)
            .date()
            .isoformat()
            for item in scenarios
        }
    )
    return {
        "summary": {
            "require_retest": True,
            "source_timeframe": "15S",
            "micro_execution_timeframe": "5S",
            "parent_acceptance_timeframe": "15S",
            "raw_five_second_summary": raw["summary"],
            "diagnostic_counts": dict(sorted(counters.items())),
            "entry_ready": len(scenarios),
            "active_days": len(active_days),
            "active_day_labels": active_days,
            "parent_acceptance": (
                "completed 15S close remains beyond the broken 5S swing with "
                "same-direction net aggressor flow"
            ),
            "orders_or_pnl": False,
            "future_information": False,
        },
        "scenarios": scenarios,
    }


__all__ = [
    "diagnose_parent_acceptance",
    "parent_accepts",
]

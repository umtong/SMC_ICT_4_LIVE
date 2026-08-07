"""Execute a completed 15S sweep thesis on a causal five-second structure."""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

import numpy as np
import pandas as pd

import diagnose_impact_resilience_1s as impact
import run_local_liquidity_sweep_mss_retest as local
from five_second_flow_bars import (
    NS_PER_FIFTEEN_SECONDS,
    entry_second_index,
    latest_five_second_boundary,
    prepare_five_second_bars,
    same_wall_clock_second_index,
    scaled_execution_logic,
)


def diagnose_five_second_execution(
    seconds: pd.DataFrame,
    *,
    one_pools: Iterable[impact.Pool],
    five_pools: Iterable[impact.Pool],
    trade_start_ns: int,
    trade_end_ns: int,
    logic: local.LocalSweepMSSLogic,
    require_retest: bool,
) -> dict[str, Any]:
    """Preserve the 15S sweep event and resolve only MSS/retest on 5S bars."""
    if not require_retest:
        raise ValueError("five-second successor freezes the useful retest state")
    one_pool_list = list(one_pools)
    five_pool_list = list(five_pools)
    bars_15 = local._prepare_local_bars(seconds, logic)
    # Pandas may coerce an int64 nanosecond endpoint through float64 when a
    # heterogeneous row is selected. Keep the timestamp column as Python ints
    # so 14.999999999s cannot silently become 15.000000000s.
    bars_15["timestamp_ns"] = bars_15["timestamp_ns"].astype(object)
    source_pools = impact._pool_confirmations(
        bars_15,
        timeframe="15S",
        radius=logic.source_pivot_radius,
    )
    contacts, contact_summary = local._pool_first_touches(bars_15, source_pools)

    execution_logic = scaled_execution_logic(logic)
    bars_5 = prepare_five_second_bars(seconds, execution_logic)
    bars_5["timestamp_ns"] = bars_5["timestamp_ns"].astype(object)
    pools_5 = impact._pool_confirmations(
        bars_5,
        timeframe="5S",
        radius=logic.source_pivot_radius,
    )
    five_timestamps = bars_5["timestamp_ns"].astype("int64").to_numpy()

    second_work = seconds.copy().sort_values("timestamp_ns", kind="stable").reset_index(drop=True)
    timestamps = second_work["timestamp_ns"].astype("int64").to_numpy()
    highs = second_work["high"].astype(float).to_numpy()
    lows = second_work["low"].astype(float).to_numpy()
    closes = second_work["close"].astype(float).to_numpy()
    previous_close = np.empty_like(closes)
    previous_close[0] = closes[0]
    previous_close[1:] = closes[:-1]
    target_pools = {
        "15S": source_pools,
        "1M": one_pool_list,
        "5M": five_pool_list,
    }
    target_touch_cache: dict[str, int | None] = {}

    counters: Counter[str] = Counter()
    scenarios: list[dict[str, Any]] = []
    for contact_15_index, source_pool in contacts:
        event = bars_15.iloc[contact_15_index]
        event_ns = int(event["timestamp_ns"])
        if not trade_start_ns <= event_ns < trade_end_ns:
            continue
        direction = local._sweep_direction(event, source_pool, logic)
        if direction is None:
            counters["FIRST_TOUCH_NOT_QUALIFIED_SWEEP"] += 1
            continue
        counters["QUALIFIED_SWEEP"] += 1
        event_start_ns = event_ns - NS_PER_FIFTEEN_SECONDS + 1
        boundary = latest_five_second_boundary(
            pools_5,
            direction=direction,
            event_start_ns=event_start_ns,
            event_close=float(event["close"]),
            source_pivot_ns=int(source_pool.pivot_ts_ns),
            context_ns=logic.mss_context_bars * NS_PER_FIFTEEN_SECONDS,
        )
        if boundary is None:
            counters["NO_CAUSAL_5S_OPPOSING_SWING_FOR_MSS"] += 1
            continue
        contact_5_index = same_wall_clock_second_index(five_timestamps, event_ns)
        if contact_5_index is None:
            counters["NO_ALIGNED_5S_EVENT_CLOSE"] += 1
            continue
        event_atr = float(event["atr"])
        event_extreme = (
            float(event["low"]) if direction == "LONG" else float(event["high"])
        )
        mss_index, mss_reason = local._mss_index(
            bars_5,
            contact_index=contact_5_index,
            direction=direction,
            boundary=boundary,
            event_extreme=event_extreme,
            event_atr=event_atr,
            logic=execution_logic,
        )
        if mss_index is None:
            counters[mss_reason] += 1
            continue
        counters["MSS_CONFIRMED"] += 1
        retest_index, retest_reason = local._break_retest_index(
            bars_5,
            mss_index=mss_index,
            direction=direction,
            boundary_level=boundary.level,
            event_extreme=event_extreme,
            event_atr=event_atr,
            logic=execution_logic,
        )
        if retest_index is None:
            counters[retest_reason] += 1
            continue
        counters["BREAK_RETEST_CONFIRMED"] += 1

        observed = bars_5.iloc[retest_index]
        observed_ns = int(observed["timestamp_ns"])
        entry_second = entry_second_index(timestamps, observed_ns)
        if entry_second is None:
            counters["NO_EXECUTION_SECOND"] += 1
            continue
        entry = float(observed["close"])
        stop = (
            event_extreme - logic.stop_buffer_atr * event_atr
            if direction == "LONG"
            else event_extreme + logic.stop_buffer_atr * event_atr
        )
        risk = entry - stop if direction == "LONG" else stop - entry
        if risk <= 0.0:
            counters["NONPOSITIVE_RISK"] += 1
            continue
        selected = local._target_pool(
            target_pools,
            direction=direction,
            entry=entry,
            stop=stop,
            entry_index=entry_second,
            timestamps=timestamps,
            previous_close=previous_close,
            highs=highs,
            lows=lows,
            touch_cache=target_touch_cache,
            minimum_rr=logic.minimum_rr,
        )
        if selected is None:
            counters["NO_CAUSAL_TARGET_AT_MINIMUM_RR"] += 1
            continue
        target_pool, expected_rr = selected
        counters["ENTRY_READY"] += 1
        scenarios.append(
            {
                "scenario_id": f"c07-multiclock-{event_ns}-{direction}",
                "outcome": "ENTRY_READY",
                "direction": direction,
                "entry": entry,
                "stop": stop,
                "target": float(target_pool.level),
                "expected_rr": float(expected_rr),
                "source_pool_id": source_pool.pool_id,
                "observed_time_ns": observed_ns,
                "sweep": {
                    "timestamp_ns": event_ns,
                    "pool_id": source_pool.pool_id,
                    "pool_side": source_pool.side,
                    "pool_level": float(source_pool.level),
                    "pool_pivot_ts_ns": int(source_pool.pivot_ts_ns),
                    "pool_confirmed_ts_ns": int(source_pool.confirmed_ts_ns),
                    "open": float(event["open"]),
                    "high": float(event["high"]),
                    "low": float(event["low"]),
                    "close": float(event["close"]),
                    "atr": event_atr,
                    "event_extreme": event_extreme,
                    "signed_quote": float(event["signed_quote"]),
                    "imbalance": float(event["imbalance"]),
                    "quote_volume": float(event["quote_volume"]),
                },
                "mss": {
                    "execution_timeframe": "5S",
                    "timestamp_ns": int(bars_5.iloc[mss_index]["timestamp_ns"]),
                    "boundary_pool_id": boundary.pool_id,
                    "boundary_level": float(boundary.level),
                    "boundary_pivot_ts_ns": int(boundary.pivot_ts_ns),
                    "boundary_confirmed_ts_ns": int(boundary.confirmed_ts_ns),
                    "close": float(bars_5.iloc[mss_index]["close"]),
                    "body_atr": float(bars_5.iloc[mss_index]["body_atr"]),
                    "imbalance": float(bars_5.iloc[mss_index]["imbalance"]),
                },
                "retest": {
                    "execution_timeframe": "5S",
                    "timestamp_ns": observed_ns,
                    "boundary_level": float(boundary.level),
                    "close": float(observed["close"]),
                    "imbalance": float(observed["imbalance"]),
                },
                "target_pool": {
                    "pool_id": target_pool.pool_id,
                    "timeframe": target_pool.timeframe,
                    "level": float(target_pool.level),
                    "confirmed_ts_ns": int(target_pool.confirmed_ts_ns),
                },
            }
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
            "execution_timeframe": "5S",
            "source_pools": len(source_pools),
            "execution_pools": len(pools_5),
            "contact_summary": contact_summary,
            "diagnostic_counts": dict(sorted(counters.items())),
            "entry_ready": len(scenarios),
            "active_days": len(active_days),
            "active_day_labels": active_days,
            "source_event_logic_unchanged": True,
            "wall_clock_windows_unchanged": True,
            "orders_or_pnl": False,
            "future_information": False,
        },
        "scenarios": scenarios,
    }

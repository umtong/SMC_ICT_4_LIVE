"""Route each local liquidity first touch into rejection or acceptance state.

The rejection branch is the previously validated 15-second sweep -> MSS ->
broken-level retest scenario. The complementary acceptance branch trades only
when the literal first-touch bar closes outside the pool with displacement and
same-direction aggressor flow, then the broken source level is mitigated and
holds on a later completed bar. A source pool is consumed at its first touch and
can belong to only one branch.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any, Iterable

import numpy as np
import pandas as pd

import diagnose_impact_resilience_1s as impact
import run_local_liquidity_sweep_mss_retest as local


_BASE_REVERSAL_DIAGNOSE = local.diagnose


def acceptance_direction(
    row: pd.Series,
    pool: impact.Pool,
    logic: local.LocalSweepMSSLogic,
) -> str | None:
    """Classify an efficient outside close as accepted liquidity transfer."""
    atr = float(row["atr"])
    if not np.isfinite(atr) or atr <= 0.0:
        return None
    required = (
        "signed_flow_reference",
        "quote_volume_reference",
        "imbalance_reference",
        "body_reference",
    )
    if any(not np.isfinite(float(row[name])) for name in required):
        return None
    signed = float(row["signed_quote"])
    imbalance = float(row["imbalance"])
    flow_ok = (
        abs(signed) >= float(row["signed_flow_reference"])
        and float(row["quote_volume"]) >= float(row["quote_volume_reference"])
        and abs(imbalance)
        >= max(
            logic.minimum_attack_imbalance,
            float(row["imbalance_reference"]),
        )
    )
    body_ok = float(row["body_atr"]) >= max(
        logic.minimum_body_atr,
        float(row["body_reference"]),
    )
    if not flow_ok or not body_ok:
        return None
    if float(row["price_efficiency"]) < logic.maximum_event_efficiency:
        return None

    if pool.side == "UPPER":
        penetration = (float(row["high"]) - pool.level) / atr
        if (
            logic.minimum_penetration_atr
            <= penetration
            <= logic.maximum_penetration_atr
            and float(row["close"]) > pool.level
            and float(row["body"]) > 0.0
            and float(row["close_location"])
            >= logic.displacement_close_location
            and signed > 0.0
            and imbalance > 0.0
        ):
            return "LONG"
    else:
        penetration = (pool.level - float(row["low"])) / atr
        if (
            logic.minimum_penetration_atr
            <= penetration
            <= logic.maximum_penetration_atr
            and float(row["close"]) < pool.level
            and float(row["body"]) < 0.0
            and float(row["close_location"])
            <= 1.0 - logic.displacement_close_location
            and signed < 0.0
            and imbalance < 0.0
        ):
            return "SHORT"
    return None


def source_level_retest(
    bars: pd.DataFrame,
    *,
    contact_index: int,
    direction: str,
    source_level: float,
    logic: local.LocalSweepMSSLogic,
) -> tuple[int | None, str]:
    """Return the first completed mitigation which accepts the broken source."""
    end = min(
        len(bars.index),
        contact_index + 1 + logic.maximum_retest_bars,
    )
    for index in range(contact_index + 1, end):
        row = bars.iloc[index]
        range_ = max(float(row["range"]), 1e-12)
        if direction == "LONG":
            touched = float(row["low"]) <= source_level
            rejected = (
                touched
                and float(row["close"]) > source_level
                and float(row["close"]) > float(row["open"])
                and (float(row["close"]) - float(row["low"])) / range_
                >= logic.retest_close_location
                and float(row["signed_quote"]) > 0.0
            )
            if rejected:
                return index, "SOURCE_LEVEL_RETEST_ACCEPTED"
            if float(row["close"]) <= source_level:
                return None, "ACCEPTED_BREAK_CLOSED_BACK_INSIDE"
        else:
            touched = float(row["high"]) >= source_level
            rejected = (
                touched
                and float(row["close"]) < source_level
                and float(row["close"]) < float(row["open"])
                and (float(row["high"]) - float(row["close"])) / range_
                >= logic.retest_close_location
                and float(row["signed_quote"]) < 0.0
            )
            if rejected:
                return index, "SOURCE_LEVEL_RETEST_ACCEPTED"
            if float(row["close"]) >= source_level:
                return None, "ACCEPTED_BREAK_CLOSED_BACK_INSIDE"
    return None, "SOURCE_LEVEL_RETEST_NOT_CONFIRMED"


def diagnose_acceptance(
    seconds: pd.DataFrame,
    *,
    one_pools: Iterable[impact.Pool],
    five_pools: Iterable[impact.Pool],
    trade_start_ns: int,
    trade_end_ns: int,
    logic: local.LocalSweepMSSLogic,
) -> dict[str, Any]:
    """Build accepted-break continuation scenarios without orders or PnL."""
    one_pool_list = list(one_pools)
    five_pool_list = list(five_pools)
    bars = local._prepare_local_bars(seconds, logic)
    bars["timestamp_ns"] = bars["timestamp_ns"].astype(object)
    source_pools = impact._pool_confirmations(
        bars,
        timeframe="15S",
        radius=logic.source_pivot_radius,
    )
    contacts, contact_summary = local._pool_first_touches(bars, source_pools)

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
    for contact_index, source_pool in contacts:
        contact = bars.iloc[contact_index]
        contact_ns = int(contact["timestamp_ns"])
        if not trade_start_ns <= contact_ns < trade_end_ns:
            continue
        if local._sweep_direction(contact, source_pool, logic) is not None:
            counters["REJECTION_BRANCH_RESERVED"] += 1
            continue
        direction = acceptance_direction(contact, source_pool, logic)
        if direction is None:
            counters["FIRST_TOUCH_NOT_ACCEPTED_BREAK"] += 1
            continue
        counters["ACCEPTED_BREAK"] += 1
        retest_index, retest_reason = source_level_retest(
            bars,
            contact_index=contact_index,
            direction=direction,
            source_level=float(source_pool.level),
            logic=logic,
        )
        if retest_index is None:
            counters[retest_reason] += 1
            continue
        counters["SOURCE_LEVEL_RETEST_ACCEPTED"] += 1
        retest = bars.iloc[retest_index]
        observed_ns = int(retest["timestamp_ns"])
        entry_index = local._entry_second_index(timestamps, observed_ns)
        if entry_index is None:
            counters["NO_EXECUTION_SECOND"] += 1
            continue
        entry = float(retest["close"])
        atr = float(retest["atr"])
        stop = (
            float(retest["low"]) - logic.stop_buffer_atr * atr
            if direction == "LONG"
            else float(retest["high"]) + logic.stop_buffer_atr * atr
        )
        risk = entry - stop if direction == "LONG" else stop - entry
        if not np.isfinite(risk) or risk <= 0.0:
            counters["NONPOSITIVE_RETEST_INVALIDATION"] += 1
            continue
        target = local._target_pool(
            target_pools,
            direction=direction,
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
            counters["NO_CAUSAL_TARGET_AT_MINIMUM_RR"] += 1
            continue
        target_pool, expected_rr = target
        counters["ENTRY_READY"] += 1
        scenarios.append(
            {
                "scenario_id": f"c07-local-acceptance-{contact_ns}-{direction}",
                "outcome": "ENTRY_READY",
                "branch": "ACCEPTANCE_CONTINUATION",
                "direction": direction,
                "entry": entry,
                "stop": stop,
                "target": float(target_pool.level),
                "expected_rr": float(expected_rr),
                "source_pool_id": source_pool.pool_id,
                "observed_time_ns": observed_ns,
                "sweep": {
                    "timestamp_ns": contact_ns,
                    "state": "ACCEPTED_BREAK",
                    "pool_id": source_pool.pool_id,
                    "pool_side": source_pool.side,
                    "pool_level": float(source_pool.level),
                    "pool_pivot_ts_ns": int(source_pool.pivot_ts_ns),
                    "pool_confirmed_ts_ns": int(source_pool.confirmed_ts_ns),
                    "open": float(contact["open"]),
                    "high": float(contact["high"]),
                    "low": float(contact["low"]),
                    "close": float(contact["close"]),
                    "atr": float(contact["atr"]),
                    "signed_quote": float(contact["signed_quote"]),
                    "imbalance": float(contact["imbalance"]),
                    "price_efficiency": float(contact["price_efficiency"]),
                    "body_atr": float(contact["body_atr"]),
                },
                "mss": {
                    "timestamp_ns": contact_ns,
                    "state": "SOURCE_LIQUIDITY_ACCEPTED",
                    "boundary_pool_id": source_pool.pool_id,
                    "boundary_level": float(source_pool.level),
                    "close": float(contact["close"]),
                    "body_atr": float(contact["body_atr"]),
                    "imbalance": float(contact["imbalance"]),
                },
                "retest": {
                    "timestamp_ns": observed_ns,
                    "state": "BROKEN_SOURCE_LEVEL_MITIGATED_AND_HELD",
                    "boundary_level": float(source_pool.level),
                    "open": float(retest["open"]),
                    "high": float(retest["high"]),
                    "low": float(retest["low"]),
                    "close": float(retest["close"]),
                    "signed_quote": float(retest["signed_quote"]),
                    "imbalance": float(retest["imbalance"]),
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
            "branch": "ACCEPTANCE_CONTINUATION",
            "local_pools": len(source_pools),
            "contact_summary": contact_summary,
            "diagnostic_counts": dict(sorted(counters.items())),
            "entry_ready": len(scenarios),
            "active_days": len(active_days),
            "active_day_labels": active_days,
            "orders_or_pnl": False,
            "future_information": False,
        },
        "scenarios": scenarios,
    }


def diagnose_router(
    seconds: pd.DataFrame,
    *,
    one_pools: Iterable[impact.Pool],
    five_pools: Iterable[impact.Pool],
    trade_start_ns: int,
    trade_end_ns: int,
    logic: local.LocalSweepMSSLogic,
    require_retest: bool,
) -> dict[str, Any]:
    """Combine mutually exclusive rejection and acceptance scenarios."""
    if not require_retest:
        raise ValueError("auction-state router requires completed retests")
    one_pool_list = list(one_pools)
    five_pool_list = list(five_pools)
    reversal = _BASE_REVERSAL_DIAGNOSE(
        seconds,
        one_pools=one_pool_list,
        five_pools=five_pool_list,
        trade_start_ns=trade_start_ns,
        trade_end_ns=trade_end_ns,
        logic=logic,
        require_retest=True,
    )
    acceptance = diagnose_acceptance(
        seconds,
        one_pools=one_pool_list,
        five_pools=five_pool_list,
        trade_start_ns=trade_start_ns,
        trade_end_ns=trade_end_ns,
        logic=logic,
    )
    scenarios: list[dict[str, Any]] = []
    for raw in reversal.get("scenarios", ()):
        item = deepcopy(dict(raw))
        item["branch"] = "REJECTION_REVERSAL"
        scenarios.append(item)
    scenarios.extend(deepcopy(list(acceptance.get("scenarios", ()))))
    scenarios.sort(
        key=lambda item: (
            int(item["observed_time_ns"]),
            str(item["scenario_id"]),
        )
    )
    source_episodes = [
        (
            str(item["source_pool_id"]),
            int(item["sweep"]["timestamp_ns"]),
        )
        for item in scenarios
    ]
    if len(source_episodes) != len(set(source_episodes)):
        raise RuntimeError("one source first-touch episode entered multiple branches")
    branch_counts = Counter(str(item["branch"]) for item in scenarios)
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
            "family": "local_15s_auction_state_router",
            "branches": ["REJECTION_REVERSAL", "ACCEPTANCE_CONTINUATION"],
            "branch_entry_counts": dict(sorted(branch_counts.items())),
            "entry_ready": len(scenarios),
            "active_days": len(active_days),
            "active_day_labels": active_days,
            "rejection_summary": reversal["summary"],
            "acceptance_summary": acceptance["summary"],
            "source_pool_reuse": False,
            "orders_or_pnl": False,
            "future_information": False,
        },
        "scenarios": scenarios,
    }


__all__ = [
    "acceptance_direction",
    "diagnose_acceptance",
    "diagnose_router",
    "source_level_retest",
]

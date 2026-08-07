"""Parent external sweep -> 15S MSS -> same-boundary 5S retest.

The parent-external predecessor removed many internal-liquidity recoils, but its
remaining XRP failures were almost entirely routes in which a five-second swing
alone declared the market-structure shift. This successor separates state
confirmation from entry timing:

1. literal first touch of causal, still-unconsumed 1M/5M external liquidity;
2. unchanged 15S attack-flow sweep and completed reclaim;
3. completed displacement close through an independent protected 15S swing;
4. first valid 5S rejection retest of that exact broken 15S boundary;
5. unchanged source-extreme stop and nearest causal 15S/1M/5M target.

The five-second clock cannot select a different MSS boundary or direction. It
only times entry after the parent fifteen-second state transition already
completed. Signal discovery creates no orders, fills, PnL, cash or NAV.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import json
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

import backtest as base
import diagnose_impact_resilience_1s as impact
from diagnose_aggtrade_resilience_v2 import preconsume_before_event_window
from diagnose_failed_flow import aggregate_flow
from event_signal_data import CausalTradeSignal
from exact_timestamp_context import exact_local_bar_timestamps
from five_second_flow_bars import prepare_five_second_bars, scaled_execution_logic
from nautilus_trader.model.identifiers import InstrumentId
from parent_external_multiclock_scenario import parent_source_first_touches
import run_local_liquidity_sweep_mss_retest as local


NS_PER_SECOND = 1_000_000_000
NS_PER_FIVE_SECONDS = 5 * NS_PER_SECOND
NS_PER_FIFTEEN_SECONDS = 15 * NS_PER_SECOND


def _parent_and_target_pools(
    bundle: Any,
) -> tuple[
    list[impact.Pool],
    dict[str, list[impact.Pool]],
    dict[str, Any],
]:
    """Return causal parent sources and the unchanged target hierarchy."""
    impact_logic = impact.ImpactLogic()
    minute = impact._minute_features(
        bundle.minute_positioning.frame,
        atr_period=impact_logic.minute_atr_period,
    )
    five = aggregate_flow(
        bundle.minute_positioning.frame,
        5,
        impact_logic.oi_period,
    )
    five["timestamp_ns"] = five["timestamp_ns"].astype("int64")
    event_start_ns = int(bundle.seconds.iloc[0]["timestamp_ns"])

    one_all = impact._pool_confirmations(
        minute,
        timeframe="1M",
        radius=impact_logic.one_minute_pivot_radius,
    )
    five_all = impact._pool_confirmations(
        five,
        timeframe="5M",
        radius=impact_logic.five_minute_pivot_radius,
    )
    one_pools, one_pre = preconsume_before_event_window(
        one_all,
        minute,
        event_start_ns=event_start_ns,
    )
    five_pools, five_pre = preconsume_before_event_window(
        five_all,
        minute,
        event_start_ns=event_start_ns,
    )
    one = list(one_pools)
    five_minute = list(five_pools)
    return [*one, *five_minute], {"1M": one, "5M": five_minute}, {
        "source_timeframes": ["1M", "5M"],
        "one_minute_total_confirmed": len(one_all),
        "five_minute_total_confirmed": len(five_all),
        "one_minute_available_at_event_window": len(one),
        "five_minute_available_at_event_window": len(five_minute),
        "one_minute_preconsumption": one_pre,
        "five_minute_preconsumption": five_pre,
        "event_window_start_ns": event_start_ns,
    }


def _independent_latest_15s_boundary(
    pools: Iterable[impact.Pool],
    *,
    source_pool: impact.Pool,
    direction: str,
    contact_index: int,
    bars: pd.DataFrame,
    logic: local.LocalSweepMSSLogic,
) -> impact.Pool | None:
    """Select the latest protected 15S swing distinct from the parent source."""
    eligible = [
        pool
        for pool in pools
        if pool.timeframe == "15S"
        and int(pool.pivot_ts_ns) != int(source_pool.pivot_ts_ns)
    ]
    return local._latest_opposing_swing(
        eligible,
        direction=direction,
        contact_index=contact_index,
        bars=bars,
        logic=logic,
    )


def five_second_boundary_retest_index(
    five_bars: pd.DataFrame,
    *,
    mss_completed_ns: int,
    direction: str,
    boundary_level: float,
    event_extreme: float,
    event_atr: float,
    logic: local.LocalSweepMSSLogic,
) -> tuple[int | None, str]:
    """Find a 5S retest only after the 15S MSS bar is complete.

    The physical retest window remains the original six fifteen-second bars,
    represented as eighteen five-second bars. The five-second path may not
    choose another boundary or perform a second MSS search.
    """
    if direction not in {"LONG", "SHORT"}:
        raise ValueError(f"unsupported direction: {direction}")
    if event_atr <= 0.0 or boundary_level <= 0.0 or event_extreme <= 0.0:
        raise ValueError("prices and event ATR must be positive")
    if five_bars.empty:
        return None, "NO_FIVE_SECOND_BARS"

    timestamps = five_bars["timestamp_ns"].map(int).to_numpy(dtype=object)
    first = int(np.searchsorted(timestamps, int(mss_completed_ns), side="right"))
    if first >= len(five_bars.index):
        return None, "NO_FIVE_SECOND_BAR_AFTER_15S_MSS"
    maximum_bars = logic.maximum_retest_bars * (
        NS_PER_FIFTEEN_SECONDS // NS_PER_FIVE_SECONDS
    )
    end = min(len(five_bars.index), first + maximum_bars)
    for index in range(first, end):
        row = five_bars.iloc[index]
        range_ = max(float(row["range"]), 1e-12)
        if direction == "LONG":
            if float(row["low"]) <= (
                event_extreme - logic.stop_buffer_atr * event_atr
            ):
                return None, "SOURCE_INVALIDATED_DURING_5S_RETEST"
            touched = float(row["low"]) <= boundary_level
            rejected = (
                touched
                and float(row["close"]) > boundary_level
                and float(row["close"]) > float(row["open"])
                and (float(row["close"]) - float(row["low"])) / range_
                >= logic.retest_close_location
                and float(row["signed_quote"]) > 0.0
            )
        else:
            if float(row["high"]) >= (
                event_extreme + logic.stop_buffer_atr * event_atr
            ):
                return None, "SOURCE_INVALIDATED_DURING_5S_RETEST"
            touched = float(row["high"]) >= boundary_level
            rejected = (
                touched
                and float(row["close"]) < boundary_level
                and float(row["close"]) < float(row["open"])
                and (float(row["high"]) - float(row["close"])) / range_
                >= logic.retest_close_location
                and float(row["signed_quote"]) < 0.0
            )
        if rejected:
            return index, "FIVE_SECOND_SAME_BOUNDARY_RETEST_CONFIRMED"
    return None, "FIVE_SECOND_SAME_BOUNDARY_RETEST_NOT_CONFIRMED"


def diagnose(
    seconds: pd.DataFrame,
    *,
    source_pools: Iterable[impact.Pool],
    one_pools: Iterable[impact.Pool],
    five_pools: Iterable[impact.Pool],
    trade_start_ns: int,
    trade_end_ns: int,
    logic: local.LocalSweepMSSLogic,
) -> dict[str, Any]:
    """Build parent-sweep/15S-MSS/5S-retest scenarios without PnL."""
    logic.validate()
    with exact_local_bar_timestamps():
        fifteen = local._prepare_local_bars(seconds, logic)
    fifteen["timestamp_ns"] = fifteen["timestamp_ns"].map(int).astype(object)
    five_logic = scaled_execution_logic(logic)
    five = prepare_five_second_bars(seconds, five_logic)
    five["timestamp_ns"] = five["timestamp_ns"].map(int).astype(object)

    local_pools = impact._pool_confirmations(
        fifteen,
        timeframe="15S",
        radius=logic.source_pivot_radius,
    )
    parent_pool_list = list(source_pools)
    contacts, contact_summary = parent_source_first_touches(
        fifteen,
        parent_pool_list,
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
        "15S": list(local_pools),
        "1M": list(one_pools),
        "5M": list(five_pools),
    }

    counters: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    scenarios: list[dict[str, Any]] = []
    for contact_index, source_pool in contacts:
        contact = fifteen.iloc[contact_index]
        contact_ns = int(contact["timestamp_ns"])
        if not trade_start_ns <= contact_ns < trade_end_ns:
            continue
        direction = local._sweep_direction(contact, source_pool, logic)
        if direction is None:
            counters["FIRST_TOUCH_NOT_QUALIFIED_SWEEP"] += 1
            continue
        counters["QUALIFIED_PARENT_SWEEP"] += 1
        source_counts[source_pool.timeframe] += 1

        boundary = _independent_latest_15s_boundary(
            local_pools,
            source_pool=source_pool,
            direction=direction,
            contact_index=contact_index,
            bars=fifteen,
            logic=logic,
        )
        if boundary is None:
            counters["NO_INDEPENDENT_CAUSAL_15S_BOUNDARY"] += 1
            continue
        event_atr = float(contact["atr"])
        event_extreme = (
            float(contact["low"])
            if direction == "LONG"
            else float(contact["high"])
        )
        mss_index, mss_reason = local._mss_index(
            fifteen,
            contact_index=contact_index,
            direction=direction,
            boundary=boundary,
            event_extreme=event_extreme,
            event_atr=event_atr,
            logic=logic,
        )
        if mss_index is None:
            counters[mss_reason] += 1
            continue
        counters["FIFTEEN_SECOND_MSS_CONFIRMED"] += 1
        mss_row = fifteen.iloc[mss_index]
        mss_ns = int(mss_row["timestamp_ns"])

        retest_index, retest_reason = five_second_boundary_retest_index(
            five,
            mss_completed_ns=mss_ns,
            direction=direction,
            boundary_level=float(boundary.level),
            event_extreme=event_extreme,
            event_atr=event_atr,
            logic=logic,
        )
        if retest_index is None:
            counters[retest_reason] += 1
            continue
        counters["FIVE_SECOND_SAME_BOUNDARY_RETEST_CONFIRMED"] += 1
        retest = five.iloc[retest_index]
        observed_ns = int(retest["timestamp_ns"])
        if observed_ns <= mss_ns:
            raise RuntimeError("5S retest was not strictly after completed 15S MSS")
        entry_index = local._entry_second_index(timestamps, observed_ns)
        if entry_index is None:
            counters["NO_EXECUTION_SECOND"] += 1
            continue
        entry = float(retest["close"])
        stop = (
            event_extreme - logic.stop_buffer_atr * event_atr
            if direction == "LONG"
            else event_extreme + logic.stop_buffer_atr * event_atr
        )
        risk = entry - stop if direction == "LONG" else stop - entry
        if risk <= 0.0:
            counters["NONPOSITIVE_RISK"] += 1
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
        scenario_id = (
            f"c07-parent-15s-mss-5s-retest-"
            f"{source_pool.pool_id}-{contact_ns}-{direction}"
        )
        scenarios.append(
            {
                "scenario_id": scenario_id,
                "outcome": "ENTRY_READY",
                "direction": direction,
                "entry": entry,
                "stop": stop,
                "target": float(target_pool.level),
                "expected_rr": float(expected_rr),
                "source_pool_id": source_pool.pool_id,
                "source_timeframe": source_pool.timeframe,
                "observed_time_ns": observed_ns,
                "sweep": {
                    "timestamp_ns": contact_ns,
                    "pool_id": source_pool.pool_id,
                    "source_timeframe": source_pool.timeframe,
                    "pool_side": source_pool.side,
                    "pool_level": float(source_pool.level),
                    "pool_pivot_ts_ns": int(source_pool.pivot_ts_ns),
                    "pool_confirmed_ts_ns": int(source_pool.confirmed_ts_ns),
                    "open": float(contact["open"]),
                    "high": float(contact["high"]),
                    "low": float(contact["low"]),
                    "close": float(contact["close"]),
                    "atr": event_atr,
                    "event_extreme": event_extreme,
                    "signed_quote": float(contact["signed_quote"]),
                    "imbalance": float(contact["imbalance"]),
                    "quote_volume": float(contact["quote_volume"]),
                },
                "mss": {
                    "execution_timeframe": "15S",
                    "timestamp_ns": mss_ns,
                    "boundary_pool_id": boundary.pool_id,
                    "boundary_level": float(boundary.level),
                    "boundary_pivot_ts_ns": int(boundary.pivot_ts_ns),
                    "boundary_confirmed_ts_ns": int(boundary.confirmed_ts_ns),
                    "source_and_boundary_pivots_distinct": (
                        int(boundary.pivot_ts_ns)
                        != int(source_pool.pivot_ts_ns)
                    ),
                    "close": float(mss_row["close"]),
                    "body_atr": float(mss_row["body_atr"]),
                    "imbalance": float(mss_row["imbalance"]),
                },
                "retest": {
                    "execution_timeframe": "5S",
                    "timestamp_ns": observed_ns,
                    "boundary_pool_id": boundary.pool_id,
                    "boundary_level": float(boundary.level),
                    "same_boundary_as_15s_mss": True,
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

    scenarios.sort(
        key=lambda item: (
            int(item["observed_time_ns"]),
            str(item["scenario_id"]),
        )
    )
    if len({item["source_pool_id"] for item in scenarios}) != len(scenarios):
        raise RuntimeError("a parent source pool produced more than one trade scenario")
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
            "family": "parent_external_15S_MSS_5S_same_boundary_retest",
            "source_timeframes": ["1M", "5M"],
            "mss_timeframe": "15S",
            "retest_timeframe": "5S",
            "retest_boundary_owned_by_15s_mss": True,
            "physical_retest_window_seconds": (
                logic.maximum_retest_bars * 15
            ),
            "parent_source_pools": len(parent_pool_list),
            "local_15s_boundary_pools": len(local_pools),
            "contact_summary": contact_summary,
            "qualified_parent_sweeps_by_timeframe": dict(
                sorted(source_counts.items())
            ),
            "diagnostic_counts": dict(sorted(counters.items())),
            "entry_ready": len(scenarios),
            "active_days": len(active_days),
            "active_day_labels": active_days,
            "source_pool_reuse": False,
            "orders_or_pnl": False,
            "future_information": False,
        },
        "scenarios": scenarios,
    }


def discover(
    *,
    config: Mapping[str, Any],
    bundle: Any,
    start: Any,
    end: Any,
    require_retest: bool,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any], dict[str, Any]]:
    del config
    if not require_retest:
        raise ValueError("hybrid successor requires the same-boundary 5S retest")
    logic = local.LocalSweepMSSLogic()
    logic.validate()
    parent_sources, targets, parent_context = _parent_and_target_pools(bundle)
    selected = diagnose(
        bundle.seconds,
        source_pools=parent_sources,
        one_pools=targets["1M"],
        five_pools=targets["5M"],
        trade_start_ns=base._utc_ns(start),
        trade_end_ns=base._utc_ns(end),
        logic=logic,
    )
    upstream = {
        "summary": selected["summary"],
        "scenarios": selected["scenarios"],
    }
    contract = {
        "family": "parent_external_15S_MSS_5S_same_boundary_retest",
        "logic": asdict(logic),
        "source_timeframes": ["1M", "5M"],
        "mss_timeframe": "15S",
        "retest_timeframe": "5S",
        "state_sequence": [
            "parent external first touch and failed attack",
            "independent protected 15S swing displacement MSS",
            "first valid 5S rejection retest of the same 15S boundary",
        ],
        "five_second_clock_selects_direction_or_boundary": False,
        "retest_boundary_owned_by_15s_mss": True,
        "physical_retest_window_seconds": logic.maximum_retest_bars * 15,
        "target_hierarchy": "15S then 1M then 5M unconsumed causal liquidity",
        "parent_source_context": parent_context,
        "selected_summary": selected["summary"],
        "loader_diagnostics": dict(bundle.diagnostics),
        "implementation_clean": (
            int(bundle.diagnostics.get("out_of_order_rows", -1)) == 0
            and int(bundle.diagnostics.get("duplicate_agg_trade_ids", -1)) == 0
            and int(bundle.diagnostics.get("noncontiguous_second_transitions", -1)) == 0
            and int(bundle.diagnostics.get("missing_seconds_from_span", -1)) == 0
        ),
        "orders_or_pnl_created_by_preprocessor": False,
        "future_information": False,
    }
    return bundle.seconds, upstream, selected, contract


def build_signals(
    *,
    report: Mapping[str, Any],
    upstream_report: Mapping[str, Any],
    instrument_id: InstrumentId,
) -> list[CausalTradeSignal]:
    del upstream_report
    output: list[CausalTradeSignal] = []
    for item in report.get("scenarios", ()):
        if item.get("outcome") != "ENTRY_READY":
            continue
        observed_ns = int(item["observed_time_ns"])
        details = {
            "structural_family": (
                "parent_external_15S_MSS_5S_same_boundary_retest"
            ),
            "source_scope": "parent_external_liquidity",
            "source_timeframe": item["source_timeframe"],
            "mss_timeframe": "15S",
            "retest_timeframe": "5S",
            "five_second_clock_selects_direction_or_boundary": False,
            "sweep": item["sweep"],
            "mss": item["mss"],
            "retest": item["retest"],
            "target_pool": item["target_pool"],
        }
        serialized = json.dumps(details, sort_keys=True)
        for forbidden in ("path", "mfe", "mae", "terminal", "realized_r"):
            if forbidden in serialized.lower():
                raise RuntimeError(f"future-path field leaked into signal: {forbidden}")
        output.append(
            CausalTradeSignal(
                instrument_id=instrument_id,
                scenario_id=str(item["scenario_id"]),
                direction=str(item["direction"]),
                entry_reference=float(item["entry"]),
                stop_price=float(item["stop"]),
                target_price=float(item["target"]),
                expected_rr=float(item["expected_rr"]),
                source_pool_id=str(item["source_pool_id"]),
                signal_kind="PARENT_EXTERNAL_15S_MSS_5S_RETEST",
                details_json=serialized,
                observed_time_ns=observed_ns,
                ts_event=observed_ns + 1,
                ts_init=observed_ns + 1,
            )
        )
    output.sort(key=lambda signal: (signal.ts_event, signal.scenario_id))
    if len({signal.scenario_id for signal in output}) != len(output):
        raise RuntimeError("duplicate hybrid scenario identifiers")
    return output


__all__ = [
    "build_signals",
    "diagnose",
    "discover",
    "five_second_boundary_retest_index",
]

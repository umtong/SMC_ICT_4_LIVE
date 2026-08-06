"""Evaluate impact -> MSS/FVG plans on causal aggregate-trade paths.

This module bridges a structural scenario detector to market-path validation. It
still creates no orders, fills, PnL, cash ledger or NAV. Qualified upstream
impact events are converted into one-minute MSS/FVG plans; execution is measured
at the first completed aggregate-trade second strictly after the signal minute.
Targets are already-confirmed and unconsumed one-minute then five-minute pools.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

import diagnose_impact_resilience_1s as impact
from model_impact_mss_fvg import (
    ImpactEvent,
    ImpactMSSFVGLogic,
    diagnose as diagnose_mss_fvg,
)
from run_aggtrade_resilience_second_safe import (
    target_pool_after_complete_confirmation_second,
)


def impact_events_from_fixed_scenarios(
    scenarios: Iterable[Mapping[str, Any]],
    *,
    source_stop_buffer_atr: float,
) -> list[ImpactEvent]:
    """Extract only impact events whose auction conditions already passed."""
    output: list[ImpactEvent] = []
    accepted = {"ENTRY_READY", "NO_CAUSAL_TARGET_AT_MINIMUM_RR"}
    for item in scenarios:
        if str(item.get("outcome")) not in accepted:
            continue
        direction = str(item["direction"])
        if direction not in {"LONG", "SHORT"}:
            raise ValueError(f"unexpected impact direction: {direction}")
        contact = item["contact"]
        terminal = item["event_terminal"]
        atr = float(contact["atr"])
        stop = float(item["stop"])
        event_extreme = (
            stop + source_stop_buffer_atr * atr
            if direction == "LONG"
            else stop - source_stop_buffer_atr * atr
        )
        output.append(
            ImpactEvent(
                event_id=str(item["scenario_id"]),
                direction=direction,  # type: ignore[arg-type]
                event_end_ns=int(terminal["timestamp_ns"]),
                source_pool_id=str(item["pool_id"]),
                source_level=float(item["liquidity_level"]),
                event_extreme=event_extreme,
            )
        )
    output.sort(key=lambda item: (item.event_end_ns, item.event_id))
    return output


def _first_second_after_completed_signal(
    timestamps: np.ndarray,
    observed_ns: int,
) -> int | None:
    observed_second = int(observed_ns) // impact.NS_PER_SECOND
    first_eligible_end_ns = (
        (observed_second + 1) * impact.NS_PER_SECOND
        + impact.NS_PER_SECOND
        - 1
    )
    index = int(np.searchsorted(timestamps, first_eligible_end_ns, side="left"))
    return None if index >= len(timestamps) else index


def evaluate(
    minutes: pd.DataFrame,
    seconds: pd.DataFrame,
    *,
    upstream_scenarios: Iterable[Mapping[str, Any]],
    target_pools: Mapping[str, Iterable[impact.Pool]],
    source_stop_buffer_atr: float,
    maximum_hold_seconds: int,
    logic: ImpactMSSFVGLogic,
    minimum_rr: float,
    require_fvg_retest: bool,
) -> dict[str, Any]:
    if maximum_hold_seconds <= 0:
        raise ValueError("maximum_hold_seconds must be positive")
    if minimum_rr <= 0.0:
        raise ValueError("minimum_rr must be positive")
    events = impact_events_from_fixed_scenarios(
        upstream_scenarios,
        source_stop_buffer_atr=source_stop_buffer_atr,
    )
    plans, event_diagnostics = diagnose_mss_fvg(
        minutes,
        events=events,
        logic=logic,
        require_fvg_retest=require_fvg_retest,
    )

    work = (
        seconds.copy()
        .sort_values("timestamp_ns", kind="stable")
        .reset_index(drop=True)
    )
    timestamps = work["timestamp_ns"].astype("int64").to_numpy()
    highs = work["high"].astype(float).to_numpy()
    lows = work["low"].astype(float).to_numpy()
    closes = work["close"].astype(float).to_numpy()
    previous_close = np.empty_like(closes)
    previous_close[0] = closes[0]
    previous_close[1:] = closes[:-1]

    counters: Counter[str] = Counter()
    scenarios: list[dict[str, Any]] = []
    target_touch_cache: dict[str, int | None] = {}
    slot_end = -1
    for plan in sorted(plans, key=lambda item: (item.observed_ns, item.scenario_id)):
        entry_index = _first_second_after_completed_signal(
            timestamps,
            plan.observed_ns,
        )
        if entry_index is None:
            counters["NO_POST_SIGNAL_TRADE_SECOND"] += 1
            continue
        if entry_index <= slot_end:
            counters["SIGNAL_DURING_ACTIVE_SLOT"] += 1
            continue
        row = work.iloc[entry_index]
        entry = float(row["close"])
        stop = float(plan.stop)
        risk = entry - stop if plan.direction == "LONG" else stop - entry
        stop_touched_in_entry_second = (
            float(row["low"]) <= stop
            if plan.direction == "LONG"
            else float(row["high"]) >= stop
        )
        if risk <= 0.0 or stop_touched_in_entry_second:
            counters["POST_SIGNAL_SOURCE_INVALIDATED"] += 1
            scenarios.append(
                {
                    "scenario_id": plan.scenario_id,
                    "outcome": "POST_SIGNAL_SOURCE_INVALIDATED",
                    "observed_ns": plan.observed_ns,
                    "entry_second_ns": int(row["timestamp_ns"]),
                    "entry": entry,
                    "stop": stop,
                    "stop_touched_in_entry_second": (
                        stop_touched_in_entry_second
                    ),
                }
            )
            continue
        selected = target_pool_after_complete_confirmation_second(
            target_pools,
            direction=plan.direction,
            entry=entry,
            stop=stop,
            entry_index=entry_index,
            timestamps=timestamps,
            previous_close=previous_close,
            highs=highs,
            lows=lows,
            touch_cache=target_touch_cache,
            minimum_rr=minimum_rr,
        )
        if selected is None:
            counters["NO_CAUSAL_TARGET_AT_MINIMUM_RR"] += 1
            scenarios.append(
                {
                    "scenario_id": plan.scenario_id,
                    "outcome": "NO_CAUSAL_TARGET_AT_MINIMUM_RR",
                    "observed_ns": plan.observed_ns,
                    "entry_second_ns": int(row["timestamp_ns"]),
                    "entry": entry,
                    "stop": stop,
                    "risk": risk,
                }
            )
            continue
        target_pool, expected_rr = selected
        path, terminal_index = impact._path_result(
            work,
            start_index=entry_index,
            direction=plan.direction,
            entry=entry,
            stop=stop,
            target=target_pool.level,
            max_hold_seconds=maximum_hold_seconds,
        )
        slot_end = max(slot_end, terminal_index)
        counters["ENTRY_READY"] += 1
        scenarios.append(
            {
                "scenario_id": plan.scenario_id,
                "outcome": "ENTRY_READY",
                "direction": plan.direction,
                "source_pool_id": plan.source_pool_id,
                "source_level": plan.source_level,
                "observed_ns": plan.observed_ns,
                "entry_second_ns": int(row["timestamp_ns"]),
                "entry": entry,
                "stop": stop,
                "risk": risk,
                "target": float(target_pool.level),
                "target_pool_id": target_pool.pool_id,
                "target_timeframe": target_pool.timeframe,
                "expected_rr": float(expected_rr),
                "mss_ns": plan.mss_ns,
                "mss_swing_id": plan.mss_swing_id,
                "mss_level": plan.mss_level,
                "fvg_id": plan.fvg_id,
                "fvg_lower": plan.fvg_lower,
                "fvg_upper": plan.fvg_upper,
                "retest_required": plan.retest_required,
                "retest_ns": plan.retest_ns,
                "body_atr": plan.body_atr,
                "displacement_rank": plan.displacement_rank,
                "path": path,
            }
        )

    entries = [
        item for item in scenarios if item["outcome"] == "ENTRY_READY"
    ]
    outcomes = Counter(str(item["path"]["outcome"]) for item in entries)
    dates = Counter(
        pd.to_datetime(
            int(item["entry_second_ns"]),
            unit="ns",
            utc=True,
        )
        .date()
        .isoformat()
        for item in entries
    )
    mfe = [float(item["path"]["mfe_r"]) for item in entries]
    mae = [float(item["path"]["mae_r"]) for item in entries]
    maximum_day_share = max(dates.values()) / len(entries) if entries else None
    gate = {
        "minimum_entry_ready": len(entries) >= 7,
        "minimum_active_days": len(dates) >= 4,
        "more_targets_than_stops": outcomes["TARGET"] > outcomes["STOP"],
        "median_mfe_at_least_minimum_rr": (
            bool(mfe) and float(pd.Series(mfe).median()) >= minimum_rr
        ),
        "median_mae_below_one_r": (
            bool(mae) and float(pd.Series(mae).median()) < 1.0
        ),
        "maximum_day_share_at_most_55pct": (
            maximum_day_share is not None and maximum_day_share <= 0.55
        ),
    }
    gate["passed"] = all(gate.values())
    return {
        "summary": {
            "upstream_impact_events": len(events),
            "mss_fvg_plans": len(plans),
            "event_diagnostic_counts": dict(
                sorted(
                    Counter(
                        item.outcome for item in event_diagnostics
                    ).items()
                )
            ),
            "path_counts": dict(sorted(counters.items())),
            "entry_ready": len(entries),
            "active_days": len(dates),
            "entries_by_day": dict(sorted(dates.items())),
            "maximum_day_share": maximum_day_share,
            "path_outcome_counts": dict(sorted(outcomes.items())),
            "target_minus_stop": outcomes["TARGET"] - outcomes["STOP"],
            "median_mfe_r": (
                float(pd.Series(mfe).median()) if mfe else None
            ),
            "median_mae_r": (
                float(pd.Series(mae).median()) if mae else None
            ),
            "diagnostic_gate": gate,
        },
        "event_diagnostics": [
            {
                "event_id": item.event_id,
                "outcome": item.outcome,
                "details": item.details,
            }
            for item in event_diagnostics
        ],
        "scenarios": scenarios,
    }


__all__ = ["evaluate", "impact_events_from_fixed_scenarios"]

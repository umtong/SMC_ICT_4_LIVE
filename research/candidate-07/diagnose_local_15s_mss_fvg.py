"""Local 15-second MSS/displacement/FVG scenario after a failed auction.

The source pool remains a causal five-minute external-liquidity pivot and the
failed-auction event remains the target-free one-second volume-time detector.
Only the structure clock changes. A five-candle fifteen-second swing is local
to the seconds-scale event; after the completed recovery terminal, the current
partial local bucket is allowed to finish, then a later complete fifteen-second
close must break that swing with displacement and a causal FVG whose three
source bars are all post-event. The baseline waits for the first valid FVG
retest. Its single ablation removes only the retest and enters after the
completed displacement bar.

The target is the nearest opposing five-minute pool known and unconsumed before
entry. It may not be skipped for a farther objective, and it must be positive
after the same adverse execution-cost contract used by risk sizing. This module
creates no orders, fills, PnL, cash ledger or NAV.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

import diagnose_impact_resilience_1s as impact
from diagnose_impact_mss_fvg_paths import _first_second_after_completed_signal
from diagnose_value_normalized_mss_fvg import _cost_adjusted_terminal_r
from execution_cost_geometry import adverse_execution_geometry
from model_impact_mss_fvg import (
    EntryPlan,
    EventDiagnostic,
    ImpactEvent,
    ImpactMSSFVGLogic,
    _directional_displacement,
    _fvg_at,
    _latest_mss_swing,
    confirmed_swings,
    minute_features,
)
from run_aggtrade_resilience_second_safe import (
    target_pool_after_complete_confirmation_second,
)


NS_PER_SECOND = 1_000_000_000


def local_structure_logic(*, bar_seconds: int = 15) -> ImpactMSSFVGLogic:
    """Return the frozen local-structure logic with physical-time scaling."""
    if bar_seconds <= 0:
        raise ValueError("bar_seconds must be positive")
    if 3600 % bar_seconds != 0 or 300 % bar_seconds != 0:
        raise ValueError(
            "bar_seconds must exactly divide sixty and five physical minutes"
        )
    logic = ImpactMSSFVGLogic(
        pivot_radius=2,
        displacement_rank_period=3600 // bar_seconds,
        minimum_displacement_rank=0.80,
        minimum_body_atr=0.15,
        minimum_close_location=0.65,
        maximum_mss_minutes=300 // bar_seconds,
        maximum_retest_minutes=300 // bar_seconds,
        retest_close_location=0.55,
        stop_buffer_atr=0.05,
    )
    logic.validate()
    return logic


def aggregate_complete_clock_bars(
    seconds: pd.DataFrame,
    *,
    bar_seconds: int = 15,
) -> pd.DataFrame:
    """Aggregate only complete contiguous Unix-aligned clock bars."""
    if bar_seconds <= 0:
        raise ValueError("bar_seconds must be positive")
    required = {"timestamp_ns", "open", "high", "low", "close", "atr"}
    missing = required.difference(seconds.columns)
    if missing:
        raise ValueError(f"second columns missing: {sorted(missing)}")
    work = (
        seconds.copy()
        .sort_values("timestamp_ns", kind="stable")
        .reset_index(drop=True)
    )
    work["timestamp_ns"] = work["timestamp_ns"].astype("int64")
    step = work["timestamp_ns"].diff().dropna()
    if bool((step != NS_PER_SECOND).any()):
        raise ValueError("second clock must be contiguous")
    width_ns = bar_seconds * NS_PER_SECOND
    work["clock_bucket"] = work["timestamp_ns"] // width_ns
    grouped = work.groupby("clock_bucket", sort=True)
    bars = grouped.agg(
        timestamp_ns=("timestamp_ns", "last"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        atr=("atr", "last"),
        source_seconds=("timestamp_ns", "count"),
    ).reset_index(drop=True)
    bars = bars[bars["source_seconds"] == bar_seconds].copy()
    bars["timestamp_ns"] = bars["timestamp_ns"].astype("int64")
    return bars.reset_index(drop=True)


def events_from_detector(
    detector_report: Mapping[str, Any],
    *,
    bar_seconds: int = 15,
) -> tuple[list[ImpactEvent], dict[str, dict[str, Any]]]:
    """Synchronize completed detector events to the local bar clock."""
    if bar_seconds <= 0:
        raise ValueError("bar_seconds must be positive")
    events: list[ImpactEvent] = []
    details: dict[str, dict[str, Any]] = {}
    accepted = [
        dict(item)
        for item in detector_report.get("scenarios", ())
        if item.get("outcome") == "EVENT_ACCEPTED"
    ]
    accepted.sort(
        key=lambda item: (
            int(item["recovery_terminal"]["timestamp_ns"]),
            str(item["scenario_id"]),
        )
    )
    for source in accepted:
        source_id = str(source["scenario_id"])
        event_id = f"{source_id}-LOCAL-15S-MSS-FVG"
        detector_end_ns = int(source["recovery_terminal"]["timestamp_ns"])
        width_ns = bar_seconds * NS_PER_SECOND
        local_search_anchor_ns = (
            (detector_end_ns // width_ns) + 1
        ) * width_ns - 1
        events.append(
            ImpactEvent(
                event_id=event_id,
                direction=str(source["direction"]),  # type: ignore[arg-type]
                event_end_ns=local_search_anchor_ns,
                source_pool_id=str(source["pool_id"]),
                source_level=float(source["liquidity_level"]),
                event_extreme=float(source["event_extreme"]),
            )
        )
        details[event_id] = {
            "source_scenario_id": source_id,
            "direction": str(source["direction"]),
            "source_pool_id": str(source["pool_id"]),
            "source_level": float(source["liquidity_level"]),
            "event_extreme": float(source["event_extreme"]),
            "contact": source["contact"],
            "recovery_terminal": source["recovery_terminal"],
            "detector_recovery_terminal_ns": detector_end_ns,
            "local_search_anchor_ns": local_search_anchor_ns,
            "mss_search_begins_after_ns": local_search_anchor_ns,
            "partial_recovery_bucket_used_for_structure": False,
        }
    return events, details


def diagnose_local_mss_fvg(
    local_bars: pd.DataFrame,
    *,
    events: Iterable[ImpactEvent],
    logic: ImpactMSSFVGLogic,
    require_fvg_retest: bool,
) -> tuple[list[EntryPlan], list[EventDiagnostic]]:
    """Diagnose local structure using only full post-event FVG source bars."""
    logic.validate()
    work = minute_features(
        local_bars,
        rank_period=logic.displacement_rank_period,
    )
    swings = confirmed_swings(work, radius=logic.pivot_radius)
    timestamps = work["timestamp_ns"].astype("int64").to_numpy()
    plans: list[EntryPlan] = []
    diagnostics: list[EventDiagnostic] = []

    for event in sorted(events, key=lambda item: (item.event_end_ns, item.event_id)):
        swing = _latest_mss_swing(swings, event=event)
        if swing is None:
            diagnostics.append(
                EventDiagnostic(event.event_id, "NO_CAUSAL_MSS_SWING", {})
            )
            continue
        first_index = int(
            np.searchsorted(timestamps, event.event_end_ns, side="right")
        )
        last_index = min(
            len(work.index),
            first_index + logic.maximum_mss_minutes,
        )
        mss_index: int | None = None
        fvg = None
        boundary_breaks = 0
        displacement_breaks = 0
        invalidated = False
        for index in range(first_index, last_index):
            row = work.iloc[index]
            source_invalid = (
                float(row["low"]) <= event.event_extreme
                if event.direction == "LONG"
                else float(row["high"]) >= event.event_extreme
            )
            if source_invalid:
                invalidated = True
                diagnostics.append(
                    EventDiagnostic(
                        event.event_id,
                        "SOURCE_INVALIDATED_BEFORE_MSS",
                        {
                            "timestamp_ns": int(row["timestamp_ns"]),
                            "event_extreme": event.event_extreme,
                        },
                    )
                )
                break
            crossed = (
                float(row["close"]) > swing.level
                if event.direction == "LONG"
                else float(row["close"]) < swing.level
            )
            if not crossed:
                continue
            boundary_breaks += 1
            if not _directional_displacement(
                row,
                direction=event.direction,
                logic=logic,
            ):
                continue
            displacement_breaks += 1
            # All three FVG source bars must be complete after the synchronized
            # event anchor; the first two post-event bars cannot yet form one.
            if index < first_index + 2:
                continue
            candidate = _fvg_at(work, index, direction=event.direction)
            if candidate is None:
                continue
            mss_index = index
            fvg = candidate
            break
        if invalidated:
            continue
        if mss_index is None or fvg is None:
            diagnostics.append(
                EventDiagnostic(
                    event.event_id,
                    "MSS_FVG_NOT_CONFIRMED_WITHIN_WINDOW",
                    {
                        "swing_id": swing.swing_id,
                        "swing_level": swing.level,
                        "boundary_breaks": boundary_breaks,
                        "displacement_breaks": displacement_breaks,
                        "post_event_fvg_required": True,
                    },
                )
            )
            continue

        mss_row = work.iloc[mss_index]
        observed_index = mss_index
        retest_ns: int | None = None
        if require_fvg_retest:
            retest_end = min(
                len(work.index),
                mss_index + 1 + logic.maximum_retest_minutes,
            )
            retest_found = False
            retest_invalidated = False
            for index in range(mss_index + 1, retest_end):
                row = work.iloc[index]
                if event.direction == "LONG":
                    touched = float(row["low"]) <= fvg.upper
                    full_traversal = float(row["low"]) <= fvg.lower
                    rejected = (
                        touched
                        and not full_traversal
                        and float(row["close"]) >= fvg.upper
                        and float(row["close"]) > float(row["open"])
                        and float(row["close_location"])
                        >= logic.retest_close_location
                    )
                    source_invalid = float(row["low"]) <= event.event_extreme
                else:
                    touched = float(row["high"]) >= fvg.lower
                    full_traversal = float(row["high"]) >= fvg.upper
                    rejected = (
                        touched
                        and not full_traversal
                        and float(row["close"]) <= fvg.lower
                        and float(row["close"]) < float(row["open"])
                        and float(row["close_location"])
                        <= 1.0 - logic.retest_close_location
                    )
                    source_invalid = float(row["high"]) >= event.event_extreme
                if source_invalid or full_traversal:
                    retest_invalidated = True
                    diagnostics.append(
                        EventDiagnostic(
                            event.event_id,
                            "RETEST_INVALIDATED",
                            {
                                "timestamp_ns": int(row["timestamp_ns"]),
                                "source_invalid": source_invalid,
                                "full_fvg_traversal": full_traversal,
                            },
                        )
                    )
                    break
                if rejected:
                    observed_index = index
                    retest_ns = int(row["timestamp_ns"])
                    retest_found = True
                    break
            if retest_invalidated:
                continue
            if not retest_found:
                diagnostics.append(
                    EventDiagnostic(
                        event.event_id,
                        "FIRST_FVG_RETEST_NOT_CONFIRMED",
                        {"fvg_id": fvg.gap_id},
                    )
                )
                continue

        entry_row = work.iloc[observed_index]
        entry = float(entry_row["close"])
        atr = float(entry_row["atr"])
        stop = (
            event.event_extreme - logic.stop_buffer_atr * atr
            if event.direction == "LONG"
            else event.event_extreme + logic.stop_buffer_atr * atr
        )
        risk = entry - stop if event.direction == "LONG" else stop - entry
        if not np.isfinite(risk) or risk <= 0.0:
            diagnostics.append(
                EventDiagnostic(
                    event.event_id,
                    "NONPOSITIVE_SOURCE_INVALIDATION",
                    {"entry": entry, "stop": stop},
                )
            )
            continue
        plan = EntryPlan(
            scenario_id=f"{event.event_id}-MSS-FVG",
            event_id=event.event_id,
            direction=event.direction,
            observed_ns=int(entry_row["timestamp_ns"]),
            entry=entry,
            stop=stop,
            source_level=event.source_level,
            source_pool_id=event.source_pool_id,
            mss_swing_id=swing.swing_id,
            mss_level=swing.level,
            mss_ns=int(mss_row["timestamp_ns"]),
            fvg_id=fvg.gap_id,
            fvg_lower=fvg.lower,
            fvg_upper=fvg.upper,
            retest_required=require_fvg_retest,
            retest_ns=retest_ns,
            body_atr=float(mss_row["body_atr"]),
            displacement_rank=float(mss_row["displacement_rank"]),
        )
        plans.append(plan)
        diagnostics.append(
            EventDiagnostic(
                event.event_id,
                "ENTRY_READY",
                {
                    "scenario_id": plan.scenario_id,
                    "observed_ns": plan.observed_ns,
                    "retest_required": require_fvg_retest,
                },
            )
        )
    return plans, diagnostics


def evaluate(
    local_bars: pd.DataFrame,
    seconds: pd.DataFrame,
    *,
    detector_report: Mapping[str, Any],
    target_pools: Mapping[str, Iterable[impact.Pool]],
    maximum_hold_seconds: int,
    mss_logic: ImpactMSSFVGLogic,
    require_fvg_retest: bool,
    price_increment: Decimal,
    taker_fee_rate: Decimal,
    funding_reserve_bps: Decimal,
) -> dict[str, Any]:
    if maximum_hold_seconds <= 0:
        raise ValueError("maximum_hold_seconds must be positive")
    mss_logic.validate()
    bar_seconds = max(
        1,
        3600 // int(mss_logic.displacement_rank_period),
    )
    events, event_details = events_from_detector(
        detector_report,
        bar_seconds=bar_seconds,
    )
    plans, mss_diagnostics = diagnose_local_mss_fvg(
        local_bars,
        events=events,
        logic=mss_logic,
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
    touch_cache: dict[str, int | None] = {}
    slot_end = -1
    target_map = {"5M": tuple(target_pools.get("5M", ()))}

    for plan in sorted(
        plans,
        key=lambda item: (item.observed_ns, item.scenario_id),
    ):
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
        stop_in_entry_second = (
            float(row["low"]) <= stop
            if plan.direction == "LONG"
            else float(row["high"]) >= stop
        )
        if risk <= 0.0 or stop_in_entry_second:
            counters["POST_SIGNAL_SOURCE_INVALIDATED"] += 1
            continue

        selected = target_pool_after_complete_confirmation_second(
            target_map,
            direction=plan.direction,
            entry=entry,
            stop=stop,
            entry_index=entry_index,
            timestamps=timestamps,
            previous_close=previous_close,
            highs=highs,
            lows=lows,
            touch_cache=touch_cache,
            minimum_rr=0.0,
        )
        if selected is None:
            counters["NO_CAUSAL_UNCONSUMED_5M_TARGET"] += 1
            continue
        target_pool, gross_rr = selected
        geometry = adverse_execution_geometry(
            direction=plan.direction,
            entry_reference=Decimal(str(entry)),
            stop_price=Decimal(str(stop)),
            target_price=Decimal(str(target_pool.level)),
            price_increment=price_increment,
            taker_fee_rate=taker_fee_rate,
            funding_reserve_bps=funding_reserve_bps,
            adverse_slippage_ticks=1,
        )
        if not geometry.target_is_net_positive:
            counters["FIRST_EXTERNAL_TARGET_NOT_NET_POSITIVE"] += 1
            scenarios.append(
                {
                    "scenario_id": plan.scenario_id,
                    "outcome": "FIRST_EXTERNAL_TARGET_NOT_NET_POSITIVE",
                    "event": event_details[plan.event_id],
                    "entry": entry,
                    "stop": stop,
                    "target": float(target_pool.level),
                    "target_pool_id": target_pool.pool_id,
                    "gross_rr": float(gross_rr),
                    "cost_adjusted_target_r": float(
                        geometry.cost_adjusted_target_r
                    ),
                }
            )
            continue

        path, terminal_index = impact._path_result(
            work,
            start_index=entry_index,
            direction=plan.direction,
            entry=entry,
            stop=stop,
            target=float(target_pool.level),
            max_hold_seconds=maximum_hold_seconds,
        )
        slot_end = max(slot_end, terminal_index)
        realized_cost_r = _cost_adjusted_terminal_r(
            path=path,
            direction=plan.direction,
            entry=Decimal(str(entry)),
            stop=Decimal(str(stop)),
            geometry=geometry,
            price_increment=price_increment,
            taker_fee_rate=taker_fee_rate,
        )
        counters["ENTRY_READY"] += 1
        scenarios.append(
            {
                "scenario_id": plan.scenario_id,
                "outcome": "ENTRY_READY",
                "direction": plan.direction,
                "event": event_details[plan.event_id],
                "observed_ns": plan.observed_ns,
                "entry_second_ns": int(row["timestamp_ns"]),
                "entry": entry,
                "stop": stop,
                "risk": risk,
                "target": float(target_pool.level),
                "target_pool_id": target_pool.pool_id,
                "target_timeframe": target_pool.timeframe,
                "gross_rr": float(gross_rr),
                "cost_adjusted_target_r": float(
                    geometry.cost_adjusted_target_r
                ),
                "realized_cost_adjusted_r": float(realized_cost_r),
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
        item for item in scenarios if item.get("outcome") == "ENTRY_READY"
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
    realized = [float(item["realized_cost_adjusted_r"]) for item in entries]
    winners = [value for value in realized if value > 0.0]
    gross_positive_r = sum(winners)
    single_winner_share = (
        max(winners) / gross_positive_r
        if winners and gross_positive_r > 0.0
        else None
    )
    mfe = [float(item["path"]["mfe_r"]) for item in entries]
    mae = [float(item["path"]["mae_r"]) for item in entries]
    maximum_day_share = max(dates.values()) / len(entries) if entries else None
    gross_cost_r = float(sum(realized))
    gate = {
        "minimum_entry_ready": len(entries) >= 7,
        "minimum_active_days": len(dates) >= 4,
        "more_targets_than_stops": outcomes["TARGET"] > outcomes["STOP"],
        "positive_cost_adjusted_structural_r": gross_cost_r > 0.0,
        "median_mae_below_one_r": (
            bool(mae) and float(pd.Series(mae).median()) < 1.0
        ),
        "maximum_day_share_at_most_55pct": (
            maximum_day_share is not None and maximum_day_share <= 0.55
        ),
        "maximum_single_winner_share_at_most_55pct": (
            single_winner_share is not None and single_winner_share <= 0.55
        ),
    }
    gate["passed"] = all(gate.values())
    return {
        "summary": {
            "detector_accepted_events": len(events),
            "local_structure_bars": len(local_bars.index),
            "mss_fvg_plans": len(plans),
            "mss_fvg_diagnostic_counts": dict(
                sorted(Counter(item.outcome for item in mss_diagnostics).items())
            ),
            "path_counts": dict(sorted(counters.items())),
            "entry_ready": len(entries),
            "active_days": len(dates),
            "entries_by_day": dict(sorted(dates.items())),
            "maximum_day_share": maximum_day_share,
            "path_outcome_counts": dict(sorted(outcomes.items())),
            "target_minus_stop": outcomes["TARGET"] - outcomes["STOP"],
            "gross_cost_adjusted_structural_r": gross_cost_r,
            "mean_cost_adjusted_structural_r": (
                gross_cost_r / len(entries) if entries else None
            ),
            "median_cost_adjusted_target_r": (
                float(
                    pd.Series(
                        [item["cost_adjusted_target_r"] for item in entries]
                    ).median()
                )
                if entries
                else None
            ),
            "median_mfe_r": float(pd.Series(mfe).median()) if mfe else None,
            "median_mae_r": float(pd.Series(mae).median()) if mae else None,
            "single_winner_share": single_winner_share,
            "diagnostic_gate": gate,
        },
        "mss_fvg_diagnostics": [
            {
                "event_id": item.event_id,
                "outcome": item.outcome,
                "details": item.details,
            }
            for item in mss_diagnostics
        ],
        "scenarios": scenarios,
    }


__all__ = [
    "aggregate_complete_clock_bars",
    "diagnose_local_mss_fvg",
    "evaluate",
    "events_from_detector",
    "local_structure_logic",
]

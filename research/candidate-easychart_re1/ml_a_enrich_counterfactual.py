"""Join plan-time scenario state to immutable counterfactual plan labels.

The join is deliberately streaming and causal: only scenario-transition rows
whose event time is no later than a plan's emission time may populate features.
Future outcome fields are never read as features.
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


CATEGORICAL = (
    "scenario_kind", "mechanism", "response_mechanism", "flow_kind",
    "flow_mechanism", "flow_strength", "state_before_flow", "acceptance",
    "mode", "source_mode", "macro_side", "active_draw_side", "latest_side",
    "local_side", "regime", "later_side", "source_zone_kind", "zone_kind",
)
NUMERIC = (
    "structure_age_ns", "event_age_from_interaction_ns", "formation_bars",
    "cumulative_signed_taker_quote", "net_price_progress", "constituent_bars",
    "sweep_high", "sweep_low", "sweep_close", "sweep_extreme",
    "reclaim_close", "support_upper", "resistance_lower", "midpoint",
    "pivot_span", "activation_close", "source_lower", "source_upper",
    "source_invalidation", "draw_target", "adverse_quote", "aligned_quote",
    "penetration", "recovery", "adverse_impact_per_quote",
    "recovery_impact_per_quote", "episode_bars", "active_aligned_bars",
    "response_open", "response_high", "response_low", "response_close",
    "detached_bar_low", "detached_bar_high", "detached_bar_close",
    "projected_lower", "projected_upper", "retest_high", "retest_low",
    "retest_close", "touch_high", "touch_low", "touch_close", "break_close",
    "break_high", "break_low", "flow_quote_volume", "flow_median_quote_volume",
    "flow_activity_ratio", "flow_trade_count", "flow_trade_size_ratio",
    "flow_taker_buy_quote_volume", "flow_signed_taker_quote",
    "flow_delta_share", "flow_delta_ratio", "flow_body_ratio",
    "flow_range_ratio", "flow_close_location", "flow_impact_per_activity",
    "flow_episode_bars", "flow_episode_cumulative_delta",
    "flow_episode_net_price_progress",
)
FIELDS = CATEGORICAL + NUMERIC


def _clean(value: Any) -> Any | None:
    if value is None:
        return None
    if isinstance(value, float) and np.isnan(value):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    return value


def _integer(value: Any) -> int | None:
    value = _clean(value)
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _box_from_setup(value: Any) -> str | None:
    value = _clean(value)
    if not isinstance(value, str):
        return None
    if value.startswith("MATURE_BOX:") and ":SWEEP:" in value:
        return value.split(":SWEEP:", 1)[0]
    return None


def _append(mapping: dict[str, list[str]], key: Any, plan_id: str) -> None:
    key = _clean(key)
    if key is not None and plan_id not in mapping[str(key)]:
        mapping[str(key)].append(plan_id)


def _event_time(row: dict[str, str]) -> int | None:
    return _integer(row.get("event_time_ns")) or _integer(row.get("ts_ns"))


def _update(
    store: dict[str, dict[str, tuple[int, Any]]],
    plan_id: str,
    row: dict[str, str],
    event_time: int,
) -> None:
    target = store[plan_id]
    for field in FIELDS:
        value = _clean(row.get(field))
        if value is None:
            continue
        prior = target.get(field)
        if prior is None or event_time >= prior[0]:
            target[field] = (event_time, value)


def _stream(events_path: Path):  # type: ignore[no-untyped-def]
    with events_path.open("r", encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle)


def enrich(events_path: Path, plans_path: Path, output_path: Path) -> dict[str, Any]:
    plans = pd.read_csv(plans_path, low_memory=False)
    if plans.empty:
        plans.to_csv(output_path, index=False)
        return {"plans": 0, "added_columns": 0}

    time_column = next(
        (name for name in ("ts_ns", "observed_time_ns", "trigger_time_ns") if name in plans),
        None,
    )
    if time_column is None:
        raise ValueError("plan table has no causal emission-time column")
    times = pd.to_numeric(plans[time_column], errors="coerce")
    plan_time = {
        str(plan_id): int(timestamp)
        for plan_id, timestamp in zip(plans["plan_id"], times)
        if pd.notna(plan_id) and pd.notna(timestamp)
    }
    if not plan_time:
        raise ValueError("plan table has no usable plan_id/time pairs")

    setup_to_plans: dict[str, list[str]] = defaultdict(list)
    box_to_plans: dict[str, list[str]] = defaultdict(list)
    for _, plan in plans.iterrows():
        plan_id = str(plan["plan_id"])
        if plan_id not in plan_time:
            continue
        setup_id = plan.get("setup_id")
        _append(setup_to_plans, setup_id, plan_id)
        _append(box_to_plans, _box_from_setup(setup_id), plan_id)

    # First pass discovers explicit box IDs attached to setup rows.  A second
    # pass is necessary because canonical-box events occur before the sweep.
    for row in _stream(events_path):
        if row.get("kind") != "scenario_transition":
            continue
        event_time = _event_time(row)
        setup_id = _clean(row.get("setup_id"))
        box_id = _clean(row.get("box_id"))
        if event_time is None or setup_id is None or box_id is None:
            continue
        for plan_id in setup_to_plans.get(str(setup_id), ()):
            if event_time <= plan_time[plan_id]:
                _append(box_to_plans, box_id, plan_id)

    plan_store: dict[str, dict[str, tuple[int, Any]]] = defaultdict(dict)
    setup_store: dict[str, dict[str, tuple[int, Any]]] = defaultdict(dict)
    box_store: dict[str, dict[str, tuple[int, Any]]] = defaultdict(dict)
    for row in _stream(events_path):
        if row.get("kind") != "scenario_transition":
            continue
        event_time = _event_time(row)
        if event_time is None:
            continue
        direct = _clean(row.get("plan_id"))
        if direct is not None:
            plan_id = str(direct)
            if plan_id in plan_time and event_time <= plan_time[plan_id]:
                _update(plan_store, plan_id, row, event_time)
        setup_id = _clean(row.get("setup_id"))
        if setup_id is not None:
            for plan_id in setup_to_plans.get(str(setup_id), ()):
                if event_time <= plan_time[plan_id]:
                    _update(setup_store, plan_id, row, event_time)
        box_id = _clean(row.get("box_id"))
        if box_id is not None:
            for plan_id in box_to_plans.get(str(box_id), ()):
                if event_time <= plan_time[plan_id]:
                    _update(box_store, plan_id, row, event_time)

    original_columns = set(plans.columns)
    for prefix, store in (
        ("trace_plan_", plan_store),
        ("trace_setup_", setup_store),
        ("trace_box_", box_store),
    ):
        columns = sorted({field for values in store.values() for field in values})
        for field in columns:
            plans[f"{prefix}{field}"] = [
                store.get(str(plan_id), {}).get(field, (0, np.nan))[1]
                for plan_id in plans["plan_id"]
            ]

    plans.to_csv(output_path, index=False)
    added = [column for column in plans.columns if column not in original_columns]
    return {
        "plans": int(len(plans)),
        "added_columns": int(len(added)),
        "plan_trace_coverage": int(sum(bool(plan_store.get(str(pid))) for pid in plans["plan_id"])),
        "setup_trace_coverage": int(sum(bool(setup_store.get(str(pid))) for pid in plans["plan_id"])),
        "box_trace_coverage": int(sum(bool(box_store.get(str(pid))) for pid in plans["plan_id"])),
        "columns": added,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--plans", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(enrich(args.events, args.plans, args.output or args.plans))


if __name__ == "__main__":
    main()

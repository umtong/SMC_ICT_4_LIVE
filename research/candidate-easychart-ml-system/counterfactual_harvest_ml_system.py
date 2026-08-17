"""ML-system counterfactual harvest with causal episode trace reconstruction.

The generic RE1 harvest kept a single plan-tagged transition. That is sufficient
for a narrow flow experiment, but it discards the earlier causal sequence which
made a complete auction plan: break flow, hold, retest, absorption and response.
This wrapper aggregates the last non-null value of every transition field from
the same mechanism-owned setup and symbol up to the plan-emission transition.
Future bars remain confined to the existing first-passage labeler and never enter
these trace features.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

import counterfactual_plan_harvest as _raw
import counterfactual_plan_harvest_fixed as _fixed
from counterfactual_plan_harvest import HarvestConfig


EPISODE_TRACE_POLICY = (
    "CAUSAL_TRACE:FOR_EACH_PLAN_AGGREGATE_ONLY_TRANSITIONS_FROM_THE_SAME_"
    "MECHANISM_OWNER_SYMBOL_AND_NAMESPACED_SETUP_WITH_EVENT_TIME_AT_OR_BEFORE_"
    "THE_PLAN_EMISSION_TRANSITION"
)


def _last_non_null(series: pd.Series) -> Any:
    values = series.dropna()
    return None if values.empty else values.iloc[-1]


def _same_value(frame: pd.DataFrame, column: str, value: Any) -> pd.Series:
    if column not in frame.columns or value is None or pd.isna(value):
        return pd.Series(True, index=frame.index)
    return frame[column] == value


def _episode_trace_rows(events: pd.DataFrame) -> pd.DataFrame:
    traces = events[events["kind"] == "scenario_transition"].copy()
    if traces.empty or "plan_id" not in traces:
        return pd.DataFrame(index=pd.Index([], name="plan_id"))
    event_time = (
        pd.to_numeric(traces["event_time_ns"], errors="coerce")
        if "event_time_ns" in traces.columns
        else pd.Series(float("nan"), index=traces.index)
    )
    fallback_time = (
        pd.to_numeric(traces["ts_ns"], errors="coerce")
        if "ts_ns" in traces.columns
        else pd.Series(float("nan"), index=traces.index)
    )
    traces["_event_time"] = event_time.fillna(fallback_time)
    if traces["_event_time"].isna().any():
        raise RuntimeError("scenario transition without a causal event timestamp")
    traces["_row_order"] = range(len(traces))
    plan_rows = traces[traces["plan_id"].notna()].copy()
    if plan_rows.empty:
        return pd.DataFrame(index=pd.Index([], name="plan_id"))
    plan_rows["_nonnull"] = plan_rows.notna().sum(axis=1)
    plan_rows = (
        plan_rows.sort_values(
            ["plan_id", "_nonnull", "_event_time", "_row_order"],
            kind="mergesort",
        )
        .groupby("plan_id", sort=False)
        .tail(1)
        .sort_values(["_event_time", "_row_order"], kind="mergesort")
    )

    ignored = {"kind", "instrument_id", "timeframe_minutes", "_nonnull"}
    records: list[dict[str, Any]] = []
    for _, plan_row in plan_rows.iterrows():
        plan_id = str(plan_row["plan_id"])
        cutoff = float(plan_row["_event_time"])
        setup_id = plan_row.get("setup_id")
        if setup_id is not None and not pd.isna(setup_id):
            mask = (
                (traces["setup_id"] == setup_id)
                & _same_value(traces, "mechanism_owner", plan_row.get("mechanism_owner"))
                & _same_value(traces, "symbol", plan_row.get("symbol"))
                & (traces["_event_time"] <= cutoff)
            )
            episode = traces[mask].copy()
        else:
            episode = plan_rows[plan_rows["plan_id"] == plan_id].copy()
        if episode.empty:
            raise RuntimeError(f"no causal trace episode reconstructed for {plan_id}")
        episode = episode.sort_values(
            ["_event_time", "_row_order"],
            kind="mergesort",
        )
        record: dict[str, Any] = {"plan_id": plan_id}
        for column in episode.columns:
            if column in ignored or column.startswith("_") or column == "plan_id":
                continue
            value = _last_non_null(episode[column])
            if value is not None:
                record[column] = value
        record["episode_trace_transition_count"] = int(len(episode))
        record["episode_trace_first_time_ns"] = int(episode["_event_time"].min())
        record["episode_trace_last_time_ns"] = int(episode["_event_time"].max())
        record["episode_trace_span_minutes"] = (
            record["episode_trace_last_time_ns"]
            - record["episode_trace_first_time_ns"]
        ) / 60_000_000_000
        record["episode_trace_policy"] = EPISODE_TRACE_POLICY
        records.append(record)
    output = pd.DataFrame(records)
    if output["plan_id"].duplicated().any():
        raise RuntimeError("episode trace reconstruction produced duplicate plan ids")
    return output.set_index("plan_id")


def harvest_counterfactual_plans(config: HarvestConfig) -> dict[str, Any]:
    original = _raw._best_trace_rows
    _raw._best_trace_rows = _episode_trace_rows
    try:
        summary = _fixed.harvest_counterfactual_plans(config)
    finally:
        _raw._best_trace_rows = original
    summary["episode_trace_policy"] = EPISODE_TRACE_POLICY
    path = config.output / "counterfactual_summary.json"
    if path.exists():
        import json

        record = json.loads(path.read_text(encoding="utf-8"))
        record["episode_trace_policy"] = EPISODE_TRACE_POLICY
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return summary


__all__ = ["HarvestConfig", "EPISODE_TRACE_POLICY", "harvest_counterfactual_plans"]

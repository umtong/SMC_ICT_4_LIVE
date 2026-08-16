"""Identity-safe counterfactual plan harvest.

This wrapper keeps the original causal labels and raw-data path, but fixes two
validity failures in the first implementation:

* ``plan_id`` may not simultaneously remain an index level and sort key;
* labels must be joined to the same stable plan order used during evaluation.

Plan identity is also asserted before any future label is computed.  A repeated
identifier would make trace joins and answer-sheet attribution invalid, so the
research run fails loudly rather than silently overwriting evidence.
"""
from __future__ import annotations

import json
from typing import Any

import pandas as pd

import counterfactual_plan_harvest as _base
from counterfactual_plan_harvest import HarvestConfig
from counterfactual_sequential_features import (
    SEQUENTIAL_FEATURE_POLICY,
    SYNCHRONIZED_COMMON_POLICY,
    build_sequential_state,
)
from data_re1_flow import load_range_flow


IDENTITY_POLICY = (
    "IMPLEMENTATION_VALIDITY:COUNTERFACTUAL_PLAN_IDENTITIES_MUST_BE_UNIQUE_AND_"
    "LABELS_ARE_JOINED_IN_THE_EXACT_STABLE_ORDER_USED_FOR_FIRST_PASSAGE_EVALUATION"
)


def _assert_unique_plan_ids(plans: pd.DataFrame) -> None:
    duplicate = plans[plans["plan_id"].duplicated(keep=False)]
    if duplicate.empty:
        return
    sample_columns = [
        column
        for column in ("plan_id", "ts_ns", "symbol", "family", "side", "entry", "stop", "target")
        if column in duplicate.columns
    ]
    sample = duplicate[sample_columns].sort_values(
        [column for column in ("plan_id", "ts_ns", "symbol") if column in sample_columns],
        kind="mergesort",
    )
    raise RuntimeError(
        "counterfactual plan identity collision; namespace mechanism owners before labeling:\n"
        + sample.head(40).to_string(index=False)
    )


def _summary(labelled: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {
        "plans": int(len(labelled)),
        "target_first": int((labelled["counterfactual_outcome"] == "TARGET_FIRST").sum()),
        "stop_first": int((labelled["counterfactual_outcome"] == "STOP_FIRST").sum()),
        "ambiguous_same_minute": int(
            (labelled["counterfactual_outcome"] == "AMBIGUOUS_SAME_MINUTE").sum()
        ),
        "unresolved": int((labelled["counterfactual_outcome"] == "UNRESOLVED").sum()),
        "sum_conservative_net_r": float(labelled["counterfactual_net_r_conservative"].sum()),
        "mean_conservative_net_r": float(labelled["counterfactual_net_r_conservative"].mean()),
        "label_policy": _base.LABEL_POLICY,
        "market_state_policy": _base.MARKET_STATE_POLICY,
        "identity_policy": IDENTITY_POLICY,
        "sequential_feature_policy": SEQUENTIAL_FEATURE_POLICY,
        "synchronized_common_policy": SYNCHRONIZED_COMMON_POLICY,
        "by_family": {},
    }
    for family, group in labelled.groupby("family", dropna=False):
        key = "<NA>" if pd.isna(family) else str(family)
        result["by_family"][key] = {
            "plans": int(len(group)),
            "target_first": int((group["counterfactual_outcome"] == "TARGET_FIRST").sum()),
            "sum_conservative_net_r": float(group["counterfactual_net_r_conservative"].sum()),
            "mean_conservative_net_r": float(group["counterfactual_net_r_conservative"].mean()),
        }
    return result


def harvest_counterfactual_plans(config: HarvestConfig) -> dict[str, Any]:
    events_path = config.output / "decision_events.csv"
    if not events_path.exists():
        raise FileNotFoundError(events_path)
    events = pd.read_csv(events_path, low_memory=False)
    plans = events[events["kind"] == "plan"].copy()
    if plans.empty:
        plans.to_csv(config.output / "counterfactual_plans.csv", index=False)
        summary = {
            "plans": 0,
            "label_policy": _base.LABEL_POLICY,
            "market_state_policy": _base.MARKET_STATE_POLICY,
            "identity_policy": IDENTITY_POLICY,
            "sequential_feature_policy": SEQUENTIAL_FEATURE_POLICY,
            "synchronized_common_policy": SYNCHRONIZED_COMMON_POLICY,
        }
        (config.output / "counterfactual_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return summary

    _assert_unique_plan_ids(plans)
    traces = _base._best_trace_rows(events)
    plans = (
        plans.set_index("plan_id", drop=False)
        .join(traces.add_prefix("trace_"), how="left")
        .reset_index(drop=True)
        .sort_values(["ts_ns", "symbol", "plan_id"], kind="mergesort")
        .reset_index(drop=True)
    )
    plans.insert(0, "counterfactual_row_id", range(len(plans)))

    frames = {
        symbol: load_range_flow(symbol, config.load_start, config.end, config.cache)
        for symbol in config.symbols
    }
    tapes: dict[str, pd.DataFrame] = {}
    for symbol, raw in frames.items():
        tape = raw.copy()
        tape.index = pd.DatetimeIndex(tape.pop("open_time_dt")) + pd.Timedelta(minutes=1)
        tapes[symbol] = tape.sort_index()
    state = _base._market_state(frames).join(
        build_sequential_state(frames).drop(columns=["symbol"], errors="ignore"),
        how="left",
    )

    labels = [
        _base._label_plan(plan, tapes[str(plan["symbol"])], state, config)
        for _, plan in plans.iterrows()
    ]
    labelled = pd.concat(
        [plans, pd.DataFrame(labels, index=plans.index)],
        axis=1,
    )
    risk = (labelled["entry"].astype(float) - labelled["stop"].astype(float)).abs()
    reward = (labelled["target"].astype(float) - labelled["entry"].astype(float)).abs()
    labelled["risk_bps"] = 10_000.0 * risk / labelled["entry"].astype(float).abs()
    labelled["target_bps"] = 10_000.0 * reward / labelled["entry"].astype(float).abs()
    labelled["zero_drift_target_first_prior"] = risk / (risk + reward).replace(0.0, pd.NA)
    labelled["risk_in_prior_sigma"] = risk / (
        labelled["entry"].astype(float).abs() * labelled["seq_prior_sigma_1m"]
    ).replace(0.0, pd.NA)
    labelled["target_in_prior_sigma"] = reward / (
        labelled["entry"].astype(float).abs() * labelled["seq_prior_sigma_1m"]
    ).replace(0.0, pd.NA)
    labelled["risk_in_prior_range"] = risk / (
        labelled["entry"].astype(float).abs()
        * labelled["seq_prior_range_fraction_1m"]
    ).replace(0.0, pd.NA)
    labelled.to_csv(config.output / "counterfactual_plans.csv", index=False)

    summary = _summary(labelled)
    (config.output / "counterfactual_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary

"""Identity-safe, causal counterfactual plan harvest.

This wrapper keeps the original future first-passage labels, while repairing
three validity failures in the first implementation:

* ``plan_id`` may not simultaneously remain an index level and sort key;
* labels must be joined to the same stable plan order used during evaluation;
* early robust-scale fallbacks may not use the full future sample.

A repeated plan identifier makes trace joins and answer-sheet attribution
invalid, so the research run fails loudly rather than silently overwriting
evidence.
"""
from __future__ import annotations

import json
from typing import Any

import pandas as pd

import counterfactual_plan_harvest as _base
from counterfactual_plan_harvest import HarvestConfig
from counterfactual_sequential_features import (
    AGGREGATED_BAR_POLICY,
    SEQUENTIAL_FEATURE_POLICY,
    SYNCHRONIZED_COMMON_POLICY,
    build_sequential_state,
)
from data_re1_flow import load_range_flow
from fee_profiles_v5 import FEE_PROFILES
from instruments import CONTRACTS


IDENTITY_POLICY = (
    "IMPLEMENTATION_VALIDITY:COUNTERFACTUAL_PLAN_IDENTITIES_MUST_BE_UNIQUE_AND_"
    "LABELS_ARE_JOINED_IN_THE_EXACT_STABLE_ORDER_USED_FOR_FIRST_PASSAGE_EVALUATION"
)
CAUSAL_STATE_REPLACEMENT_POLICY = (
    "IMPLEMENTATION_VALIDITY:LEGACY_RESEARCH_STATE_WITH_FULL_SAMPLE_FALLBACK_IS_"
    "REPLACED_BY_SHIFTED_PRIOR_ONLY_ROBUST_BASELINES"
)
ECONOMIC_GEOMETRY_POLICY = (
    "EXTERNAL_METHOD:ZERO_DRIFT_TWO_BARRIER_TARGET_FIRST_PRIOR_IS_RISK_DISTANCE_"
    "DIVIDED_BY_TOTAL_BARRIER_DISTANCE_AND_IS_COMPARED_WITH_THE_POST_COST_BREAK_EVEN_"
    "TARGET_PROBABILITY"
)


def _assert_unique_plan_ids(plans: pd.DataFrame) -> None:
    duplicate = plans[plans["plan_id"].duplicated(keep=False)]
    if duplicate.empty:
        return
    sample_columns = [
        column
        for column in (
            "plan_id",
            "ts_ns",
            "symbol",
            "family",
            "side",
            "entry",
            "stop",
            "target",
        )
        if column in duplicate.columns
    ]
    sort_columns = [
        column
        for column in ("plan_id", "ts_ns", "symbol")
        if column in sample_columns
    ]
    sample = duplicate[sample_columns].sort_values(
        sort_columns,
        kind="mergesort",
    )
    raise RuntimeError(
        "counterfactual plan identity collision; namespace mechanism owners before labeling:\n"
        + sample.head(40).to_string(index=False)
    )


def _economic_geometry(
    plan: pd.Series,
    config: HarvestConfig,
) -> dict[str, float | bool]:
    symbol = str(plan["symbol"])
    side = str(plan["side"])
    sign = 1.0 if side == "LONG" else -1.0 if side == "SHORT" else 0.0
    if sign == 0.0:
        raise ValueError(f"unknown side {side!r}")
    entry = float(plan["entry"])
    stop = float(plan["stop"])
    target = float(plan["target"])
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if risk <= 0.0 or reward <= 0.0:
        raise RuntimeError(f"nonpositive plan geometry for {plan['plan_id']}")
    tick = float(CONTRACTS[symbol].price_increment)
    fee_rate = float(FEE_PROFILES[config.fee_profile].taker_rate)
    target_net_r = _base._estimated_net_r(
        side_sign=sign,
        entry=entry,
        exit_price=target,
        risk=risk,
        tick=tick,
        entry_slippage_ticks=config.entry_slippage_ticks,
        exit_slippage_ticks=1,
        fee_rate=fee_rate,
    )
    stop_net_r = _base._estimated_net_r(
        side_sign=sign,
        entry=entry,
        exit_price=stop,
        risk=risk,
        tick=tick,
        entry_slippage_ticks=config.entry_slippage_ticks,
        exit_slippage_ticks=config.stop_slippage_ticks,
        fee_rate=fee_rate,
    )
    zero_drift_probability = risk / (risk + reward)
    denominator = target_net_r - stop_net_r
    break_even_probability = (
        -stop_net_r / denominator
        if denominator > 0.0
        else float("nan")
    )
    zero_drift_net_r = (
        zero_drift_probability * target_net_r
        + (1.0 - zero_drift_probability) * stop_net_r
    )
    post_cost_rr = (
        target_net_r / abs(stop_net_r)
        if target_net_r > 0.0 and stop_net_r < 0.0
        else float("nan")
    )
    return {
        "counterfactual_target_net_r": target_net_r,
        "counterfactual_stop_net_r": stop_net_r,
        "post_cost_reward_risk": post_cost_rr,
        "zero_drift_target_first_prior": zero_drift_probability,
        "post_cost_break_even_target_probability": break_even_probability,
        "required_target_probability_premium": (
            break_even_probability - zero_drift_probability
        ),
        "zero_drift_expected_net_r": zero_drift_net_r,
        "economic_geometry_viable": (
            target_net_r > 0.0
            and stop_net_r < 0.0
            and 0.0 < break_even_probability < 1.0
        ),
    }


def _summary(labelled: pd.DataFrame) -> dict[str, Any]:
    fast_stop = (
        (labelled["counterfactual_outcome"].isin(
            ["STOP_FIRST", "AMBIGUOUS_SAME_MINUTE"],
        ))
        & (labelled["counterfactual_minutes_to_resolution"] <= 10.0)
    )
    result: dict[str, Any] = {
        "plans": int(len(labelled)),
        "target_first": int(
            (labelled["counterfactual_outcome"] == "TARGET_FIRST").sum()
        ),
        "stop_first": int(
            (labelled["counterfactual_outcome"] == "STOP_FIRST").sum()
        ),
        "ambiguous_same_minute": int(
            (
                labelled["counterfactual_outcome"]
                == "AMBIGUOUS_SAME_MINUTE"
            ).sum()
        ),
        "unresolved": int(
            (labelled["counterfactual_outcome"] == "UNRESOLVED").sum()
        ),
        "fast_stop_within_10m": int(fast_stop.sum()),
        "sum_conservative_net_r": float(
            labelled["counterfactual_net_r_conservative"].sum()
        ),
        "mean_conservative_net_r": float(
            labelled["counterfactual_net_r_conservative"].mean()
        ),
        "mean_zero_drift_expected_net_r": float(
            labelled["zero_drift_expected_net_r"].mean()
        ),
        "median_required_target_probability_premium": float(
            labelled["required_target_probability_premium"].median()
        ),
        "economic_geometry_viable": int(
            labelled["economic_geometry_viable"].sum()
        ),
        "label_policy": _base.LABEL_POLICY,
        "identity_policy": IDENTITY_POLICY,
        "causal_state_replacement_policy": CAUSAL_STATE_REPLACEMENT_POLICY,
        "sequential_feature_policy": SEQUENTIAL_FEATURE_POLICY,
        "synchronized_common_policy": SYNCHRONIZED_COMMON_POLICY,
        "aggregated_bar_policy": AGGREGATED_BAR_POLICY,
        "economic_geometry_policy": ECONOMIC_GEOMETRY_POLICY,
        "state_feature_count": int(
            sum(
                column.startswith(
                    (
                        "local_",
                        "seq_",
                        "bar",
                        "common_",
                        "dispersion_",
                        "residual_",
                    ),
                )
                for column in labelled.columns
            )
        ),
        "by_family": {},
    }
    for family, group in labelled.groupby("family", dropna=False):
        key = "<NA>" if pd.isna(family) else str(family)
        resolved = group[
            group["counterfactual_outcome"] != "UNRESOLVED"
        ]
        family_fast_stop = (
            group["counterfactual_outcome"].isin(
                ["STOP_FIRST", "AMBIGUOUS_SAME_MINUTE"],
            )
            & (group["counterfactual_minutes_to_resolution"] <= 10.0)
        )
        result["by_family"][key] = {
            "plans": int(len(group)),
            "target_first": int(
                (group["counterfactual_outcome"] == "TARGET_FIRST").sum()
            ),
            "target_first_rate_resolved": (
                None
                if resolved.empty
                else float(
                    (
                        resolved["counterfactual_outcome"]
                        == "TARGET_FIRST"
                    ).mean()
                )
            ),
            "fast_stop_within_10m": int(family_fast_stop.sum()),
            "sum_conservative_net_r": float(
                group["counterfactual_net_r_conservative"].sum()
            ),
            "mean_conservative_net_r": float(
                group["counterfactual_net_r_conservative"].mean()
            ),
            "mean_zero_drift_expected_net_r": float(
                group["zero_drift_expected_net_r"].mean()
            ),
        }
    return result


def harvest_counterfactual_plans(
    config: HarvestConfig,
) -> dict[str, Any]:
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
            "identity_policy": IDENTITY_POLICY,
            "causal_state_replacement_policy": CAUSAL_STATE_REPLACEMENT_POLICY,
            "sequential_feature_policy": SEQUENTIAL_FEATURE_POLICY,
            "synchronized_common_policy": SYNCHRONIZED_COMMON_POLICY,
            "aggregated_bar_policy": AGGREGATED_BAR_POLICY,
            "economic_geometry_policy": ECONOMIC_GEOMETRY_POLICY,
        }
        (config.output / "counterfactual_summary.json").write_text(
            json.dumps(
                summary,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return summary

    _assert_unique_plan_ids(plans)
    traces = _base._best_trace_rows(events)
    plans = (
        plans.set_index("plan_id", drop=False)
        .join(traces.add_prefix("trace_"), how="left")
        .reset_index(drop=True)
        .sort_values(
            ["ts_ns", "symbol", "plan_id"],
            kind="mergesort",
        )
        .reset_index(drop=True)
    )
    plans.insert(0, "counterfactual_row_id", range(len(plans)))

    frames = {
        symbol: load_range_flow(
            symbol,
            config.load_start,
            config.end,
            config.cache,
        )
        for symbol in config.symbols
    }
    tapes: dict[str, pd.DataFrame] = {}
    for symbol, raw in frames.items():
        tape = raw.copy()
        tape.index = (
            pd.DatetimeIndex(tape.pop("open_time_dt"))
            + pd.Timedelta(minutes=1)
        )
        tapes[symbol] = tape.sort_index()

    # The original market-state helper used a full-sample fallback during
    # warm-up. Do not join it here: all fields come from the shifted causal
    # replacement.
    state = build_sequential_state(frames)

    labels = [
        _base._label_plan(
            plan,
            tapes[str(plan["symbol"])],
            state,
            config,
        )
        for _, plan in plans.iterrows()
    ]
    labelled = pd.concat(
        [
            plans,
            pd.DataFrame(labels, index=plans.index),
            pd.DataFrame(
                [
                    _economic_geometry(plan, config)
                    for _, plan in plans.iterrows()
                ],
                index=plans.index,
            ),
        ],
        axis=1,
    )
    risk = (
        labelled["entry"].astype(float)
        - labelled["stop"].astype(float)
    ).abs()
    reward = (
        labelled["target"].astype(float)
        - labelled["entry"].astype(float)
    ).abs()
    absolute_entry = labelled["entry"].astype(float).abs()
    labelled["risk_bps"] = 10_000.0 * risk / absolute_entry
    labelled["target_bps"] = 10_000.0 * reward / absolute_entry
    labelled["risk_in_prior_sigma"] = risk / (
        absolute_entry * labelled["seq_prior_sigma_1m"]
    ).replace(0.0, pd.NA)
    labelled["target_in_prior_sigma"] = reward / (
        absolute_entry * labelled["seq_prior_sigma_1m"]
    ).replace(0.0, pd.NA)
    labelled["risk_in_prior_range"] = risk / (
        absolute_entry
        * labelled["seq_prior_range_fraction_1m"]
    ).replace(0.0, pd.NA)
    labelled["target_in_prior_range"] = reward / (
        absolute_entry
        * labelled["seq_prior_range_fraction_1m"]
    ).replace(0.0, pd.NA)
    labelled.to_csv(
        config.output / "counterfactual_plans.csv",
        index=False,
    )

    summary = _summary(labelled)
    (config.output / "counterfactual_summary.json").write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return summary

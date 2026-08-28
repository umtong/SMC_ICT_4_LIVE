#!/usr/bin/env python3
"""V6 expert-distilled structural router.

This research router reuses fixed order-time decision policies already discovered
in independent repository branches as expert votes, then learns only how much
confirmation is enough and how early to act. All experts receive symbol-neutral,
outcome-free frames. A vote can admit a state; it can never choose a target.
Targets remain the nearest still-live market structure from V4.
"""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import easychart_b_v4 as base
import easychart_b_v5 as v5

POLICY_NAME = "ML_EASYCHART_B_V6_EXPERT_DISTILLED_EARLIEST_STRUCTURAL_ROUTE"
EXPERT_PRIORITY = {
    "V5_CAUSAL_MECHANISM": 5,
    "CONTROL_TRANSFER_V3": 4,
    "CONTROL_TRANSFER_V2": 3,
    "EASYCHART_A": 2,
    "EASYCHART_B_BASE": 1,
}
OUTCOME_COLUMNS = {
    "outcome",
    "net_r",
    "net_r_num",
    "mfe_r",
    "mae_r",
    "resolution_time_ns",
    "target_hit_time_ns",
    "stop_hit_time_ns",
    "nav_before",
    "nav_after",
    "drawdown",
}


def numeric(frame: pd.DataFrame, column: str, default: float = np.nan) -> pd.Series:
    return base.numeric(frame, column, default)


def text(frame: pd.DataFrame, column: str, default: str = "") -> pd.Series:
    return base.text(frame, column, default)


def _neutral_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.drop(
        columns=[column for column in OUTCOME_COLUMNS if column in frame],
        errors="ignore",
    ).copy()
    out["symbol"] = "NEUTRAL"
    out["research_period"] = "NEUTRAL"
    return out


def _ids_from_result(result: Any) -> set[str]:
    if isinstance(result, pd.DataFrame) and "action_id" in result:
        return set(result["action_id"].dropna().astype(str))
    if isinstance(result, pd.Series) and result.dtype == bool:
        return set(result.index[result].astype(str))
    if isinstance(result, dict):
        action_ids: set[str] = set()
        for value in result.values():
            action_ids |= _ids_from_result(value)
        return action_ids
    return set()


def _call_expert(
    module_name: str,
    function_names: tuple[str, ...],
    frame: pd.DataFrame,
) -> set[str]:
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return set()
    neutral = _neutral_frame(frame)
    for function_name in function_names:
        function = getattr(module, function_name, None)
        if not callable(function):
            continue
        try:
            action_ids = _ids_from_result(function(neutral.copy()))
            if action_ids:
                return action_ids
        except Exception:
            continue
    return set()


def expert_flags(frame: pd.DataFrame) -> pd.DataFrame:
    flags = pd.DataFrame(
        False,
        index=frame.index,
        columns=list(EXPERT_PRIORITY),
        dtype=bool,
    )
    flags["V5_CAUSAL_MECHANISM"] = pd.concat(
        v5.mechanism_masks(frame), axis=1
    ).any(axis=1).reindex(frame.index, fill_value=False)
    action_ids = text(frame, "action_id")
    experts = (
        (
            "CONTROL_TRANSFER_V3",
            "control_transfer_router_v3",
            ("choose_public_plans", "select_plans"),
        ),
        (
            "CONTROL_TRANSFER_V2",
            "control_transfer_router_v2",
            ("choose_public_plans", "select_plans"),
        ),
        (
            "EASYCHART_A",
            "easychart_a_policy",
            ("choose_public_plans", "select_plans"),
        ),
        ("EASYCHART_B_BASE", "easychart_b", ("select_plans",)),
    )
    for label, module, names in experts:
        selected_ids = _call_expert(module, names, frame)
        if selected_ids:
            flags[label] = action_ids.isin(selected_ids)
    return flags


def attach_experts(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    flags = expert_flags(out)
    for name in flags:
        out[f"expert_{name.lower()}"] = flags[name].astype(float)
    out["expert_count"] = flags.sum(axis=1).astype(float)
    out["expert_family"] = ""
    out["expert_priority"] = 0
    for name, priority in sorted(EXPERT_PRIORITY.items(), key=lambda item: item[1]):
        mask = flags[name]
        out.loc[mask, "expert_family"] = name
        out.loc[mask, "expert_priority"] = int(priority)
    return out


def raw_feature_frame(frame: pd.DataFrame) -> pd.DataFrame:
    attributed = frame if "expert_count" in frame else attach_experts(frame)
    features = base.raw_feature_frame(attributed).copy()
    for name in EXPERT_PRIORITY:
        features[f"expert_{name.lower()}"] = numeric(
            attributed, f"expert_{name.lower()}", 0.0
        )
    features["expert_count_log"] = np.log1p(
        numeric(attributed, "expert_count", 0.0).clip(lower=0.0)
    )
    progress = numeric(attributed, "arm_progress_r").clip(lower=0.0)
    gross = numeric(attributed, "gross_rr").replace(0.0, np.nan)
    consumed = numeric(attributed, "arm_structural_target_consumed_fraction")
    consumed = consumed.where(consumed.notna(), progress / gross)
    remaining = (1.0 - consumed).clip(lower=0.0)
    features["expert_revealed_remaining"] = (
        features["expert_count_log"] * progress * remaining
    )
    return features


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -35.0, 35.0)))


def score_actions(frame: pd.DataFrame, policy: dict[str, Any]) -> pd.DataFrame:
    out = attach_experts(frame)
    model = policy["model"]
    features = raw_feature_frame(out).reindex(columns=model["feature_names"])
    median = np.asarray(model["median"], dtype=float)
    scale = np.asarray(model["scale"], dtype=float)
    matrix = features.to_numpy(float)
    matrix = np.where(np.isfinite(matrix), matrix, median)
    matrix = np.clip((matrix - median) / scale, -8.0, 8.0)
    design = np.column_stack([np.ones(len(matrix), dtype=float), matrix])
    probabilities = []
    for key in (
        "coef_base_excursion",
        "coef_structural_completion",
        "coef_earliest_good_arm",
    ):
        probabilities.append(
            _sigmoid(design @ np.asarray(model[key], dtype=float))
        )
    (
        out["base_excursion_probability"],
        out["structural_completion_probability"],
        out["earliest_good_arm_probability"],
    ) = probabilities
    weights = policy.get(
        "score_weights", {"base": 0.44, "completion": 0.36, "early": 0.20}
    )
    epsilon = 1e-9
    blended_logit = np.zeros(len(out), dtype=float)
    for probability, key in zip(
        probabilities, ("base", "completion", "early"), strict=True
    ):
        probability = np.clip(probability, epsilon, 1.0 - epsilon)
        blended_logit += float(weights[key]) * np.log(
            probability / (1.0 - probability)
        )
    out["trigger_score"] = _sigmoid(blended_logit)
    return out


def eligible_mask(frame: pd.DataFrame, selection: dict[str, Any]) -> pd.Series:
    progress = numeric(frame, "arm_progress_r")
    gross = numeric(frame, "gross_rr")
    consumed = numeric(frame, "arm_structural_target_consumed_fraction")
    consumed = consumed.where(consumed.notna(), progress / gross.replace(0.0, np.nan))
    headroom = numeric(frame, "arm_structural_target_headroom_r")
    headroom = headroom.where(headroom.notna(), gross - progress)
    acceptance = pd.concat(
        [
            numeric(frame, "arm_outside_close_ratio"),
            numeric(frame, "arm_outside_volume_share"),
        ],
        axis=1,
    ).min(axis=1)
    return (
        numeric(frame, "expert_count", 0.0).ge(
            float(selection.get("minimum_expert_count", 1.0))
        )
        & gross.ge(1.0)
        & numeric(frame, "planned_target_net_r").ge(
            float(selection.get("minimum_net_completion_r", 0.25))
        )
        & progress.ge(float(selection["minimum_progress_r"]))
        & consumed.le(float(selection["maximum_consumed_fraction"]))
        & headroom.ge(float(selection.get("minimum_headroom_r", 0.35)))
        & numeric(frame, "arm_current_retrace_fraction").le(
            float(selection["maximum_current_retrace_fraction"])
        )
        & acceptance.ge(float(selection["minimum_acceptance_ratio"]))
        & numeric(frame, "arm_path_efficiency").ge(
            float(selection.get("minimum_path_efficiency", 0.0))
        )
        & numeric(frame, "base_excursion_probability").ge(
            float(selection.get("minimum_base_probability", 0.0))
        )
        & numeric(frame, "structural_completion_probability").ge(
            float(selection.get("minimum_completion_probability", 0.0))
        )
        & numeric(frame, "trigger_score").ge(float(selection["score_threshold"]))
        & text(frame, "auction_phase").isin(
            selection.get(
                "allowed_phases",
                ["EARLY_RESPONSE", "ACCEPTED_EXPANSION", "FIRST_RETEST_FORMING"],
            )
        )
    ).fillna(False)


def select_plans(
    frame: pd.DataFrame,
    policy: dict[str, Any],
    *,
    pre_scored: bool = False,
) -> pd.DataFrame:
    scored = frame.copy() if pre_scored else score_actions(frame, policy)
    selected = scored.loc[eligible_mask(scored, policy["selection"])].copy()
    if selected.empty:
        return selected
    selected["preferred_geometry"] = text(
        selected, "entry_geometry"
    ).eq(base.PREFERRED_GEOMETRY).astype(int)
    selected["target_distance_bps"] = (
        (numeric(selected, "target") - numeric(selected, "entry")).abs()
        / numeric(selected, "entry").abs().clip(lower=1e-12)
        * 10_000.0
    )
    return (
        selected.sort_values(
            [
                "state_id",
                "preferred_geometry",
                "target_distance_bps",
                "trigger_score",
                "action_id",
            ],
            ascending=[True, False, True, False, True],
            kind="mergesort",
        )
        .drop_duplicates("state_id")
        .sort_values(
            [
                "research_period",
                "episode_id",
                "order_time_ns",
                "expert_priority",
                "trigger_score",
                "action_id",
            ],
            ascending=[True, True, True, False, False, True],
            kind="mergesort",
        )
        .drop_duplicates(["research_period", "episode_id"])
        .sort_values(
            ["order_time_ns", "expert_priority", "trigger_score", "action_id"],
            ascending=[True, False, False, True],
            kind="mergesort",
        )
        .reset_index(drop=True)
    )


def route_continuous_account(
    plans: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if plans.empty:
        return plans.copy(), plans.copy()
    routed = plans.copy()
    routed["scenario_priority"] = numeric(routed, "expert_priority", 0.0)
    return base.route_continuous_account(routed)


def build_summary(
    source: pd.DataFrame,
    selected: pd.DataFrame,
    orders: pd.DataFrame,
    trades: pd.DataFrame,
    bounds: dict[str, dict[str, str]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    summary = base.build_summary(
        source,
        selected,
        orders,
        trades,
        bounds,
        {"selection": policy["selection"]},
    )
    summary.update(
        {
            "policy": POLICY_NAME,
            "fixed_r_target_cap": False,
            "expert_votes_choose_target": False,
            "runtime_expert_inputs_symbol_neutral": True,
            "runtime_expert_inputs_outcome_free": True,
            "expert_priority": EXPERT_PRIORITY,
            "target_contract": "nearest still-live causal market structure",
        }
    )
    summary["by_expert_family"] = {
        name: base.metric_block(
            orders[text(orders, "expert_family").eq(name)] if len(orders) else orders,
            trades[text(trades, "expert_family").eq(name)] if len(trades) else trades,
        )
        for name in EXPERT_PRIORITY
    }
    return summary


def run(
    root: Path,
    bounds_path: Path,
    policy_path: Path,
    output: Path,
) -> dict[str, Any]:
    bounds = json.loads(bounds_path.read_text(encoding="utf-8"))
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    actions = base.load_actions(root, bounds)
    scored = score_actions(actions, policy)
    selected = select_plans(scored, policy, pre_scored=True)
    orders, trades = route_continuous_account(selected)
    summary = build_summary(actions, selected, orders, trades, bounds, policy)
    output.mkdir(parents=True, exist_ok=True)
    selected.to_csv(output / "eligible_plans.csv", index=False)
    orders.to_csv(output / "selected_orders.csv", index=False)
    trades.to_csv(output / "closed_trades.csv", index=False)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--period-bounds", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.root, args.period_bounds, args.policy, args.output)


if __name__ == "__main__":
    main()

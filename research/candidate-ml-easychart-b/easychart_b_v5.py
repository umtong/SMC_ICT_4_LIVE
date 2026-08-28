#!/usr/bin/env python3
"""V5 causal-mechanism router for EasyChart-B.

Targets come exclusively from ``harvest_structural_v4``: the nearest still-live
pivot/range/impulse/opposing-liquidity structure visible when the plan is armed.
This router never creates, clips, or extends a target from an R number.

A trade must belong to an observable liquidity-control mechanism. Three frozen
probability heads then answer different questions:

* can the event create the repeatable base excursion (+1R before invalidation)?
* can the declared natural structure complete before invalidation?
* is this the earliest sufficiently revealed arm rather than a late chase?

Runtime inputs contain neither symbol identity nor realized outcomes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import easychart_b_v4 as base

POLICY_NAME = "ML_EASYCHART_B_V5_CAUSAL_MECHANISM_EARLIEST_STRUCTURAL_ROUTE"
MECHANISM_PRIORITY = {
    "DEFENDED_SOURCE_ABSORPTION": 6,
    "RELATIVE_SWEEP_RELEASE": 5,
    "PUSH_PULL_ABSORPTION": 4,
    "PASSIVE_DEFENDED_CONTROL": 3,
    "LOCAL_IDIOSYNCRATIC_RELEASE": 2,
    "BROAD_SPONSORED_FIRST_RETURN": 1,
}


def numeric(frame: pd.DataFrame, column: str, default: float = np.nan) -> pd.Series:
    return base.numeric(frame, column, default)


def text(frame: pd.DataFrame, column: str, default: str = "") -> pd.Series:
    return base.text(frame, column, default)


def raw_feature_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return base.raw_feature_frame(frame)


def mechanism_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    """Map observable states to causal mechanisms, not indicator strategies."""
    family = text(frame, "family")
    phase = text(frame, "auction_phase")
    location = text(frame, "location_kind")
    failed = family.eq("FAILED_AUCTION_REVERSAL")
    continuation = family.eq("ACCEPTED_AUCTION_CONTINUATION")
    accepted = phase.isin({"EARLY_RESPONSE", "ACCEPTED_EXPANSION", "FIRST_RETEST_FORMING"})
    defenses = numeric(frame, "source_defense_count")
    local3 = numeric(frame, "departure_residual_return_3m_signed")
    local5 = numeric(frame, "departure_residual_return_5m_signed")
    common30 = numeric(frame, "departure_common_return_30m_signed")
    common5 = numeric(frame, "arm_index_return_signed")
    basis = numeric(frame, "departure_basis_change_3m_signed")
    impact = numeric(frame, "departure_impact_per_activity")
    approach = numeric(frame, "approach_delta_share_12m_toward")
    displacement = numeric(frame, "sequence_block_0_return_bps_signed")
    late_opposite = numeric(frame, "sequence_block_5_delta_share_signed")
    width = numeric(frame, "zone_width_bps")
    footprint = location.str.contains("FVG|OB|BPR|BOUNDARY", regex=True)
    acceptance = pd.concat(
        [numeric(frame, "arm_outside_close_ratio"), numeric(frame, "arm_outside_volume_share")],
        axis=1,
    ).min(axis=1)
    efficiency = numeric(frame, "arm_path_efficiency")
    consumed = numeric(frame, "arm_structural_target_consumed_fraction")
    progress = numeric(frame, "arm_progress_r")
    gross = numeric(frame, "gross_rr")
    consumed = consumed.where(consumed.notna(), progress / gross.replace(0.0, np.nan))

    defended = (
        accepted
        & basis.le(-1.0)
        & impact.le(0.16)
        & defenses.ge(4.0)
        & local3.ge(-0.0005)
    )
    relative = (
        failed
        & accepted
        & common30.le(0.0)
        & local3.ge(0.0)
        & acceptance.ge(0.32)
    )
    push_pull = (
        accepted
        & displacement.ge(28.0)
        & late_opposite.le(-0.08)
        & width.ge(5.0)
        & footprint
        & efficiency.ge(0.04)
    )
    passive = (
        accepted
        & approach.le(0.12)
        & defenses.ge(5.0)
        & local5.ge(0.0)
        & consumed.le(0.72)
    )
    local_release = (
        failed
        & accepted
        & local3.ge(0.00025)
        & local3.gt(common30 + 0.00020)
        & acceptance.ge(0.38)
    )
    broad_first_return = (
        continuation
        & accepted
        & common5.ge(0.0015)
        & local3.ge(-0.0003)
        & phase.isin({"EARLY_RESPONSE", "FIRST_RETEST_FORMING"})
        & consumed.le(0.62)
    )
    return {
        "DEFENDED_SOURCE_ABSORPTION": defended.fillna(False),
        "RELATIVE_SWEEP_RELEASE": relative.fillna(False),
        "PUSH_PULL_ABSORPTION": push_pull.fillna(False),
        "PASSIVE_DEFENDED_CONTROL": passive.fillna(False),
        "LOCAL_IDIOSYNCRATIC_RELEASE": local_release.fillna(False),
        "BROAD_SPONSORED_FIRST_RETURN": broad_first_return.fillna(False),
    }


def attach_mechanism(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    masks = mechanism_masks(out)
    out["mechanism"] = ""
    out["mechanism_priority"] = 0
    for name, priority in sorted(MECHANISM_PRIORITY.items(), key=lambda item: item[1]):
        mask = masks[name].reindex(out.index, fill_value=False)
        out.loc[mask, "mechanism"] = name
        out.loc[mask, "mechanism_priority"] = int(priority)
    return out


def _sigmoid(values: np.ndarray) -> np.ndarray:
    values = np.clip(values, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-values))


def _probabilities(frame: pd.DataFrame, model: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    names = list(model["feature_names"])
    features = raw_feature_frame(frame).reindex(columns=names)
    median = np.asarray(model["median"], dtype=float)
    scale = np.asarray(model["scale"], dtype=float)
    matrix = features.to_numpy(float)
    matrix = np.where(np.isfinite(matrix), matrix, median)
    matrix = np.clip((matrix - median) / scale, -8.0, 8.0)
    design = np.column_stack([np.ones(len(matrix), dtype=float), matrix])
    return (
        _sigmoid(design @ np.asarray(model["coef_base_excursion"], dtype=float)),
        _sigmoid(design @ np.asarray(model["coef_structural_completion"], dtype=float)),
        _sigmoid(design @ np.asarray(model["coef_earliest_good_arm"], dtype=float)),
    )


def score_actions(frame: pd.DataFrame, policy: dict[str, Any]) -> pd.DataFrame:
    out = attach_mechanism(frame)
    out["base_excursion_probability"] = np.nan
    out["structural_completion_probability"] = np.nan
    out["earliest_good_arm_probability"] = np.nan
    models = policy["models"]
    global_model = models["global"]
    by_mechanism = models.get("by_mechanism", {})
    for name in [*MECHANISM_PRIORITY, ""]:
        mask = text(out, "mechanism").eq(name)
        if not bool(mask.any()):
            continue
        model = by_mechanism.get(name, global_model) if name else global_model
        p_base, p_completion, p_early = _probabilities(out.loc[mask], model)
        out.loc[mask, "base_excursion_probability"] = p_base
        out.loc[mask, "structural_completion_probability"] = p_completion
        out.loc[mask, "earliest_good_arm_probability"] = p_early

    eps = 1e-9
    weights = policy.get("score_weights", {"base": 0.42, "completion": 0.36, "early": 0.22})
    logits = np.zeros(len(out), dtype=float)
    for column, key in (
        ("base_excursion_probability", "base"),
        ("structural_completion_probability", "completion"),
        ("earliest_good_arm_probability", "early"),
    ):
        probability = numeric(out, column).to_numpy(float)
        probability = np.clip(probability, eps, 1.0 - eps)
        logits += float(weights[key]) * np.log(probability / (1.0 - probability))
    out["trigger_score"] = _sigmoid(logits)
    return out


def eligible_mask(frame: pd.DataFrame, selection: dict[str, Any]) -> pd.Series:
    progress = numeric(frame, "arm_progress_r")
    gross = numeric(frame, "gross_rr")
    consumed = numeric(frame, "arm_structural_target_consumed_fraction")
    consumed = consumed.where(consumed.notna(), progress / gross.replace(0.0, np.nan))
    headroom = numeric(frame, "arm_structural_target_headroom_r")
    headroom = headroom.where(headroom.notna(), gross - progress)
    acceptance = pd.concat(
        [numeric(frame, "arm_outside_close_ratio"), numeric(frame, "arm_outside_volume_share")],
        axis=1,
    ).min(axis=1)
    return (
        text(frame, "mechanism").ne("")
        & gross.ge(1.0)
        & numeric(frame, "planned_target_net_r").ge(float(selection.get("minimum_net_completion_r", 0.25)))
        & progress.ge(float(selection["minimum_progress_r"]))
        & consumed.ge(float(selection.get("minimum_consumed_fraction", 0.0)))
        & consumed.le(float(selection["maximum_consumed_fraction"]))
        & headroom.ge(float(selection.get("minimum_headroom_r", 0.35)))
        & numeric(frame, "arm_current_retrace_fraction").le(float(selection["maximum_current_retrace_fraction"]))
        & acceptance.ge(float(selection["minimum_acceptance_ratio"]))
        & numeric(frame, "arm_path_efficiency").ge(float(selection.get("minimum_path_efficiency", 0.0)))
        & numeric(frame, "base_excursion_probability").ge(float(selection.get("minimum_base_probability", 0.0)))
        & numeric(frame, "structural_completion_probability").ge(float(selection.get("minimum_completion_probability", 0.0)))
        & numeric(frame, "trigger_score").ge(float(selection["score_threshold"]))
        & text(frame, "auction_phase").isin(selection.get("allowed_phases", ["EARLY_RESPONSE", "ACCEPTED_EXPANSION", "FIRST_RETEST_FORMING"]))
    ).fillna(False)


def select_plans(frame: pd.DataFrame, policy: dict[str, Any], *, pre_scored: bool = False) -> pd.DataFrame:
    scored = frame.copy() if pre_scored else score_actions(frame, policy)
    selected = scored.loc[eligible_mask(scored, policy["selection"])].copy()
    if selected.empty:
        return selected
    selected["preferred_geometry"] = text(selected, "entry_geometry").eq(base.PREFERRED_GEOMETRY).astype(int)
    selected["target_distance_bps"] = (
        (numeric(selected, "target") - numeric(selected, "entry")).abs()
        / numeric(selected, "entry").abs().clip(lower=1e-12)
        * 10_000.0
    )
    selected = (
        selected.sort_values(
            ["state_id", "preferred_geometry", "target_distance_bps", "trigger_score", "action_id"],
            ascending=[True, False, True, False, True],
            kind="mergesort",
        )
        .drop_duplicates("state_id")
        .sort_values(
            ["research_period", "episode_id", "order_time_ns", "mechanism_priority", "trigger_score", "action_id"],
            ascending=[True, True, True, False, False, True],
            kind="mergesort",
        )
        .drop_duplicates(["research_period", "episode_id"])
        .sort_values(
            ["order_time_ns", "mechanism_priority", "trigger_score", "action_id"],
            ascending=[True, False, False, True],
            kind="mergesort",
        )
        .reset_index(drop=True)
    )
    return selected


def route_continuous_account(plans: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if plans.empty:
        return plans.copy(), plans.copy()
    routed = plans.copy()
    routed["scenario_priority"] = numeric(routed, "mechanism_priority", 0.0)
    return base.route_continuous_account(routed)


def build_summary(
    source: pd.DataFrame,
    selected: pd.DataFrame,
    orders: pd.DataFrame,
    trades: pd.DataFrame,
    period_bounds: dict[str, dict[str, str]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    summary = base.build_summary(source, selected, orders, trades, period_bounds, {"selection": policy["selection"]})
    summary.update(
        {
            "policy": POLICY_NAME,
            "fixed_r_target_cap": False,
            "generic_model_fallback_can_create_trade": False,
            "runtime_requires_observable_causal_mechanism": True,
            "target_contract": "nearest still-live causal market structure visible at arm time",
            "trigger_contract": "earliest joint base-excursion, structural-completion and timing confirmation while route remains",
            "mechanism_priority": MECHANISM_PRIORITY,
            "score_weights": policy.get("score_weights"),
        }
    )
    by_mechanism: dict[str, Any] = {}
    for name in MECHANISM_PRIORITY:
        oo = orders[text(orders, "mechanism").eq(name)] if len(orders) else orders
        tt = trades[text(trades, "mechanism").eq(name)] if len(trades) else trades
        by_mechanism[name] = base.metric_block(oo, tt)
    summary["by_mechanism"] = by_mechanism
    return summary


def run(root: Path, period_bounds_path: Path, policy_path: Path, output: Path) -> dict[str, Any]:
    bounds = json.loads(period_bounds_path.read_text(encoding="utf-8"))
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
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
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

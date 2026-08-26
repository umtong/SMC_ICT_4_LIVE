#!/usr/bin/env python3
"""Research a deterministic first-response family router on fixed target plans.

This is deliberately independent of the learned hazard policy.  It converts the
same pre-entry auction, flow, structure and source-strength measurements into two
shared mechanism scores: failed-auction control transfer and accepted-auction
control persistence.  Development windows determine only the score cutoff and
reachable target fraction for each scenario family.  The fixed family union then
runs unchanged on all fresh windows with one pending order or position globally.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def load_core():
    path = Path(__file__).resolve().parents[1] / "candidate-ml-first-reachable-control-v4" / "reachable_control_policy.py"
    spec = importlib.util.spec_from_file_location("reachable_control_core", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import core policy from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CORE = load_core()

REVERSAL_FEATURES = {
    "auction_failure_pressure": 1.35,
    "auction_effort_result": 0.70,
    "event_activity_ratio": 0.55,
    "event_impact_per_activity": 0.65,
    "confirmation_activity_ratio": 0.55,
    "confirmation_impact_per_activity": 0.75,
    "source_strength_ratio": 0.75,
    "source_semantic_weight": 0.50,
    "source_defense_count": 0.35,
    "arm_effort_result_ratio": 0.45,
    "arm_path_efficiency": 0.40,
    "relative_auction_failure_pressure_rank": 0.85,
    "relative_source_strength_ratio_rank": 0.55,
    "relative_risk_bps_rank": -0.45,
}
CONTINUATION_FEATURES = {
    "auction_acceptance_strength": 1.30,
    "auction_path_efficiency": 0.95,
    "auction_progress_r": 0.55,
    "auction_route_headroom_r": 0.55,
    "arm_path_efficiency": 0.70,
    "arm_flow_share_signed": 0.65,
    "arm_activity_ratio": 0.35,
    "confirmation_delta_share_signed": 0.55,
    "confirmation_common_breadth_5m_signed": 0.45,
    "confirmation_common_breadth_15m_signed": 0.40,
    "departure_common_breadth_15m_signed": 0.35,
    "source_strength_ratio": 0.45,
    "target_strength_ratio": 0.30,
    "route_profile_path_low_volume_fraction": 0.35,
    "relative_auction_acceptance_strength_rank": 0.75,
    "relative_arm_path_efficiency_rank": 0.50,
    "relative_risk_bps_rank": -0.45,
}


def robust_parameters(dev: pd.DataFrame, feature_names: set[str]) -> dict[str, tuple[float, float]]:
    lower = {str(column).lower(): str(column) for column in dev.columns}
    result: dict[str, tuple[float, float]] = {}
    for name in feature_names:
        column = lower.get(name.lower())
        if column is None:
            continue
        values = pd.to_numeric(dev[column], errors="coerce")
        median = float(values.median())
        mad = float((values - median).abs().median())
        scale = 1.4826 * mad
        if not math.isfinite(scale) or scale < 1e-9:
            scale = float(values.std(ddof=0))
        if math.isfinite(median) and math.isfinite(scale) and scale >= 1e-9:
            result[name] = (median, scale)
    return result


def weighted_score(frame: pd.DataFrame, weights: dict[str, float], params: dict[str, tuple[float, float]]) -> pd.Series:
    lower = {str(column).lower(): str(column) for column in frame.columns}
    total = pd.Series(0.0, index=frame.index)
    mass = pd.Series(0.0, index=frame.index)
    for name, weight in weights.items():
        column = lower.get(name.lower())
        if column is None or name not in params:
            continue
        median, scale = params[name]
        values = pd.to_numeric(frame[column], errors="coerce")
        z = ((values - median) / scale).clip(-4.0, 4.0)
        available = z.notna()
        total.loc[available] += weight * z.loc[available]
        mass.loc[available] += abs(weight)
    return total / mass.replace(0.0, np.nan)


def attach_scores(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    result = frame.copy()
    dev = result[result["_role"] == "dev"]
    reversal_params = robust_parameters(dev, set(REVERSAL_FEATURES))
    continuation_params = robust_parameters(dev, set(CONTINUATION_FEATURES))
    reversal = weighted_score(result, REVERSAL_FEATURES, reversal_params)
    continuation = weighted_score(result, CONTINUATION_FEATURES, continuation_params)
    result["_mechanism"] = CORE.mechanism(result)
    failed = result["_mechanism"].str.startswith("failed")
    accepted = result["_mechanism"].str.startswith("accepted")
    result["_causal_score"] = np.where(failed, reversal, np.where(accepted, continuation, np.nan))
    result["_expected_log"] = result["_causal_score"]
    return result, {
        "reversal_features": REVERSAL_FEATURES,
        "continuation_features": CONTINUATION_FEATURES,
        "reversal_scaler": reversal_params,
        "continuation_scaler": continuation_params,
    }


def search(frame: pd.DataFrame, dev_periods: list[str]):
    dev = frame[frame["_role"] == "dev"].copy()
    fractions = tuple(sorted(float(value) for value in dev["route_fraction"].unique()))
    fraction_sets = [(value,) for value in fractions]
    fraction_sets += [
        tuple(value for value in fractions if value <= 0.18),
        tuple(value for value in fractions if value <= 0.25),
        tuple(value for value in fractions if value <= 0.33),
        fractions,
    ]
    fraction_sets = list(dict.fromkeys(item for item in fraction_sets if item))
    mechanisms = sorted(value for value in dev["_mechanism"].dropna().unique() if value != "other")
    rows = []
    components = []
    for mechanism in mechanisms:
        mechanism_scores = dev.loc[dev["_mechanism"] == mechanism, "_causal_score"].dropna()
        if mechanism_scores.empty:
            continue
        thresholds = sorted(set(float(mechanism_scores.quantile(q)) for q in (0.15, 0.30, 0.45, 0.58, 0.68, 0.78, 0.86, 0.92)))
        for fraction_set in fraction_sets:
            for threshold in thresholds:
                provisional = CORE.Component(mechanism, fraction_set, threshold, 0.0)
                _, stats = CORE.simulate(dev, [provisional], dev_periods)
                if stats["completed_trades"] < 4:
                    continue
                active = sum(row["completed_trades"] > 0 for row in stats["by_period"])
                if active < 2:
                    continue
                component = CORE.Component(mechanism, fraction_set, threshold, float(stats["robust_objective"]))
                components.append(component)
                rows.append({
                    "component": component.name,
                    "mechanism": mechanism,
                    "fractions": "+".join(map(str, fraction_set)),
                    "threshold": threshold,
                    "active_development_periods": active,
                    **{f"development_{key}": value for key, value in stats.items() if key != "by_period"},
                })
    components.sort(key=lambda item: item.score, reverse=True)
    return components, pd.DataFrame(rows).sort_values("development_robust_objective", ascending=False)


def greedy(frame: pd.DataFrame, components, dev_periods: list[str]):
    dev = frame[frame["_role"] == "dev"]
    selected = []
    current = -1e18
    for _ in range(4):
        best = None
        best_score = current
        used = {component.mechanism for component in selected}
        for component in components[:160]:
            if component.mechanism in used:
                continue
            _, stats = CORE.simulate(dev, selected + [component], dev_periods)
            if stats["robust_objective"] > best_score + 0.001:
                best = component
                best_score = float(stats["robust_objective"])
        if best is None:
            break
        selected.append(best)
        current = best_score
    return selected


def safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [safe(item) for item in value]
    if isinstance(value, tuple):
        return [safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    raw = CORE.read_plans(args.root)
    frame, schema = CORE.normalize(raw)
    frame, score_model = attach_scores(frame)
    dev_periods = sorted(frame.loc[frame["_role"] == "dev", "_period"].unique())
    fresh_periods = sorted(frame.loc[frame["_role"] == "fresh", "_period"].unique())
    components, catalog = search(frame, dev_periods)
    if not components:
        raise SystemExit("no deterministic family component produced executable development trades")
    policies = {"best_family_component": [components[0]]}
    fused = greedy(frame, components, dev_periods)
    if fused:
        policies["causal_family_fusion"] = fused

    variants = []
    completed_by_name = {}
    stats_by_name = {}
    for name, policy in policies.items():
        dev_completed, dev_stats = CORE.simulate(frame[frame["_role"] == "dev"], policy, dev_periods)
        fresh_completed, fresh_stats = CORE.simulate(frame[frame["_role"] == "fresh"], policy, fresh_periods)
        completed_by_name[name] = pd.concat([
            dev_completed.assign(_evaluation_role="dev"),
            fresh_completed.assign(_evaluation_role="fresh"),
        ], ignore_index=True, sort=False)
        stats_by_name[name] = (dev_stats, fresh_stats)
        variants.append({
            "variant": name,
            "development_objective": dev_stats["robust_objective"],
            "development_trades": dev_stats["completed_trades"],
            "development_mean_net_r": dev_stats["mean_net_r"],
            "development_nav": dev_stats["ending_nav_multiplier"],
            "development_maximum_drawdown": dev_stats["maximum_drawdown"],
            "fresh_trades": fresh_stats["completed_trades"],
            "fresh_mean_net_r": fresh_stats["mean_net_r"],
            "fresh_nav": fresh_stats["ending_nav_multiplier"],
            "fresh_maximum_drawdown": fresh_stats["maximum_drawdown"],
            "fresh_trades_per_day": fresh_stats["trades_per_day"],
            "components": len(policy),
        })
    variant_table = pd.DataFrame(variants).sort_values(["development_objective", "development_trades"], ascending=[False, False])
    selected_name = str(variant_table.iloc[0]["variant"])
    selected_policy = policies[selected_name]
    development, fresh_stats = stats_by_name[selected_name]
    completed = completed_by_name[selected_name]

    summary = {
        "policy": "ML_FIRST_CAUSAL_FAMILY_ROUTER_V5",
        "selected_variant": selected_name,
        "components": [{
            "name": component.name,
            "mechanism": component.mechanism,
            "fractions": list(component.fractions),
            "minimum_family_score": component.threshold,
            "development_component_score": component.score,
        } for component in selected_policy],
        "development": development,
        "fresh": fresh_stats,
        "development_periods": dev_periods,
        "fresh_periods": fresh_periods,
        "score_model": score_model,
        "schema": schema,
        "causal_contract": {
            "one_global_pending_or_position": True,
            "one_selection_per_causal_episode": True,
            "symbol_identity_not_used": True,
            "future_path_and_outcome_not_used": True,
            "same_rules_for_btc_eth_sol_xrp": True,
        },
    }

    output_columns = [column for column in completed.columns if not str(column).startswith("_")]
    output_columns += ["_period", "_evaluation_role", "_decision", "_fill_ts", "_exit_ts", "_net_r", "_causal_score", "_mechanism", "_component"]
    output_columns = list(dict.fromkeys(column for column in output_columns if column in completed.columns))
    completed[output_columns].to_csv(args.output / "completed_trades.csv", index=False)
    variant_table.to_csv(args.output / "variant_metrics.csv", index=False)
    catalog.to_csv(args.output / "component_catalog.csv", index=False)
    pd.DataFrame(development["by_period"] + fresh_stats["by_period"]).to_csv(args.output / "period_metrics.csv", index=False)
    (args.output / "summary.json").write_text(json.dumps(safe(summary), indent=2, sort_keys=True) + "\n")
    (args.output / "family_score_model.json").write_text(json.dumps(safe(score_model), indent=2, sort_keys=True) + "\n")
    print(json.dumps(safe(summary), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

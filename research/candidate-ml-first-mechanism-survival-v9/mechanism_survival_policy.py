#!/usr/bin/env python3
"""Learn ordered target survival separately for each causal auction mechanism.

Failed-auction reversal and accepted-auction continuation are not treated as one
latent state.  They share the same event detector, fill model, structural stop,
fixed target actions and global account, but each mechanism receives its own
conditional target-reach model when development support is sufficient.  A
shared reach model remains only a statistical fallback.  The exact mechanism
families are then fused through one real pending-order/position timeline.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold


def import_v7():
    path = Path(__file__).resolve().parents[1] / "candidate-ml-first-ordered-reachability-v7" / "ordered_reachability_policy.py"
    spec = importlib.util.spec_from_file_location("mechanism_survival_v7_core", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


V7 = import_v7()
CORE = V7.CORE
RISK = V7.RISK


def assign_roles(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    fresh = result["_period"].astype(str).str.lower().str.contains(r"(^|[-_/])z-", regex=True)
    result["_role"] = np.where(fresh, "fresh", "dev")
    return result


def enough_binary_support(frame: pd.DataFrame, label: str, minimum_rows: int = 90) -> bool:
    if len(frame) < minimum_rows:
        return False
    values = frame[label].astype(int)
    return int(values.sum()) >= 18 and int((1 - values).sum()) >= 18


def balanced_event_weights(frame: pd.DataFrame, label: str) -> np.ndarray:
    duplicate_count = frame.groupby("_event_key")["route_fraction"].transform("count").clip(lower=1)
    return CORE.balanced_weights(frame[label].astype(int).to_numpy(), 1.0 / duplicate_count.to_numpy(float))


def train_reach_models(
    train: pd.DataFrame,
    features: list[str],
    numeric: list[str],
    categorical: list[str],
    seed: int,
):
    filled = train[train["_filled"] & train["_net_r"].notna()].copy()
    shared = CORE.fit_binary(
        filled[features], filled["_won"], numeric, categorical, seed,
        balanced_event_weights(filled, "_won") if len(filled) else None,
    )
    models = {"__shared__": shared}
    for mechanism, subset in filled.groupby("_mechanism", sort=False):
        if enough_binary_support(subset, "_won"):
            models[str(mechanism)] = CORE.fit_binary(
                subset[features], subset["_won"], numeric, categorical, seed + 100 + len(models),
                balanced_event_weights(subset, "_won"),
            )
    return models


def predict_reach(models, frame: pd.DataFrame, features: list[str]) -> np.ndarray:
    output = np.empty(len(frame), dtype=float)
    output[:] = np.nan
    shared = models["__shared__"]
    for mechanism, indices in frame.groupby("_mechanism", sort=False).groups.items():
        model = models.get(str(mechanism), shared)
        locations = frame.index.get_indexer(pd.Index(indices))
        output[locations] = model.predict_proba(frame.loc[indices, features])[:, 1]
    if np.isnan(output).any():
        missing = np.isnan(output)
        output[missing] = shared.predict_proba(frame.iloc[np.flatnonzero(missing)][features])[:, 1]
    return output


def fit_predictions(frame: pd.DataFrame, numeric: list[str], categorical: list[str]):
    dev = frame[frame["_role"] == "dev"].copy()
    fresh = frame[frame["_role"] == "fresh"].copy()
    dev["_event_key"] = V7.event_key(dev)
    fresh["_event_key"] = V7.event_key(fresh)
    dev["_mechanism"] = V7.mechanism(dev)
    fresh["_mechanism"] = V7.mechanism(fresh)

    reach_features = numeric + categorical
    fill_numeric = [
        column for column in numeric
        if column != "route_fraction"
        and not str(column).lower().startswith("target_")
        and str(column).lower() not in {"gross_rr", "route_rr"}
    ]
    fill_features = fill_numeric + categorical
    periods = sorted(dev["_period"].unique())
    splits = min(10, len(periods))
    if splits < 5:
        raise SystemExit(f"insufficient development periods: {periods}")

    dev["_raw_fill"] = np.nan
    dev["_raw_reach"] = np.nan
    groups = dev["_period"].astype(str).to_numpy()
    splitter = GroupKFold(n_splits=splits)
    for fold, (train_index, test_index) in enumerate(splitter.split(dev, groups=groups)):
        train = dev.iloc[train_index]
        test = dev.iloc[test_index]
        fill_train = train.sort_values("route_fraction").drop_duplicates("_event_key", keep="first")
        fill_model = CORE.fit_binary(
            fill_train[fill_features], fill_train["_filled"], fill_numeric, categorical, 1100 + fold,
            CORE.balanced_weights(fill_train["_filled"].astype(int).to_numpy()),
        )
        reach_models = train_reach_models(train, reach_features, numeric, categorical, 1200 + fold * 20)
        dev.loc[test.index, "_raw_fill"] = fill_model.predict_proba(test[fill_features])[:, 1]
        dev.loc[test.index, "_raw_reach"] = predict_reach(reach_models, test, reach_features)

    fill_all = dev.sort_values("route_fraction").drop_duplicates("_event_key", keep="first")
    final_fill = CORE.fit_binary(
        fill_all[fill_features], fill_all["_filled"], fill_numeric, categorical, 1401,
        CORE.balanced_weights(fill_all["_filled"].astype(int).to_numpy()),
    )
    final_reach = train_reach_models(dev, reach_features, numeric, categorical, 1501)
    fresh["_raw_fill"] = final_fill.predict_proba(fresh[fill_features])[:, 1]
    fresh["_raw_reach"] = predict_reach(final_reach, fresh, reach_features)

    combined = pd.concat([dev, fresh], ignore_index=False).sort_index()
    combined["_raw_fill"] = combined["_raw_fill"].clip(0.005, 0.995)
    combined["_raw_reach"] = combined["_raw_reach"].clip(0.005, 0.995)
    bundle = {
        "fill_model": final_fill,
        "reach_models": final_reach,
        "fill_numeric_features": fill_numeric,
        "fill_categorical_features": categorical,
        "reach_numeric_features": numeric,
        "reach_categorical_features": categorical,
        "mechanism_conditioned": True,
    }
    return combined, bundle


def exact_components(frame: pd.DataFrame, periods: list[str]):
    dev = frame[frame["_role"] == "dev"]
    mechanisms = sorted(value for value in dev["_mechanism"].dropna().unique() if value != "other")
    maximums = sorted(float(value) for value in dev["route_fraction"].unique())
    penalties = [0.5, 1.0, 1.5, 2.0, 2.5]
    components = []
    rows = []
    for mechanism in mechanisms:
        scoped = dev[dev["_mechanism"] == mechanism]
        for maximum in maximums:
            available = scoped[scoped["route_fraction"] <= maximum + 1e-12]
            for penalty in penalties:
                scores = V7.score_for_penalty(available, penalty).replace([np.inf, -np.inf], np.nan).dropna()
                if scores.empty:
                    continue
                thresholds = sorted(set(
                    [-0.0015, -0.00075, -0.00025, 0.0, 0.00015, 0.00035]
                    + [float(scores.quantile(q)) for q in (0.40, 0.55, 0.68, 0.78, 0.86, 0.92, 0.96)]
                ))
                for threshold in thresholds:
                    provisional = V7.Component(str(mechanism), maximum, penalty, threshold, 0.0)
                    _, stats = V7.simulate(dev, [provisional], periods)
                    if stats["completed_trades"] < 6 or stats["positive_periods"] < 4:
                        continue
                    component = V7.Component(str(mechanism), maximum, penalty, threshold, float(stats["robust_objective"]))
                    components.append(component)
                    rows.append({
                        "component": component.name,
                        "mechanism": mechanism,
                        "max_fraction": maximum,
                        "uncertainty_penalty": penalty,
                        "minimum_expected_log": threshold,
                        **{f"development_{key}": value for key, value in stats.items() if key != "by_period"},
                    })
    components.sort(key=lambda item: item.score, reverse=True)
    catalog = pd.DataFrame(rows)
    if not catalog.empty:
        catalog = catalog.sort_values("development_robust_objective", ascending=False)
    return components, catalog


def greedy_exact_union(frame: pd.DataFrame, candidates, periods: list[str]):
    dev = frame[frame["_role"] == "dev"]
    selected = []
    current = -1e18
    for _ in range(4):
        best = None
        best_score = current
        used = {component.scope for component in selected}
        for candidate in candidates[:240]:
            if candidate.scope in used:
                continue
            _, stats = V7.simulate(dev, selected + [candidate], periods)
            if stats["robust_objective"] > best_score + 0.0008:
                best = candidate
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
    frame = assign_roles(frame)
    numeric, categorical = CORE.feature_columns(frame)
    predicted, bundle = fit_predictions(frame, numeric, categorical)
    predicted = assign_roles(predicted)
    predicted["_mechanism"] = V7.mechanism(predicted)
    calibrated, fill_table, reach_table = V7.calibrated_predictions(predicted)
    calibrated = assign_roles(calibrated)
    calibrated["_planned_reward_r"] = V7.planned_reward(calibrated)

    development_periods = sorted(calibrated.loc[calibrated["_role"] == "dev", "_period"].unique())
    fresh_periods = sorted(calibrated.loc[calibrated["_role"] == "fresh", "_period"].unique())
    if len(development_periods) < 20 or len(fresh_periods) < 6:
        raise SystemExit(f"unexpected period partition: dev={development_periods}, fresh={fresh_periods}")

    components, catalog = exact_components(calibrated, development_periods)
    if not components:
        raise SystemExit("no mechanism-conditioned component survived development")
    policies = {"best_mechanism_component": [components[0]]}
    fused = greedy_exact_union(calibrated, components, development_periods)
    if fused:
        policies["mechanism_survival_fusion"] = fused
    failed = [component for component in components if component.scope.startswith("failed")]
    accepted = [component for component in components if component.scope.startswith("accepted")]
    if failed:
        policies["failed_auction_survival"] = [failed[0]]
    if accepted:
        policies["accepted_auction_survival"] = [accepted[0]]

    rows = []
    completed_by_name = {}
    stats_by_name = {}
    for name, policy in policies.items():
        dev_completed, dev_stats = V7.simulate(calibrated[calibrated["_role"] == "dev"], policy, development_periods)
        fresh_completed, fresh_stats = V7.simulate(calibrated[calibrated["_role"] == "fresh"], policy, fresh_periods)
        completed_by_name[name] = pd.concat([
            dev_completed.assign(_evaluation_role="development"),
            fresh_completed.assign(_evaluation_role="fresh"),
        ], ignore_index=True, sort=False)
        stats_by_name[name] = (dev_stats, fresh_stats)
        rows.append({
            "variant": name,
            "development_objective": dev_stats["robust_objective"],
            "development_trades": dev_stats["completed_trades"],
            "development_mean_net_r": dev_stats["mean_net_r"],
            "development_nav": dev_stats["ending_nav_multiplier"],
            "development_maximum_drawdown": dev_stats["maximum_drawdown"],
            "development_positive_periods": dev_stats["positive_periods"],
            "fresh_trades": fresh_stats["completed_trades"],
            "fresh_mean_net_r": fresh_stats["mean_net_r"],
            "fresh_nav": fresh_stats["ending_nav_multiplier"],
            "fresh_maximum_drawdown": fresh_stats["maximum_drawdown"],
            "fresh_trades_per_day": fresh_stats["trades_per_day"],
            "fresh_positive_periods": fresh_stats["positive_periods"],
            "components": len(policy),
        })
    variants = pd.DataFrame(rows).sort_values(["development_objective", "development_trades"], ascending=[False, False])
    selected_name = str(variants.iloc[0]["variant"])
    selected_policy = policies[selected_name]
    development, fresh_stats = stats_by_name[selected_name]
    completed = completed_by_name[selected_name]

    summary = {
        "policy": "ML_FIRST_MECHANISM_CONDITIONED_SURVIVAL_V9",
        "selected_variant": selected_name,
        "components": [{
            "name": component.name,
            "mechanism": component.scope,
            "maximum_route_fraction": component.max_fraction,
            "uncertainty_penalty": component.penalty,
            "minimum_expected_log_growth": component.threshold,
            "development_component_score": component.score,
        } for component in selected_policy],
        "development": development,
        "fresh": fresh_stats,
        "development_periods": development_periods,
        "fresh_periods": fresh_periods,
        "schema": schema,
        "numeric_features": numeric,
        "categorical_features": categorical,
        "causal_contract": {
            "separate_reach_models_for_auction_mechanisms": True,
            "shared_model_used_only_when_mechanism_support_is_insufficient": True,
            "ordered_target_survival_enforced": True,
            "grouped_oof_development_predictions": True,
            "one_global_pending_or_position": True,
            "one_selection_per_causal_episode": True,
            "risk_fraction": RISK,
        },
    }

    columns = [column for column in completed.columns if not str(column).startswith("_")]
    columns += [
        "_period", "_evaluation_role", "_decision", "_fill_ts", "_exit_ts", "_net_r",
        "_raw_fill", "_raw_reach", "_fill_calibrated", "_reach_calibrated",
        "_policy_expected_log", "_planned_reward_r", "_mechanism", "_component",
    ]
    columns = list(dict.fromkeys(column for column in columns if column in completed.columns))
    completed[columns].to_csv(args.output / "completed_trades.csv", index=False)
    variants.to_csv(args.output / "variant_metrics.csv", index=False)
    catalog.to_csv(args.output / "component_catalog.csv", index=False)
    pd.DataFrame(development["by_period"] + fresh_stats["by_period"]).to_csv(args.output / "period_metrics.csv", index=False)
    fill_table.to_csv(args.output / "fill_calibration.csv", index=False)
    reach_table.to_csv(args.output / "reach_calibration.csv", index=False)
    (args.output / "summary.json").write_text(json.dumps(safe(summary), indent=2, sort_keys=True) + "\n")
    joblib.dump(bundle | {"selected_policy": selected_policy, "schema": schema}, args.output / "model_bundle.joblib")
    print(json.dumps(safe(summary), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

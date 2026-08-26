#!/usr/bin/env python3
"""Select the reachable completion frontier with an ordered causal hazard model.

The policy treats target distance as an ordered action rather than five unrelated
classifications.  It learns fill and target-before-stop hazards from prior
windows, calibrates them only with grouped out-of-fold development predictions,
enforces non-increasing reachability as the completion frontier moves farther
away, and routes one event/fraction globally by conservative expected account
log-growth.  The newly prefixed `u-` windows remain untouched until the complete
policy is fixed.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold


def import_core():
    path = Path(__file__).resolve().parents[1] / "candidate-ml-first-reachable-control-v4" / "reachable_control_policy.py"
    spec = importlib.util.spec_from_file_location("ordered_reachability_core", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CORE = import_core()
RISK = CORE.RISK


def assign_roles(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    fresh = result["_period"].astype(str).str.lower().str.contains(r"(^|[-_/])u-", regex=True)
    result["_role"] = np.where(fresh, "fresh", "dev")
    return result


def event_key(frame: pd.DataFrame) -> pd.Series:
    return (
        frame["_period"].astype(str)
        + "|"
        + frame["_episode"].astype(str)
        + "|"
        + frame["_decision"].dt.floor("s").astype(str)
    )


def fit_oof_models(frame: pd.DataFrame, numeric: list[str], categorical: list[str]):
    dev = frame[frame["_role"] == "dev"].copy()
    fresh = frame[frame["_role"] == "fresh"].copy()
    dev["_event_key"] = event_key(dev)
    fresh["_event_key"] = event_key(fresh)

    reach_features = numeric + categorical
    fill_numeric = [
        column
        for column in numeric
        if column != "route_fraction"
        and not str(column).lower().startswith("target_")
        and str(column).lower() not in {"gross_rr", "route_rr"}
    ]
    fill_categorical = categorical
    fill_features = fill_numeric + fill_categorical

    periods = sorted(dev["_period"].unique())
    splits = min(8, len(periods))
    if splits < 4:
        raise SystemExit(f"need at least four development periods, found {periods}")

    dev["_raw_fill"] = np.nan
    dev["_raw_reach"] = np.nan
    groups = dev["_period"].astype(str).to_numpy()
    splitter = GroupKFold(n_splits=splits)
    for fold, (train_index, test_index) in enumerate(splitter.split(dev, groups=groups)):
        train = dev.iloc[train_index]
        test = dev.iloc[test_index]

        fill_train = train.sort_values("route_fraction").drop_duplicates("_event_key", keep="first")
        fill_model = CORE.fit_binary(
            fill_train[fill_features],
            fill_train["_filled"],
            fill_numeric,
            fill_categorical,
            700 + fold,
            CORE.balanced_weights(fill_train["_filled"].astype(int).to_numpy()),
        )
        reach_train = train[train["_filled"] & train["_net_r"].notna()].copy()
        duplicate_count = reach_train.groupby("_event_key")["route_fraction"].transform("count").clip(lower=1)
        reach_weight = CORE.balanced_weights(
            reach_train["_won"].astype(int).to_numpy(),
            1.0 / duplicate_count.to_numpy(float),
        ) if len(reach_train) else None
        reach_model = CORE.fit_binary(
            reach_train[reach_features],
            reach_train["_won"],
            numeric,
            categorical,
            800 + fold,
            reach_weight,
        )
        dev.loc[test.index, "_raw_fill"] = fill_model.predict_proba(test[fill_features])[:, 1]
        dev.loc[test.index, "_raw_reach"] = reach_model.predict_proba(test[reach_features])[:, 1]

    fill_all = dev.sort_values("route_fraction").drop_duplicates("_event_key", keep="first")
    final_fill = CORE.fit_binary(
        fill_all[fill_features],
        fill_all["_filled"],
        fill_numeric,
        fill_categorical,
        901,
        CORE.balanced_weights(fill_all["_filled"].astype(int).to_numpy()),
    )
    reach_all = dev[dev["_filled"] & dev["_net_r"].notna()].copy()
    duplicate_count = reach_all.groupby("_event_key")["route_fraction"].transform("count").clip(lower=1)
    final_reach = CORE.fit_binary(
        reach_all[reach_features],
        reach_all["_won"],
        numeric,
        categorical,
        902,
        CORE.balanced_weights(
            reach_all["_won"].astype(int).to_numpy(),
            1.0 / duplicate_count.to_numpy(float),
        ) if len(reach_all) else None,
    )
    fresh["_raw_fill"] = final_fill.predict_proba(fresh[fill_features])[:, 1]
    fresh["_raw_reach"] = final_reach.predict_proba(fresh[reach_features])[:, 1]

    combined = pd.concat([dev, fresh], ignore_index=False).sort_index()
    combined["_raw_fill"] = combined["_raw_fill"].clip(0.005, 0.995)
    combined["_raw_reach"] = combined["_raw_reach"].clip(0.005, 0.995)
    bundle = {
        "fill_model": final_fill,
        "reach_model": final_reach,
        "fill_numeric_features": fill_numeric,
        "fill_categorical_features": fill_categorical,
        "reach_numeric_features": numeric,
        "reach_categorical_features": categorical,
    }
    return combined, bundle


def mechanism(frame: pd.DataFrame) -> pd.Series:
    base = CORE.mechanism(frame)
    return base.astype(str)


def calibration_table(dev: pd.DataFrame, probability: str, label: str, group_columns: list[str]) -> pd.DataFrame:
    rows = []
    for group_values, group in dev.groupby(group_columns, dropna=False, sort=False):
        if not isinstance(group_values, tuple):
            group_values = (group_values,)
        valid = group[[probability, label]].dropna()
        if len(valid) < 30:
            continue
        quantiles = np.unique(valid[probability].quantile(np.linspace(0, 1, 9)).to_numpy(float))
        if len(quantiles) < 3:
            quantiles = np.array([0.0, 0.5, 1.0])
        quantiles[0] = -np.inf
        quantiles[-1] = np.inf
        bins = pd.cut(valid[probability], bins=quantiles, include_lowest=True, duplicates="drop")
        for interval, subset in valid.groupby(bins, observed=True):
            n = int(len(subset))
            positives = int(subset[label].astype(int).sum())
            empirical = (positives + 4.0) / (n + 8.0)
            rows.append(
                {
                    **{column: value for column, value in zip(group_columns, group_values)},
                    "left": float(interval.left),
                    "right": float(interval.right),
                    "n": n,
                    "positives": positives,
                    "empirical_probability": float(empirical),
                    "raw_probability_mean": float(subset[probability].mean()),
                }
            )
    return pd.DataFrame(rows)


def apply_calibration(
    frame: pd.DataFrame,
    table: pd.DataFrame,
    raw_column: str,
    output_prefix: str,
    group_columns: list[str],
) -> pd.DataFrame:
    result = frame.copy()
    result[f"_{output_prefix}_calibrated"] = result[raw_column]
    result[f"_{output_prefix}_calibration_n"] = 20.0
    if table.empty:
        return result
    grouped = {
        tuple(str(row[column]) for column in group_columns): group.copy()
        for _, group in table.groupby(group_columns, dropna=False, sort=False)
        for row in [group.iloc[0]]
    }
    for index, row in result.iterrows():
        key = tuple(str(row[column]) for column in group_columns)
        group = grouped.get(key)
        if group is None:
            continue
        raw = float(row[raw_column])
        match = group[(group["left"] < raw) & (raw <= group["right"])]
        if match.empty:
            nearest = (group["raw_probability_mean"] - raw).abs().idxmin()
            selected = group.loc[nearest]
        else:
            selected = match.iloc[0]
        empirical = float(selected["empirical_probability"])
        n = float(selected["n"])
        blend = min(0.75, n / (n + 35.0))
        result.at[index, f"_{output_prefix}_calibrated"] = blend * empirical + (1.0 - blend) * raw
        result.at[index, f"_{output_prefix}_calibration_n"] = n
    return result


def enforce_order(frame: pd.DataFrame, column: str) -> pd.Series:
    output = pd.Series(index=frame.index, dtype=float)
    for _, group in frame.groupby("_event_key", sort=False):
        ordered = group.sort_values("route_fraction")
        values = ordered[column].to_numpy(float)
        monotonic = np.minimum.accumulate(values)
        output.loc[ordered.index] = monotonic
    return output


def calibrated_predictions(frame: pd.DataFrame):
    result = frame.copy()
    result["_mechanism"] = mechanism(result)
    dev = result[result["_role"] == "dev"].copy()

    fill_events = dev.sort_values("route_fraction").drop_duplicates("_event_key", keep="first")
    fill_table = calibration_table(fill_events, "_raw_fill", "_filled", ["_mechanism"])
    reach_dev = dev[dev["_filled"] & dev["_net_r"].notna()].copy()
    reach_table = calibration_table(reach_dev, "_raw_reach", "_won", ["_mechanism", "route_fraction"])

    result = apply_calibration(result, fill_table, "_raw_fill", "fill", ["_mechanism"])
    result = apply_calibration(result, reach_table, "_raw_reach", "reach", ["_mechanism", "route_fraction"])
    result["_reach_calibrated"] = enforce_order(result, "_reach_calibrated")
    result["_raw_reach"] = enforce_order(result, "_raw_reach")
    return result, fill_table, reach_table


def planned_reward(frame: pd.DataFrame) -> pd.Series:
    reward = pd.to_numeric(frame["_gross_rr"], errors="coerce")
    development_winners = frame[(frame["_role"] == "dev") & frame["_filled"] & frame["_won"] & frame["_net_r"].notna()]
    fallback = development_winners.groupby("route_fraction")["_net_r"].median()
    reward = reward.fillna(frame["route_fraction"].map(fallback)).fillna(1.0)
    return (reward - 0.08).clip(lower=0.05)


def score_for_penalty(frame: pd.DataFrame, penalty: float) -> pd.Series:
    fill = frame["_fill_calibrated"].astype(float)
    reach = frame["_reach_calibrated"].astype(float)
    fill_n = frame["_fill_calibration_n"].astype(float).clip(lower=5.0)
    reach_n = frame["_reach_calibration_n"].astype(float).clip(lower=5.0)
    fill_lower = (fill - penalty * np.sqrt(fill * (1.0 - fill) / (fill_n + 8.0))).clip(0.005, 0.995)
    reach_lower = (reach - penalty * np.sqrt(reach * (1.0 - reach) / (reach_n + 8.0))).clip(0.005, 0.995)
    reward = frame["_planned_reward_r"].astype(float)
    win_log = np.log1p(RISK * reward)
    loss_log = math.log(1.0 - RISK)
    return fill_lower * (reach_lower * win_log + (1.0 - reach_lower) * loss_log)


@dataclass(frozen=True)
class Component:
    scope: str
    max_fraction: float
    penalty: float
    threshold: float
    score: float

    @property
    def name(self) -> str:
        return f"{self.scope}|f<={self.max_fraction:.2f}|pen={self.penalty:.2f}|elog>={self.threshold:.6g}"


def scope_mask(frame: pd.DataFrame, scope: str) -> pd.Series:
    mechanism_value = frame["_mechanism"].astype(str)
    if scope == "all":
        return pd.Series(True, index=frame.index)
    if scope == "failed":
        return mechanism_value.str.startswith("failed")
    if scope == "accepted":
        return mechanism_value.str.startswith("accepted")
    if scope == "first_retest":
        return mechanism_value.str.contains("first_retest")
    return mechanism_value.eq(scope)


def component_candidates(frame: pd.DataFrame, component: Component) -> pd.DataFrame:
    work = frame[scope_mask(frame, component.scope) & (frame["route_fraction"] <= component.max_fraction + 1e-12)].copy()
    work["_policy_expected_log"] = score_for_penalty(work, component.penalty)
    work = work[work["_policy_expected_log"] >= component.threshold]
    work["_component"] = component.name
    work["_component_priority"] = component.score
    return work


def candidate_union(frame: pd.DataFrame, components: list[Component]) -> pd.DataFrame:
    pieces = [component_candidates(frame, component) for component in components]
    pieces = [piece for piece in pieces if not piece.empty]
    if not pieces:
        return frame.iloc[0:0].copy()
    candidates = pd.concat(pieces, ignore_index=True, sort=False)
    candidates = candidates.sort_values(
        ["_period", "_event_key", "_policy_expected_log", "_component_priority", "route_fraction"],
        ascending=[True, True, False, False, True],
        kind="mergesort",
    ).drop_duplicates(["_period", "_event_key"], keep="first")
    return candidates


def simulate(frame: pd.DataFrame, components: list[Component], periods: list[str]):
    candidates = candidate_union(frame, components)
    completed_rows = []
    selected_rows = []
    nav = 1.0
    peak = 1.0
    maximum_drawdown = 0.0
    period_rows = []
    for period in periods:
        subset = candidates[candidates["_period"] == period].copy()
        subset["_minute"] = subset["_decision"].dt.floor("min")
        busy_until = pd.Timestamp.min.tz_localize("UTC")
        locked: set[str] = set()
        before = nav
        period_completed = 0
        for _, simultaneous in subset.groupby("_minute", sort=True):
            decision = simultaneous["_decision"].min()
            if decision < busy_until:
                continue
            simultaneous = simultaneous[~simultaneous["_episode"].isin(locked)]
            if simultaneous.empty:
                continue
            chosen = simultaneous.sort_values(
                ["_policy_expected_log", "_component_priority", "route_fraction"],
                ascending=[False, False, True],
                kind="mergesort",
            ).iloc[0]
            locked.add(str(chosen["_episode"]))
            selected_rows.append(chosen)
            if bool(chosen["_filled"]):
                busy_until = max(chosen["_exit_ts"], chosen["_fill_ts"] + pd.Timedelta(minutes=1))
                if pd.notna(chosen["_net_r"]):
                    value = float(chosen["_net_r"])
                    nav *= max(1e-12, 1.0 + RISK * value)
                    peak = max(peak, nav)
                    maximum_drawdown = max(maximum_drawdown, 1.0 - nav / peak)
                    completed_rows.append(chosen)
                    period_completed += 1
            else:
                busy_until = max(chosen["_cancel_ts"], decision + pd.Timedelta(minutes=1))
        period_rows.append(
            {
                "period": period,
                "completed_trades": period_completed,
                "nav_multiplier": float(nav / before) if before else 0.0,
                "log_growth": float(math.log(max(1e-12, nav / before))) if before else -math.inf,
            }
        )
    completed = pd.DataFrame(completed_rows) if completed_rows else frame.iloc[0:0].copy()
    selected = pd.DataFrame(selected_rows) if selected_rows else frame.iloc[0:0].copy()
    values = completed["_net_r"].astype(float).to_numpy() if len(completed) else np.array([], dtype=float)
    gross_win = float(values[values > 0].sum()) if len(values) else 0.0
    gross_loss = float(-values[values < 0].sum()) if len(values) else 0.0
    logs = np.array([row["log_growth"] for row in period_rows], dtype=float)
    days = max(1, 7 * len(periods))
    trades_per_day = len(completed) / days
    robust = (
        math.log(max(1e-12, nav))
        - 0.55 * float(logs.std(ddof=0) * math.sqrt(max(1, len(logs))))
        - 0.08 * max(0.0, 1.0 - trades_per_day)
        - 0.04 * maximum_drawdown
    )
    if len(completed) == 0:
        robust = -1e9
    stats = {
        "selected_plans": int(len(selected)),
        "completed_trades": int(len(completed)),
        "calendar_days": int(days),
        "trades_per_day": float(trades_per_day),
        "win_rate": float((values > 0).mean()) if len(values) else 0.0,
        "mean_net_r": float(values.mean()) if len(values) else 0.0,
        "median_net_r": float(np.median(values)) if len(values) else 0.0,
        "profit_factor_r": float(gross_win / gross_loss) if gross_loss > 0 else (None if gross_win == 0 else math.inf),
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(maximum_drawdown),
        "robust_objective": float(robust),
        "positive_periods": int(sum(row["log_growth"] > 0 for row in period_rows)),
        "by_period": period_rows,
    }
    return completed, stats


def search_components(frame: pd.DataFrame, periods: list[str]):
    dev = frame[frame["_role"] == "dev"]
    scopes = ["all", "failed", "accepted", "first_retest"] + sorted(
        value for value in dev["_mechanism"].dropna().unique() if value != "other"
    )
    scopes = list(dict.fromkeys(scopes))
    max_fractions = sorted(float(value) for value in dev["route_fraction"].unique())
    penalties = [0.5, 1.0, 1.5, 2.0]
    components = []
    rows = []
    for scope in scopes:
        scoped = dev[scope_mask(dev, scope)]
        if scoped.empty:
            continue
        for maximum in max_fractions:
            available = scoped[scoped["route_fraction"] <= maximum + 1e-12]
            for penalty in penalties:
                values = score_for_penalty(available, penalty).replace([np.inf, -np.inf], np.nan).dropna()
                if values.empty:
                    continue
                thresholds = sorted(set(
                    [-0.0015, -0.00075, -0.00025, 0.0, 0.00015]
                    + [float(values.quantile(q)) for q in (0.45, 0.62, 0.76, 0.87, 0.94)]
                ))
                for threshold in thresholds:
                    provisional = Component(scope, maximum, penalty, threshold, 0.0)
                    _, stats = simulate(dev, [provisional], periods)
                    if stats["completed_trades"] < 5 or stats["positive_periods"] < 3:
                        continue
                    component = Component(scope, maximum, penalty, threshold, float(stats["robust_objective"]))
                    components.append(component)
                    rows.append(
                        {
                            "component": component.name,
                            "scope": scope,
                            "max_fraction": maximum,
                            "penalty": penalty,
                            "threshold": threshold,
                            **{f"development_{key}": value for key, value in stats.items() if key != "by_period"},
                        }
                    )
    components.sort(key=lambda component: component.score, reverse=True)
    catalog = pd.DataFrame(rows)
    if not catalog.empty:
        catalog = catalog.sort_values("development_robust_objective", ascending=False)
    return components, catalog


def greedy_union(frame: pd.DataFrame, candidates: list[Component], periods: list[str]) -> list[Component]:
    dev = frame[frame["_role"] == "dev"]
    selected: list[Component] = []
    current = -1e18
    for _ in range(6):
        best = None
        best_score = current
        used = {component.scope for component in selected if component.scope != "all"}
        for candidate in candidates[:240]:
            if candidate.scope == "all" or candidate.scope in used:
                continue
            _, stats = simulate(dev, selected + [candidate], periods)
            if stats["robust_objective"] > best_score + 0.001:
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
    predicted, model_bundle = fit_oof_models(frame, numeric, categorical)
    predicted = assign_roles(predicted)
    calibrated, fill_table, reach_table = calibrated_predictions(predicted)
    calibrated = assign_roles(calibrated)
    calibrated["_planned_reward_r"] = planned_reward(calibrated)

    development_periods = sorted(calibrated.loc[calibrated["_role"] == "dev", "_period"].unique())
    fresh_periods = sorted(calibrated.loc[calibrated["_role"] == "fresh", "_period"].unique())
    if len(development_periods) < 12 or len(fresh_periods) < 6:
        raise SystemExit(f"unexpected period partition: dev={development_periods}, fresh={fresh_periods}")

    components, catalog = search_components(calibrated, development_periods)
    if not components:
        raise SystemExit("no ordered-reachability component produced multi-period development growth")
    policies: dict[str, list[Component]] = {"best_single_scope": [components[0]]}
    best_all = next((component for component in components if component.scope == "all"), None)
    if best_all is not None:
        policies["shared_ordered_router"] = [best_all]
    fused = greedy_union(calibrated, components, development_periods)
    if fused:
        policies["ordered_scenario_fusion"] = fused

    rows = []
    completed_by_name = {}
    stats_by_name = {}
    for name, policy in policies.items():
        dev_completed, dev_stats = simulate(calibrated[calibrated["_role"] == "dev"], policy, development_periods)
        fresh_completed, fresh_stats = simulate(calibrated[calibrated["_role"] == "fresh"], policy, fresh_periods)
        completed_by_name[name] = pd.concat([
            dev_completed.assign(_evaluation_role="development"),
            fresh_completed.assign(_evaluation_role="fresh"),
        ], ignore_index=True, sort=False)
        stats_by_name[name] = (dev_stats, fresh_stats)
        rows.append(
            {
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
            }
        )
    variants = pd.DataFrame(rows).sort_values(["development_objective", "development_trades"], ascending=[False, False])
    selected_name = str(variants.iloc[0]["variant"])
    selected_policy = policies[selected_name]
    development, fresh_stats = stats_by_name[selected_name]
    completed = completed_by_name[selected_name]

    summary = {
        "policy": "ML_FIRST_ORDERED_REACHABILITY_ROUTER_V7",
        "selected_variant": selected_name,
        "components": [
            {
                "name": component.name,
                "scope": component.scope,
                "maximum_route_fraction": component.max_fraction,
                "uncertainty_penalty": component.penalty,
                "minimum_expected_log_growth": component.threshold,
                "development_component_score": component.score,
            }
            for component in selected_policy
        ],
        "development": development,
        "fresh": fresh_stats,
        "development_periods": development_periods,
        "fresh_periods": fresh_periods,
        "schema": schema,
        "numeric_features": numeric,
        "categorical_features": categorical,
        "causal_contract": {
            "target_reachability_monotonic_with_distance": True,
            "fill_and_reach_models_grouped_oof_on_development_periods": True,
            "symbol_identity_excluded": True,
            "absolute_prices_and_future_path_excluded": True,
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
    joblib.dump(model_bundle | {"selected_policy": selected_policy, "schema": schema}, args.output / "model_bundle.joblib")
    print(json.dumps(safe(summary), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Apply the frozen v7 ordered policy to an entirely new window panel."""
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


def import_v7():
    path = Path(__file__).resolve().with_name("ordered_reachability_policy.py")
    spec = importlib.util.spec_from_file_location("ordered_reachability_v7_module", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


V7 = import_v7()
CORE = V7.CORE
Component = V7.Component  # Allows joblib to resolve the v7 __main__.Component class if present.


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


def prepare_features(frame: pd.DataFrame, numeric: list[str], categorical: list[str]) -> pd.DataFrame:
    result = frame.copy()
    for column in numeric:
        if column not in result:
            result[column] = np.nan
        result[column] = pd.to_numeric(result[column], errors="coerce")
    for column in categorical:
        if column not in result:
            result[column] = np.nan
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--source-policy", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    source_summary = json.loads((args.source_policy / "summary.json").read_text())
    bundle = joblib.load(args.source_policy / "model_bundle.joblib")
    fill_table = pd.read_csv(args.source_policy / "fill_calibration.csv")
    reach_table = pd.read_csv(args.source_policy / "reach_calibration.csv")

    raw = CORE.read_plans(args.root)
    frame, schema = CORE.normalize(raw)
    frame["_role"] = "fresh"
    frame["_event_key"] = V7.event_key(frame)

    fill_numeric = list(bundle["fill_numeric_features"])
    fill_categorical = list(bundle["fill_categorical_features"])
    reach_numeric = list(bundle["reach_numeric_features"])
    reach_categorical = list(bundle["reach_categorical_features"])
    frame = prepare_features(frame, list(dict.fromkeys(fill_numeric + reach_numeric)), list(dict.fromkeys(fill_categorical + reach_categorical)))
    frame["_raw_fill"] = bundle["fill_model"].predict_proba(frame[fill_numeric + fill_categorical])[:, 1]
    frame["_raw_reach"] = bundle["reach_model"].predict_proba(frame[reach_numeric + reach_categorical])[:, 1]
    frame["_raw_fill"] = frame["_raw_fill"].clip(0.005, 0.995)
    frame["_raw_reach"] = frame["_raw_reach"].clip(0.005, 0.995)
    frame["_mechanism"] = V7.mechanism(frame)
    frame = V7.apply_calibration(frame, fill_table, "_raw_fill", "fill", ["_mechanism"])
    frame = V7.apply_calibration(frame, reach_table, "_raw_reach", "reach", ["_mechanism", "route_fraction"])
    frame["_reach_calibrated"] = V7.enforce_order(frame, "_reach_calibrated")
    frame["_raw_reach"] = V7.enforce_order(frame, "_raw_reach")
    frame["_planned_reward_r"] = pd.to_numeric(frame["_gross_rr"], errors="coerce").fillna(1.0).sub(0.08).clip(lower=0.05)

    components = [
        V7.Component(
            scope=str(item["scope"]),
            max_fraction=float(item["maximum_route_fraction"]),
            penalty=float(item["uncertainty_penalty"]),
            threshold=float(item["minimum_expected_log_growth"]),
            score=float(item["development_component_score"]),
        )
        for item in source_summary["components"]
    ]
    periods = sorted(frame["_period"].unique())
    completed, stats = V7.simulate(frame, components, periods)

    summary = {
        "policy": "ML_FIRST_ORDERED_REACHABILITY_ROUTER_V7_FROZEN_FOURTH_PANEL",
        "source_policy": source_summary["policy"],
        "source_selected_variant": source_summary["selected_variant"],
        "source_development": source_summary["development"],
        "source_third_panel": source_summary["fresh"],
        "components": source_summary["components"],
        "fourth_panel": stats,
        "fourth_periods": periods,
        "schema": schema,
        "frozen_contract": {
            "models_refit": False,
            "calibration_refit": False,
            "components_or_thresholds_changed": False,
            "one_global_pending_or_position": True,
            "one_selection_per_causal_episode": True,
            "risk_fraction": V7.RISK,
        },
    }

    columns = [column for column in completed.columns if not str(column).startswith("_")]
    columns += [
        "_period", "_decision", "_fill_ts", "_exit_ts", "_net_r", "_raw_fill",
        "_raw_reach", "_fill_calibrated", "_reach_calibrated", "_policy_expected_log",
        "_planned_reward_r", "_mechanism", "_component",
    ]
    columns = list(dict.fromkeys(column for column in columns if column in completed.columns))
    completed[columns].to_csv(args.output / "completed_trades.csv", index=False)
    pd.DataFrame(stats["by_period"]).to_csv(args.output / "period_metrics.csv", index=False)
    (args.output / "summary.json").write_text(json.dumps(safe(summary), indent=2, sort_keys=True) + "\n")
    print(json.dumps(safe(summary), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

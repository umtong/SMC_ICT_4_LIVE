#!/usr/bin/env python3
"""Empirical-Bayes action values for coherent first-response liquidity episodes."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pandas as pd

VARIANTS = ("both", "reversal", "continuation")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plans", required=True, type=Path)
    parser.add_argument("--router-runtime", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--risk", type=float, default=0.03)
    return parser.parse_args()


def load_runtime(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("mechanism_runtime", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def classify_mechanism(data: pd.DataFrame, runtime: ModuleType, schema: Any) -> pd.DataFrame:
    frame = data.copy()
    descriptors = runtime.text_stack(frame, (schema.family, schema.phase, *schema.geometry))
    continuation = descriptors.str.contains(r"ACCEPT|CONTINUATION|EXPANSION", regex=True)
    reversal = descriptors.str.contains(r"FAIL|REVERS|RECLAIM|REJECTION|FAKE|TRAP", regex=True)
    acceptance = (
        pd.to_numeric(frame["auction_acceptance_strength"], errors="coerce")
        if "auction_acceptance_strength" in frame.columns
        else pd.Series(np.nan, index=frame.index, dtype=float)
    )
    failure = (
        pd.to_numeric(frame["auction_failure_pressure"], errors="coerce")
        if "auction_failure_pressure" in frame.columns
        else pd.Series(np.nan, index=frame.index, dtype=float)
    )
    frame["_mechanism"] = np.where(
        reversal & (~continuation | failure.gt(acceptance)),
        "reversal",
        np.where(continuation, "continuation", "unclassified"),
    )
    frame = frame.loc[frame["_mechanism"].isin({"reversal", "continuation"})].copy()
    if frame.empty:
        raise RuntimeError("No reversal or continuation mechanism survived coherent response preparation")
    phase = frame[schema.phase].astype(str) if schema.phase else pd.Series("", index=frame.index)
    family = frame[schema.family].astype(str) if schema.family else pd.Series("", index=frame.index)
    geometry_parts = [frame[name].astype(str) for name in schema.geometry if name in frame.columns]
    geometry = geometry_parts[0] if geometry_parts else pd.Series("", index=frame.index)
    for part in geometry_parts[1:]:
        geometry = geometry.str.cat(part, sep="|")
    frame["_state"] = (
        frame["_mechanism"].astype(str)
        .str.cat(phase.fillna(""), sep="|")
        .str.cat(family.fillna(""), sep="|")
        .str.cat(geometry.fillna(""), sep="|")
    )
    return frame


def beta_rate(wins: float, count: float, prior_mean: float, prior_strength: float) -> float:
    return float((wins + prior_mean * prior_strength) / (count + prior_strength))


def probability_table(train: pd.DataFrame) -> tuple[float, dict[str, float], dict[float, float], dict[tuple[str, float], float]]:
    target = train["_target_first"].astype(int)
    global_p = beta_rate(float(target.sum()), float(len(target)), 0.5, 4.0)

    mechanism: dict[str, float] = {}
    for key, group in train.groupby("_mechanism", dropna=False):
        wins = float(group["_target_first"].sum())
        mechanism[str(key)] = beta_rate(wins, float(len(group)), global_p, 12.0)

    fraction: dict[float, float] = {}
    for key, group in train.groupby("_target_fraction", dropna=False):
        if pd.isna(key):
            continue
        wins = float(group["_target_first"].sum())
        fraction[float(key)] = beta_rate(wins, float(len(group)), global_p, 12.0)

    cell: dict[tuple[str, float], float] = {}
    for (state, frac), group in train.groupby(["_state", "_target_fraction"], dropna=False):
        if pd.isna(frac):
            continue
        mech = str(group["_mechanism"].iloc[0])
        parent = 0.5 * mechanism.get(mech, global_p) + 0.5 * fraction.get(float(frac), global_p)
        wins = float(group["_target_first"].sum())
        cell[(str(state), float(frac))] = beta_rate(wins, float(len(group)), parent, 16.0)
    return global_p, mechanism, fraction, cell


def score(train: pd.DataFrame, test: pd.DataFrame, risk: float) -> tuple[pd.DataFrame, dict[str, Any]]:
    global_p, mechanism, fraction, cell = probability_table(train)
    scored = test.copy()
    probability: list[float] = []
    for row in scored.itertuples(index=False):
        state = str(getattr(row, "_state"))
        mech = str(getattr(row, "_mechanism"))
        frac_raw = getattr(row, "_target_fraction")
        frac = float(frac_raw) if pd.notna(frac_raw) else math.nan
        if math.isfinite(frac) and (state, frac) in cell:
            p = cell[(state, frac)]
        elif math.isfinite(frac):
            p = 0.5 * mechanism.get(mech, global_p) + 0.5 * fraction.get(frac, global_p)
        else:
            p = mechanism.get(mech, global_p)
        probability.append(float(np.clip(p, 0.01, 0.99)))
    p = np.asarray(probability, dtype=float)
    win_r = scored["_planned_win_r"].clip(lower=1.0, upper=20.0).to_numpy(float)
    scored["predicted_target_probability"] = p
    scored["predicted_expected_r"] = p * win_r - (1.0 - p)
    scored["predicted_log_growth"] = p * np.log1p(risk * win_r) + (1.0 - p) * math.log1p(-risk)
    return scored, {
        "train_rows": int(len(train)),
        "global_probability": global_p,
        "mechanism_probability": mechanism,
        "fraction_probability": {str(k): v for k, v in fraction.items()},
        "state_fraction_cells": len(cell),
    }


def subset_variant(data: pd.DataFrame, variant: str) -> pd.DataFrame:
    if variant == "both":
        return data.copy()
    return data.loc[data["_mechanism"].eq(variant)].copy()


def walk_forward(
    data: pd.DataFrame,
    variant: str,
    runtime: ModuleType,
    risk: float,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    variant_data = subset_variant(data, variant)
    periods = sorted(variant_data["_period"].unique())
    selected: list[pd.DataFrame] = []
    history: list[dict[str, Any]] = []
    for index in range(1, len(periods)):
        train_periods = periods[:index]
        test_period = periods[index]
        train = variant_data.loc[variant_data["_period"].isin(train_periods)].copy()
        test = variant_data.loc[variant_data["_period"].eq(test_period)].copy()
        if train.empty or test.empty:
            continue
        scored, fit = score(train, test, risk)
        routed, result = runtime.route(scored, risk)
        selected.append(routed)
        history.append(
            {
                "train_periods": train_periods,
                "test_period": test_period,
                "fit": fit,
                "metrics": result,
            }
        )
    combined = pd.concat(selected, ignore_index=True, sort=False) if selected else variant_data.iloc[0:0].copy()
    return combined.sort_values("_entry_time", kind="mergesort"), history


def evaluate_fresh(
    dev: pd.DataFrame,
    fresh: pd.DataFrame,
    variant: str,
    runtime: ModuleType,
    risk: float,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    train = subset_variant(dev, variant)
    test = subset_variant(fresh, variant)
    scored, fit = score(train, test, risk)
    selected, summary = runtime.route(scored, risk)
    return selected, summary, fit


def choose_variant(dev_results: dict[str, tuple[pd.DataFrame, list[dict[str, Any]]]], runtime: ModuleType, risk: float) -> str:
    ranked: list[tuple[float, int, str]] = []
    for variant, (trades, _) in dev_results.items():
        summary = runtime.metrics(trades, risk)
        ranked.append((float(summary["ending_nav"]), int(summary["trades"]), variant))
    ranked.sort(reverse=True)
    return ranked[0][2]


def export_trades(frame: pd.DataFrame, schema: Any, path: Path) -> None:
    columns = [
        name
        for name in (
            "_decision_time",
            "_entry_time",
            "_exit_time",
            "_period",
            "_role",
            "_symbol",
            "_side",
            "_episode",
            "_mechanism",
            "_state",
            "_target_fraction",
            "_planned_win_r",
            "_target_first",
            "_net_r",
            "predicted_target_probability",
            "predicted_expected_r",
            "predicted_log_growth",
            "nav_after",
            schema.family,
            schema.phase,
            *schema.geometry,
        )
        if name and name in frame.columns
    ]
    frame[columns].to_csv(path, index=False)


def main() -> None:
    args = parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    runtime = load_runtime(args.router_runtime.resolve())
    raw = pd.read_csv(args.plans, low_memory=False)
    schema = runtime.infer_schema(raw)
    prepared = runtime.prepare(raw, schema)
    data = classify_mechanism(prepared, runtime, schema)
    dev = data.loc[data["_role"].str.contains("dev")].copy()
    fresh = data.loc[data["_role"].str.contains("fresh")].copy()
    if dev.empty or fresh.empty:
        raise RuntimeError(f"Need dev and fresh rows; dev={len(dev)} fresh={len(fresh)}")

    dev_results = {variant: walk_forward(dev, variant, runtime, args.risk) for variant in VARIANTS}
    chosen = choose_variant(dev_results, runtime, args.risk)

    variants: dict[str, Any] = {}
    fresh_trades: dict[str, pd.DataFrame] = {}
    for variant in VARIANTS:
        dev_trades, history = dev_results[variant]
        selected, fresh_summary, fit = evaluate_fresh(dev, fresh, variant, runtime, args.risk)
        fresh_trades[variant] = selected
        variants[variant] = {
            "development_oof": runtime.metrics(dev_trades, args.risk),
            "development_history": history,
            "fresh": fresh_summary,
            "fresh_by_period": runtime.period_metrics(selected, args.risk).to_dict("records"),
            "final_fit": fit,
        }
        export_trades(dev_trades, schema, output / f"development_{variant}_trades.csv")
        export_trades(selected, schema, output / f"fresh_{variant}_trades.csv")

    selected = fresh_trades[chosen]
    export_trades(selected, schema, output / "selected_fresh_trades.csv")
    runtime.period_metrics(selected, args.risk).to_csv(output / "selected_fresh_period_metrics.csv", index=False)

    summary = {
        "policy": "CAUSAL_FIRST_RESPONSE_EMPIRICAL_ACTION_VALUE",
        "selected_variant_from_development": chosen,
        "risk_fraction": args.risk,
        "eligible_rows": int(len(data)),
        "development_rows": int(len(dev)),
        "fresh_rows": int(len(fresh)),
        "selected_fresh": runtime.metrics(selected, args.risk),
        "selected_fresh_by_period": runtime.period_metrics(selected, args.risk).to_dict("records"),
        "selected_fraction_usage": {
            str(key): int(value)
            for key, value in selected["_target_fraction"].value_counts(dropna=False).sort_index().to_dict().items()
        },
        "variants": variants,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()

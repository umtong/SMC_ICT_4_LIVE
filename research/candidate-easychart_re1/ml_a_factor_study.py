"""Causal plan-factor comparison for EasyChart RE1 ML_a.

EasyChart still fixes entry, stop and target.  ML only ranks completed immutable
plans.  Future bars create research labels and are never features.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from fee_profiles_v5 import FEE_PROFILES
from instruments import CONTRACTS

LABEL = {"TARGET_FIRST": 1, "STOP_FIRST": 0, "AMBIGUOUS_SAME_MINUTE": 0}
LIVE_BLOCKS = {"structure", "geometry", "plan_live"}


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def num(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce")


def load(paths: list[Path]) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for path in paths:
        frame = pd.read_csv(path, low_memory=False)
        if frame.empty:
            continue
        meta = read_json(path.parent / "metrics.json") or read_json(path.parent / "run.json")
        time_col = next((c for c in ("ts_ns", "observed_time_ns", "trigger_time_ns") if c in frame), None)
        if time_col is None:
            continue
        times = pd.to_datetime(num(frame, time_col), unit="ns", utc=True)
        start = pd.to_datetime(meta.get("start"), utc=True) if meta.get("start") else times.min().floor("D")
        end = pd.to_datetime(meta.get("end"), utc=True) if meta.get("end") else times.max().floor("D")
        frame["_period"] = str(meta.get("start") or path.parent.name)
        frame["_period_start"] = start
        frame["_period_days"] = int(meta.get("calendar_days") or ((end - start).days + 1))
        parts.append(frame)
    if not parts:
        return pd.DataFrame()
    data = pd.concat(parts, ignore_index=True, sort=False)
    return data.drop_duplicates(["_period", "plan_id"], keep="last").reset_index(drop=True)


def engineer(data: pd.DataFrame, fee_profile: str, entry_ticks: int, stop_ticks: int) -> pd.DataFrame:
    out = data.copy()
    time_col = next(c for c in ("ts_ns", "observed_time_ns", "trigger_time_ns") if c in out)
    out["_time"] = pd.to_datetime(num(out, time_col), unit="ns", utc=True)
    out["_resolved"] = pd.to_datetime(out.get("counterfactual_resolution_time"), errors="coerce", utc=True)
    out["_y"] = out["counterfactual_outcome"].map(LABEL)
    out = out[out["_y"].notna()].copy()
    out["_y"] = out["_y"].astype(int)

    entry, stop, target = num(out, "entry"), num(out, "stop"), num(out, "target")
    risk = (entry - stop).abs().replace(0.0, np.nan)
    reward = (target - entry).abs()
    out["f_gross_rr"] = num(out, "gross_rr").fillna(reward / risk)
    out["f_risk_fraction"] = risk / entry.abs().replace(0.0, np.nan)
    out["f_reward_fraction"] = reward / entry.abs().replace(0.0, np.nan)
    out["f_overlap_width_r"] = (num(out, "overlap_upper") - num(out, "overlap_lower")).abs() / risk
    plan_ns = num(out, time_col)
    for raw, name in (
        ("setup_observed_time_ns", "f_setup_age_min"),
        ("interaction_time_ns", "f_interaction_age_min"),
        ("trigger_time_ns", "f_trigger_age_min"),
    ):
        out[name] = (plan_ns - num(out, raw)) / 60_000_000_000.0
    h, d, t = num(out, "higher_timeframe_minutes"), num(out, "decision_timeframe_minutes"), num(out, "trigger_timeframe_minutes")
    out["f_higher_decision_ratio"] = h / d.replace(0.0, np.nan)
    out["f_decision_trigger_ratio"] = d / t.replace(0.0, np.nan)
    hour = out["_time"].dt.hour + out["_time"].dt.minute / 60.0
    out["f_hour_sin"] = np.sin(2 * math.pi * hour / 24)
    out["f_hour_cos"] = np.cos(2 * math.pi * hour / 24)

    episode = out.get("causal_event_id", out.get("setup_id", out["plan_id"]))
    out["_episode"] = episode.fillna(out["plan_id"]).astype(str)
    size = out.groupby(["_period", "_episode"])["_episode"].transform("size")
    out["_weight"] = 1.0 / size.clip(lower=1)

    sign = out["side"].map({"LONG": 1.0, "SHORT": -1.0})
    tick = out["symbol"].map({k: float(v.price_increment) for k, v in CONTRACTS.items()})
    fee = float(FEE_PROFILES[fee_profile].taker_rate)
    actual_entry = entry + sign * entry_ticks * tick
    win_exit = target - sign * tick
    loss_exit = stop - sign * stop_ticks * tick
    out["_win_r"] = sign * (win_exit - actual_entry) / risk - fee * (actual_entry.abs() + win_exit.abs()) / risk
    out["_loss_r"] = sign * (loss_exit - actual_entry) / risk - fee * (actual_entry.abs() + loss_exit.abs()) / risk
    out["_actual_r"] = num(out, "counterfactual_net_r_conservative")
    out.loc[out["_actual_r"].isna() & out["_y"].eq(1), "_actual_r"] = out["_win_r"]
    out.loc[out["_actual_r"].isna() & out["_y"].eq(0), "_actual_r"] = out["_loss_r"]
    return out.sort_values(["_time", "symbol", "plan_id"]).reset_index(drop=True)


def blocks(data: pd.DataFrame) -> dict[str, list[str]]:
    structure = [
        "symbol", "side", "family", "scale_name", "scenario_path",
        "higher_zone_kind", "lower_zone_kind", "trigger_zone_kind", "target_zone_kind",
        "higher_strength_ratio", "lower_strength_ratio", "trigger_strength_ratio",
        "source_rule_count", "higher_timeframe_minutes", "decision_timeframe_minutes",
        "trigger_timeframe_minutes",
    ]
    geometry = [
        "f_gross_rr", "f_risk_fraction", "f_reward_fraction", "f_overlap_width_r",
        "f_setup_age_min", "f_interaction_age_min", "f_trigger_age_min",
        "f_higher_decision_ratio", "f_decision_trigger_ratio",
    ]
    clock = ["f_hour_sin", "f_hour_cos"]
    flow = [c for c in data if c.startswith(("trace_flow_", "local_"))]
    cross = [c for c in data if c.startswith(("common_", "residual_", "factor_", "trace_state_", "trace_acceptance"))]

    def valid(items: list[str]) -> list[str]:
        return list(dict.fromkeys(c for c in items if c in data and data[c].notna().any() and data[c].nunique(dropna=True) > 1))

    result = {
        "structure": valid(structure),
        "geometry": valid(geometry),
        "plan_live": valid(structure + geometry + clock),
        "flow": valid(flow),
        "cross_market": valid(cross),
        "all": valid(structure + geometry + clock + flow + cross),
    }
    return {name: cols for name, cols in result.items() if cols}


def model(name: str, train: pd.DataFrame, cols: list[str]) -> Pipeline:
    cats = [c for c in cols if not pd.api.types.is_numeric_dtype(train[c])]
    nums = [c for c in cols if c not in cats]
    transforms = []
    if nums:
        steps: list[tuple[str, Any]] = [("impute", SimpleImputer(strategy="median"))]
        if name == "logit":
            steps.append(("scale", StandardScaler()))
        transforms.append(("num", Pipeline(steps), nums))
    if cats:
        transforms.append(("cat", Pipeline([
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore", min_frequency=2, sparse_output=False)),
        ]), cats))
    classifier: Any
    if name == "logit":
        classifier = LogisticRegression(C=0.25, max_iter=2000, random_state=17)
    else:
        classifier = HistGradientBoostingClassifier(
            learning_rate=0.05, max_iter=160, max_leaf_nodes=7,
            min_samples_leaf=max(8, min(30, len(train) // 15)),
            l2_regularization=2.0, random_state=17,
        )
    return Pipeline([("features", ColumnTransformer(transforms)), ("model", classifier)])


def predict_oos(data: pd.DataFrame, factor_blocks: dict[str, list[str]], min_train: int) -> pd.DataFrame:
    order = list(data.groupby("_period")["_period_start"].min().sort_values().index)
    results = []
    keep = [
        "_period", "_period_days", "_time", "_episode", "_y", "_actual_r",
        "_win_r", "_loss_r", "f_gross_rr", "counterfactual_minutes_to_resolution",
        "plan_id", "symbol", "side", "family", "scenario_path",
    ]
    for i in range(min_train, len(order)):
        train = data[data["_period"].isin(order[:i])].copy()
        test = data[data["_period"].eq(order[i])].copy()
        train = train[train["_resolved"].isna() | train["_resolved"].lt(test["_period_start"].min())]
        if len(train) < 24 or train["_y"].nunique() < 2:
            continue
        for block, cols in factor_blocks.items():
            for model_name in ("logit", "hist_gb"):
                fitted = model(model_name, train, cols)
                try:
                    fitted.fit(train[cols], train["_y"], model__sample_weight=train["_weight"])
                    probability = fitted.predict_proba(test[cols])[:, 1]
                except Exception as exc:
                    print(f"skip {order[i]} {block} {model_name}: {exc}")
                    continue
                part = test[keep].copy()
                part["block"], part["model"] = block, model_name
                part["p_target"] = probability
                part["expected_r"] = probability * part["_win_r"] + (1 - probability) * part["_loss_r"]
                results.append(part)
    return pd.concat(results, ignore_index=True) if results else pd.DataFrame()


def episode_choice(frame: pd.DataFrame) -> pd.DataFrame:
    index = frame.groupby(["_period", "_episode"])["expected_r"].idxmax()
    return frame.loc[index].reset_index(drop=True)


def select_coverage(frame: pd.DataFrame, selection: str) -> pd.DataFrame:
    pieces = []
    for _, group in frame.groupby("_period"):
        ordered = group.sort_values("expected_r", ascending=False)
        if selection == "positive_ev":
            pieces.append(ordered[ordered["expected_r"] > 0])
        else:
            pieces.append(ordered.head(max(1, math.ceil(float(selection) * len(ordered)))))
    return pd.concat(pieces, ignore_index=True) if pieces else frame.iloc[:0]


def economics(frame: pd.DataFrame, days: int) -> dict[str, Any]:
    if frame.empty:
        return {"episodes": 0, "win_rate": None, "sum_r": 0.0, "mean_r": None, "per_day": 0.0, "nav_proxy": 1.0}
    ordered = frame.sort_values("_time")
    nav = (1 + 0.03 * ordered["_actual_r"].clip(lower=-1.5)).clip(lower=0.001).cumprod()
    return {
        "episodes": int(len(ordered)), "win_rate": float(ordered["_y"].mean()),
        "sum_r": float(ordered["_actual_r"].sum()), "mean_r": float(ordered["_actual_r"].mean()),
        "mean_rr": float(ordered["f_gross_rr"].mean()),
        "median_minutes": float(pd.to_numeric(ordered["counterfactual_minutes_to_resolution"], errors="coerce").median()),
        "per_day": float(len(ordered) / max(days, 1)), "nav_proxy": float(nav.iloc[-1]),
    }


def evaluate(predictions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    probability_rows, economic_rows = [], []
    for (block, model_name), group in predictions.groupby(["block", "model"]):
        y, p = group["_y"].to_numpy(), group["p_target"].clip(1e-6, 1 - 1e-6).to_numpy()
        probability_rows.append({
            "block": block, "model": model_name, "plans": len(group),
            "brier": brier_score_loss(y, p), "log_loss": log_loss(y, p, labels=[0, 1]),
            "auc": roc_auc_score(y, p) if len(np.unique(y)) == 2 else np.nan,
        })
        episodes = episode_choice(group)
        days = int(episodes.groupby("_period")["_period_days"].first().sum())
        for selection in ("1.0", "0.8", "0.6", "positive_ev"):
            selected = select_coverage(episodes, selection)
            economic_rows.append({
                "block": block, "model": model_name, "selection": selection,
                "coverage": len(selected) / max(len(episodes), 1), "days": days,
                **economics(selected, days),
            })
    sample = predictions.sort_values(["block", "model"]).drop_duplicates(["_period", "plan_id"])
    first = sample.sort_values(["_time", "symbol", "plan_id"]).groupby(["_period", "_episode"]).head(1)
    days = int(first.groupby("_period")["_period_days"].first().sum())
    return pd.DataFrame(probability_rows), pd.DataFrame(economic_rows), {"days": days, **economics(first, days)}


def choose(table: pd.DataFrame, baseline: dict[str, Any], live_only: bool) -> dict[str, Any] | None:
    candidates = table[table["selection"].isin(["1.0", "0.8", "0.6", "positive_ev"])].copy()
    if live_only:
        candidates = candidates[candidates["block"].isin(LIVE_BLOCKS)]
    candidates = candidates[(candidates["episodes"] >= 8) & (candidates["per_day"] >= 0.6 * baseline["per_day"])]
    if candidates.empty:
        return None
    candidates["score"] = candidates["mean_r"] * np.sqrt(candidates["episodes"]) + 0.15 * candidates["sum_r"]
    best = candidates.sort_values(["score", "sum_r", "per_day"], ascending=False).iloc[0]
    return {k: (None if pd.isna(v) else v.item() if hasattr(v, "item") else v) for k, v in best.items()}


def fit_final(data: pd.DataFrame, factor_blocks: dict[str, list[str]], chosen: dict[str, Any], output: Path) -> dict[str, Any]:
    cols = factor_blocks[str(chosen["block"])]
    fitted = model(str(chosen["model"]), data, cols)
    fitted.fit(data[cols], data["_y"], model__sample_weight=data["_weight"])
    joblib.dump(fitted, output / "ml_a_plan_model.joblib")
    fingerprint = sha256("\n".join(f"{a}|{b}" for a, b in data[["plan_id", "_y"]].itertuples(index=False, name=None)).encode()).hexdigest()
    manifest = {
        "status": "DEVELOPMENT_ONLY", "model": chosen["model"], "block": chosen["block"],
        "selection": chosen["selection"], "features": cols, "rows": len(data),
        "episodes": data[["_period", "_episode"]].drop_duplicates().shape[0],
        "train_start": data["_time"].min().isoformat(), "train_end": data["_time"].max().isoformat(),
        "fingerprint": fingerprint,
    }
    (output / "ml_a_model_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fee-profile", choices=tuple(FEE_PROFILES), default="usd_m_vip0")
    parser.add_argument("--entry-slippage-ticks", type=int, default=2)
    parser.add_argument("--stop-slippage-ticks", type=int, default=2)
    parser.add_argument("--min-train-periods", type=int, default=2)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    data = engineer(load(args.inputs), args.fee_profile, args.entry_slippage_ticks, args.stop_slippage_ticks)
    if len(data) < 30 or data["_period"].nunique() < 3 or data["_y"].nunique() < 2:
        summary = {"status": "INSUFFICIENT_DATA", "plans": len(data), "periods": data["_period"].nunique()}
        (args.output / "factor_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
        return
    factor_blocks = blocks(data)
    predictions = predict_oos(data, factor_blocks, args.min_train_periods)
    predictions.to_csv(args.output / "oos_predictions.csv", index=False)
    probability, economic, baseline = evaluate(predictions)
    probability.to_csv(args.output / "factor_probability_metrics.csv", index=False)
    economic.to_csv(args.output / "factor_economic_metrics.csv", index=False)
    research, deployable = choose(economic, baseline, False), choose(economic, baseline, True)
    manifest = fit_final(data, factor_blocks, deployable, args.output) if deployable else None
    summary = {
        "status": "DEVELOPMENT_FACTOR_STUDY_COMPLETE", "plans": len(data),
        "periods": data["_period"].nunique(),
        "episodes": data[["_period", "_episode"]].drop_duplicates().shape[0],
        "target_rate": data["_y"].mean(), "baseline": baseline,
        "chosen_research": research, "chosen_deployable": deployable,
        "model_manifest": manifest, "factor_blocks": factor_blocks,
        "note": "Counterfactual episode ranking is not a one-position continuous-account result.",
    }
    (args.output / "factor_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

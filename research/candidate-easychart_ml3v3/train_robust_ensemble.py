#!/usr/bin/env python3
"""Fit ML3v3 across non-contiguous periods without optimizing a win-rate target."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from features_ml3v3 import FEATURE_CLIP_RANGES, FEATURE_DEFAULTS, FEATURE_NAMES
from ml1_model import MODEL_SCHEMA, PortableBinaryModel
from robust_ensemble import ENSEMBLE_SCHEMA, PeriodRobustEnsemble
from train_ml1 import _calibrate, _export_tree, _logit

POLICY = (
    "NONCONTIGUOUS_WINDOWS;EQUAL_WINDOW_AND_CAUSAL_EVENT_WEIGHT;SHALLOW_EXTRA_TREES;"
    "ROTATING_PERIOD_EXCLUSION;DISJOINT_PERIOD_CALIBRATION;SYMBOL_ID_NOT_A_FEATURE;"
    "LEAVE_ONE_SYMBOL_OUT_DIAGNOSTIC;NO_PRIOR_POLICY_COMPARATOR;NO_THRESHOLD_SEARCH"
)
RISK = 0.03
COLS = [f"mlf_{name}" for name in FEATURE_NAMES]


def parse_dataset(text: str) -> tuple[str, Path]:
    if "=" not in text:
        raise argparse.ArgumentTypeError("dataset must be WINDOW=/path.csv")
    name, path = text.split("=", 1)
    return name.strip(), Path(path)


def args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", action="append", type=parse_dataset, required=True)
    p.add_argument("--model-output", type=Path, required=True)
    p.add_argument("--report-output", type=Path, required=True)
    p.add_argument("--oof-output", type=Path, required=True)
    p.add_argument("--n-estimators", type=int, default=128)
    p.add_argument("--max-depth", type=int, default=6)
    p.add_argument("--min-samples-leaf", type=int, default=20)
    p.add_argument("--random-state", type=int, default=3907)
    p.add_argument("--minimum-window-rows", type=int, default=60)
    p.add_argument("--minimum-windows", type=int, default=5)
    return p.parse_args()


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load(specs: list[tuple[str, Path]], minimum: int) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    required = {
        "plan_id", "event_group_id", "symbol", "family", "side", "scenario_path",
        "scale_name", "event_time_ns", "label_end_ns", "label",
        "counterfactual_minutes_to_resolution", "counterfactual_net_r_conservative",
        "ml_win_net_r", "ml_loss_net_r", *COLS,
    }
    frames, records, seen = [], [], set()
    for window, path in specs:
        if not window or window in seen:
            raise RuntimeError(f"invalid or duplicate window {window!r}")
        seen.add(window)
        frame = pd.read_csv(path, low_memory=False)
        missing = sorted(required - set(frame.columns))
        if missing:
            raise RuntimeError(f"{window} missing {missing[:12]}")
        frame = frame[frame["label"].notna()].copy()
        if len(frame) < minimum:
            raise RuntimeError(f"{window} has {len(frame)} rows, need {minimum}")
        frame["window_id"] = window
        frame["plan_id"] = window + "::" + frame["plan_id"].astype(str)
        frame["event_group_id"] = window + "::" + frame["event_group_id"].astype(str)
        frames.append(frame)
        records.append({"window_id": window, "path": str(path), "sha256": sha(path), "rows": len(frame)})
    data = pd.concat(frames, ignore_index=True)
    for c in ("event_time_ns", "label_end_ns"):
        data[c] = pd.to_numeric(data[c], errors="raise").astype("int64")
    data["label"] = pd.to_numeric(data["label"], errors="raise").astype(int)
    for name, col in zip(FEATURE_NAMES, COLS, strict=True):
        lo, hi = FEATURE_CLIP_RANGES[name]
        data[col] = pd.to_numeric(data[col], errors="coerce").fillna(FEATURE_DEFAULTS[name]).clip(lo, hi)
    data = data.sort_values(["event_time_ns", "window_id", "symbol", "plan_id"], kind="mergesort")
    order = data.groupby("window_id")["event_time_ns"].min().sort_values().index.tolist()
    return data.reset_index(drop=True), [{**r, "order": order.index(r["window_id"])} for r in records]


def weights(frame: pd.DataFrame) -> np.ndarray:
    count = frame.groupby("event_group_id")["plan_id"].transform("count").to_numpy(float)
    base = 1.0 / np.maximum(count, 1.0)
    out = np.zeros(len(frame))
    windows = frame["window_id"].astype(str).to_numpy()
    for w in set(windows):
        mask = windows == w
        out[mask] = base[mask] / base[mask].sum()
    return out * max(1, len(set(windows)))


def fit_doc(train: pd.DataFrame, calibration: pd.DataFrame, cfg: argparse.Namespace, seed: int, member: str) -> dict[str, Any]:
    if train["label"].nunique() < 2:
        raise RuntimeError(f"{member} training has one class")
    clf = ExtraTreesClassifier(
        n_estimators=cfg.n_estimators, max_depth=cfg.max_depth,
        min_samples_leaf=cfg.min_samples_leaf, max_features="sqrt",
        bootstrap=False, class_weight=None, random_state=seed, n_jobs=-1,
    )
    clf.fit(train[COLS].to_numpy(float), train["label"].to_numpy(int), sample_weight=weights(train))
    positive = int(np.flatnonzero(clf.classes_ == 1)[0])
    raw = clf.predict_proba(calibration[COLS].to_numpy(float))[:, positive]
    y = calibration["label"].to_numpy(int)
    if len(np.unique(y)) >= 2 and np.std(raw) >= 1e-8:
        lr = LogisticRegression(C=0.5, solver="lbfgs", random_state=seed)
        lr.fit(_logit(raw).reshape(-1, 1), y, sample_weight=weights(calibration))
        coefficient, intercept = float(lr.coef_[0, 0]), float(lr.intercept_[0])
    else:
        observed = float(np.clip(y.mean(), 1e-4, 1 - 1e-4))
        predicted = float(np.clip(raw.mean(), 1e-4, 1 - 1e-4))
        coefficient = 1.0
        intercept = math.log(observed / (1 - observed)) - math.log(predicted / (1 - predicted))
    importance = sorted(
        ({"feature": name, "importance": float(value)} for name, value in zip(FEATURE_NAMES, clf.feature_importances_, strict=True)),
        key=lambda item: item["importance"], reverse=True,
    )
    doc = {
        "schema": MODEL_SCHEMA, "model_type": "extra_trees_binary", "status": "trained",
        "model_id": "pending", "feature_names": list(FEATURE_NAMES),
        "feature_defaults": dict(FEATURE_DEFAULTS),
        "feature_clip_ranges": {n: list(FEATURE_CLIP_RANGES[n]) for n in FEATURE_NAMES},
        "trees": [_export_tree(tree, positive) for tree in clf.estimators_],
        "calibration": {"kind": "platt_logit", "coefficient": coefficient, "intercept": intercept},
        "decision": {"kind": "outer_period_robust_log_growth", "member_threshold": None},
        "training": {"policy": POLICY, "member": member, "top_features": importance[:30]},
    }
    doc["model_id"] = PortableBinaryModel.stable_id(doc)
    return doc


def predict(doc: dict[str, Any], frame: pd.DataFrame) -> np.ndarray:
    model = PortableBinaryModel(doc)
    return np.asarray([
        model.probability({name: row[f"mlf_{name}"] for name in FEATURE_NAMES})
        for row in frame[COLS].to_dict(orient="records")
    ])


def metrics(frame: pd.DataFrame, p: np.ndarray) -> dict[str, Any]:
    y = frame["label"].to_numpy(int)
    win = pd.to_numeric(frame["ml_win_net_r"], errors="coerce").to_numpy(float)
    loss = pd.to_numeric(frame["ml_loss_net_r"], errors="coerce").to_numpy(float)
    target_r = win / np.maximum(np.abs(loss), 1e-12)
    expected_r = p * target_r - (1 - p)
    expected_log = p * np.log1p(RISK * target_r) + (1 - p) * math.log(1 - RISK)
    work = frame.copy()
    work["p"] = p
    work["g"] = expected_log
    work["accepted"] = (expected_log > 0) & (expected_r > 0)
    selected = work[work["accepted"]].sort_values(
        ["event_group_id", "g", "p"], ascending=[True, False, False], kind="mergesort"
    ).drop_duplicates("event_group_id")
    realized = pd.to_numeric(selected["counterfactual_net_r_conservative"], errors="coerce").dropna()
    days = max(1, frame["event_date"].nunique()) if "event_date" in frame else 1
    return {
        "rows": len(frame), "event_groups": frame["event_group_id"].nunique(),
        "target_first_rate": float(y.mean()),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, np.clip(p, 1e-8, 1 - 1e-8), labels=[0, 1])),
        "roc_auc": None if len(np.unique(y)) < 2 else float(roc_auc_score(y, p)),
        "selected_event_groups": len(selected), "selected_per_day": len(selected) / days,
        "selected_target_first_rate": None if selected.empty else float(selected["label"].mean()),
        "selected_mean_net_r": None if realized.empty else float(realized.mean()),
        "selected_sum_net_r": float(realized.sum()) if not realized.empty else 0.0,
    }


def duration_priors(data: pd.DataFrame) -> dict[str, Any]:
    work = data.copy()
    work["duration"] = pd.to_numeric(work["counterfactual_minutes_to_resolution"], errors="coerce")
    work = work[np.isfinite(work["duration"]) & (work["duration"] > 0)]
    if work.empty:
        return {"exact": {}, "state": {}, "family": {}, "global": 60.0}
    def mapping(keys: list[str]) -> dict[str, float]:
        per_window = work.groupby(keys + ["window_id"], dropna=False)["duration"].median().reset_index()
        out = {}
        for key, group in per_window.groupby(keys, dropna=False):
            key_tuple = key if isinstance(key, tuple) else (key,)
            if len(group) >= 2:
                out["|".join(map(str, key_tuple))] = float(group["duration"].median())
        return out
    return {
        "exact": mapping(["family", "scenario_path", "scale_name"]),
        "state": mapping(["scenario_path", "scale_name"]),
        "family": mapping(["family"]),
        "global": float(work.groupby("window_id")["duration"].median().median()),
    }


def main() -> None:
    cfg = args()
    if len(cfg.dataset) < cfg.minimum_windows:
        raise RuntimeError("too few non-contiguous development windows")
    data, records = load(cfg.dataset, cfg.minimum_window_rows)
    windows = data.groupby("window_id")["event_time_ns"].min().sort_values().index.tolist()
    if len(windows) < cfg.minimum_windows:
        raise RuntimeError("too few unique windows")

    oof_parts, oof_report = [], {}
    for i, evaluation_window in enumerate(windows):
        calibration_window = windows[(i + 1) % len(windows)]
        train = data[~data["window_id"].isin({evaluation_window, calibration_window})]
        calibration = data[data["window_id"] == calibration_window]
        evaluation = data[data["window_id"] == evaluation_window].copy()
        doc = fit_doc(train, calibration, cfg, cfg.random_state + i, f"oof-{evaluation_window}")
        p = predict(doc, evaluation)
        evaluation["oof_robust_probability"] = p
        oof_parts.append(evaluation)
        oof_report[evaluation_window] = {"calibration_window": calibration_window, "metrics": metrics(evaluation, p)}
    oof = pd.concat(oof_parts).sort_values(["event_time_ns", "window_id", "symbol", "plan_id"])

    members = []
    for i, calibration_window in enumerate(windows):
        train = data[data["window_id"] != calibration_window]
        calibration = data[data["window_id"] == calibration_window]
        doc = fit_doc(train, calibration, cfg, cfg.random_state + 1000 + i, f"period-{calibration_window}")
        members.append({
            "member_id": doc["model_id"], "calibration_window": calibration_window,
            "training_windows": [w for w in windows if w != calibration_window], "model": doc,
        })

    ensemble_doc = PeriodRobustEnsemble.finalize_document({
        "schema": ENSEMBLE_SCHEMA, "status": "trained", "ensemble_id": "pending",
        "feature_names": list(FEATURE_NAMES), "members": members,
        "duration_priors": duration_priors(data),
        "aggregation": {
            "probability_quantile": 0.25, "duration_floor_minutes": 1.0,
            "decision": "positive_after_cost_fixed_risk_expected_log_growth",
            "risk_fraction_runtime_source": "strategy_config",
        },
        "training": {
            "policy": POLICY, "windows": windows, "datasets": records,
            "rows": len(data), "event_groups": data["event_group_id"].nunique(),
            "symbols": sorted(data["symbol"].astype(str).unique()),
            "n_estimators": cfg.n_estimators, "max_depth": cfg.max_depth,
            "min_samples_leaf": cfg.min_samples_leaf, "random_state": cfg.random_state,
        },
    })
    PeriodRobustEnsemble(ensemble_doc).assert_selectable()

    loso = {}
    calibration_window = windows[-1]
    for i, symbol in enumerate(sorted(data["symbol"].astype(str).unique())):
        train = data[(data["symbol"].astype(str) != symbol) & (data["window_id"] != calibration_window)]
        calibration = data[(data["symbol"].astype(str) != symbol) & (data["window_id"] == calibration_window)]
        evaluation = data[data["symbol"].astype(str) == symbol]
        if train["label"].nunique() < 2 or calibration.empty:
            loso[symbol] = {"unavailable": True}
        else:
            doc = fit_doc(train, calibration, cfg, cfg.random_state + 5000 + i, f"loso-{symbol}")
            loso[symbol] = metrics(evaluation, predict(doc, evaluation))

    oof_p = oof["oof_robust_probability"].to_numpy(float)
    report = {
        "policy": POLICY, "ensemble_id": ensemble_doc["ensemble_id"], "windows": windows,
        "datasets": records, "feature_count": len(FEATURE_NAMES),
        "oof": metrics(oof, oof_p), "oof_by_window": oof_report,
        "oof_by_symbol": {
            str(symbol): metrics(group, group["oof_robust_probability"].to_numpy(float))
            for symbol, group in oof.groupby("symbol", sort=True)
        },
        "leave_one_symbol_out": loso,
        "note": "No performance threshold or incumbent-policy comparison was optimized.",
    }
    for path, payload in ((cfg.model_output, ensemble_doc), (cfg.report_output, report)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    cfg.oof_output.parent.mkdir(parents=True, exist_ok=True)
    oof.to_csv(cfg.oof_output, index=False)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

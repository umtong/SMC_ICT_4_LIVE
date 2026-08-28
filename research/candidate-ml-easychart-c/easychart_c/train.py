#!/usr/bin/env python3
"""Memory-bounded training for the EasyChart C causal-response router."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

HERE = Path(__file__).resolve().parent
CANDIDATE = HERE.parent
RESEARCH = CANDIDATE.parent
for path in (
    HERE,
    RESEARCH / "candidate-easychart-ml-system",
    RESEARCH / "candidate-easychart_re1",
    RESEARCH / "candidate-easychart-v5",
    RESEARCH / "candidate-easychart-v3",
):
    sys.path.insert(0, str(path))

import robust_router_system  # noqa: E402,F401
from robust_router_system import (  # noqa: E402
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    ROW_ALIASES,
    row_feature_record,
)
from easychart_c.core import (  # noqa: E402
    DEFAULT_SCORE_QUANTILE,
    EXCLUDED_TRIGGER_KINDS,
    EXCLUDED_HIGHER_ZONE_KINDS,
    FEATURES,
    FIRST_OBJECTIVE_R,
    MAX_TARGET_COST_R,
    MODEL_VERSION,
    feature_frame,
)


def required_columns() -> set[str]:
    columns = set(NUMERIC_FEATURES) | set(CATEGORICAL_FEATURES)
    columns.update(
        {
            "counterfactual_outcome",
            "counterfactual_mfe_r",
            "counterfactual_target_net_r",
            "counterfactual_stop_net_r",
            "counterfactual_minutes_to_resolution",
            "economic_geometry_viable",
            "gross_rr",
            "symbol",
            "side",
            "entry",
            "stop",
            "target",
            "overlap_lower",
            "overlap_upper",
            "observed_time_ns",
            "ts_ns",
            "setup_observed_time_ns",
            "interaction_time_ns",
            "trigger_time_ns",
            "plan_id",
            "causal_event_id",
            "family",
            "mechanism_owner",
        },
    )
    for aliases in ROW_ALIASES.values():
        columns.update(aliases)
    return columns


def environment_from(path: Path) -> str:
    """Return a stable environment label independent of artifact folder prefixes."""

    for part in reversed(path.parts):
        marker = "harvest-"
        if marker in part:
            return part.split(marker, 1)[1].removeprefix("dev-")
        if part.startswith("easychart-c-dev-"):
            return part.removeprefix("easychart-c-dev-")
    return path.parent.name.removeprefix("dev-")


def read_inputs(root: Path) -> pd.DataFrame:
    paths = sorted(root.rglob("counterfactual_plans.csv"))
    if len(paths) != 8:
        raise RuntimeError(f"expected eight development tables, found {len(paths)} under {root}")
    needed = required_columns()
    frames: list[pd.DataFrame] = []
    for path in paths:
        frame = pd.read_csv(
            path,
            usecols=lambda name: name in needed,
            low_memory=False,
        )
        frame["environment"] = environment_from(path)
        metrics_path = path.with_name("metrics.json")
        if metrics_path.exists():
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            calendar_days = int(metrics.get("calendar_days", 0))
        else:
            calendar_days = 0
        if calendar_days <= 0:
            raise RuntimeError(f"missing positive calendar_days beside {path}")
        frame["environment_calendar_days"] = calendar_days
        frames.append(frame)
    return pd.concat(frames, ignore_index=True, sort=False)


def prepare(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    resolved = frame["counterfactual_outcome"].isin(
        ["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE"],
    )
    frame = frame.loc[resolved].copy()
    for name in (
        "gross_rr",
        "counterfactual_mfe_r",
        "counterfactual_target_net_r",
        "counterfactual_stop_net_r",
        "counterfactual_minutes_to_resolution",
        "observed_time_ns",
        "ts_ns",
    ):
        if name in frame:
            frame[name] = pd.to_numeric(frame[name], errors="coerce")
    frame["target_cost_r"] = (
        frame["gross_rr"] - frame["counterfactual_target_net_r"]
    )
    frame = frame[
        frame["gross_rr"].ge(1.0)
        & frame["counterfactual_mfe_r"].notna()
        & frame["counterfactual_stop_net_r"].notna()
        & frame["target_cost_r"].le(MAX_TARGET_COST_R)
    ].copy()
    frame["label"] = frame["counterfactual_mfe_r"].ge(FIRST_OBJECTIVE_R).astype(int)
    frame["first_objective_net_r"] = np.where(
        frame["label"].eq(1),
        FIRST_OBJECTIVE_R - frame["target_cost_r"],
        frame["counterfactual_stop_net_r"],
    )
    records = [row_feature_record(row) for row in frame.to_dict(orient="records")]
    return frame.reset_index(drop=True), records


def new_model(seed: int) -> CatBoostClassifier:
    return CatBoostClassifier(
        iterations=250,
        depth=2,
        learning_rate=0.04,
        l2_leaf_reg=8.0,
        loss_function="Logloss",
        eval_metric="AUC",
        verbose=False,
        random_seed=seed,
        random_strength=0.4,
        bootstrap_type="Bayesian",
        bagging_temperature=0.5,
        auto_class_weights="Balanced",
        thread_count=-1,
        allow_writing_files=False,
    )


def oof_predictions(
    frame: pd.DataFrame,
    matrix: pd.DataFrame,
    *,
    seed: int,
) -> np.ndarray:
    output = np.full(len(frame), np.nan, dtype=np.float64)
    environments = frame["environment"].astype(str).to_numpy()
    cat_features = [matrix.columns.get_loc(name) for name in CATEGORICAL_FEATURES]
    for fold, held in enumerate(sorted(set(environments))):
        train = environments != held
        test = ~train
        model = new_model(seed)
        model.fit(matrix.loc[train], frame.loc[train, "label"], cat_features=cat_features)
        output[test] = model.predict_proba(matrix.loc[test])[:, 1]
    return output


def continuous_slot_summary(
    frame: pd.DataFrame,
    scores: np.ndarray,
    threshold: float,
) -> dict[str, Any]:
    work = frame.copy()
    work["score"] = scores
    work = work[
        work["score"].ge(threshold)
        & ~work["trigger_zone_kind"].astype(str).isin(EXCLUDED_TRIGGER_KINDS)
        & ~work["higher_zone_kind"].astype(str).isin(EXCLUDED_HIGHER_ZONE_KINDS)
    ].sort_values(
        ["environment", "observed_time_ns", "score"],
        ascending=[True, True, False],
    )
    selected: list[Any] = []
    open_until: dict[str, int] = {}
    for row in work.itertuples(index=False):
        environment = str(row.environment)
        observed = int(row.observed_time_ns)
        if observed < open_until.get(environment, -1):
            continue
        selected.append(row)
        minutes = float(row.counterfactual_minutes_to_resolution)
        if not np.isfinite(minutes):
            minutes = 240.0
        open_until[environment] = observed + int(max(1.0, minutes) * 60_000_000_000)
    chosen = pd.DataFrame(selected)
    if chosen.empty:
        raise RuntimeError("OOF policy selected no trades")
    net = chosen["first_objective_net_r"].to_numpy(dtype=float)
    environment_metrics = {
        str(name): {
            "trades": int(len(group)),
            "win_rate": float(group["label"].mean()),
            "mean_net_r": float(group["first_objective_net_r"].mean()),
            "nav_multiple": float(np.prod(1.0 + 0.03 * group["first_objective_net_r"])),
        }
        for name, group in chosen.groupby("environment")
    }
    calendar_days = int(
        frame.groupby("environment")["environment_calendar_days"].max().sum()
    )
    return {
        "trades": int(len(chosen)),
        "calendar_days": calendar_days,
        "trades_per_day": float(len(chosen) / calendar_days),
        "win_rate": float(chosen["label"].mean()),
        "mean_net_r": float(net.mean()),
        "profit_factor": float(net[net > 0].sum() / -net[net < 0].sum()),
        "nav_multiple": float(np.prod(1.0 + 0.03 * net)),
        "average_planned_rr": float(FIRST_OBJECTIVE_R),
        "average_hold_minutes": float(chosen["counterfactual_minutes_to_resolution"].mean()),
        "positive_environments": int(
            sum(item["mean_net_r"] > 0 for item in environment_metrics.values())
        ),
        "environments": environment_metrics,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--model-output", type=Path, required=True)
    parser.add_argument("--metadata-output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260828)
    args = parser.parse_args()

    frame, records = prepare(read_inputs(args.input_root))
    matrix = feature_frame(records)
    cat_features = [matrix.columns.get_loc(name) for name in CATEGORICAL_FEATURES]
    oof = oof_predictions(frame, matrix, seed=args.seed)
    threshold = float(np.quantile(oof[np.isfinite(oof)], DEFAULT_SCORE_QUANTILE))
    oof_summary = continuous_slot_summary(frame, oof, threshold)

    model = new_model(args.seed)
    model.fit(matrix, frame["label"], cat_features=cat_features)
    args.model_output.parent.mkdir(parents=True, exist_ok=True)
    model.save_model(str(args.model_output))
    trained_through = int(
        pd.to_numeric(
            frame["observed_time_ns"].fillna(frame.get("ts_ns")),
            errors="coerce",
        ).max()
    )
    metadata = {
        "model_version": MODEL_VERSION,
        "features": list(FEATURES),
        "categorical_features": list(CATEGORICAL_FEATURES),
        "probability_threshold": threshold,
        "score_quantile": DEFAULT_SCORE_QUANTILE,
        "first_objective_r": FIRST_OBJECTIVE_R,
        "max_target_cost_r": MAX_TARGET_COST_R,
        "excluded_trigger_kinds": list(EXCLUDED_TRIGGER_KINDS),
        "excluded_higher_zone_kinds": list(EXCLUDED_HIGHER_ZONE_KINDS),
        "risk_fraction": 0.03,
        "trained_through_ns": trained_through,
        "development_rows": int(len(frame)),
        "development_target_rate": float(frame["label"].mean()),
        "environments": sorted(frame["environment"].astype(str).unique()),
        "symbols": sorted(frame["symbol"].astype(str).unique()),
        "label": "ONE_STRUCTURAL_RISK_UNIT_TRADES_BEFORE_CAUSAL_STOP",
        "feature_information_time": "PLAN_EMISSION_OR_EARLIER_ONLY",
    }
    args.metadata_output.parent.mkdir(parents=True, exist_ok=True)
    args.metadata_output.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    diagnostics = {
        "metadata": metadata,
        "oof": {
            "roc_auc": float(roc_auc_score(frame["label"], oof)),
            "brier": float(brier_score_loss(frame["label"], oof)),
            "log_loss": float(log_loss(frame["label"], oof)),
            "policy": oof_summary,
        },
    }
    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    args.summary_output.write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

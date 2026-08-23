#!/usr/bin/env python3
"""Blocked destination/action learning and one-account routing.

The model does not invent direction from a raw return target.  It first estimates
whether the action-side liquidity destination is reached before the opposing pool,
then estimates whether the complete immutable trade reaches its target before its
invalidation after costs.  Period and symbol are never model inputs.  Every
development prediction is produced by a model which did not train on that period.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence
import json
import math
import re

import numpy as np
import pandas as pd

from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, RobustScaler


RISK_FRACTION = 0.03
PERIOD_PATTERN = re.compile(r"((?:dev|eval)-\d{4}-[a-z]{3})")

LABEL_TOKENS = (
    "outcome",
    "net_r",
    "fill_state",
    "fill_index",
    "fill_time",
    "resolution",
    "holding",
    "entry_wait",
    "mfe",
    "mae",
    "destination_label",
    "destination_resolution",
    "actual_entry",
)
IDENTITY_TOKENS = (
    "action_id",
    "episode_id",
    "state_id",
    "level_id",
    "diagnostic_",
    "time_ns",
    "_index",
)
ABSOLUTE_PRICE_COLUMNS = {
    "entry",
    "stop",
    "target",
    "source_price",
    "source_lower",
    "source_upper",
    "upper_price",
    "lower_price",
}
EXPLICIT_EXCLUDE = {
    "symbol",
    "period",
    "side",
    "action_side",
    "source_side",
    "objective_id",
    "source_level_id",
}


@dataclass
class FoldPrediction:
    probability: np.ndarray
    disagreement: np.ndarray


def _period_from_path(path: Path) -> str:
    for part in path.parts[::-1]:
        match = PERIOD_PATTERN.search(part.lower())
        if match:
            return match.group(1)
    raise RuntimeError(f"period not encoded in artifact path: {path}")


def _read_universes(root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    action_frames: list[pd.DataFrame] = []
    state_frames: list[pd.DataFrame] = []
    for path in root.rglob("coherent_actions.csv"):
        frame = pd.read_csv(path)
        if frame.empty:
            continue
        frame["period"] = _period_from_path(path)
        action_frames.append(frame)
    for path in root.rglob("destination_states.csv"):
        frame = pd.read_csv(path)
        if frame.empty:
            continue
        frame["period"] = _period_from_path(path)
        state_frames.append(frame)
    if not action_frames:
        raise RuntimeError(f"no coherent action files found below {root}")
    if not state_frames:
        raise RuntimeError(f"no destination state files found below {root}")
    actions = pd.concat(action_frames, ignore_index=True, sort=False)
    states = pd.concat(state_frames, ignore_index=True, sort=False)
    actions = actions.drop_duplicates(["period", "action_id"], keep="last").reset_index(drop=True)
    states = states.drop_duplicates(["period", "state_id"], keep="last").reset_index(drop=True)
    return actions, states


def _feature_columns(frame: pd.DataFrame, *, keep_economics: bool) -> list[str]:
    columns: list[str] = []
    for column in frame.columns:
        name = str(column)
        lower = name.lower()
        if name in EXPLICIT_EXCLUDE or name in ABSOLUTE_PRICE_COLUMNS:
            continue
        if any(token in lower for token in IDENTITY_TOKENS):
            continue
        if any(token in lower for token in LABEL_TOKENS):
            continue
        if not keep_economics and name in {
            "gross_rr",
            "risk_bps",
            "target_bps",
            "target_net_r",
            "stop_net_r",
            "post_cost_reward_risk",
            "post_cost_break_even_probability",
            "round_trip_cost_r_target",
            "actual_target_net_r",
            "actual_stop_net_r",
        }:
            continue
        series = frame[name]
        if series.notna().sum() < max(20, int(0.15 * len(frame))):
            continue
        if series.nunique(dropna=True) <= 1:
            continue
        if pd.api.types.is_numeric_dtype(series):
            finite = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
            if finite.notna().sum() < max(20, int(0.15 * len(frame))):
                continue
        else:
            if series.nunique(dropna=True) > 64:
                continue
        columns.append(name)
    return sorted(columns)


def _preprocessor(frame: pd.DataFrame, columns: Sequence[str]) -> ColumnTransformer:
    numeric = [column for column in columns if pd.api.types.is_numeric_dtype(frame[column])]
    categorical = [column for column in columns if column not in numeric]
    transformers: list[tuple[str, Any, list[str]]] = []
    if numeric:
        transformers.append(
            (
                "num",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
                        ("scale", RobustScaler(quantile_range=(10.0, 90.0))),
                    ]
                ),
                numeric,
            )
        )
    if categorical:
        transformers.append(
            (
                "cat",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        (
                            "onehot",
                            OneHotEncoder(
                                handle_unknown="ignore",
                                min_frequency=2,
                                sparse_output=False,
                            ),
                        ),
                    ]
                ),
                categorical,
            )
        )
    return ColumnTransformer(transformers, remainder="drop", sparse_threshold=0.0)


def _model_templates(seed: int) -> list[Any]:
    return [
        ExtraTreesClassifier(
            n_estimators=420,
            max_depth=7,
            min_samples_leaf=10,
            max_features=0.65,
            class_weight="balanced_subsample",
            random_state=seed,
            n_jobs=-1,
        ),
        HistGradientBoostingClassifier(
            max_iter=220,
            learning_rate=0.045,
            max_leaf_nodes=15,
            min_samples_leaf=18,
            l2_regularization=3.0,
            random_state=seed + 101,
        ),
        LogisticRegression(
            C=0.18,
            class_weight="balanced",
            max_iter=2400,
            solver="liblinear",
            random_state=seed + 211,
        ),
    ]


def _fit_raw_ensemble(
    train: pd.DataFrame,
    target: np.ndarray,
    test: pd.DataFrame,
    columns: Sequence[str],
    seed: int,
) -> tuple[np.ndarray, np.ndarray]:
    pre = _preprocessor(train, columns)
    x_train = pre.fit_transform(train[list(columns)])
    x_test = pre.transform(test[list(columns)])
    probabilities: list[np.ndarray] = []
    for template in _model_templates(seed):
        model = clone(template)
        model.fit(x_train, target)
        probability = model.predict_proba(x_test)[:, 1]
        probabilities.append(np.asarray(probability, dtype=float))
    matrix = np.column_stack(probabilities)
    return matrix.mean(axis=1), matrix.std(axis=1, ddof=0)


def _inner_calibration_predictions(
    frame: pd.DataFrame,
    target: np.ndarray,
    groups: np.ndarray,
    columns: Sequence[str],
    seed: int,
) -> np.ndarray:
    unique = np.unique(groups)
    if len(unique) < 2:
        raw, _ = _fit_raw_ensemble(frame, target, frame, columns, seed)
        return raw
    splitter = GroupKFold(n_splits=min(4, len(unique)))
    output = np.full(len(frame), np.nan, dtype=float)
    for fold, (train_index, test_index) in enumerate(splitter.split(frame, target, groups)):
        if len(np.unique(target[train_index])) < 2:
            output[test_index] = float(np.mean(target[train_index]))
            continue
        raw, _ = _fit_raw_ensemble(
            frame.iloc[train_index],
            target[train_index],
            frame.iloc[test_index],
            columns,
            seed + 1000 * (fold + 1),
        )
        output[test_index] = raw
    missing = ~np.isfinite(output)
    output[missing] = float(np.mean(target))
    return output


def _fit_calibrated(
    train: pd.DataFrame,
    target: np.ndarray,
    groups: np.ndarray,
    test: pd.DataFrame,
    columns: Sequence[str],
    seed: int,
) -> FoldPrediction:
    if len(np.unique(target)) < 2:
        base = np.full(len(test), float(np.mean(target)), dtype=float)
        return FoldPrediction(base, np.zeros(len(test), dtype=float))
    inner = _inner_calibration_predictions(train, target, groups, columns, seed)
    calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.002, y_max=0.998)
    calibrator.fit(inner, target)
    raw, disagreement = _fit_raw_ensemble(train, target, test, columns, seed + 991)
    calibrated = np.asarray(calibrator.predict(raw), dtype=float)
    return FoldPrediction(np.clip(calibrated, 0.002, 0.998), disagreement)


def _blocked_predictions(
    frame: pd.DataFrame,
    target: np.ndarray,
    columns: Sequence[str],
    *,
    development_mask: np.ndarray,
    seed: int,
) -> tuple[np.ndarray, np.ndarray]:
    probabilities = np.full(len(frame), np.nan, dtype=float)
    disagreements = np.full(len(frame), np.nan, dtype=float)
    development = frame.loc[development_mask].copy()
    dev_target = target[development_mask]
    dev_groups = development.period.astype(str).to_numpy()
    for fold, period in enumerate(sorted(development.period.astype(str).unique())):
        test_mask = development.period.astype(str).eq(period).to_numpy()
        train_mask = ~test_mask
        prediction = _fit_calibrated(
            development.loc[train_mask],
            dev_target[train_mask],
            dev_groups[train_mask],
            development.loc[test_mask],
            columns,
            seed + fold * 101,
        )
        original_indices = development.index.to_numpy()[test_mask]
        probabilities[original_indices] = prediction.probability
        disagreements[original_indices] = prediction.disagreement

    evaluation_mask = ~development_mask
    if evaluation_mask.any():
        prediction = _fit_calibrated(
            development,
            dev_target,
            dev_groups,
            frame.loc[evaluation_mask],
            columns,
            seed + 100_003,
        )
        probabilities[evaluation_mask] = prediction.probability
        disagreements[evaluation_mask] = prediction.disagreement
    return probabilities, disagreements


def _destination_target(states: pd.DataFrame) -> np.ndarray:
    label = states.destination_label.astype(str)
    long_side = states.action_side.astype(str).eq("LONG")
    aligned = (long_side & label.eq("UPPER_FIRST")) | (~long_side & label.eq("LOWER_FIRST"))
    return aligned.astype(int).to_numpy()


def _resolved_destination_mask(states: pd.DataFrame) -> np.ndarray:
    return states.destination_label.astype(str).isin(["UPPER_FIRST", "LOWER_FIRST"]).to_numpy()


def _resolved_action_mask(actions: pd.DataFrame) -> np.ndarray:
    return actions.outcome.astype(str).isin(
        ["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE", "TIME_EXIT"]
    ).to_numpy()


def _attach_destination_predictions(
    actions: pd.DataFrame,
    states: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    resolved = _resolved_destination_mask(states)
    modeling = states.loc[resolved].copy().reset_index(drop=True)
    target = _destination_target(modeling)
    development = modeling.period.astype(str).str.startswith("dev-").to_numpy()
    columns = _feature_columns(modeling, keep_economics=False)
    probability, disagreement = _blocked_predictions(
        modeling,
        target,
        columns,
        development_mask=development,
        seed=23017,
    )
    modeling["destination_probability"] = probability
    modeling["destination_disagreement"] = disagreement
    mapping = modeling[["period", "state_id", "destination_probability", "destination_disagreement"]]
    output = actions.merge(mapping, on=["period", "state_id"], how="left")
    base = float(np.mean(target[development])) if development.any() else float(np.mean(target))
    output["destination_probability"] = pd.to_numeric(
        output.destination_probability, errors="coerce"
    ).fillna(base)
    output["destination_disagreement"] = pd.to_numeric(
        output.destination_disagreement, errors="coerce"
    ).fillna(0.25)
    summary = {
        "resolved_states": int(len(modeling)),
        "development_states": int(development.sum()),
        "feature_count": int(len(columns)),
        "development_base_rate": base,
        "oof_accuracy_at_half": float(
            ((modeling.loc[development, "destination_probability"] >= 0.5).astype(int) == target[development]).mean()
        ) if development.any() else None,
        "features": list(columns),
    }
    return output, summary


def _action_predictions(actions: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    resolved = _resolved_action_mask(actions)
    modeling = actions.loc[resolved].copy().reset_index(drop=True)
    target = modeling.outcome.astype(str).eq("TARGET_FIRST").astype(int).to_numpy()
    development = modeling.period.astype(str).str.startswith("dev-").to_numpy()
    columns = _feature_columns(modeling, keep_economics=True)
    probability, disagreement = _blocked_predictions(
        modeling,
        target,
        columns,
        development_mask=development,
        seed=71237,
    )
    modeling["action_probability"] = probability
    modeling["action_disagreement"] = disagreement
    output = actions.merge(
        modeling[["period", "action_id", "action_probability", "action_disagreement"]],
        on=["period", "action_id"],
        how="left",
    )
    base = float(np.mean(target[development])) if development.any() else float(np.mean(target))
    output["action_probability"] = pd.to_numeric(output.action_probability, errors="coerce").fillna(base)
    output["action_disagreement"] = pd.to_numeric(output.action_disagreement, errors="coerce").fillna(0.25)
    summary = {
        "resolved_actions": int(len(modeling)),
        "development_actions": int(development.sum()),
        "feature_count": int(len(columns)),
        "development_base_rate": base,
        "oof_accuracy_at_half": float(
            ((modeling.loc[development, "action_probability"] >= 0.5).astype(int) == target[development]).mean()
        ) if development.any() else None,
        "features": list(columns),
    }
    return output, summary


def _score_actions(actions: pd.DataFrame) -> pd.DataFrame:
    output = actions.copy()
    target_r = pd.to_numeric(output.actual_target_net_r, errors="coerce")
    stop_r = pd.to_numeric(output.actual_stop_net_r, errors="coerce")
    p_destination = pd.to_numeric(output.destination_probability, errors="coerce").clip(0.002, 0.998)
    p_action = pd.to_numeric(output.action_probability, errors="coerce").clip(0.002, 0.998)
    uncertainty = (
        0.55 * pd.to_numeric(output.action_disagreement, errors="coerce").fillna(0.25)
        + 0.35 * pd.to_numeric(output.destination_disagreement, errors="coerce").fillna(0.25)
        + 0.10 * (1.0 - (2.0 * (p_destination - 0.5)).abs())
    )
    combined = np.sqrt(p_destination * p_action)
    conservative = (combined - uncertainty).clip(0.002, 0.998)
    output["combined_probability"] = combined
    output["selection_uncertainty"] = uncertainty
    output["conservative_probability"] = conservative
    output["break_even_probability_actual"] = (-stop_r / (target_r - stop_r)).replace([np.inf, -np.inf], np.nan)
    output["robust_expected_r"] = conservative * target_r + (1.0 - conservative) * stop_r
    output["expected_log_growth"] = (
        conservative * np.log1p(RISK_FRACTION * target_r.clip(lower=-0.999 / RISK_FRACTION))
        + (1.0 - conservative) * np.log1p(RISK_FRACTION * stop_r.clip(lower=-0.999 / RISK_FRACTION))
    )
    return output


def _one_account(actions: pd.DataFrame, subset: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = actions[
        actions.period.astype(str).str.startswith(subset)
        & actions.fill_state.astype(str).eq("FILLED_MARKET_NEXT_OPEN")
        & pd.to_numeric(actions.robust_expected_r, errors="coerce").gt(0.0)
        & actions.outcome.astype(str).isin(["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE", "TIME_EXIT"])
    ].copy()
    if frame.empty:
        return frame, {
            "trades": 0,
            "wins": 0,
            "win_rate": None,
            "mean_net_r": None,
            "ending_nav": 100_000.0,
            "maximum_drawdown": 0.0,
        }
    frame = frame.sort_values(
        ["fill_time_ns", "expected_log_growth", "source_timeframe_minutes", "action_id"],
        ascending=[True, False, False, True],
    )
    # One immutable action owns each causal source interaction.
    frame = frame.sort_values("expected_log_growth", ascending=False).drop_duplicates(
        ["period", "episode_id"], keep="first"
    ).sort_values(["fill_time_ns", "expected_log_growth"], ascending=[True, False])
    selected: list[pd.Series] = []
    busy_until = -1
    last_clock = -1
    for fill_time, group in frame.groupby("fill_time_ns", sort=True):
        fill_time = int(fill_time)
        if fill_time <= busy_until:
            continue
        candidate = group.sort_values(
            ["expected_log_growth", "robust_expected_r", "source_timeframe_minutes", "action_id"],
            ascending=[False, False, False, True],
        ).iloc[0]
        selected.append(candidate)
        busy_until = int(candidate.resolution_time_ns)
        last_clock = fill_time
    output = pd.DataFrame(selected).reset_index(drop=True) if selected else frame.iloc[0:0].copy()
    nav = 100_000.0
    peak = nav
    max_drawdown = 0.0
    nav_before: list[float] = []
    nav_after: list[float] = []
    for _, row in output.iterrows():
        nav_before.append(nav)
        result = _safe_float(row.net_r, 0.0)
        nav *= max(1e-9, 1.0 + RISK_FRACTION * result)
        peak = max(peak, nav)
        max_drawdown = max(max_drawdown, 1.0 - nav / peak)
        nav_after.append(nav)
    output["nav_before"] = nav_before
    output["nav_after"] = nav_after
    wins = int(output.outcome.astype(str).eq("TARGET_FIRST").sum())
    result = pd.to_numeric(output.net_r, errors="coerce")
    summary = {
        "trades": int(len(output)),
        "wins": wins,
        "win_rate": wins / len(output) if len(output) else None,
        "mean_net_r": float(result.mean()) if len(output) else None,
        "median_net_r": float(result.median()) if len(output) else None,
        "ending_nav": float(nav),
        "maximum_drawdown": float(max_drawdown),
        "by_period": {
            str(period): {
                "trades": int(len(group)),
                "wins": int(group.outcome.astype(str).eq("TARGET_FIRST").sum()),
                "win_rate": float(group.outcome.astype(str).eq("TARGET_FIRST").mean()),
                "mean_net_r": float(pd.to_numeric(group.net_r, errors="coerce").mean()),
            }
            for period, group in output.groupby("period")
        },
        "by_branch": {
            str(branch): {
                "trades": int(len(group)),
                "win_rate": float(group.outcome.astype(str).eq("TARGET_FIRST").mean()),
                "mean_net_r": float(pd.to_numeric(group.net_r, errors="coerce").mean()),
            }
            for branch, group in output.groupby("narrative_branch")
        },
    }
    return output, summary


def _safe_float(value: Any, default: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _nearest_historical_cases(
    scored: pd.DataFrame,
    selected: pd.DataFrame,
    output: Path,
) -> None:
    causal_columns = _feature_columns(scored, keep_economics=True)
    numeric = [column for column in causal_columns if pd.api.types.is_numeric_dtype(scored[column])]
    if not numeric or selected.empty:
        pd.DataFrame().to_csv(output, index=False)
        return
    development = scored[scored.period.astype(str).str.startswith("dev-")].copy()
    if development.empty:
        pd.DataFrame().to_csv(output, index=False)
        return
    med = development[numeric].apply(pd.to_numeric, errors="coerce").median()
    mad = (development[numeric].apply(pd.to_numeric, errors="coerce") - med).abs().median()
    scale = (1.4826 * mad).replace(0.0, 1.0).fillna(1.0)
    dev_matrix = ((development[numeric].apply(pd.to_numeric, errors="coerce").fillna(med) - med) / scale).clip(-8, 8).to_numpy(float)
    records: list[dict[str, Any]] = []
    for _, row in selected.iterrows():
        vector = ((pd.to_numeric(row[numeric], errors="coerce").fillna(med) - med) / scale).clip(-8, 8).to_numpy(float)
        distance = np.sqrt(np.mean((dev_matrix - vector) ** 2, axis=1))
        order = np.argsort(distance)
        rank = 0
        for position in order:
            case = development.iloc[int(position)]
            if str(case.period) == str(row.period):
                continue
            rank += 1
            records.append(
                {
                    "selected_action_id": row.action_id,
                    "selected_period": row.period,
                    "neighbor_rank": rank,
                    "distance": float(distance[position]),
                    "neighbor_action_id": case.action_id,
                    "neighbor_period": case.period,
                    "neighbor_branch": case.narrative_branch,
                    "neighbor_setup_kind": case.setup_kind,
                    "neighbor_location_kind": case.location_kind,
                    "neighbor_response_kind": case.response_kind,
                    "neighbor_outcome": case.outcome,
                    "neighbor_net_r": case.net_r,
                }
            )
            if rank >= 12:
                break
    pd.DataFrame(records).to_csv(output, index=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    actions, states = _read_universes(args.root)
    actions, destination_summary = _attach_destination_predictions(actions, states)
    actions, action_summary = _action_predictions(actions)
    scored = _score_actions(actions)
    scored.to_csv(args.output / "scored_action_universe.csv", index=False)

    dev_trades, dev_summary = _one_account(scored, "dev-")
    eval_trades, eval_summary = _one_account(scored, "eval-")
    dev_trades.to_csv(args.output / "development_oof_account_trades.csv", index=False)
    eval_trades.to_csv(args.output / "evaluation_account_trades.csv", index=False)
    selected = pd.concat([dev_trades, eval_trades], ignore_index=True, sort=False)
    _nearest_historical_cases(scored, selected, args.output / "selected_trade_neighbors.csv")

    summary = {
        "destination_model": destination_summary,
        "action_model": action_summary,
        "development_oof_account": dev_summary,
        "evaluation_account": eval_summary,
        "action_universe": int(len(actions)),
        "state_universe": int(len(states)),
        "selection_rule": "positive conservative expected R after direction and action uncertainty; one global position",
        "symbol_in_model": False,
        "period_in_model": False,
        "risk_fraction": RISK_FRACTION,
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

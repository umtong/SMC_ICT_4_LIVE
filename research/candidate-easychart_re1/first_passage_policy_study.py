#!/usr/bin/env python3
"""Cross-regime first-passage and selective-coverage study.

This is research-only. It consumes future-labelled counterfactual plans after
strategy execution and never exports fitted thresholds to the live strategy.
The purpose is to discover whether a causal state variable transfers across
periods while preserving enough opportunity coverage.

The study deliberately borrows from selective prediction:
- deterministic rejection of economically impossible geometry;
- risk/coverage curves instead of a single accuracy number;
- leave-one-period-out threshold selection;
- worst-training-period mean R as the primary selection objective.

No selected-plan results are interpreted as a continuous account. Plans can
overlap and still require the production global-slot router in a later test.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


RESEARCH_ONLY_POLICY = (
    "RESEARCH_ONLY:ALL_FUTURE_LABELS_AND_SELECTED_THRESHOLDS_ARE_DEVELOPMENT_"
    "ARTIFACTS_AND_ARE_NEVER_IMPORTED_BY_THE_TRADING_STRATEGY"
)
SELECTIVE_COVERAGE_POLICY = (
    "EXTERNAL_METHOD:ECONOMICALLY_VIABLE_PLANS_ARE_RANKED_BY_CAUSAL_EVIDENCE_"
    "AND_EVALUATED_AS_A_RISK_COVERAGE_CURVE_WITH_LEAVE_ONE_PERIOD_OUT_SELECTION"
)
NON_ACCOUNT_POLICY = (
    "VALIDITY:COUNTERFACTUAL_PLAN_COVERAGE_IS_NOT_ACCOUNT_TRADE_FREQUENCY_OR_NAV_"
    "BECAUSE_OVERLAPPING_PLANS_HAVE_NOT_PASSED_GLOBAL_SINGLE_POSITION_ARBITRATION"
)

PERIOD_TOKENS = (
    "may-2024",
    "february-2025",
    "live-examples-2025",
    "february-2026",
)
COVERAGES = (0.15, 0.25, 0.35, 0.50, 0.70, 1.00)
MIN_SCOPE_TOTAL = 24
MIN_SCOPE_PERIOD = 5
MIN_FEATURE_PERIOD_FRACTION = 0.60


@dataclass(frozen=True)
class CandidatePolicy:
    feature: str
    direction: int
    threshold: float
    target_coverage: float
    robust_train_mean_r: float
    pooled_train_mean_r: float
    train_selected: int
    train_available: int
    train_min_oriented_auc: float | None


def _period_name(path: Path) -> str:
    text = "/".join(path.parts)
    for token in PERIOD_TOKENS:
        if token in text:
            return token
    return path.parent.name


def _discover(root: Path) -> list[Path]:
    paths = sorted(root.rglob("counterfactual_plans.csv"))
    if not paths:
        raise FileNotFoundError(
            f"no counterfactual_plans.csv found under {root}"
        )
    return paths


def _load(paths: Iterable[Path]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in paths:
        frame = pd.read_csv(path, low_memory=False)
        if frame.empty:
            continue
        frame["period"] = _period_name(path)
        frame["source_path"] = str(path)
        frames.append(frame)
    if not frames:
        raise RuntimeError("all counterfactual plan files were empty")
    data = pd.concat(frames, ignore_index=True, sort=False)
    required = {
        "plan_id",
        "period",
        "symbol",
        "family",
        "side",
        "counterfactual_outcome",
        "counterfactual_net_r_conservative",
        "economic_geometry_viable",
    }
    missing = sorted(required - set(data.columns))
    if missing:
        raise KeyError(f"counterfactual inputs missing columns: {missing}")
    data["plan_key"] = (
        data["period"].astype(str)
        + "::"
        + data["plan_id"].astype(str)
    )
    duplicate = data[data["plan_key"].duplicated(keep=False)]
    if not duplicate.empty:
        raise RuntimeError(
            "duplicate period-scoped counterfactual plan identities:\n"
            + duplicate[
                ["period", "plan_id", "symbol", "family"]
            ].head(40).to_string(index=False)
        )
    data["net_r"] = pd.to_numeric(
        data["counterfactual_net_r_conservative"],
        errors="coerce",
    )
    data["target_first"] = (
        data["counterfactual_outcome"] == "TARGET_FIRST"
    ).astype(float)
    data.loc[
        data["counterfactual_outcome"] == "UNRESOLVED",
        "target_first",
    ] = np.nan
    data["fast_stop_10m"] = (
        data["counterfactual_outcome"].isin(
            ["STOP_FIRST", "AMBIGUOUS_SAME_MINUTE"],
        )
        & (
            pd.to_numeric(
                data.get("counterfactual_minutes_to_resolution"),
                errors="coerce",
            )
            <= 10.0
        )
    )
    data["side_sign"] = data["side"].map(
        {"LONG": 1.0, "SHORT": -1.0},
    )
    if data["side_sign"].isna().any():
        bad = sorted(data.loc[data["side_sign"].isna(), "side"].unique())
        raise ValueError(f"unknown sides: {bad}")
    if "ts_ns" in data:
        data["plan_time"] = pd.to_datetime(
            pd.to_numeric(data["ts_ns"], errors="coerce"),
            unit="ns",
            utc=True,
        )
    elif "observed_time_ns" in data:
        data["plan_time"] = pd.to_datetime(
            pd.to_numeric(data["observed_time_ns"], errors="coerce"),
            unit="ns",
            utc=True,
        )
    else:
        data["plan_time"] = pd.NaT
    return _aligned_features(data)


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame[column], errors="coerce")


def _aligned_features(data: pd.DataFrame) -> pd.DataFrame:
    output = data.copy()
    columns = list(output.columns)

    # Directional fractions and breadth use the matching side, not a fitted sign.
    for column in columns:
        if "positive_fraction" in column:
            negative = column.replace("positive_fraction", "negative_fraction")
            if negative in output:
                name = "aligned_" + column.replace(
                    "positive_fraction",
                    "direction_fraction",
                )
                output[name] = np.where(
                    output["side_sign"] > 0.0,
                    _numeric(output, column),
                    _numeric(output, negative),
                )
        if "positive_breadth" in column:
            negative = column.replace("positive_breadth", "negative_breadth")
            if negative in output:
                name = "aligned_" + column.replace(
                    "positive_breadth",
                    "direction_breadth",
                )
                output[name] = np.where(
                    output["side_sign"] > 0.0,
                    _numeric(output, column),
                    _numeric(output, negative),
                )

    signed_tokens = (
        "return_z",
        "path_efficiency",
        "delta_share",
        "net_return",
        "last_return",
        "body_fraction",
        "signed_quote",
        "net_price_progress",
        "cumulative_delta",
    )
    excluded_tokens = (
        "flow_progress_product",
        "dispersion_",
        "positive_fraction",
        "negative_fraction",
        "positive_breadth",
        "negative_breadth",
    )
    for column in columns:
        if column.startswith("aligned_"):
            continue
        if not any(token in column for token in signed_tokens):
            continue
        if any(token in column for token in excluded_tokens):
            continue
        values = pd.to_numeric(output[column], errors="coerce")
        if values.notna().sum() == 0:
            continue
        output[f"aligned_{column}"] = values * output["side_sign"]
    return output


def _feature_candidates(data: pd.DataFrame) -> list[str]:
    explicit = {
        "gross_rr",
        "risk_bps",
        "target_bps",
        "risk_in_prior_sigma",
        "target_in_prior_sigma",
        "risk_in_prior_range",
        "target_in_prior_range",
        "post_cost_reward_risk",
        "zero_drift_target_first_prior",
        "post_cost_break_even_target_probability",
        "required_target_probability_premium",
        "zero_drift_expected_net_r",
        "counterfactual_target_net_r",
        "counterfactual_stop_net_r",
    }
    prefixes = (
        "aligned_",
        "seq_",
        "bar",
        "local_activity_",
        "local_trade_count_",
        "local_close_location_",
        "local_range_fraction_",
        "common_",
        "dispersion_",
        "residual_",
        "trace_flow_",
    )
    future_tokens = (
        "counterfactual_outcome",
        "counterfactual_resolution",
        "counterfactual_minutes",
        "counterfactual_mfe",
        "counterfactual_mae",
        "counterfactual_net_r_conservative",
        "mfe_r_",
        "mae_r_",
        "target_first",
        "fast_stop",
    )
    output: list[str] = []
    for column in data.columns:
        if any(token in column for token in future_tokens):
            continue
        if column not in explicit and not column.startswith(prefixes):
            continue
        values = pd.to_numeric(data[column], errors="coerce")
        if values.notna().sum() < 10 or values.nunique(dropna=True) < 3:
            continue
        # Raw signed state is represented by an aligned counterpart.
        if (
            not column.startswith("aligned_")
            and f"aligned_{column}" in data.columns
        ):
            continue
        output.append(column)
    return sorted(set(output))


def _auc(values: pd.Series, labels: pd.Series) -> float | None:
    valid = values.notna() & labels.notna()
    x = values[valid].astype(float)
    y = labels[valid].astype(int)
    positives = int(y.sum())
    negatives = int((1 - y).sum())
    if positives == 0 or negatives == 0:
        return None
    ranks = x.rank(method="average")
    rank_sum = float(ranks[y == 1].sum())
    return (
        rank_sum - positives * (positives + 1) / 2.0
    ) / (positives * negatives)


def _scope_frames(data: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
    scopes: list[tuple[str, pd.DataFrame]] = [("ALL", data)]
    for column in ("scenario_path", "scale_name", "family"):
        if column not in data:
            continue
        for value, group in data.groupby(column, dropna=False):
            if pd.isna(value):
                continue
            counts = group.groupby("period").size()
            if (
                len(group) >= MIN_SCOPE_TOTAL
                and len(counts) >= 3
                and int(counts.min()) >= MIN_SCOPE_PERIOD
            ):
                scopes.append((f"{column}::{value}", group))
    return scopes


def _scope_summary(scopes: list[tuple[str, pd.DataFrame]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for scope, group in scopes:
        viable = group[group["economic_geometry_viable"].astype(bool)]
        resolved = group[group["target_first"].notna()]
        fast_stop = group["fast_stop_10m"].fillna(False)
        days = _days(group)
        rows.append(
            {
                "scope": scope,
                "plans": int(len(group)),
                "periods": int(group["period"].nunique()),
                "plans_per_calendar_day_diagnostic": (
                    None if days <= 0.0 else float(len(group) / days)
                ),
                "economic_geometry_viable": int(len(viable)),
                "economic_geometry_viable_rate": float(
                    len(viable) / len(group)
                ),
                "target_first_rate_resolved": (
                    None
                    if resolved.empty
                    else float(resolved["target_first"].mean())
                ),
                "fast_stop_10m_rate": float(fast_stop.mean()),
                "mean_net_r_all": float(group["net_r"].mean()),
                "mean_net_r_viable": (
                    None if viable.empty else float(viable["net_r"].mean())
                ),
                "sum_net_r_viable": (
                    None if viable.empty else float(viable["net_r"].sum())
                ),
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["plans", "scope"],
        ascending=[False, True],
    )


def _days(frame: pd.DataFrame) -> float:
    times = frame["plan_time"].dropna()
    if times.empty:
        return float(frame["period"].nunique())
    return max(
        1.0,
        float(
            (times.max().normalize() - times.min().normalize()).days + 1
        ),
    )


def _feature_stability(
    scopes: list[tuple[str, pd.DataFrame]],
    features: list[str],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for scope, raw in scopes:
        frame = raw[raw["economic_geometry_viable"].astype(bool)]
        for feature in features:
            if feature not in frame:
                continue
            values = pd.to_numeric(frame[feature], errors="coerce")
            pooled_auc = _auc(values, frame["target_first"])
            if pooled_auc is None:
                continue
            direction = 1 if pooled_auc >= 0.5 else -1
            period_aucs: list[float] = []
            for _, period in frame.groupby("period"):
                auc = _auc(
                    pd.to_numeric(period[feature], errors="coerce"),
                    period["target_first"],
                )
                if auc is not None:
                    period_aucs.append(
                        auc if direction > 0 else 1.0 - auc
                    )
            if len(period_aucs) < 2:
                continue
            oriented = values * direction
            valid_r = oriented.notna() & frame["net_r"].notna()
            rank_corr = (
                None
                if int(valid_r.sum()) < 3
                else float(
                    oriented[valid_r].corr(
                        frame.loc[valid_r, "net_r"],
                        method="spearman",
                    )
                )
            )
            rows.append(
                {
                    "scope": scope,
                    "feature": feature,
                    "direction": direction,
                    "pooled_oriented_auc": (
                        pooled_auc
                        if direction > 0
                        else 1.0 - pooled_auc
                    ),
                    "periods_with_auc": len(period_aucs),
                    "minimum_period_oriented_auc": min(period_aucs),
                    "median_period_oriented_auc": float(
                        np.median(period_aucs)
                    ),
                    "all_periods_at_least_random": bool(
                        min(period_aucs) >= 0.5
                    ),
                    "spearman_with_net_r": rank_corr,
                    "non_null": int(values.notna().sum()),
                }
            )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        [
            "all_periods_at_least_random",
            "minimum_period_oriented_auc",
            "pooled_oriented_auc",
        ],
        ascending=[False, False, False],
    )


def _training_auc(
    frame: pd.DataFrame,
    feature: str,
) -> tuple[int, float | None]:
    pooled = _auc(
        pd.to_numeric(frame[feature], errors="coerce"),
        frame["target_first"],
    )
    if pooled is None:
        return 1, None
    direction = 1 if pooled >= 0.5 else -1
    period_aucs: list[float] = []
    for _, period in frame.groupby("period"):
        auc = _auc(
            pd.to_numeric(period[feature], errors="coerce"),
            period["target_first"],
        )
        if auc is not None:
            period_aucs.append(
                auc if direction > 0 else 1.0 - auc
            )
    return (
        direction,
        None if not period_aucs else min(period_aucs),
    )


def _fit_policy(
    train: pd.DataFrame,
    features: list[str],
    coverage: float,
) -> CandidatePolicy | None:
    best: CandidatePolicy | None = None
    for feature in features:
        if feature not in train:
            continue
        values = pd.to_numeric(train[feature], errors="coerce")
        period_fraction = train.assign(_available=values.notna()).groupby(
            "period",
        )["_available"].mean()
        if (
            period_fraction.empty
            or float(period_fraction.min())
            < MIN_FEATURE_PERIOD_FRACTION
        ):
            continue
        direction, min_auc = _training_auc(train, feature)
        if min_auc is not None and min_auc < 0.45:
            continue
        score = values * direction
        available = train[score.notna()].copy()
        available["_score"] = score[score.notna()]
        if len(available) < 10:
            continue
        threshold = float(
            available["_score"].quantile(
                1.0 - coverage,
                interpolation="lower",
            )
        )
        selected = available[available["_score"] >= threshold]
        means: list[float] = []
        valid_counts = True
        for period_name, period in available.groupby("period"):
            chosen = selected[selected["period"] == period_name]
            minimum = max(
                2,
                int(math.floor(coverage * len(period) * 0.40)),
            )
            if len(chosen) < minimum:
                valid_counts = False
                break
            means.append(float(chosen["net_r"].mean()))
        if not valid_counts or not means:
            continue
        policy = CandidatePolicy(
            feature=feature,
            direction=direction,
            threshold=threshold,
            target_coverage=coverage,
            robust_train_mean_r=min(means),
            pooled_train_mean_r=float(selected["net_r"].mean()),
            train_selected=int(len(selected)),
            train_available=int(len(available)),
            train_min_oriented_auc=min_auc,
        )
        key = (
            policy.robust_train_mean_r,
            policy.pooled_train_mean_r,
            -abs(
                policy.train_selected
                / max(policy.train_available, 1)
                - coverage
            ),
            policy.feature,
        )
        if best is None:
            best = policy
        else:
            best_key = (
                best.robust_train_mean_r,
                best.pooled_train_mean_r,
                -abs(
                    best.train_selected
                    / max(best.train_available, 1)
                    - coverage
                ),
                best.feature,
            )
            if key > best_key:
                best = policy
    return best


def _evaluate_policy(
    policy: CandidatePolicy,
    holdout: pd.DataFrame,
    scope: str,
    holdout_name: str,
) -> dict[str, Any]:
    values = pd.to_numeric(
        holdout[policy.feature],
        errors="coerce",
    )
    score = values * policy.direction
    available = holdout[score.notna()].copy()
    available["_score"] = score[score.notna()]
    selected = available[
        available["_score"] >= policy.threshold
    ]
    resolved = selected[selected["target_first"].notna()]
    return {
        "scope": scope,
        "holdout_period": holdout_name,
        "target_coverage": policy.target_coverage,
        "selected_feature": policy.feature,
        "direction": policy.direction,
        "train_threshold": policy.threshold,
        "train_robust_mean_r": policy.robust_train_mean_r,
        "train_pooled_mean_r": policy.pooled_train_mean_r,
        "train_min_oriented_auc": policy.train_min_oriented_auc,
        "train_selected": policy.train_selected,
        "train_available": policy.train_available,
        "holdout_plans": int(len(holdout)),
        "holdout_feature_available": int(len(available)),
        "holdout_selected": int(len(selected)),
        "holdout_coverage": (
            None
            if available.empty
            else float(len(selected) / len(available))
        ),
        "holdout_plans_per_day_diagnostic": (
            0.0
            if selected.empty
            else float(len(selected) / _days(holdout))
        ),
        "holdout_mean_net_r": (
            None if selected.empty else float(selected["net_r"].mean())
        ),
        "holdout_sum_net_r": (
            None if selected.empty else float(selected["net_r"].sum())
        ),
        "holdout_target_first_rate_resolved": (
            None
            if resolved.empty
            else float(resolved["target_first"].mean())
        ),
        "holdout_fast_stop_10m_rate": (
            None
            if selected.empty
            else float(selected["fast_stop_10m"].mean())
        ),
        "non_account_frequency_policy": NON_ACCOUNT_POLICY,
    }


def _leave_one_period_out(
    scopes: list[tuple[str, pd.DataFrame]],
    features: list[str],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for scope, raw in scopes:
        viable = raw[raw["economic_geometry_viable"].astype(bool)].copy()
        periods = sorted(viable["period"].unique())
        if len(periods) < 3:
            continue
        for holdout_name in periods:
            train = viable[viable["period"] != holdout_name]
            holdout = viable[viable["period"] == holdout_name]
            if len(train) < 15 or len(holdout) < MIN_SCOPE_PERIOD:
                continue
            for coverage in COVERAGES:
                if coverage >= 0.999:
                    rows.append(
                        {
                            "scope": scope,
                            "holdout_period": holdout_name,
                            "target_coverage": 1.0,
                            "selected_feature": "<ALL_ECONOMICALLY_VIABLE>",
                            "direction": 1,
                            "train_threshold": float("-inf"),
                            "train_robust_mean_r": float(
                                train.groupby("period")["net_r"].mean().min()
                            ),
                            "train_pooled_mean_r": float(train["net_r"].mean()),
                            "train_min_oriented_auc": None,
                            "train_selected": int(len(train)),
                            "train_available": int(len(train)),
                            "holdout_plans": int(len(holdout)),
                            "holdout_feature_available": int(len(holdout)),
                            "holdout_selected": int(len(holdout)),
                            "holdout_coverage": 1.0,
                            "holdout_plans_per_day_diagnostic": float(
                                len(holdout) / _days(holdout)
                            ),
                            "holdout_mean_net_r": float(
                                holdout["net_r"].mean()
                            ),
                            "holdout_sum_net_r": float(
                                holdout["net_r"].sum()
                            ),
                            "holdout_target_first_rate_resolved": float(
                                holdout.loc[
                                    holdout["target_first"].notna(),
                                    "target_first",
                                ].mean()
                            ),
                            "holdout_fast_stop_10m_rate": float(
                                holdout["fast_stop_10m"].mean()
                            ),
                            "non_account_frequency_policy": NON_ACCOUNT_POLICY,
                        }
                    )
                    continue
                policy = _fit_policy(train, features, coverage)
                if policy is None:
                    continue
                rows.append(
                    _evaluate_policy(
                        policy,
                        holdout,
                        scope,
                        holdout_name,
                    )
                )
    return pd.DataFrame(rows)


def _aggregate_loo(loo: pd.DataFrame) -> pd.DataFrame:
    if loo.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for (scope, coverage), group in loo.groupby(
        ["scope", "target_coverage"],
    ):
        selected = int(group["holdout_selected"].sum())
        weighted_sum = float(group["holdout_sum_net_r"].fillna(0.0).sum())
        means = group["holdout_mean_net_r"].dropna()
        rows.append(
            {
                "scope": scope,
                "target_coverage": float(coverage),
                "holdout_periods": int(group["holdout_period"].nunique()),
                "selected_plans": selected,
                "pooled_holdout_mean_net_r": (
                    None if selected == 0 else weighted_sum / selected
                ),
                "minimum_holdout_mean_net_r": (
                    None if means.empty else float(means.min())
                ),
                "positive_holdout_periods": int((means > 0.0).sum()),
                "mean_actual_holdout_coverage": float(
                    group["holdout_coverage"].dropna().mean()
                ),
                "mean_plans_per_day_diagnostic": float(
                    group["holdout_plans_per_day_diagnostic"].mean()
                ),
                "selected_features": "|".join(
                    sorted(set(group["selected_feature"].astype(str)))
                ),
                "all_holdouts_positive": bool(
                    len(means) == group["holdout_period"].nunique()
                    and (means > 0.0).all()
                ),
                "non_account_frequency_policy": NON_ACCOUNT_POLICY,
            }
        )
    return pd.DataFrame(rows).sort_values(
        [
            "all_holdouts_positive",
            "minimum_holdout_mean_net_r",
            "pooled_holdout_mean_net_r",
        ],
        ascending=[False, False, False],
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    paths = _discover(args.root)
    data = _load(paths)
    scopes = _scope_frames(data)
    features = _feature_candidates(data)

    args.output.mkdir(parents=True, exist_ok=True)
    scope_summary = _scope_summary(scopes)
    stability = _feature_stability(scopes, features)
    loo = _leave_one_period_out(scopes, features)
    aggregate = _aggregate_loo(loo)

    scope_summary.to_csv(
        args.output / "first_passage_scope_summary.csv",
        index=False,
    )
    stability.to_csv(
        args.output / "first_passage_feature_stability.csv",
        index=False,
    )
    loo.to_csv(
        args.output / "first_passage_loo_risk_coverage.csv",
        index=False,
    )
    aggregate.to_csv(
        args.output / "first_passage_loo_aggregate.csv",
        index=False,
    )

    summary = {
        "inputs": [str(path) for path in paths],
        "plans": int(len(data)),
        "periods": sorted(data["period"].unique().tolist()),
        "features_considered": len(features),
        "scopes_considered": len(scopes),
        "economically_viable_plans": int(
            data["economic_geometry_viable"].astype(bool).sum()
        ),
        "research_only_policy": RESEARCH_ONLY_POLICY,
        "selective_coverage_policy": SELECTIVE_COVERAGE_POLICY,
        "non_account_frequency_policy": NON_ACCOUNT_POLICY,
        "promotion_candidates": (
            []
            if aggregate.empty
            else aggregate[
                aggregate["all_holdouts_positive"].astype(bool)
                & (
                    aggregate["mean_plans_per_day_diagnostic"]
                    >= 1.0
                )
            ]
            .head(20)
            .to_dict(orient="records")
        ),
        "warning": (
            "Promotion candidates remain research hypotheses. They require a "
            "single-position continuous NautilusTrader account test with "
            "collision arbitration and untouched evaluation periods."
        ),
    }
    (args.output / "first_passage_policy_summary.json").write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


if __name__ == "__main__":
    main()

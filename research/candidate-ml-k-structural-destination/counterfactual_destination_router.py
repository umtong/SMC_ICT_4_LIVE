#!/usr/bin/env python3
"""Strict-causal structural-destination controller for candidate ML-k.

The generator emits exact immutable alternatives for causally live opposing
liquidity frontiers.  This controller:
1. attributes the move to local auction response rather than broad crypto beta;
2. learns fill and target-before-stop probabilities from mature earlier labels;
3. enforces the first-passage fact that farther targets cannot be easier;
4. chooses one TP before entry by expected post-cost log-NAV growth per account
   occupation hour; and
5. routes one continuous account with one pending order or filled position.

Symbol identity, absolute price, outcome fields and post-decision path statistics
are never model features.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import sys
from typing import Any, Sequence

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_DIR = REPO_ROOT / "research/candidate-liquidity-episode-policy-v1"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import route_episode_policy as base  # noqa: E402

try:
    from sklearn.ensemble import (  # noqa: E402
        HistGradientBoostingClassifier,
        HistGradientBoostingRegressor,
    )
except Exception as exc:  # pragma: no cover
    raise RuntimeError("scikit-learn is required for structural destination routing") from exc

MODEL_VERSION = "candidate-ml-k-counterfactual-structural-destination-v1"
RISK_FRACTION = 0.03
EPS = 1e-12
NS_PER_MINUTE = 60_000_000_000
CASCADE_MINUTES = 4
MIN_BINARY_ROWS = 36
MIN_CLASS_ROWS = 6
MIN_DURATION_ROWS = 40

LOCAL_NUMERIC = (
    "control_move_atr",
    "control_path_efficiency",
    "control_flow_share_signed",
    "control_activity_ratio",
    "control_effort_result",
    "relative_return_signed",
    "oi_log_change",
    "basis_change_signed_bps",
    "ctx_residual_return_5m_signed",
    "ctx_residual_return_15m_signed",
    "ctx_residual_return_60m_signed",
    "ctx_structure_15m_signed",
    "ctx_structure_60m_signed",
    "ctx_structure_240m_signed",
    "ctx_structure_vote_signed",
    "ctx_structure_agreement",
    "ctx_oi_log_change",
    "ctx_basis_change_bps_signed",
    "ctx_dealing_range_position_signed",
    "source_scale_log",
    "source_strength",
    "source_confluence_count",
    "control_composite",
)
COMMON_NUMERIC = (
    "common_factor_signed",
    "common_breadth_signed",
    "ctx_common_return_5m_signed",
    "ctx_common_return_15m_signed",
    "ctx_common_return_60m_signed",
    "ctx_common_breadth_5m_signed",
    "ctx_common_breadth_15m_signed",
    "ctx_common_breadth_60m_signed",
    "ctx_momentum_vote",
    "ctx_breadth_vote",
    "market_alignment",
    "mechanism_coherence",
)
TARGET_NUMERIC = (
    "gross_rr",
    "planned_target_net_r",
    "risk_bps",
    "route_strength",
    "route_scale",
    "route_scale_log",
    "route_to_source_log_ratio",
    "target_candidate_rank",
    "target_candidate_count",
    "target_frontier_percentile",
    "target_distance_atr",
    "frontier_spacing_atr",
)
FAMILY_TOKENS = (
    "FAILED_AUCTION_REVERSAL",
    "ACCEPTED_AUCTION_CONTINUATION",
    "INITIATIVE_MITIGATION_CONTINUATION",
)
GEOMETRY_TOKENS = (
    "OB_FVG_OVERLAP",
    "FVG",
    "LAST_OPPOSITE_BODY",
    "TRANSFERRED_SOURCE",
    "SOURCE_OVERLAP",
)
SOURCE_TOKENS = (
    "DYNAMIC_CHANNEL",
    "DYNAMIC_TRENDLINE",
    "SEMANTIC",
    "DIRECTIONAL_CHANGE",
)
ROUTE_TOKENS = (
    "DYNAMIC_CHANNEL",
    "DYNAMIC_TRENDLINE",
    "PREVIOUS_DAY",
    "DIRECTIONAL_CHANGE",
    "SEMANTIC",
)


def _series(frame: pd.DataFrame, name: str, default: Any = 0.0) -> pd.Series:
    if name in frame:
        return frame[name]
    return pd.Series(default, index=frame.index)


def _number(frame: pd.DataFrame, name: str, default: float = 0.0) -> pd.Series:
    return pd.to_numeric(_series(frame, name, default), errors="coerce").fillna(default)


def _time(frame: pd.DataFrame, name: str) -> pd.Series:
    return pd.to_datetime(
        pd.to_numeric(_series(frame, name, np.nan), errors="coerce"),
        unit="ns",
        utc=True,
        errors="coerce",
    )


def _sigmoid(value: np.ndarray | float) -> np.ndarray:
    x = np.clip(np.asarray(value, dtype=float), -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-x))


def _logit(value: np.ndarray | float) -> np.ndarray:
    p = np.clip(np.asarray(value, dtype=float), 1e-5, 1.0 - 1e-5)
    return np.log(p / (1.0 - p))


def _token(text: pd.Series, value: str) -> pd.Series:
    return text.astype(str).str.contains(value, case=False, regex=False).astype(float)


def causal_features(
    frame: pd.DataFrame,
    *,
    include_common: bool,
    include_target: bool,
) -> pd.DataFrame:
    """Create a fixed event-relative feature space shared by all four symbols."""
    output = pd.DataFrame(index=frame.index)
    for name in LOCAL_NUMERIC:
        output[name] = _number(frame, name)
    if include_common:
        for name in COMMON_NUMERIC:
            output[name] = _number(frame, name)
    if include_target:
        for name in TARGET_NUMERIC:
            output[name] = _number(frame, name)

    family = _series(frame, "family", "").astype(str)
    geometry = _series(frame, "entry_geometry", "").astype(str)
    source = _series(frame, "source_kind", "").astype(str)
    route = (
        _series(frame, "route_kind", "").astype(str)
        + "|"
        + _series(frame, "route_family", "").astype(str)
    )
    for value in FAMILY_TOKENS:
        output[f"family__{value.lower()}"] = family.eq(value).astype(float)
    for value in GEOMETRY_TOKENS:
        output[f"geometry__{value.lower()}"] = _token(geometry, value)
    for value in SOURCE_TOKENS:
        output[f"source__{value.lower()}"] = _token(source, value)
    if include_target:
        for value in ROUTE_TOKENS:
            output[f"route__{value.lower()}"] = _token(route, value)
        bucket = _series(frame, "route_scale_bucket", "").astype(str)
        for value in ("LOCAL", "INTRADAY", "MESO", "DAILY_PLUS"):
            output[f"route_scale__{value.lower()}"] = bucket.eq(value).astype(float)

    return output.replace([np.inf, -np.inf], np.nan).fillna(0.0).astype(float)


def _episode_weights(frame: pd.DataFrame) -> np.ndarray:
    if frame.empty or "episode_id" not in frame:
        return np.ones(len(frame), dtype=float)
    counts = frame.groupby("episode_id").episode_id.transform("size").to_numpy(float)
    return 1.0 / np.maximum(counts, 1.0)


@dataclass
class BinaryEnsemble:
    models: list[HistGradientBoostingClassifier]
    prior: float
    prior_uncertainty: float
    ready: bool
    diagnostics: dict[str, Any]

    def predict(self, features: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        if not self.models:
            return (
                np.full(len(features), self.prior, dtype=float),
                np.full(len(features), self.prior_uncertainty, dtype=float),
            )
        matrix = np.vstack(
            [model.predict_proba(features)[:, 1] for model in self.models]
        )
        shrink = float(self.diagnostics.get("shrink", 0.0))
        mean = self.prior + shrink * (matrix.mean(axis=0) - self.prior)
        uncertainty = np.sqrt(matrix.var(axis=0) + self.prior_uncertainty**2)
        return np.clip(mean, 0.005, 0.995), uncertainty


def fit_binary(
    frame: pd.DataFrame,
    label: str,
    features: pd.DataFrame,
    *,
    random_state: int,
) -> BinaryEnsemble:
    raw = pd.to_numeric(_series(frame, label, np.nan), errors="coerce")
    valid = raw.notna()
    work = frame.loc[valid]
    x = features.loc[valid]
    y = raw.loc[valid].astype(int)
    positives = int(y.sum()) if len(y) else 0
    negatives = int(len(y) - positives)
    prior = float((positives + 6.0) / (len(y) + 12.0)) if len(y) else 0.5
    prior_uncertainty = math.sqrt(
        prior * (1.0 - prior) / max(len(y) + 12.0, 1.0)
    )
    diagnostics: dict[str, Any] = {
        "rows": int(len(y)),
        "positives": positives,
        "negatives": negatives,
        "prior": prior,
        "ready": False,
        "models": 0,
    }
    if (
        len(y) < MIN_BINARY_ROWS
        or min(positives, negatives) < MIN_CLASS_ROWS
        or x.shape[1] == 0
    ):
        return BinaryEnsemble([], prior, prior_uncertainty, False, diagnostics)

    weights = _episode_weights(work)
    models: list[HistGradientBoostingClassifier] = []
    settings = (
        (5, 2.5, random_state),
        (7, 5.0, random_state + 101),
        (9, 9.0, random_state + 211),
    )
    for leaves, l2, seed in settings:
        model = HistGradientBoostingClassifier(
            learning_rate=0.04,
            max_iter=150,
            max_leaf_nodes=leaves,
            min_samples_leaf=max(10, min(30, len(y) // 8)),
            l2_regularization=l2,
            early_stopping=False,
            random_state=seed,
        )
        model.fit(x, y, sample_weight=weights)
        models.append(model)
    shrink = float(len(y) / (len(y) + 180.0))
    diagnostics.update(
        {"ready": True, "models": len(models), "shrink": shrink}
    )
    return BinaryEnsemble(
        models, prior, prior_uncertainty, True, diagnostics
    )


@dataclass
class DurationModel:
    models: list[HistGradientBoostingRegressor]
    fallback: float
    diagnostics: dict[str, Any]

    def predict(self, features: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        if not self.models:
            return np.full(len(features), self.fallback), np.zeros(len(features))
        matrix = np.vstack(
            [
                np.expm1(np.clip(model.predict(features), 0.0, 10.0))
                for model in self.models
            ]
        )
        return np.maximum(1.0, matrix.mean(axis=0)), matrix.std(axis=0)


def fit_duration(
    frame: pd.DataFrame,
    features: pd.DataFrame,
) -> DurationModel:
    label = pd.to_numeric(
        _series(frame, "terminal_minutes_label", np.nan), errors="coerce"
    )
    valid = label.notna() & label.gt(0.0)
    work = frame.loc[valid]
    x = features.loc[valid]
    y = np.log1p(label.loc[valid].to_numpy(float))
    fallback = (
        float(label.loc[valid].median()) if valid.any() else 60.0
    )
    diagnostics = {
        "rows": int(valid.sum()),
        "fallback_minutes": fallback,
        "ready": False,
    }
    if valid.sum() < MIN_DURATION_ROWS or x.shape[1] == 0:
        return DurationModel([], fallback, diagnostics)
    weights = _episode_weights(work)
    models: list[HistGradientBoostingRegressor] = []
    for leaves, l2, seed in ((5, 4.0, 31001), (8, 10.0, 31103)):
        model = HistGradientBoostingRegressor(
            loss="absolute_error",
            learning_rate=0.04,
            max_iter=140,
            max_leaf_nodes=leaves,
            min_samples_leaf=max(10, min(30, len(y) // 8)),
            l2_regularization=l2,
            early_stopping=False,
            random_state=seed,
        )
        model.fit(x, y, sample_weight=weights)
        models.append(model)
    diagnostics.update({"ready": True, "models": len(models)})
    return DurationModel(models, fallback, diagnostics)


def prepare_labels(orders: pd.DataFrame) -> pd.DataFrame:
    output = orders.copy()
    output["order_time"] = base._period_start(output)
    fill_time = _time(output, "fill_time_ns")
    terminal_time = _time(output, "order_terminal_time_ns")
    resolution_time = _time(output, "resolution_time_ns")
    outcome = _series(output, "outcome", "").astype(str)
    output["fill_label"] = fill_time.notna().astype(int)
    output["resolved_label"] = outcome.isin(base.RESOLVED_OUTCOMES)
    output["target_label"] = outcome.eq("TARGET_FIRST").astype(int)
    output["fill_label_available_time"] = fill_time.where(
        output.fill_label.eq(1), terminal_time
    )
    output["target_label_available_time"] = resolution_time.where(
        output.resolved_label
    )
    output["terminal_label_available_time"] = terminal_time
    order_ns = pd.to_numeric(
        _series(output, "order_time_ns", np.nan), errors="coerce"
    )
    terminal_ns = pd.to_numeric(
        _series(output, "order_terminal_time_ns", np.nan), errors="coerce"
    )
    output["terminal_minutes_label"] = np.maximum(
        1.0, (terminal_ns - order_ns) / NS_PER_MINUTE
    )
    return output


def nearest_state_table(orders: pd.DataFrame) -> pd.DataFrame:
    work = orders.copy()
    work["_rank"] = _number(work, "target_candidate_rank", 0.0)
    work["_gross"] = _number(work, "gross_rr", 0.0)
    work = (
        work.sort_values(
            ["period", "episode_id", "_rank", "_gross", "action_id"]
        )
        .drop_duplicates(["period", "episode_id"], keep="first")
        .reset_index(drop=True)
    )
    work["ownership_label"] = np.where(
        work.resolved_label,
        pd.to_numeric(work.target_label, errors="coerce"),
        np.nan,
    )
    work["ownership_label_available_time"] = work.target_label_available_time
    return work


def fit_ownership(
    states: pd.DataFrame,
    *,
    random_state: int,
) -> dict[str, Any]:
    full_x = causal_features(
        states, include_common=True, include_target=False
    )
    local_x = causal_features(
        states, include_common=False, include_target=False
    )
    common_x = pd.DataFrame(index=states.index)
    for name in COMMON_NUMERIC:
        common_x[name] = _number(states, name)
    common_x = common_x.replace([np.inf, -np.inf], np.nan).fillna(0.0)

    return {
        "full": fit_binary(
            states, "ownership_label", full_x, random_state=random_state
        ),
        "local": fit_binary(
            states, "ownership_label", local_x, random_state=random_state + 1000
        ),
        "common": fit_binary(
            states, "ownership_label", common_x, random_state=random_state + 2000
        ),
        "full_columns": list(full_x.columns),
        "local_columns": list(local_x.columns),
        "common_columns": list(common_x.columns),
    }


def predict_ownership(
    models: dict[str, Any],
    states: pd.DataFrame,
) -> pd.DataFrame:
    output = states.copy()
    full_x = causal_features(output, include_common=True, include_target=False)
    local_x = causal_features(output, include_common=False, include_target=False)
    common_x = pd.DataFrame(index=output.index)
    for name in COMMON_NUMERIC:
        common_x[name] = _number(output, name)
    common_x = common_x.replace([np.inf, -np.inf], np.nan).fillna(0.0)

    full, full_std = models["full"].predict(full_x)
    local, local_std = models["local"].predict(local_x)
    common, common_std = models["common"].predict(common_x)
    prior = float(models["full"].prior)
    prior_logit = float(_logit(prior))
    common_uplift = np.maximum(0.0, _logit(common) - prior_logit)
    residual = _sigmoid(_logit(full) - common_uplift)
    local_support = np.sqrt(np.clip(full * local, 1e-6, 1.0))
    counterfactual = np.minimum(residual, local_support + 0.08)
    uncertainty = np.sqrt(full_std**2 + local_std**2 + common_std**2)
    counterfactual = np.clip(
        counterfactual - 0.20 * uncertainty, 0.005, 0.995
    )

    output["p_ownership_full"] = full
    output["p_ownership_local"] = local
    output["p_ownership_common"] = common
    output["p_ownership_counterfactual"] = counterfactual
    output["common_only_positive_logit_uplift"] = common_uplift
    output["ownership_uncertainty"] = uncertainty
    output["ownership_models_ready"] = (
        models["full"].ready and models["local"].ready
    )
    return output


def _map_ownership(
    candidates: pd.DataFrame,
    states: pd.DataFrame,
) -> pd.DataFrame:
    columns = [
        "period",
        "episode_id",
        "p_ownership_full",
        "p_ownership_local",
        "p_ownership_common",
        "p_ownership_counterfactual",
        "common_only_positive_logit_uplift",
        "ownership_uncertainty",
        "ownership_models_ready",
    ]
    return candidates.merge(
        states[columns].drop_duplicates(["period", "episode_id"]),
        on=["period", "episode_id"],
        how="left",
    )


def _monotone_target_probabilities(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    adjusted = pd.Series(np.nan, index=output.index, dtype=float)
    for _, group in output.groupby(["period", "episode_id"], sort=False):
        ordered = group.sort_values(
            ["gross_rr", "target_candidate_rank", "action_id"]
        )
        values = pd.to_numeric(
            ordered.p_target_owned, errors="coerce"
        ).to_numpy(float)
        finite = np.isfinite(values)
        if finite.any():
            running = np.minimum.accumulate(
                np.where(finite, values, 1.0)
            )
            running[~finite] = np.nan
            adjusted.loc[ordered.index] = running
    output["p_target_if_filled"] = adjusted
    return output


def strict_causal_score(
    orders: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    output = prepare_labels(orders)
    states = nearest_state_table(output)
    output["p_fill"] = np.nan
    output["p_target_raw"] = np.nan
    output["p_target_owned"] = np.nan
    output["predicted_terminal_minutes"] = np.nan
    output["models_ready"] = False
    output["prediction_source"] = "insufficient_mature_development_history"

    diagnostics: dict[str, Any] = {}
    period_order = (
        output.groupby("period").order_time.min().sort_values().index.tolist()
    )
    pieces: list[pd.DataFrame] = []
    for sequence, period in enumerate(period_order):
        test = output[output.period.astype(str).eq(str(period))].copy()
        if test.empty:
            continue
        test_start = test.order_time.min()
        development = output.role.astype(str).eq("dev")

        ownership_train = states[
            states.role.astype(str).eq("dev")
            & states.ownership_label.notna()
            & states.ownership_label_available_time.notna()
            & states.ownership_label_available_time.lt(test_start)
        ].copy()
        ownership_test = states[
            states.period.astype(str).eq(str(period))
        ].copy()
        ownership_models = fit_ownership(
            ownership_train, random_state=41000 + sequence * 37
        )
        ownership_scored = predict_ownership(
            ownership_models, ownership_test
        )
        test = _map_ownership(test, ownership_scored)

        fill_train = output[
            development
            & output.fill_label_available_time.notna()
            & output.fill_label_available_time.lt(test_start)
        ].copy()
        target_train = output[
            development
            & output.resolved_label
            & output.target_label_available_time.notna()
            & output.target_label_available_time.lt(test_start)
        ].copy()
        duration_train = output[
            development
            & output.terminal_label_available_time.notna()
            & output.terminal_label_available_time.lt(test_start)
        ].copy()

        fill_x = causal_features(
            fill_train, include_common=False, include_target=True
        )
        target_x = causal_features(
            target_train, include_common=False, include_target=True
        )
        duration_x = causal_features(
            duration_train, include_common=False, include_target=True
        )
        test_x = causal_features(
            test, include_common=False, include_target=True
        )

        fill_model = fit_binary(
            fill_train,
            "fill_label",
            fill_x,
            random_state=51000 + sequence * 41,
        )
        target_model = fit_binary(
            target_train,
            "target_label",
            target_x,
            random_state=61000 + sequence * 43,
        )
        duration_model = fit_duration(duration_train, duration_x)

        p_fill, fill_std = fill_model.predict(test_x)
        p_target_raw, target_std = target_model.predict(test_x)
        duration, duration_std = duration_model.predict(test_x)
        ownership = pd.to_numeric(
            test.p_ownership_counterfactual, errors="coerce"
        ).fillna(0.5).to_numpy(float)
        ownership_bound = np.sqrt(
            np.clip(p_target_raw * ownership, 1e-8, 1.0)
        )
        p_target_owned = np.minimum(
            p_target_raw, ownership_bound + 0.03
        )

        ready = (
            fill_model.ready
            and target_model.ready
            and bool(
                pd.Series(test.ownership_models_ready)
                .fillna(False)
                .astype(bool)
                .all()
            )
        )
        test["p_fill"] = p_fill
        test["p_fill_uncertainty"] = fill_std
        test["p_target_raw"] = p_target_raw
        test["p_target_uncertainty"] = target_std
        test["p_target_owned"] = p_target_owned
        test["predicted_terminal_minutes"] = np.maximum(
            1.0, duration + 0.15 * duration_std
        )
        test["models_ready"] = ready
        if ready:
            test["prediction_source"] = MODEL_VERSION

        diagnostics[str(period)] = {
            "test_start": str(test_start),
            "test_rows": int(len(test)),
            "ownership_rows": int(len(ownership_train)),
            "ownership_full": ownership_models["full"].diagnostics,
            "ownership_local": ownership_models["local"].diagnostics,
            "ownership_common": ownership_models["common"].diagnostics,
            "fill_model": fill_model.diagnostics,
            "target_model": target_model.diagnostics,
            "duration_model": duration_model.diagnostics,
            "latest_fill_label": (
                str(fill_train.fill_label_available_time.max())
                if len(fill_train)
                else None
            ),
            "latest_target_label": (
                str(target_train.target_label_available_time.max())
                if len(target_train)
                else None
            ),
        }
        pieces.append(test)

    scored = (
        pd.concat(pieces, ignore_index=True, sort=False)
        if pieces
        else output.iloc[:0].copy()
    )
    scored = _monotone_target_probabilities(scored)
    target_r = _number(scored, "planned_target_net_r")
    p_fill = pd.to_numeric(scored.p_fill, errors="coerce")
    p_target = pd.to_numeric(
        scored.p_target_if_filled, errors="coerce"
    )
    win_log = np.log(np.maximum(EPS, 1.0 + RISK_FRACTION * target_r))
    loss_log = math.log(1.0 - RISK_FRACTION)
    scored["break_even_target_probability"] = np.where(
        win_log - loss_log > EPS,
        -loss_log / (win_log - loss_log),
        1.0,
    )
    scored["target_probability_edge"] = (
        p_target - scored.break_even_target_probability
    )
    scored["expected_log_growth"] = p_fill * (
        p_target * win_log + (1.0 - p_target) * loss_log
    )
    scored["expected_log_growth_per_hour"] = (
        scored.expected_log_growth
        / np.maximum(
            _number(scored, "predicted_terminal_minutes", 60.0) / 60.0,
            1.0 / 60.0,
        )
    )
    scored["policy_eligible"] = (
        scored.models_ready.fillna(False)
        & _number(scored, "gross_rr").ge(1.0)
        & target_r.gt(0.0)
        & scored.expected_log_growth.gt(0.0)
        & scored.target_probability_edge.gt(0.0)
    )
    return scored, diagnostics


def best_destination_per_episode(scored: pd.DataFrame) -> pd.DataFrame:
    work = scored[
        scored.models_ready.fillna(False)
        & _number(scored, "gross_rr").ge(1.0)
    ].copy()
    if work.empty:
        return work
    work = work.sort_values(
        [
            "period",
            "episode_id",
            "expected_log_growth_per_hour",
            "expected_log_growth",
            "p_target_if_filled",
            "planned_target_net_r",
            "action_id",
        ],
        ascending=[True, True, False, False, False, False, True],
    )
    return (
        work.drop_duplicates(["period", "episode_id"], keep="first")
        .reset_index(drop=True)
    )


def assign_market_episodes(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.sort_values(
        ["period", "order_time_ns", "episode_id"]
    ).copy()
    identifiers: dict[int, str] = {}
    for period, group in output.groupby("period", sort=True):
        cluster = 0
        cluster_start: int | None = None
        for index, row in group.iterrows():
            timestamp = int(row.order_time_ns)
            if (
                cluster_start is None
                or timestamp - cluster_start
                > CASCADE_MINUTES * NS_PER_MINUTE
            ):
                cluster += 1
                cluster_start = timestamp
            identifiers[index] = f"{period}:CAUSAL_MARKET_EVENT:{cluster}"
    output["market_episode_id"] = pd.Series(identifiers)
    return output


def route_one_account(
    scored: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    episode_best = best_destination_per_episode(scored)
    eligible = episode_best[
        episode_best.policy_eligible.fillna(False)
    ].copy()
    if eligible.empty:
        empty = episode_best.iloc[:0].copy()
        return empty, empty, empty, {
            "selected_orders": 0,
            "closed_trades": 0,
            "ending_nav_multiplier": 1.0,
            "maximum_drawdown": 0.0,
        }

    eligible = assign_market_episodes(eligible)
    eligible["order_time"] = _time(eligible, "order_time_ns")
    eligible["fill_time"] = _time(eligible, "fill_time_ns")
    eligible["terminal_time"] = _time(
        eligible, "order_terminal_time_ns"
    )
    eligible = eligible.sort_values(
        [
            "order_time",
            "expected_log_growth_per_hour",
            "expected_log_growth",
            "p_target_if_filled",
            "episode_id",
        ],
        ascending=[True, False, False, False, True],
    )

    selected: list[pd.Series] = []
    replacements: list[pd.Series] = []
    used_episodes: set[str] = set()
    used_market: set[str] = set()
    active: pd.Series | None = None

    for timestamp, simultaneous in eligible.groupby("order_time", sort=True):
        timestamp = pd.Timestamp(timestamp)
        if pd.isna(timestamp):
            continue
        if active is not None:
            terminal = pd.Timestamp(active.terminal_time)
            if pd.isna(terminal):
                terminal = timestamp
            if timestamp >= terminal:
                selected.append(active)
                used_episodes.add(str(active.episode_id))
                used_market.add(str(active.market_episode_id))
                active = None

        pool = simultaneous[
            ~simultaneous.episode_id.astype(str).isin(used_episodes)
            & ~simultaneous.market_episode_id.astype(str).isin(used_market)
        ]
        if pool.empty:
            continue
        candidate = pool.iloc[0].copy()

        if active is None:
            active = candidate
            continue

        fill_time = pd.Timestamp(active.fill_time)
        if not pd.isna(fill_time) and fill_time <= timestamp:
            continue
        independent = (
            str(candidate.episode_id) != str(active.episode_id)
            and str(candidate.market_episode_id)
            != str(active.market_episode_id)
        )
        stronger = (
            float(candidate.expected_log_growth_per_hour)
            > float(active.expected_log_growth_per_hour) + EPS
        )
        if independent and stronger:
            replaced = active.copy()
            replaced["replacement_time_ns"] = int(timestamp.value)
            replaced["replacement_reason"] = (
                "HIGHER_INDEPENDENT_EXPECTED_LOG_GROWTH_PER_ACCOUNT_HOUR"
            )
            replacements.append(replaced)
            used_episodes.add(str(active.episode_id))
            used_market.add(str(active.market_episode_id))
            active = candidate

    if active is not None:
        selected.append(active)

    orders = (
        pd.DataFrame(selected).reset_index(drop=True)
        if selected
        else eligible.iloc[:0].copy()
    )
    replacement_frame = (
        pd.DataFrame(replacements).reset_index(drop=True)
        if replacements
        else orders.iloc[:0].copy()
    )
    outcome = _series(orders, "outcome", "").astype(str)
    trades = orders[
        pd.to_numeric(_series(orders, "net_r", np.nan), errors="coerce").notna()
        & outcome.isin(base.RESOLVED_OUTCOMES)
    ].copy()
    trades = trades.sort_values(
        ["order_terminal_time_ns", "order_time_ns"]
    ).reset_index(drop=True)
    trades["net_r"] = pd.to_numeric(trades.net_r, errors="coerce")

    nav = 1.0
    peak = 1.0
    maximum_drawdown = 0.0
    nav_before: list[float] = []
    nav_after: list[float] = []
    for result in trades.net_r.astype(float):
        nav_before.append(nav)
        nav *= max(EPS, 1.0 + RISK_FRACTION * result)
        peak = max(peak, nav)
        maximum_drawdown = max(maximum_drawdown, 1.0 - nav / peak)
        nav_after.append(nav)
    trades["nav_before"] = nav_before
    trades["nav_after"] = nav_after
    wins = trades.outcome.astype(str).eq("TARGET_FIRST")

    summary = {
        "eligible_episode_plans": int(len(eligible)),
        "selected_orders": int(len(orders)),
        "replaced_pending_orders": int(len(replacement_frame)),
        "closed_trades": int(len(trades)),
        "target_first": int(wins.sum()),
        "target_first_rate": float(wins.mean()) if len(trades) else None,
        "mean_net_r": float(trades.net_r.mean()) if len(trades) else None,
        "median_net_r": float(trades.net_r.median()) if len(trades) else None,
        "mean_planned_gross_rr": (
            float(_number(trades, "gross_rr").mean())
            if len(trades)
            else None
        ),
        "median_holding_minutes": (
            float(_number(trades, "holding_minutes").median())
            if len(trades)
            else None
        ),
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(maximum_drawdown),
        "independent_market_episodes_traded": (
            int(trades.market_episode_id.nunique())
            if len(trades)
            else 0
        ),
        "risk_fraction": RISK_FRACTION,
    }
    return orders, trades, replacement_frame, summary


def _group_metrics(frame: pd.DataFrame, key: str) -> dict[str, Any]:
    if frame.empty or key not in frame:
        return {}
    output: dict[str, Any] = {}
    for value, group in frame.groupby(key, dropna=False):
        wins = group.outcome.astype(str).eq("TARGET_FIRST")
        output[str(value)] = {
            "trades": int(len(group)),
            "target_first_rate": float(wins.mean()),
            "mean_net_r": float(pd.to_numeric(group.net_r).mean()),
            "mean_gross_rr": float(_number(group, "gross_rr").mean()),
            "median_hold_minutes": float(
                _number(group, "holding_minutes").median()
            ),
        }
    return output


def risk_sized_quantity(
    *,
    nav: float,
    entry: float,
    stop: float,
    quantity_step: float,
) -> dict[str, float]:
    if min(nav, entry, stop, quantity_step) <= 0.0:
        raise ValueError("nav, prices and quantity_step must be positive")
    distance = abs(entry - stop)
    if distance <= 0.0:
        raise ValueError("entry and stop must differ")
    raw = nav * RISK_FRACTION / distance
    quantity = math.floor(raw / quantity_step + EPS) * quantity_step
    if quantity <= 0.0:
        raise ValueError("quantity rounds to zero")
    risk_cash = quantity * distance
    notional = quantity * entry
    return {
        "quantity": quantity,
        "risk_cash": risk_cash,
        "risk_fraction": risk_cash / nav,
        "notional": notional,
        "implied_leverage": notional / nav,
    }


def route_research(root: Path, output_dir: Path) -> dict[str, Any]:
    episodes, period_days, source_summaries = base.load_universe(root)
    if episodes.empty:
        raise RuntimeError(f"No structural destination artifacts below {root}")
    orders = episodes[
        base._bool_series(episodes["order_exists"])
    ].copy()
    if orders.empty:
        raise RuntimeError("The generator emitted no executable target candidates")

    scored, model_diagnostics = strict_causal_score(orders)
    selected, trades, replacements, account = route_one_account(scored)
    calendar_days = int(sum(period_days.values()))
    account.update(
        {
            "diagnostic_calendar_days": calendar_days,
            "closed_trades_per_diagnostic_day": (
                float(len(trades) / calendar_days)
                if calendar_days
                else 0.0
            ),
            "by_period": _group_metrics(trades, "period"),
            "by_family": _group_metrics(trades, "family"),
            "by_symbol": _group_metrics(trades, "symbol"),
            "by_route_family": _group_metrics(trades, "route_family"),
        }
    )

    summary = {
        "policy_version": MODEL_VERSION,
        "decision_policy": (
            "causal direction/liquidity/structure -> first-return entry and "
            "structural invalidation -> exact live structural target frontiers -> "
            "local-vs-common counterfactual ownership -> strict-causal fill and "
            "target first-passage models -> monotone destination choice by expected "
            "post-cost log NAV growth per occupied account hour -> one continuous "
            "global pending/position slot"
        ),
        "risk_fraction": RISK_FRACTION,
        "one_global_account_slot": True,
        "one_executed_plan_per_causal_episode": True,
        "counterfactual_rows_are_not_simultaneous_orders": True,
        "gross_planned_rr_floor": 1.0,
        "partial_entries_or_exits": False,
        "forced_time_exit": False,
        "daily_loss_limit": False,
        "symbol_identity_is_model_feature": False,
        "absolute_price_is_model_feature": False,
        "future_information_is_model_feature": False,
        "raw_common_market_features_enter_target_model": False,
        "target_probability_monotone_in_distance": True,
        "hand_designed_reachable_frontier_prior": False,
        "candidate_rows": int(len(orders)),
        "causal_episodes": int(orders.episode_id.nunique()),
        "mean_candidates_per_episode": float(
            orders.groupby("episode_id").size().mean()
        ),
        "model_diagnostics": model_diagnostics,
        "account": account,
        "period_days": period_days,
        "source_summaries": source_summaries,
        "feature_contract": {
            "local_numeric": list(LOCAL_NUMERIC),
            "common_numeric_used_only_for_ownership": list(COMMON_NUMERIC),
            "target_numeric": list(TARGET_NUMERIC),
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    scored.to_csv(
        output_dir / "scored_destination_candidates.csv.gz",
        index=False,
        compression="gzip",
    )
    best_destination_per_episode(scored).to_csv(
        output_dir / "best_destination_per_episode.csv.gz",
        index=False,
        compression="gzip",
    )
    selected.to_csv(output_dir / "selected_orders.csv", index=False)
    trades.to_csv(output_dir / "closed_trades.csv", index=False)
    replacements.to_csv(
        output_dir / "replaced_pending_orders.csv", index=False
    )
    scored[
        ~scored.policy_eligible.fillna(False)
    ].sort_values(
        "expected_log_growth_per_hour",
        ascending=False,
        na_position="last",
    ).head(500).to_csv(
        output_dir / "near_miss_destination_candidates.csv", index=False
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    route_research(args.root, args.output)


if __name__ == "__main__":
    main()

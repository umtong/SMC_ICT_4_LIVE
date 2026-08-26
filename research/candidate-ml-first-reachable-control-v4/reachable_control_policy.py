#!/usr/bin/env python3
"""Fit and evaluate a direct reachable-control policy on candidate-4 plans.

The inherited engine creates the market event, entry, structural invalidation and
several fixed completion distances.  This policy estimates only two causal
hazards: whether the declared first return fills and, conditional on a fill,
whether the chosen completion frontier trades before invalidation.  Model
selection uses grouped out-of-fold development predictions.  Fresh predictions
are produced by models fitted on all development rows after the policy has been
fixed.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder

RISK = 0.03
DEV_PERIOD_HINTS = ("2024-feb", "2024-aug", "2025-feb", "2025-aug")
FRESH_PERIOD_HINTS = ("2025-nov", "2026-jan", "2026-mar", "2026-apr")

ALLOWED_NUMERIC_PREFIXES = (
    "approach_", "arm_", "auction_", "confirmation_", "dealing_range_",
    "departure_", "directional_gap_", "event_", "liquidity_", "relative_",
    "route_", "semantic_", "sequence_", "source_", "structure_", "target_",
    "volume_route_", "vwap_",
)
ALLOWED_NUMERIC_EXACT = {
    "gross_rr", "route_rr", "risk_bps", "zone_width_bps", "target_scale_minutes",
    "target_strength_ratio", "source_scale_minutes", "source_age_minutes",
    "source_strength_ratio", "source_semantic_weight", "source_defense_count",
}
ALLOWED_CATEGORICAL = (
    "family", "auction_phase", "entry_geometry", "route_kind", "setup_kind",
    "location_kind", "narrative_branch", "source_pool_kind", "side",
)
FORBIDDEN_FEATURE_TOKENS = (
    "target_first", "stop_first", "outcome", "realized", "resolved", "future_",
    "mfe", "mae", "exit_price", "exit_ts", "close_ts", "fill_ts", "filled_at",
    "cancel_ts", "expiry_ts", "pnl", "net_r", "trade_r", "return_after",
    "bars_after", "minutes_after", "symbol", "instrument", "absolute_price",
)


def first_column(frame: pd.DataFrame, names: Iterable[str], contains: tuple[str, ...] = ()) -> str | None:
    lower = {str(column).lower(): str(column) for column in frame.columns}
    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]
    if contains:
        for column in frame.columns:
            text = str(column).lower()
            if all(token in text for token in contains):
                return str(column)
    return None


def bool_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False).astype(bool)
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce").fillna(0).ne(0)
    text = series.astype(str).str.strip().str.lower()
    return text.isin({"1", "true", "yes", "y", "filled", "target", "tp", "win", "winner"})


def fraction_from_path(path: Path) -> float:
    for part in reversed(path.parts):
        match = re.fullmatch(r"f(\d{3})", part.lower())
        if match:
            return int(match.group(1)) / 1000.0
    raise ValueError(f"cannot infer fraction from {path}")


def read_plans(root: Path) -> pd.DataFrame:
    files = sorted(root.rglob("all_candidate_plans.csv.gz"))
    if not files:
        files = sorted(root.rglob("*candidate*plans*.csv*"))
    if not files:
        raise SystemExit(f"no aggregate candidate-plan files under {root}")
    pieces: list[pd.DataFrame] = []
    for file in files:
        frame = pd.read_csv(file, low_memory=False)
        if frame.empty:
            continue
        frame["route_fraction"] = fraction_from_path(file)
        frame["plan_source"] = str(file.relative_to(root))
        pieces.append(frame)
    if not pieces:
        raise SystemExit("all aggregate plan files were empty")
    return pd.concat(pieces, ignore_index=True, sort=False)


def infer_role(period: pd.Series, role: pd.Series | None) -> pd.Series:
    if role is not None:
        text = role.astype(str).str.lower()
        return pd.Series(np.where(text.str.contains("fresh|fixed|eval"), "fresh", "dev"), index=period.index)
    text = period.astype(str).str.lower()
    fresh = text.str.contains("fresh|fixed|eval")
    for token in FRESH_PERIOD_HINTS:
        fresh |= text.str.contains(re.escape(token))
    return pd.Series(np.where(fresh, "fresh", "dev"), index=period.index)


def normalize(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str | None]]:
    frame = raw.copy()
    period_col = first_column(frame, ["period", "window", "period_name", "evaluation_period"])
    role_col = first_column(frame, ["role", "sample_role", "evaluation_role"])
    decision_col = first_column(frame, ["decision_ts", "signal_ts", "plan_ts", "created_at", "decision_time"])
    fill_col = first_column(frame, ["filled", "is_filled", "entry_filled", "fill_flag"])
    fill_ts_col = first_column(frame, ["fill_ts", "entry_ts", "filled_at", "entry_time", "entry_timestamp"])
    cancel_col = first_column(frame, ["cancel_ts", "expiry_ts", "entry_expiry_ts", "pending_end_ts", "cancelled_at"])
    exit_col = first_column(frame, ["exit_ts", "close_ts", "resolved_at", "resolution_ts", "exit_time", "closed_at"])
    net_r_col = first_column(
        frame,
        ["net_r", "realized_net_r", "trade_net_r", "resolved_net_r", "outcome_r", "pnl_r", "return_r"],
        contains=("net", "r"),
    )
    target_first_col = first_column(frame, ["target_first", "is_target_first", "tp_first", "target_hit_first"])
    status_col = first_column(frame, ["status", "resolution", "outcome", "trade_outcome"])
    episode_col = first_column(frame, ["causal_episode_id", "episode_id", "source_episode_id", "event_id", "parent_episode_id"])
    symbol_col = first_column(frame, ["symbol", "instrument", "instrument_id"])
    side_col = first_column(frame, ["side", "direction", "trade_side"])
    gross_rr_col = first_column(frame, ["gross_rr", "planned_gross_rr", "route_rr", "reward_r"])
    pending_minutes_col = first_column(frame, ["entry_valid_minutes", "pending_minutes", "entry_ttl_minutes", "valid_for_minutes"])

    if period_col is None or decision_col is None or net_r_col is None:
        raise SystemExit(
            f"required plan columns missing: period={period_col} decision={decision_col} net_r={net_r_col}"
        )
    frame["_period"] = frame[period_col].astype(str).str.strip()
    frame["_role"] = infer_role(frame["_period"], frame[role_col] if role_col else None)
    frame["_decision"] = pd.to_datetime(frame[decision_col], utc=True, errors="coerce")
    frame["_fill_ts"] = pd.to_datetime(frame[fill_ts_col], utc=True, errors="coerce") if fill_ts_col else pd.NaT
    frame["_cancel_ts"] = pd.to_datetime(frame[cancel_col], utc=True, errors="coerce") if cancel_col else pd.NaT
    frame["_exit_ts"] = pd.to_datetime(frame[exit_col], utc=True, errors="coerce") if exit_col else pd.NaT
    frame["_net_r"] = pd.to_numeric(frame[net_r_col], errors="coerce")
    frame["_gross_rr"] = pd.to_numeric(frame[gross_rr_col], errors="coerce") if gross_rr_col else np.nan

    if fill_col:
        frame["_filled"] = bool_series(frame[fill_col])
    elif fill_ts_col:
        frame["_filled"] = frame["_fill_ts"].notna()
    elif status_col:
        frame["_filled"] = frame[status_col].astype(str).str.lower().str.contains("fill|target|stop|win|loss")
    else:
        frame["_filled"] = frame["_net_r"].notna()

    if target_first_col:
        frame["_won"] = bool_series(frame[target_first_col])
    elif status_col:
        status = frame[status_col].astype(str).str.lower()
        frame["_won"] = status.str.contains("target|take_profit|\btp\b|win")
    else:
        frame["_won"] = frame["_net_r"].gt(0)
    frame.loc[~frame["_filled"], "_won"] = False

    if pending_minutes_col:
        pending = pd.to_numeric(frame[pending_minutes_col], errors="coerce").clip(lower=1, upper=24 * 60).fillna(30)
    else:
        pending = pd.Series(30.0, index=frame.index)
    frame["_cancel_ts"] = frame["_cancel_ts"].fillna(frame["_decision"] + pd.to_timedelta(pending, unit="m"))
    frame["_fill_ts"] = frame["_fill_ts"].fillna(frame["_decision"])
    frame["_exit_ts"] = frame["_exit_ts"].fillna(frame["_fill_ts"] + pd.Timedelta(minutes=1))

    if episode_col:
        frame["_episode"] = frame[episode_col].astype(str)
    else:
        symbol = frame[symbol_col].astype(str) if symbol_col else pd.Series("MARKET", index=frame.index)
        side = frame[side_col].astype(str) if side_col else pd.Series("SIDE", index=frame.index)
        frame["_episode"] = symbol + "|" + side + "|" + frame["_decision"].dt.floor("min").astype(str)

    frame = frame[frame["_decision"].notna()].copy()
    key = frame["_period"].astype(str) + "|" + frame["_episode"].astype(str) + "|" + frame["route_fraction"].astype(str) + "|" + frame["_decision"].astype(str)
    frame = frame.loc[~key.duplicated()].reset_index(drop=True)

    schema = {
        "period": period_col, "role": role_col, "decision": decision_col, "fill": fill_col,
        "fill_ts": fill_ts_col, "cancel": cancel_col, "exit": exit_col, "net_r": net_r_col,
        "target_first": target_first_col, "status": status_col, "episode": episode_col,
        "symbol": symbol_col, "side": side_col, "gross_rr": gross_rr_col,
    }
    return frame, schema


def feature_columns(frame: pd.DataFrame) -> tuple[list[str], list[str]]:
    numeric: list[str] = ["route_fraction"]
    categorical: list[str] = []
    for column in frame.columns:
        lower = str(column).lower()
        if lower.startswith("_") or lower in {"route_fraction", "plan_source"}:
            continue
        if any(token in lower for token in FORBIDDEN_FEATURE_TOKENS):
            continue
        if lower in ALLOWED_CATEGORICAL:
            categorical.append(str(column))
            continue
        if lower in ALLOWED_NUMERIC_EXACT or lower.startswith(ALLOWED_NUMERIC_PREFIXES):
            values = pd.to_numeric(frame[column], errors="coerce")
            if values.notna().sum() >= max(20, int(0.05 * len(frame))) and values.nunique(dropna=True) > 1:
                frame[column] = values
                numeric.append(str(column))
    # Keep the model compact enough for grouped research while retaining every
    # market-mechanism family.  Rank by observed non-missing variance only, never
    # by target labels.
    if len(numeric) > 260:
        base = ["route_fraction"]
        ranked = []
        for column in numeric:
            if column == "route_fraction":
                continue
            values = pd.to_numeric(frame[column], errors="coerce")
            scale = float(values.var(skipna=True)) if values.notna().any() else 0.0
            coverage = float(values.notna().mean())
            ranked.append((coverage * math.log1p(max(0.0, scale)), column))
        numeric = base + [column for _, column in sorted(ranked, reverse=True)[:259]]
    return list(dict.fromkeys(numeric)), list(dict.fromkeys(categorical))


class ConstantProbability:
    def __init__(self, probability: float):
        self.probability = float(np.clip(probability, 1e-4, 1 - 1e-4))

    def predict_proba(self, X: Any) -> np.ndarray:
        p = np.full(len(X), self.probability, dtype=float)
        return np.column_stack([1 - p, p])


def build_model(numeric: list[str], categorical: list[str], seed: int) -> Pipeline:
    transformers = []
    if numeric:
        transformers.append(("numeric", SimpleImputer(strategy="median", add_indicator=True), numeric))
    if categorical:
        transformers.append(
            (
                "categorical",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("ordinal", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)),
                    ]
                ),
                categorical,
            )
        )
    preprocessor = ColumnTransformer(transformers=transformers, remainder="drop", sparse_threshold=0.0)
    model = HistGradientBoostingClassifier(
        learning_rate=0.045,
        max_iter=180,
        max_leaf_nodes=15,
        min_samples_leaf=24,
        l2_regularization=2.5,
        random_state=seed,
    )
    return Pipeline([("preprocessor", preprocessor), ("model", model)])


def balanced_weights(labels: np.ndarray, duplicate_weight: np.ndarray | None = None) -> np.ndarray:
    labels = labels.astype(int)
    positive = max(1, int(labels.sum()))
    negative = max(1, int(len(labels) - labels.sum()))
    weights = np.where(labels == 1, len(labels) / (2 * positive), len(labels) / (2 * negative)).astype(float)
    if duplicate_weight is not None:
        weights *= duplicate_weight
    return weights


def fit_binary(X: pd.DataFrame, y: pd.Series, numeric: list[str], categorical: list[str], seed: int, sample_weight: np.ndarray | None = None):
    labels = y.astype(int).to_numpy()
    if len(np.unique(labels)) < 2 or len(labels) < 40:
        return ConstantProbability(float(labels.mean()) if len(labels) else 0.0)
    model = build_model(numeric, categorical, seed)
    kwargs = {}
    if sample_weight is not None:
        kwargs["model__sample_weight"] = sample_weight
    model.fit(X, labels, **kwargs)
    return model


def predict_hazards(frame: pd.DataFrame, numeric: list[str], categorical: list[str]) -> tuple[pd.DataFrame, dict[str, Any]]:
    dev = frame[frame["_role"] == "dev"].copy()
    fresh = frame[frame["_role"] == "fresh"].copy()
    periods = sorted(dev["_period"].unique())
    if len(periods) < 3:
        raise SystemExit(f"need at least three development periods, found {periods}")

    dev["_p_fill"] = np.nan
    dev["_p_win"] = np.nan
    feature_set = numeric + categorical
    for fold, holdout in enumerate(periods):
        train = dev[dev["_period"] != holdout]
        test = dev[dev["_period"] == holdout]
        duplicate_count = train.groupby(["_period", "_episode", "_decision"])["route_fraction"].transform("count").clip(lower=1)
        fill_weight = balanced_weights(train["_filled"].astype(int).to_numpy(), 1.0 / duplicate_count.to_numpy(float))
        fill_model = fit_binary(train[feature_set], train["_filled"], numeric, categorical, 100 + fold, fill_weight)
        filled_train = train[train["_filled"] & train["_net_r"].notna()]
        win_model = fit_binary(
            filled_train[feature_set],
            filled_train["_won"],
            numeric,
            categorical,
            200 + fold,
            balanced_weights(filled_train["_won"].astype(int).to_numpy()) if len(filled_train) else None,
        )
        dev.loc[test.index, "_p_fill"] = fill_model.predict_proba(test[feature_set])[:, 1]
        dev.loc[test.index, "_p_win"] = win_model.predict_proba(test[feature_set])[:, 1]

    duplicate_count = dev.groupby(["_period", "_episode", "_decision"])["route_fraction"].transform("count").clip(lower=1)
    final_fill = fit_binary(
        dev[feature_set],
        dev["_filled"],
        numeric,
        categorical,
        401,
        balanced_weights(dev["_filled"].astype(int).to_numpy(), 1.0 / duplicate_count.to_numpy(float)),
    )
    filled_dev = dev[dev["_filled"] & dev["_net_r"].notna()]
    final_win = fit_binary(
        filled_dev[feature_set],
        filled_dev["_won"],
        numeric,
        categorical,
        402,
        balanced_weights(filled_dev["_won"].astype(int).to_numpy()) if len(filled_dev) else None,
    )
    fresh["_p_fill"] = final_fill.predict_proba(fresh[feature_set])[:, 1]
    fresh["_p_win"] = final_win.predict_proba(fresh[feature_set])[:, 1]

    combined = pd.concat([dev, fresh], ignore_index=False).sort_index()
    combined["_p_fill"] = combined["_p_fill"].clip(0.01, 0.99)
    combined["_p_win"] = combined["_p_win"].clip(0.01, 0.99)

    # The positive payoff is known before entry from the fixed target.  Use a
    # conservative cost allowance when the engine exposes gross rather than net
    # planned R.  Outcome R remains accounting-only.
    planned = combined["_gross_rr"].copy()
    positive_by_fraction = (
        dev.loc[dev["_filled"] & dev["_won"] & dev["_net_r"].notna()]
        .groupby("route_fraction")["_net_r"].median()
    )
    fallback = combined["route_fraction"].map(positive_by_fraction)
    planned = planned.fillna(fallback).fillna(1.0)
    combined["_planned_net_reward_r"] = np.maximum(0.05, planned.astype(float) - 0.08)
    win_log = np.log1p(RISK * combined["_planned_net_reward_r"].to_numpy(float))
    loss_log = math.log(1.0 - RISK)
    combined["_expected_log"] = combined["_p_fill"] * (
        combined["_p_win"] * win_log + (1.0 - combined["_p_win"]) * loss_log
    )
    bundle = {"fill_model": final_fill, "win_model": final_win, "numeric_features": numeric, "categorical_features": categorical}
    return combined, bundle


def mechanism(frame: pd.DataFrame) -> pd.Series:
    family_col = first_column(frame, ["family"])
    phase_col = first_column(frame, ["auction_phase"])
    family = frame[family_col].astype(str).str.upper() if family_col else pd.Series("OTHER", index=frame.index)
    phase = frame[phase_col].astype(str).str.upper() if phase_col else pd.Series("OTHER", index=frame.index)
    first = phase.str.contains("FIRST_RETEST")
    failed = family.str.contains("FAILED_AUCTION|REVERSAL")
    accepted = family.str.contains("ACCEPTED_AUCTION|CONTINUATION")
    return pd.Series(
        np.select(
            [failed & first, failed & ~first, accepted & first, accepted & ~first],
            ["failed_first_retest", "failed_other", "accepted_first_retest", "accepted_other"],
            default="other",
        ),
        index=frame.index,
    )


@dataclass(frozen=True)
class Component:
    mechanism: str
    fractions: tuple[float, ...]
    threshold: float
    score: float

    @property
    def name(self) -> str:
        fractions = "+".join(f"{value:.2f}" for value in self.fractions)
        return f"{self.mechanism}|f={fractions}|elog>={self.threshold:.6g}"


def component_mask(frame: pd.DataFrame, component: Component) -> pd.Series:
    mask = frame["_mechanism"].eq(component.mechanism) if component.mechanism != "all" else pd.Series(True, index=frame.index)
    mask &= frame["route_fraction"].round(6).isin([round(value, 6) for value in component.fractions])
    mask &= frame["_expected_log"] >= component.threshold
    return mask


def select_candidates(frame: pd.DataFrame, components: list[Component]) -> pd.DataFrame:
    pieces = []
    for rank, component in enumerate(components):
        subset = frame[component_mask(frame, component)].copy()
        subset["_component"] = component.name
        subset["_component_priority"] = component.score + 1e-9 * (len(components) - rank)
        pieces.append(subset)
    if not pieces:
        return frame.iloc[0:0].copy()
    candidates = pd.concat(pieces, ignore_index=True, sort=False)
    candidates = candidates.sort_values(
        ["_period", "_episode", "_decision", "_expected_log", "_component_priority", "route_fraction"],
        ascending=[True, True, True, False, False, True],
        kind="mergesort",
    )
    return candidates.drop_duplicates(["_period", "_episode", "_decision"], keep="first")


def simulate(frame: pd.DataFrame, components: list[Component], periods: list[str]) -> tuple[pd.DataFrame, dict[str, Any]]:
    candidates = select_candidates(frame, components)
    completed_rows: list[pd.Series] = []
    selected_plan_rows: list[pd.Series] = []
    period_metrics = []
    nav = 1.0
    peak = 1.0
    maximum_drawdown = 0.0

    for period in periods:
        period_frame = candidates[candidates["_period"] == period].copy()
        period_frame["_minute"] = period_frame["_decision"].dt.floor("min")
        busy_until = pd.Timestamp.min.tz_localize("UTC")
        locked_episodes: set[str] = set()
        before = nav
        period_completed = 0
        for _, simultaneous in period_frame.groupby("_minute", sort=True):
            decision = simultaneous["_decision"].min()
            if decision < busy_until:
                continue
            simultaneous = simultaneous[~simultaneous["_episode"].isin(locked_episodes)]
            if simultaneous.empty:
                continue
            chosen = simultaneous.sort_values(
                ["_expected_log", "_component_priority", "route_fraction"],
                ascending=[False, False, True],
                kind="mergesort",
            ).iloc[0]
            locked_episodes.add(str(chosen["_episode"]))
            selected_plan_rows.append(chosen)
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
        period_metrics.append(
            {
                "period": period,
                "completed_trades": period_completed,
                "nav_multiplier": float(nav / before) if before else 0.0,
                "log_growth": float(math.log(max(1e-12, nav / before))) if before else -math.inf,
            }
        )

    completed = pd.DataFrame(completed_rows) if completed_rows else frame.iloc[0:0].copy()
    selected_plans = pd.DataFrame(selected_plan_rows) if selected_plan_rows else frame.iloc[0:0].copy()
    values = completed["_net_r"].astype(float).to_numpy() if len(completed) else np.array([], dtype=float)
    gross_win = float(values[values > 0].sum()) if len(values) else 0.0
    gross_loss = float(-values[values < 0].sum()) if len(values) else 0.0
    logs = np.array([row["log_growth"] for row in period_metrics], dtype=float)
    days = max(1, 7 * len(periods))
    tpd = len(completed) / days
    robust = math.log(max(1e-12, nav)) - 0.60 * float(logs.std(ddof=0) * math.sqrt(max(1, len(logs)))) - 0.03 * max(0.0, 1.0 - tpd)
    if len(completed) == 0:
        robust = -1e9
    stats = {
        "selected_plans": int(len(selected_plans)),
        "completed_trades": int(len(completed)),
        "calendar_days": int(days),
        "trades_per_day": float(tpd),
        "win_rate": float((values > 0).mean()) if len(values) else 0.0,
        "mean_net_r": float(values.mean()) if len(values) else 0.0,
        "median_net_r": float(np.median(values)) if len(values) else 0.0,
        "profit_factor_r": float(gross_win / gross_loss) if gross_loss > 0 else (None if gross_win == 0 else math.inf),
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(maximum_drawdown),
        "robust_objective": float(robust),
        "by_period": period_metrics,
    }
    return completed, stats


def search_components(dev: pd.DataFrame, periods: list[str]) -> tuple[list[Component], pd.DataFrame]:
    fractions = tuple(sorted(float(value) for value in dev["route_fraction"].unique()))
    fraction_sets = [(value,) for value in fractions]
    fraction_sets += [tuple(value for value in fractions if value <= 0.25), tuple(value for value in fractions if value <= 0.33), fractions]
    fraction_sets = list(dict.fromkeys(item for item in fraction_sets if item))
    mechanisms = ["all"] + sorted(str(value) for value in dev["_mechanism"].unique())
    score_values = dev["_expected_log"].replace([np.inf, -np.inf], np.nan).dropna()
    quantiles = sorted(set(float(score_values.quantile(q)) for q in (0.35, 0.50, 0.62, 0.72, 0.80, 0.88, 0.94)))
    thresholds = sorted(set([-0.004, -0.002, -0.001, 0.0] + quantiles))

    rows = []
    components: list[Component] = []
    for mech in mechanisms:
        for fraction_set in fraction_sets:
            for threshold in thresholds:
                provisional = Component(mech, fraction_set, threshold, 0.0)
                _, stats = simulate(dev, [provisional], periods)
                if stats["completed_trades"] < 4:
                    continue
                active = sum(row["completed_trades"] > 0 for row in stats["by_period"])
                if active < 2:
                    continue
                component = Component(mech, fraction_set, threshold, float(stats["robust_objective"]))
                components.append(component)
                rows.append(
                    {
                        "component": component.name,
                        "mechanism": mech,
                        "fractions": "+".join(map(str, fraction_set)),
                        "threshold": threshold,
                        **{f"development_{key}": value for key, value in stats.items() if key != "by_period"},
                        "active_development_periods": active,
                    }
                )
    components.sort(key=lambda item: item.score, reverse=True)
    return components, pd.DataFrame(rows).sort_values("development_robust_objective", ascending=False)


def greedy_fusion(dev: pd.DataFrame, candidates: list[Component], periods: list[str]) -> list[Component]:
    # Components for the same mechanism are alternatives, not additive filters.
    pool = candidates[:180]
    selected: list[Component] = []
    current = -1e18
    for _ in range(5):
        best = None
        best_score = current
        used = {item.mechanism for item in selected if item.mechanism != "all"}
        for candidate in pool:
            if candidate in selected or candidate.mechanism == "all" or candidate.mechanism in used:
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


def safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): safe_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [safe_json(item) for item in value]
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

    raw = read_plans(args.root)
    frame, schema = normalize(raw)
    numeric, categorical = feature_columns(frame)
    predicted, bundle = predict_hazards(frame, numeric, categorical)
    predicted["_mechanism"] = mechanism(predicted)
    dev = predicted[predicted["_role"] == "dev"].copy()
    fresh = predicted[predicted["_role"] == "fresh"].copy()
    dev_periods = sorted(dev["_period"].unique())
    fresh_periods = sorted(fresh["_period"].unique())
    if not fresh_periods:
        raise SystemExit("no fresh plans after role normalization")

    components, catalog = search_components(dev, dev_periods)
    if not components:
        raise SystemExit("no direct reachable-control component completed four development trades")
    policies: dict[str, list[Component]] = {"best_single_component": [components[0]]}
    fused = greedy_fusion(dev, components, dev_periods)
    if fused:
        policies["mechanism_fusion"] = fused
    best_all = next((component for component in components if component.mechanism == "all"), None)
    if best_all is not None:
        policies["best_shared_router"] = [best_all]

    variants = []
    completed_by_variant: dict[str, pd.DataFrame] = {}
    stats_by_variant: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for name, policy in policies.items():
        dev_completed, dev_stats = simulate(dev, policy, dev_periods)
        fresh_completed, fresh_stats = simulate(fresh, policy, fresh_periods)
        completed_by_variant[name] = pd.concat([dev_completed.assign(_evaluation_role="dev"), fresh_completed.assign(_evaluation_role="fresh")], ignore_index=True, sort=False)
        stats_by_variant[name] = (dev_stats, fresh_stats)
        variants.append(
            {
                "variant": name,
                "development_objective": dev_stats["robust_objective"],
                "development_trades": dev_stats["completed_trades"],
                "development_mean_net_r": dev_stats["mean_net_r"],
                "development_nav": dev_stats["ending_nav_multiplier"],
                "development_maximum_drawdown": dev_stats["maximum_drawdown"],
                "fresh_trades": fresh_stats["completed_trades"],
                "fresh_mean_net_r": fresh_stats["mean_net_r"],
                "fresh_nav": fresh_stats["ending_nav_multiplier"],
                "fresh_maximum_drawdown": fresh_stats["maximum_drawdown"],
                "fresh_trades_per_day": fresh_stats["trades_per_day"],
                "components": len(policy),
            }
        )
    variant_table = pd.DataFrame(variants).sort_values(["development_objective", "development_trades"], ascending=[False, False])
    selected_name = str(variant_table.iloc[0]["variant"])
    selected_policy = policies[selected_name]
    development, fresh_stats = stats_by_variant[selected_name]
    completed = completed_by_variant[selected_name]

    summary = {
        "policy": "ML_FIRST_DIRECT_REACHABLE_CONTROL_V4",
        "selected_variant": selected_name,
        "risk_fraction": RISK,
        "components": [
            {
                "name": component.name,
                "mechanism": component.mechanism,
                "fractions": list(component.fractions),
                "minimum_expected_log_growth": component.threshold,
                "development_component_score": component.score,
            }
            for component in selected_policy
        ],
        "development": development,
        "fresh": fresh_stats,
        "development_periods": dev_periods,
        "fresh_periods": fresh_periods,
        "numeric_features": numeric,
        "categorical_features": categorical,
        "schema": schema,
        "causal_contract": {
            "selection_excludes_symbol_identity": True,
            "selection_excludes_absolute_prices": True,
            "selection_excludes_post_entry_path_and_outcomes": True,
            "one_global_pending_or_position": True,
            "one_selection_per_causal_episode": True,
            "entry_stop_and_target_inherited_before_selection": True,
        },
    }

    public_columns = [column for column in completed.columns if not str(column).startswith("_")]
    public_columns += ["_period", "_evaluation_role", "_decision", "_fill_ts", "_exit_ts", "_net_r", "_expected_log", "_p_fill", "_p_win", "_mechanism", "_component"]
    public_columns = list(dict.fromkeys(column for column in public_columns if column in completed.columns))
    completed[public_columns].to_csv(args.output / "completed_trades.csv", index=False)
    variant_table.to_csv(args.output / "variant_metrics.csv", index=False)
    catalog.to_csv(args.output / "component_catalog.csv", index=False)
    pd.DataFrame(development["by_period"] + fresh_stats["by_period"]).to_csv(args.output / "period_metrics.csv", index=False)
    audit = {
        "numeric_features": numeric,
        "categorical_features": categorical,
        "forbidden_tokens": list(FORBIDDEN_FEATURE_TOKENS),
        "columns_not_used": sorted(set(map(str, frame.columns)) - set(numeric) - set(categorical)),
    }
    (args.output / "feature_audit.json").write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    (args.output / "summary.json").write_text(json.dumps(safe_json(summary), indent=2, sort_keys=True) + "\n")
    joblib.dump(bundle | {"selected_policy": selected_policy, "schema": schema}, args.output / "model_bundle.joblib")
    print(json.dumps(safe_json(summary), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

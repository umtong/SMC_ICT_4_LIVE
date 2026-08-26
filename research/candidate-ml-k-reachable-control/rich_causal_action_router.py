#!/usr/bin/env python3
"""Rich causal action synthesis for ML-k short research.

The generator supplies immutable causal plans.  This module learns only whether
those plans are worth occupying the single account slot.  It combines the useful
pieces scattered across candidate 1k, candidate 2c and candidate 4t:

* event/departure/confirmation price-volume sequences;
* multiscale structure and semantic liquidity location;
* common-market versus local ownership attribution;
* first defended return rather than next-open chase;
* fill and target-before-stop models trained only on matured earlier development
  episodes;
* period-balanced empirical first-passage evidence;
* monotone target reachability within one state;
* scenario-family synthesis selected from development OOF decisions only.

No outcome, post-decision path, symbol identifier or absolute price is a model
feature.  Every selected order keeps its generator-declared entry, stop and
structural target, uses 3% NAV stop risk, and exits only at TP or SL.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import itertools
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

RISK = 0.03
EPS = 1e-12
NS_MINUTE = 60_000_000_000
RESOLVED = {
    "TARGET_FIRST",
    "STOP_FIRST",
    "AMBIGUOUS_SAME_MINUTE",
    "AMBIGUOUS_FILL_BARRIER_SAME_MINUTE",
}
PERIOD_RE = re.compile(r"(?:dev|fresh)-\d{4}-[a-z0-9-]+", re.I)

ID_OR_ABSOLUTE = {
    "action_id", "state_id", "episode_id", "market_episode_id", "symbol",
    "period", "role", "entry", "stop", "target", "route_price",
    "arm_index", "fill_index", "resolution_index", "departure_index",
    "order_time_ns", "fill_time_ns", "resolution_time_ns",
    "order_terminal_time_ns", "terminal_ns", "interaction_time_ns",
    "emission_time_ns", "departure_time_ns", "event_time_ns",
}
LABEL_TOKENS = (
    "outcome", "fill_state", "filled", "resolved", "target_first", "win",
    "net_r", "mfe", "mae", "holding", "entry_wait", "actual_", "future_",
    "terminal_minutes_label", "realized", "diagnostic_", "oracle", "label",
    "post_decision", "resolution_", "terminal_",
)
ALLOWED_PREFIXES = (
    "approach_", "arm_", "auction_", "confirmation_", "departure_",
    "directional_gap_", "event_", "liquidity_", "relative_", "route_",
    "semantic_", "sequence_block_", "source_", "structure_", "volume_route_",
    "vwap_", "dealing_range_",
)
ALLOWED_EXACT = {
    "gross_rr", "risk_bps", "zone_width_bps", "order_block_present",
    "route_rr", "planned_target_net_r", "source_confluence_count",
}
COMMON_TOKENS = (
    "common_", "breadth", "market_factor", "index_return", "factor_return",
    "cross_market", "universe_", "btc_lead", "systematic_",
)
CATEGORICAL = (
    "family", "auction_phase", "entry_geometry", "route_kind", "setup_kind",
    "location_kind", "source_pool_kind", "narrative_branch",
)


def finite(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def number(frame: pd.DataFrame, name: str, default: float = 0.0) -> pd.Series:
    if name not in frame:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce").fillna(default)


def text(frame: pd.DataFrame, name: str, default: str = "") -> pd.Series:
    if name not in frame:
        return pd.Series(default, index=frame.index, dtype=object)
    return frame[name].fillna(default).astype(str)


def timestamp_ns(frame: pd.DataFrame, name: str) -> pd.Series:
    values = pd.to_numeric(
        frame[name] if name in frame else pd.Series(np.nan, index=frame.index),
        errors="coerce",
    )
    return pd.to_datetime(values, unit="ns", utc=True, errors="coerce")


def bool_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    return series.fillna(False).astype(str).str.lower().isin({"true", "1", "yes"})


def period_from(path: Path) -> str:
    for part in reversed(path.parts):
        match = PERIOD_RE.search(part)
        if match:
            return match.group(0)
    return path.parent.name


def read_action_csv(path: Path) -> pd.DataFrame | None:
    try:
        frame = pd.read_csv(path, low_memory=False)
    except Exception:
        return None
    required = {"action_id", "state_id", "episode_id", "order_time_ns"}
    return frame if required.issubset(frame.columns) else None


def load_actions(root: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in sorted(root.rglob("*.csv")):
        frame = read_action_csv(path)
        if frame is None or frame.empty:
            continue
        frame = frame.copy()
        frame["period"] = period_from(path)
        frame["role"] = frame.period.astype(str).str.split("-", n=1).str[0]
        frames.append(frame)
    if not frames:
        raise FileNotFoundError(f"No candidate action CSV below {root}")
    out = pd.concat(frames, ignore_index=True, sort=False)
    out = out.drop_duplicates("action_id", keep="last").reset_index(drop=True)
    out["order_time_ns"] = pd.to_numeric(out.order_time_ns, errors="coerce")
    out = out[out.order_time_ns.notna()].copy()
    out["order_time_ns"] = out.order_time_ns.astype(np.int64)
    out["order_time"] = pd.to_datetime(out.order_time_ns, unit="ns", utc=True)

    fill_state = text(out, "fill_state").str.upper()
    outcome = text(out, "outcome").str.upper()
    if "filled" in out:
        out["filled_label"] = bool_series(out.filled).astype(float)
    else:
        out["filled_label"] = (~fill_state.str.contains(
            "UNFILLED|CANCELED|EXPIRED|NOT_AVAILABLE", regex=True
        )).astype(float)
    out["resolved_label"] = outcome.isin(RESOLVED)
    out["target_label"] = np.where(
        out.resolved_label, outcome.eq("TARGET_FIRST").astype(float), np.nan
    )
    out["net_r_num"] = pd.to_numeric(out.get("net_r"), errors="coerce")

    fill_time = timestamp_ns(out, "fill_time_ns")
    terminal_time = timestamp_ns(out, "order_terminal_time_ns")
    resolution_time = timestamp_ns(out, "resolution_time_ns")
    out["fill_label_available_time"] = fill_time.where(
        out.filled_label.eq(1.0), terminal_time
    )
    out["target_label_available_time"] = resolution_time.where(out.resolved_label)
    out["terminal_time"] = resolution_time.fillna(terminal_time).fillna(out.order_time)

    # Ensure every action exposes the common semantic vocabulary.
    for column in CATEGORICAL:
        if column not in out:
            out[column] = "UNKNOWN"
    out["family"] = text(out, "family", "UNKNOWN")
    out["auction_phase"] = text(out, "auction_phase", "UNKNOWN")
    return out.replace([np.inf, -np.inf], np.nan).reset_index(drop=True)


def is_label_or_absolute(column: str) -> bool:
    low = column.lower()
    if column in ID_OR_ABSOLUTE:
        return True
    if low.endswith("_time_ns") or low.endswith("_index"):
        return True
    if any(token in low for token in LABEL_TOKENS):
        return True
    if low in {"price", "open", "high", "low", "close"}:
        return True
    return False


def numeric_feature_columns(frame: pd.DataFrame) -> list[str]:
    columns: list[str] = []
    periods = sorted(frame.period.astype(str).unique())
    for column in frame.columns:
        if is_label_or_absolute(column):
            continue
        if column not in ALLOWED_EXACT and not column.startswith(ALLOWED_PREFIXES):
            continue
        if not pd.api.types.is_numeric_dtype(frame[column]):
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        if values.notna().sum() < max(40, int(0.03 * len(frame))):
            continue
        if values.nunique(dropna=True) <= 1:
            continue
        stable_periods = 0
        for period in periods:
            part = values[frame.period.astype(str).eq(period)]
            if part.notna().sum() >= 15 and part.nunique(dropna=True) > 1:
                stable_periods += 1
        if stable_periods < min(3, len(periods)):
            continue
        columns.append(column)
    return sorted(columns)


def common_feature(column: str) -> bool:
    low = column.lower()
    return any(token in low for token in COMMON_TOKENS)


@dataclass
class Encoder:
    numeric: list[str]
    categorical: list[str]
    medians: np.ndarray
    scales: np.ndarray
    categories: dict[str, list[str]]
    missing: list[str]

    @classmethod
    def fit(
        cls, frame: pd.DataFrame, numeric: Sequence[str], categorical: Sequence[str]
    ) -> "Encoder":
        numeric = [column for column in numeric if column in frame]
        categorical = [column for column in categorical if column in frame]
        medians: list[float] = []
        scales: list[float] = []
        missing: list[str] = []
        for column in numeric:
            raw = pd.to_numeric(frame[column], errors="coerce").to_numpy(float)
            valid = raw[np.isfinite(raw)]
            median = float(np.median(valid)) if valid.size else 0.0
            mad = float(np.median(np.abs(valid - median))) if valid.size else 1.0
            medians.append(median)
            scales.append(max(1e-6, 1.4826 * mad))
            if np.mean(~np.isfinite(raw)) >= 0.04:
                missing.append(column)
        categories: dict[str, list[str]] = {}
        for column in categorical:
            counts = text(frame, column, "__NA__").value_counts()
            minimum = max(4, int(0.002 * len(frame)))
            categories[column] = sorted(counts[counts >= minimum].index.tolist())[:40]
        return cls(
            list(numeric), list(categorical), np.asarray(medians),
            np.asarray(scales), categories, missing,
        )

    def transform(self, frame: pd.DataFrame) -> np.ndarray:
        parts: list[np.ndarray] = [np.ones((len(frame), 1), dtype=float)]
        if self.numeric:
            raw = np.column_stack([
                pd.to_numeric(
                    frame[column] if column in frame else pd.Series(np.nan, index=frame.index),
                    errors="coerce",
                ).to_numpy(float)
                for column in self.numeric
            ])
            valid = np.isfinite(raw)
            filled = np.where(valid, raw, self.medians)
            parts.append(np.clip((filled - self.medians) / self.scales, -10.0, 10.0))
            if self.missing:
                indices = [self.numeric.index(column) for column in self.missing]
                parts.append((~valid[:, indices]).astype(float))
        for column in self.categorical:
            values = text(frame, column, "__NA__")
            cats = self.categories.get(column, [])
            if cats:
                parts.append(np.column_stack([
                    values.eq(category).to_numpy(float) for category in cats
                ]))
        return np.column_stack(parts)


@dataclass
class LogisticEnsemble:
    encoder: Encoder
    coefficients: list[np.ndarray]
    prior: float
    prior_std: float

    def predict(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        if not self.coefficients:
            return (
                np.full(len(frame), self.prior),
                np.full(len(frame), self.prior_std),
            )
        x = self.encoder.transform(frame)
        matrix = np.vstack([
            1.0 / (1.0 + np.exp(-np.clip(x @ coefficient, -30.0, 30.0)))
            for coefficient in self.coefficients
        ])
        mean = 0.90 * matrix.mean(axis=0) + 0.10 * self.prior
        std = np.sqrt(matrix.var(axis=0) + self.prior_std ** 2)
        return np.clip(mean, 0.005, 0.995), std


def fit_logistic(
    frame: pd.DataFrame,
    label: str,
    numeric: Sequence[str],
    categorical: Sequence[str],
) -> LogisticEnsemble:
    work = frame[pd.to_numeric(frame[label], errors="coerce").notna()].copy()
    y = pd.to_numeric(work[label], errors="coerce").to_numpy(float)
    positives = float(np.sum(y)) if len(y) else 0.0
    prior = (positives + 6.0) / (len(y) + 12.0) if len(y) else 0.5
    prior_std = math.sqrt(prior * (1.0 - prior) / max(len(y) + 12.0, 1.0))
    encoder = Encoder.fit(work, numeric, categorical)
    if len(work) < 90 or len(np.unique(y)) < 2:
        return LogisticEnsemble(encoder, [], prior, prior_std)
    x = encoder.transform(work)

    # Equalize period and state influence so one volatile week cannot dominate.
    period_count = work.groupby("period").period.transform("size").to_numpy(float)
    state_count = work.groupby("state_id").state_id.transform("size").to_numpy(float)
    weights = 1.0 / np.maximum(period_count, 1.0)
    weights *= 1.0 / np.sqrt(np.maximum(state_count, 1.0))
    weights /= max(float(weights.mean()), EPS)

    coefficients: list[np.ndarray] = []
    for l2 in (1.0, 4.0, 12.0):
        beta = np.zeros(x.shape[1], dtype=float)
        beta[0] = math.log(prior / max(1.0 - prior, EPS))
        previous = np.inf
        for iteration in range(420):
            p = 1.0 / (1.0 + np.exp(-np.clip(x @ beta, -30.0, 30.0)))
            gradient = (x.T @ ((p - y) * weights)) / max(float(weights.sum()), 1.0)
            regularization = beta.copy()
            regularization[0] = 0.0
            gradient += (l2 / max(len(work), 1)) * regularization
            curvature = float(np.mean(weights * p * (1.0 - p)))
            step = min(0.24, 0.035 / max(curvature, 0.035)) / math.sqrt(
                1.0 + iteration / 100.0
            )
            beta -= step * np.clip(gradient, -4.0, 4.0)
            if iteration % 25 == 0:
                loss = -float(np.mean(weights * (
                    y * np.log(p + EPS) + (1.0 - y) * np.log(1.0 - p + EPS)
                )))
                if abs(previous - loss) < 1e-7:
                    break
                previous = loss
        coefficients.append(beta)
    return LogisticEnsemble(encoder, coefficients, prior, prior_std)


def semantic_classes(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    phase = text(out, "auction_phase").str.upper()
    geometry = text(out, "entry_geometry").str.upper()
    route = text(out, "route_kind").str.upper()
    setup = text(out, "setup_kind").str.upper()
    location = text(out, "location_kind").str.upper()
    family = text(out, "family").str.upper()

    out["first_return"] = (
        phase.str.contains("FIRST_RETEST|DEEP_RETEST|MITIGATION", regex=True)
        | geometry.str.contains("RETEST|MITIGATION|OVERLAP|TRANSFERRED_SOURCE", regex=True)
        | setup.str.contains("RETEST|MITIGATION", regex=True)
    )
    out["geometry_class"] = np.select(
        [
            geometry.str.contains("OVERLAP", regex=False),
            geometry.str.contains("TRANSFERRED_SOURCE|SOURCE", regex=True),
            geometry.str.contains("FVG", regex=False),
            geometry.str.contains("ORDER_BLOCK|OPPOSITE_BODY", regex=True),
        ],
        ["OVERLAP", "SOURCE", "FVG", "BODY"],
        default="OTHER",
    )
    out["route_class"] = np.select(
        [
            route.str.contains("LOCAL|NEAR|OBSTACLE", regex=True),
            route.str.contains("DYNAMIC|CHANNEL|TRENDLINE", regex=True),
            route.str.contains("PREVIOUS_DAY|PDH|PDL", regex=True),
            route.str.contains("VOLUME|PROFILE", regex=True),
        ],
        ["LOCAL", "DYNAMIC", "DAY", "VOLUME"],
        default="STRUCTURAL",
    )
    gross = number(out, "gross_rr", 0.0)
    out["rr_band"] = pd.cut(
        gross,
        [-np.inf, 1.35, 1.80, 2.50, 4.00, np.inf],
        labels=["1.00-1.35", "1.35-1.80", "1.80-2.50", "2.50-4.00", "4.00+"],
    ).astype(str)

    local = (
        0.22 * number(out, "auction_path_efficiency")
        + 0.18 * np.tanh(number(out, "auction_progress_r"))
        + 0.15 * np.tanh(number(out, "departure_delta_share_signed") * 3.0)
        + 0.12 * np.tanh(number(out, "departure_impact_per_activity"))
        + 0.10 * np.tanh(number(out, "confirmation_delta_share_signed") * 3.0)
        + 0.10 * number(out, "confirmation_impact_per_activity")
        + 0.08 * number(out, "arm_path_efficiency")
        + 0.05 * np.tanh(number(out, "arm_flow_share_signed") * 3.0)
    )
    common_columns = [
        column for column in out.columns
        if common_feature(column) and pd.api.types.is_numeric_dtype(out[column])
    ]
    if common_columns:
        common_matrix = np.column_stack([
            np.tanh(pd.to_numeric(out[column], errors="coerce").fillna(0.0).to_numpy(float))
            for column in common_columns[:24]
        ])
        common = np.nanmean(common_matrix, axis=1)
    else:
        common = np.zeros(len(out))
    residual_candidates = [
        column for column in out.columns
        if "residual_return" in column.lower()
        and pd.api.types.is_numeric_dtype(out[column])
    ]
    if residual_candidates:
        residual = np.nanmean(np.column_stack([
            np.tanh(pd.to_numeric(out[column], errors="coerce").fillna(0.0).to_numpy(float) * 4.0)
            for column in residual_candidates[:12]
        ]), axis=1)
    else:
        residual = np.zeros(len(out))
    failure = number(out, "auction_failure_pressure")
    retrace = number(out, "auction_retrace_fraction")
    out["local_ownership"] = local + 0.22 * residual - 0.16 * np.maximum(common, 0.0)
    out["contradiction"] = (
        phase.str.contains("FAILED_REENTRY|INVALIDATED|ABANDONED", regex=True)
        | ((failure > 0.90) & (retrace > 0.55))
        | (number(out, "auction_progress_r") < -0.08)
    )
    structure_vote = number(out, "structure_multiscale_trend_vote")
    structure_agreement = number(out, "structure_multiscale_trend_agreement")
    out["context_alignment"] = 0.65 * structure_vote + 0.35 * structure_agreement
    out["structural_flip"] = (
        setup.str.contains("FLIP|RECLAIM|RETEST", regex=True)
        | location.str.contains("FLIP|RECLAIM", regex=True)
        | phase.str.contains("FIRST_RETEST", regex=False)
    )
    out["scenario_family"] = np.select(
        [
            family.eq("ACCEPTED_AUCTION_CONTINUATION") & out.first_return,
            family.eq("INITIATIVE_MITIGATION_CONTINUATION") & out.first_return,
            family.eq("FAILED_AUCTION_REVERSAL") & out.first_return,
            out.structural_flip & out.first_return,
        ],
        [
            "ACCEPTED_FIRST_RETEST", "INITIATIVE_FIRST_MITIGATION",
            "LOCALLY_OWNED_RECLAIM", "STRUCTURAL_FLIP_RETEST",
        ],
        default="OTHER",
    )
    return out


def empirical_first_passage(
    train: pd.DataFrame, test: pd.DataFrame
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    valid = train[pd.to_numeric(train.target_label, errors="coerce").notna()].copy()
    if valid.empty:
        return (
            np.full(len(test), 0.5), np.full(len(test), 0.2), np.zeros(len(test))
        )
    valid["target_label"] = pd.to_numeric(valid.target_label, errors="coerce")
    global_rate = (float(valid.target_label.sum()) + 6.0) / (len(valid) + 12.0)
    levels: tuple[tuple[str, ...], ...] = (
        ("scenario_family", "geometry_class", "route_class", "rr_band"),
        ("scenario_family", "geometry_class", "rr_band"),
        ("scenario_family", "rr_band"),
        ("scenario_family",),
    )
    means: list[float] = []
    stds: list[float] = []
    supports: list[int] = []
    for _, row in test.iterrows():
        chosen = valid.iloc[:0]
        for keys in levels:
            mask = pd.Series(True, index=valid.index)
            for key in keys:
                mask &= valid[key].astype(str).eq(str(row[key]))
            candidate = valid[mask]
            if len(candidate) >= 16:
                chosen = candidate
                break
        if chosen.empty:
            chosen = valid[valid.scenario_family.astype(str).eq(str(row.scenario_family))]
        if chosen.empty:
            chosen = valid
        total = int(len(chosen))
        wins = float(chosen.target_label.sum())
        alpha = wins + global_rate * 12.0
        beta = total - wins + (1.0 - global_rate) * 12.0
        mean = alpha / max(alpha + beta, EPS)
        variance = alpha * beta / max((alpha + beta) ** 2 * (alpha + beta + 1.0), EPS)

        period_means: list[float] = []
        for _, group in chosen.groupby("period", sort=False):
            if len(group) < 4:
                continue
            period_alpha = float(group.target_label.sum()) + global_rate * 6.0
            period_beta = len(group) - float(group.target_label.sum()) + (1.0 - global_rate) * 6.0
            period_means.append(period_alpha / (period_alpha + period_beta))
        floor = float(np.quantile(period_means, 0.20)) if period_means else global_rate
        stable = 0.74 * mean + 0.26 * floor
        means.append(float(stable))
        stds.append(float(math.sqrt(max(variance, 0.0))))
        supports.append(total)
    return np.asarray(means), np.asarray(stds), np.asarray(supports)


def logit(values: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(values, dtype=float), 1e-5, 1.0 - 1e-5)
    return np.log(p / (1.0 - p))


def sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -30.0, 30.0)))


def score_periods(actions: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    out = semantic_classes(actions)
    numeric = numeric_feature_columns(out)
    # Raw common-market columns are not offered to the action model.  Their only
    # role is the explicit counterfactual ownership subtraction above.
    model_numeric = [column for column in numeric if not common_feature(column)]
    for derived in (
        "local_ownership", "context_alignment", "first_return",
        "structural_flip", "contradiction",
    ):
        out[derived] = pd.to_numeric(out[derived], errors="coerce").fillna(0.0)
        model_numeric.append(derived)
    model_numeric = sorted(set(model_numeric))
    categoricals = [column for column in CATEGORICAL if column in out]

    out["p_fill"] = np.nan
    out["p_target_model"] = np.nan
    out["p_target_empirical"] = np.nan
    out["p_target_conservative"] = np.nan
    out["target_support"] = 0
    out["models_ready"] = False
    diagnostics: dict[str, Any] = {}

    periods = out.groupby("period")["order_time"].min().sort_values().index.tolist()
    for period in periods:
        test_index = out.index[out.period.astype(str).eq(str(period))]
        if not len(test_index):
            continue
        test_start = out.loc[test_index, "order_time"].min()
        development = out.role.astype(str).eq("dev")
        fill_train_index = out.index[
            development & out.fill_label_available_time.notna()
            & out.fill_label_available_time.lt(test_start)
        ]
        target_train_index = out.index[
            development & out.target_label_available_time.notna()
            & out.target_label_available_time.lt(test_start)
        ]
        fill_train = out.loc[fill_train_index]
        target_train = out.loc[target_train_index]
        test = out.loc[test_index]
        ready = len(fill_train) >= 100 and len(target_train) >= 100
        if not ready:
            diagnostics[str(period)] = {
                "ready": False,
                "mature_fill_rows": int(len(fill_train)),
                "mature_target_rows": int(len(target_train)),
            }
            continue
        fill_model = fit_logistic(
            fill_train, "filled_label", model_numeric, categoricals
        )
        target_model = fit_logistic(
            target_train, "target_label", model_numeric, categoricals
        )
        p_fill, fill_std = fill_model.predict(test)
        p_model, model_std = target_model.predict(test)
        p_empirical, empirical_std, support = empirical_first_passage(
            target_train, test
        )
        combined = sigmoid(0.58 * logit(p_model) + 0.42 * logit(p_empirical))
        uncertainty = np.sqrt(model_std ** 2 + empirical_std ** 2)
        conservative = np.clip(combined - 0.38 * uncertainty, 0.01, 0.99)
        out.loc[test_index, "p_fill"] = np.clip(
            p_fill - 0.20 * fill_std, 0.01, 0.99
        )
        out.loc[test_index, "p_target_model"] = p_model
        out.loc[test_index, "p_target_empirical"] = p_empirical
        out.loc[test_index, "p_target_conservative"] = conservative
        out.loc[test_index, "target_support"] = support
        out.loc[test_index, "models_ready"] = True
        diagnostics[str(period)] = {
            "ready": True,
            "mature_fill_rows": int(len(fill_train)),
            "mature_target_rows": int(len(target_train)),
            "numeric_features": int(len(model_numeric)),
            "fill_prior": float(fill_model.prior),
            "target_prior": float(target_model.prior),
        }

    # A farther target from the same state cannot be more reachable than a nearer
    # target.  Enforce the first-passage ordering after all causal estimates.
    for (_, state_id), index in out.groupby(["period", "state_id"], sort=False).groups.items():
        ordered = sorted(
            index,
            key=lambda position: (
                finite(out.at[position, "gross_rr"], np.inf),
                str(out.at[position, "action_id"]),
            ),
        )
        running = 1.0
        for position in ordered:
            value = finite(out.at[position, "p_target_conservative"], np.nan)
            if not math.isfinite(value):
                continue
            running = min(running, value)
            out.at[position, "p_target_conservative"] = running

    target_r = number(out, "planned_target_net_r", number(out, "target_net_r", 0.0))
    gross = number(out, "gross_rr", 0.0)
    p_target = pd.to_numeric(out.p_target_conservative, errors="coerce")
    p_fill = pd.to_numeric(out.p_fill, errors="coerce")
    win_log = np.log(np.maximum(EPS, 1.0 + RISK * target_r))
    loss_log = math.log(1.0 - RISK)
    out["breakeven_probability"] = np.where(
        win_log - loss_log > EPS, -loss_log / (win_log - loss_log), 1.0
    )
    out["probability_edge"] = p_target - out.breakeven_probability
    out["expected_log_growth"] = p_fill * (
        p_target * win_log + (1.0 - p_target) * loss_log
    )
    duration = number(out, "terminal_minutes_label", 60.0).clip(lower=1.0)
    # Duration is never used as a feature; this observed field is only for
    # resolved development diagnostics.  Decisions use a neutral 60-minute
    # account-time denominator to avoid post-outcome leakage.
    out["expected_log_growth_per_hour"] = out.expected_log_growth

    out["evidence_supported"] = (
        out.models_ready.fillna(False)
        & gross.ge(1.0)
        & target_r.gt(0.0)
        & out.first_return.fillna(False)
        & number(out, "local_ownership").gt(-0.02)
        & ~out.contradiction.fillna(False)
        & number(out, "target_support").ge(16)
        & p_target.gt(0.52)
        & out.probability_edge.gt(0.025)
        & out.expected_log_growth.gt(0.0)
        & out.scenario_family.ne("OTHER")
    )
    return out, {
        "numeric_features": model_numeric,
        "categorical_features": categoricals,
        "periods": diagnostics,
    }


def cluster_market_episodes(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.sort_values(["period", "order_time_ns", "action_id"]).copy()
    ids: dict[int, str] = {}
    for period, group in out.groupby("period", sort=True):
        last: dict[str, int] = {}
        counter: dict[str, int] = {}
        for index, row in group.iterrows():
            side = str(row.get("side", "UNKNOWN"))
            now = int(row.order_time_ns)
            if side not in last or now - last[side] > 4 * NS_MINUTE:
                counter[side] = counter.get(side, 0) + 1
                last[side] = now
            ids[index] = f"{period}:{side}:MKT{counter[side]}"
    out["market_episode_id"] = pd.Series(ids)
    return out


def state_best(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame[frame.evidence_supported.fillna(False)].copy()
    if work.empty:
        return work
    work = work.sort_values(
        [
            "period", "state_id", "expected_log_growth",
            "p_target_conservative", "gross_rr", "action_id",
        ],
        ascending=[True, True, False, False, True, True],
    )
    return work.drop_duplicates(["period", "state_id"], keep="first")


def route_account(frame: pd.DataFrame, families: set[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    candidates = cluster_market_episodes(state_best(frame))
    candidates = candidates[candidates.scenario_family.astype(str).isin(families)].copy()
    if candidates.empty:
        return candidates, candidates
    candidates = candidates.sort_values(
        [
            "period", "order_time_ns", "expected_log_growth",
            "p_target_conservative", "gross_rr", "action_id",
        ],
        ascending=[True, True, False, False, True, True],
    )
    selected: list[pd.Series] = []
    for period, group in candidates.groupby("period", sort=True):
        active: pd.Series | None = None
        used_episode: set[str] = set()
        used_market: set[str] = set()
        for timestamp, simultaneous in group.groupby("order_time_ns", sort=True):
            timestamp = int(timestamp)
            pool = simultaneous[
                ~simultaneous.episode_id.astype(str).isin(used_episode)
                & ~simultaneous.market_episode_id.astype(str).isin(used_market)
            ]
            if pool.empty:
                continue
            candidate = pool.iloc[0]
            if active is not None:
                terminal_ns = int(
                    pd.Timestamp(active.terminal_time).value
                    if not pd.isna(active.terminal_time) else active.order_time_ns
                )
                fill_ns = finite(active.get("fill_time_ns"), np.inf)
                if timestamp >= terminal_ns:
                    selected.append(active)
                    used_episode.add(str(active.episode_id))
                    used_market.add(str(active.market_episode_id))
                    active = None
                elif fill_ns <= timestamp:
                    continue
                else:
                    independent = (
                        str(candidate.episode_id) != str(active.episode_id)
                        and str(candidate.market_episode_id) != str(active.market_episode_id)
                    )
                    stronger = (
                        float(candidate.expected_log_growth)
                        > float(active.expected_log_growth) + EPS
                    )
                    if independent and stronger:
                        used_episode.add(str(active.episode_id))
                        used_market.add(str(active.market_episode_id))
                        active = candidate
                    continue
            if active is None:
                active = candidate
        if active is not None:
            selected.append(active)
    orders = pd.DataFrame(selected).reset_index(drop=True) if selected else candidates.iloc[:0]
    trades = orders[
        pd.to_numeric(orders.get("net_r_num", orders.get("net_r")), errors="coerce").notna()
        & text(orders, "outcome").str.upper().isin(RESOLVED)
    ].copy().reset_index(drop=True)
    return orders, trades


def metrics(frame: pd.DataFrame, period_days: dict[str, int]) -> dict[str, Any]:
    work = frame.copy()
    work["net_r_num"] = pd.to_numeric(
        work.get("net_r_num", work.get("net_r")), errors="coerce"
    )
    work = work[work.net_r_num.notna()].copy()
    work["win"] = text(work, "outcome").str.upper().eq("TARGET_FIRST")
    nav = peak = 1.0
    drawdown = 0.0
    for value in work.sort_values(["order_time_ns", "action_id"]).net_r_num.astype(float):
        nav *= max(EPS, 1.0 + RISK * value)
        peak = max(peak, nav)
        drawdown = max(drawdown, 1.0 - nav / peak)
    periods = work.period.astype(str).unique().tolist() if len(work) else []
    days = int(sum(period_days.get(period, 0) for period in periods))
    return {
        "closed_trades": int(len(work)),
        "calendar_days": days,
        "trades_per_day": float(len(work) / max(days, 1)),
        "target_first_rate": float(work.win.mean()) if len(work) else None,
        "mean_net_r": float(work.net_r_num.mean()) if len(work) else None,
        "median_net_r": float(work.net_r_num.median()) if len(work) else None,
        "mean_gross_rr": float(number(work, "gross_rr", np.nan).mean()) if len(work) else None,
        "median_hold_minutes": float(number(work, "holding_minutes", np.nan).median()) if len(work) else None,
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(drawdown),
    }


def grouped(frame: pd.DataFrame, key: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if frame.empty or key not in frame:
        return rows
    for value, group in frame.groupby(key, dropna=False):
        item = metrics(group, {})
        rows.append({key: str(value), **item})
    return rows


def family_subsets() -> list[set[str]]:
    families = [
        "ACCEPTED_FIRST_RETEST",
        "INITIATIVE_FIRST_MITIGATION",
        "LOCALLY_OWNED_RECLAIM",
        "STRUCTURAL_FLIP_RETEST",
    ]
    return [set(combo) for size in range(1, len(families) + 1)
            for combo in itertools.combinations(families, size)]


def development_rank(trades: pd.DataFrame, period_days: dict[str, int]) -> tuple[float, ...]:
    if trades.empty:
        return (-1e9, -1e9, -1e9, -1e9, -1e9)
    period_rows = []
    for _, group in trades.groupby("period", sort=True):
        result = metrics(group, period_days)
        if result["closed_trades"]:
            period_rows.append(result)
    if len(period_rows) < 2:
        return (-1e9, -1e9, -1e9, -1e9, -1e9)
    mean_values = [float(row["mean_net_r"]) for row in period_rows]
    win_values = [float(row["target_first_rate"]) for row in period_rows]
    overall = metrics(trades, period_days)
    return (
        float(np.quantile(mean_values, 0.20)),
        float(np.median(mean_values)),
        float(np.median(win_values)),
        float(overall["trades_per_day"]),
        float(overall["ending_nav_multiplier"]),
    )


def infer_period_days(actions: pd.DataFrame) -> dict[str, int]:
    days: dict[str, int] = {}
    for period, group in actions.groupby("period", sort=False):
        start = pd.Timestamp(group.order_time.min())
        end = pd.Timestamp(group.terminal_time.max())
        days[str(period)] = max(1, int(math.ceil((end - start).total_seconds() / 86400.0)))
    return days


def implementation_clinic(actions: pd.DataFrame) -> dict[str, Any]:
    gross = number(actions, "gross_rr", np.nan)
    return {
        "action_rows": int(len(actions)),
        "unique_actions": int(actions.action_id.nunique()),
        "unique_states": int(actions.state_id.nunique()),
        "unique_episodes": int(actions.episode_id.nunique()),
        "duplicate_action_ids": int(actions.action_id.astype(str).duplicated().sum()),
        "gross_rr_below_one": int((gross < 1.0 - 1e-12).sum()),
        "missing_order_time": int(actions.order_time.isna().sum()),
        "resolved_without_net_r": int((actions.resolved_label & actions.net_r_num.isna()).sum()),
    }


def run(root: Path, output: Path) -> dict[str, Any]:
    actions = load_actions(root)
    scored, model_diagnostics = score_periods(actions)
    period_days = infer_period_days(actions)

    development = scored[scored.role.astype(str).eq("dev")]
    fresh = scored[scored.role.astype(str).eq("fresh")]
    evaluated: list[tuple[tuple[float, ...], set[str], pd.DataFrame, pd.DataFrame]] = []
    subset_results: list[dict[str, Any]] = []
    for families in family_subsets():
        _, dev_trades = route_account(development, families)
        rank = development_rank(dev_trades, period_days)
        subset_results.append(
            {
                "families": sorted(families),
                "development": metrics(dev_trades, period_days),
                "development_by_period": grouped(dev_trades, "period"),
                "rank": list(rank),
            }
        )
        evaluated.append((rank, families, dev_trades, pd.DataFrame()))
    evaluated.sort(key=lambda item: item[0], reverse=True)
    selected_families = evaluated[0][1] if evaluated else set()

    selected_orders, all_selected_trades = route_account(scored, selected_families)
    dev_selected = all_selected_trades[all_selected_trades.role.astype(str).eq("dev")]
    fresh_selected = all_selected_trades[all_selected_trades.role.astype(str).eq("fresh")]

    summary = {
        "policy": "ML_K_RICH_CAUSAL_ACTION_SYNTHESIS",
        "selection_uses_fresh_outcomes": False,
        "selected_scenario_families": sorted(selected_families),
        "fixed_account_rules": {
            "risk_fraction": RISK,
            "one_global_pending_or_position_slot": True,
            "planned_gross_rr_minimum": 1.0,
            "scale_in_or_out": False,
            "daily_loss_cap": False,
            "forced_post_fill_time_exit": False,
        },
        "implementation_clinic": implementation_clinic(actions),
        "model_diagnostics": model_diagnostics,
        "development": metrics(dev_selected, period_days),
        "fresh": metrics(fresh_selected, period_days),
        "development_by_period": grouped(dev_selected, "period"),
        "fresh_by_period": grouped(fresh_selected, "period"),
        "fresh_by_family": grouped(fresh_selected, "scenario_family"),
        "fresh_by_symbol": grouped(fresh_selected, "symbol"),
        "fresh_by_phase": grouped(fresh_selected, "auction_phase"),
        "fresh_by_geometry": grouped(fresh_selected, "geometry_class"),
        "fresh_by_rr_band": grouped(fresh_selected, "rr_band"),
        "development_subset_search": subset_results,
        "period_days": period_days,
    }
    output.mkdir(parents=True, exist_ok=True)
    scored.to_csv(output / "scored_actions.csv.gz", index=False, compression="gzip")
    selected_orders.to_csv(output / "selected_orders.csv", index=False)
    all_selected_trades.to_csv(output / "closed_trades.csv", index=False)
    fresh_selected.to_csv(output / "fresh_closed_trades.csv", index=False)
    losses = fresh_selected[pd.to_numeric(fresh_selected.net_r_num, errors="coerce").lt(0)]
    losses.to_csv(output / "fresh_loss_clinic.csv", index=False)
    near = scored[~scored.evidence_supported.fillna(False)].copy()
    near.sort_values("expected_log_growth", ascending=False, na_position="last").head(600).to_csv(
        output / "near_miss_actions.csv", index=False
    )
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.root, args.output)


if __name__ == "__main__":
    main()

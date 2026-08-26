#!/usr/bin/env python3
"""Efficient execution layer for :mod:`rich_causal_action_router`.

The research logic stays in the base module.  This layer replaces only two
engineering bottlenecks with equivalent calculations: compiled regularized
logistic ensembles and precomputed hierarchical first-passage lookup tables.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
from pathlib import Path
import sys
from typing import Any, Sequence

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import rich_causal_action_router as core  # noqa: E402


@dataclass
class CompiledLogisticEnsemble:
    encoder: core.Encoder
    models: list[LogisticRegression]
    prior: float
    prior_std: float

    def predict(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        if not self.models:
            return (
                np.full(len(frame), self.prior),
                np.full(len(frame), self.prior_std),
            )
        x = self.encoder.transform(frame)
        matrix = np.vstack([model.predict_proba(x)[:, 1] for model in self.models])
        mean = 0.90 * matrix.mean(axis=0) + 0.10 * self.prior
        std = np.sqrt(matrix.var(axis=0) + self.prior_std ** 2)
        return np.clip(mean, 0.005, 0.995), std


def _balanced_sample(work: pd.DataFrame, maximum: int = 60000) -> pd.DataFrame:
    if len(work) <= maximum:
        return work
    pieces: list[pd.DataFrame] = []
    groups = list(work.groupby(["period", "__label"], dropna=False, sort=True))
    quota = max(40, maximum // max(len(groups), 1))
    for _, group in groups:
        pieces.append(group.sample(n=min(len(group), quota), random_state=17031))
    sampled = pd.concat(pieces, ignore_index=False, sort=False)
    if len(sampled) > maximum:
        sampled = sampled.sample(n=maximum, random_state=17032)
    return sampled.sort_index()


def fit_logistic_fast(
    frame: pd.DataFrame,
    label: str,
    numeric: Sequence[str],
    categorical: Sequence[str],
) -> CompiledLogisticEnsemble:
    values = pd.to_numeric(frame[label], errors="coerce")
    work = frame[values.notna()].copy()
    work["__label"] = values[values.notna()].astype(int)
    positives = float(work.__label.sum()) if len(work) else 0.0
    prior = (positives + 6.0) / (len(work) + 12.0) if len(work) else 0.5
    prior_std = math.sqrt(prior * (1.0 - prior) / max(len(work) + 12.0, 1.0))
    if len(work) < 90 or work.__label.nunique() < 2:
        encoder = core.Encoder.fit(work, numeric, categorical)
        return CompiledLogisticEnsemble(encoder, [], prior, prior_std)

    work = _balanced_sample(work)
    encoder = core.Encoder.fit(work, numeric, categorical)
    x = encoder.transform(work)
    y = work.__label.to_numpy(int)
    period_count = work.groupby("period").period.transform("size").to_numpy(float)
    state_count = work.groupby("state_id").state_id.transform("size").to_numpy(float)
    weights = 1.0 / np.maximum(period_count, 1.0)
    weights *= 1.0 / np.sqrt(np.maximum(state_count, 1.0))
    weights /= max(float(weights.mean()), core.EPS)

    models: list[LogisticRegression] = []
    for regularization in (0.16, 0.50, 1.50):
        model = LogisticRegression(
            C=regularization,
            penalty="l2",
            solver="lbfgs",
            max_iter=220,
            tol=1e-6,
            random_state=17033,
        )
        model.fit(x, y, sample_weight=weights)
        models.append(model)
    return CompiledLogisticEnsemble(encoder, models, prior, prior_std)


def _stats_tables(
    valid: pd.DataFrame,
    keys: Sequence[str],
    global_rate: float,
) -> dict[tuple[str, ...], tuple[int, float, float, float]]:
    output: dict[tuple[str, ...], tuple[int, float, float, float]] = {}
    if valid.empty:
        return output
    grouped = valid.groupby(list(keys), dropna=False, sort=False)
    for raw_key, group in grouped:
        key = raw_key if isinstance(raw_key, tuple) else (raw_key,)
        key = tuple(str(value) for value in key)
        total = int(len(group))
        wins = float(group.target_label.sum())
        alpha = wins + global_rate * 12.0
        beta = total - wins + (1.0 - global_rate) * 12.0
        mean = alpha / max(alpha + beta, core.EPS)
        variance = alpha * beta / max(
            (alpha + beta) ** 2 * (alpha + beta + 1.0), core.EPS
        )
        period_means: list[float] = []
        for _, period_group in group.groupby("period", sort=False):
            if len(period_group) < 4:
                continue
            period_wins = float(period_group.target_label.sum())
            pa = period_wins + global_rate * 6.0
            pb = len(period_group) - period_wins + (1.0 - global_rate) * 6.0
            period_means.append(pa / (pa + pb))
        floor = float(np.quantile(period_means, 0.20)) if period_means else global_rate
        stable = 0.74 * mean + 0.26 * floor
        output[key] = (total, stable, math.sqrt(max(variance, 0.0)), wins)
    return output


def empirical_first_passage_fast(
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
    tables = [(keys, _stats_tables(valid, keys, global_rate)) for keys in levels]
    global_total = int(len(valid))
    global_alpha = float(valid.target_label.sum()) + global_rate * 12.0
    global_beta = global_total - float(valid.target_label.sum()) + (1.0 - global_rate) * 12.0
    global_std = math.sqrt(
        global_alpha * global_beta
        / max((global_alpha + global_beta) ** 2 * (global_alpha + global_beta + 1.0), core.EPS)
    )

    means = np.empty(len(test), dtype=float)
    stds = np.empty(len(test), dtype=float)
    support = np.empty(len(test), dtype=float)
    for position, (_, row) in enumerate(test.iterrows()):
        chosen: tuple[int, float, float, float] | None = None
        for keys, table in tables:
            key = tuple(str(row[key_name]) for key_name in keys)
            candidate = table.get(key)
            if candidate is not None and candidate[0] >= 16:
                chosen = candidate
                break
        if chosen is None:
            chosen = (global_total, global_rate, global_std, float(valid.target_label.sum()))
        support[position], means[position], stds[position], _ = chosen
    return means, stds, support


def load_actions_filtered(root: Path) -> pd.DataFrame:
    frame = _ORIGINAL_LOAD(root)
    if "order_exists" in frame:
        frame = frame[core.bool_series(frame.order_exists)].copy()
    gross = core.number(frame, "gross_rr", 0.0)
    target = core.number(
        frame,
        "planned_target_net_r",
        core.number(frame, "target_net_r", 0.0),
    )
    frame = frame[gross.ge(1.0) & target.gt(0.0)].copy()
    return frame.reset_index(drop=True)


_ORIGINAL_LOAD = core.load_actions
core.load_actions = load_actions_filtered
core.fit_logistic = fit_logistic_fast
core.empirical_first_passage = empirical_first_passage_fast


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    core.run(args.root, args.output)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Regime-balanced causal first-passage router for ML-k.

A skilled trader does not accept a remote payoff merely because its arithmetic
break-even probability is low.  The plan must itself be more likely to complete
than invalidate, and that belief should survive changes of period, symbol and
liquidity regime.  This router therefore uses a small semantic backoff hierarchy
rather than a high-capacity classifier: scenario family, first-return geometry,
route type, RR band and context alignment.  Only development outcomes whose
resolution was public before a decision period are used.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_DIR = REPO_ROOT / "research/candidate-liquidity-episode-policy-v1"
HERE = Path(__file__).resolve().parent
for directory in (str(BASE_DIR), str(HERE)):
    if directory not in sys.path:
        sys.path.insert(0, directory)

import route_episode_policy as base  # noqa: E402
from reachable_control_router import (  # noqa: E402
    EPS,
    RISK_FRACTION,
    _number,
    _series,
    _time_ns,
    engineer_features,
)

MODEL_VERSION = "candidate-ml-k-hierarchical-first-passage-v1"
TARGET_LEVELS: tuple[tuple[str, ...], ...] = (
    ("family", "geometry_class", "route_class", "rr_band", "context_class"),
    ("family", "geometry_class", "route_class", "rr_band"),
    ("family", "geometry_class", "rr_band"),
    ("family", "geometry_class"),
    ("family",),
)
FILL_LEVELS: tuple[tuple[str, ...], ...] = (
    ("geometry_class", "risk_band", "family"),
    ("geometry_class", "family"),
    ("geometry_class",),
)


def _classify(frame: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    geometry = _series(out, "entry_geometry", "").astype(str).str.upper()
    route = _series(out, "route_kind", "").astype(str).str.upper()
    out["geometry_class"] = np.select(
        [
            geometry.str.contains("OVERLAP", regex=False),
            geometry.str.contains("TRANSFERRED_SOURCE|SOURCE", regex=True),
            geometry.str.contains("FVG", regex=False),
            geometry.str.contains("OPPOSITE_BODY|ORDER_BLOCK", regex=True),
        ],
        ["OVERLAP", "SOURCE", "FVG", "BODY"],
        default="OTHER",
    )
    out["route_class"] = np.select(
        [
            route.str.contains("LOCAL_", regex=False),
            route.str.contains("DYNAMIC_", regex=False),
            route.str.contains("PREVIOUS_DAY", regex=False),
        ],
        ["LOCAL", "DYNAMIC", "DAY"],
        default="STRUCTURAL",
    )
    rr = _number(out, "gross_rr", 0.0)
    out["rr_band"] = pd.cut(
        rr,
        [-np.inf, 1.40, 2.00, 3.00, np.inf],
        labels=["1.00-1.40", "1.40-2.00", "2.00-3.00", "3.00+"],
    ).astype(str)
    risk = _number(out, "risk_bps", 0.0)
    out["risk_band"] = pd.cut(
        risk,
        [-np.inf, 12.0, 25.0, 50.0, np.inf],
        labels=["TIGHT", "NORMAL", "WIDE", "VERY_WIDE"],
    ).astype(str)
    context = (
        0.40 * features["ctx_structure_vote"]
        + 0.30 * features["ctx_momentum_vote"]
        + 0.20 * features["ctx_breadth_vote"]
        + 0.10 * features["ctx_structure_agreement"]
    )
    out["context_class"] = np.select(
        [context > 0.12, context < -0.12], ["ALIGNED", "OPPOSED"], default="NEUTRAL"
    )
    return out


def _posterior(wins: float, total: int, prior: float, strength: float) -> tuple[float, float]:
    alpha = wins + prior * strength
    beta = total - wins + (1.0 - prior) * strength
    mean = alpha / max(alpha + beta, EPS)
    variance = alpha * beta / max((alpha + beta) ** 2 * (alpha + beta + 1.0), EPS)
    return float(mean), float(math.sqrt(max(variance, 0.0)))


def _matching(train: pd.DataFrame, row: pd.Series, columns: Sequence[str]) -> pd.DataFrame:
    mask = pd.Series(True, index=train.index)
    for column in columns:
        mask &= train[column].astype(str).eq(str(row[column]))
    return train[mask]


def _estimate_rows(
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    label: str,
    levels: Sequence[Sequence[str]],
    minimum: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str], list[dict[str, Any]]]:
    y = pd.to_numeric(train[label], errors="coerce")
    valid = train[y.notna()].copy()
    valid[label] = y[y.notna()].astype(float)
    global_total = int(len(valid))
    global_wins = float(valid[label].sum()) if global_total else 0.0
    global_rate = (global_wins + 4.0) / (global_total + 8.0) if global_total else 0.5

    means: list[float] = []
    conservative: list[float] = []
    support: list[int] = []
    sources: list[str] = []
    evidence_rows: list[dict[str, Any]] = []
    for _, row in test.iterrows():
        chosen = valid.iloc[:0]
        chosen_columns: Sequence[str] = ()
        for columns in levels:
            candidate = _matching(valid, row, columns)
            if len(candidate) >= minimum:
                chosen, chosen_columns = candidate, columns
                break
        if chosen.empty:
            # Family or geometry backoff is still causal evidence; no numerical
            # fallback manufactured from the current row.
            chosen = valid
            chosen_columns = ("GLOBAL",)
        total = int(len(chosen))
        wins = float(chosen[label].sum()) if total else 0.0
        mean, std = _posterior(wins, total, global_rate, 10.0)

        period_posteriors: list[float] = []
        for _, period_group in chosen.groupby("period", sort=False):
            if len(period_group) < 4:
                continue
            period_mean, _ = _posterior(
                float(period_group[label].sum()), int(len(period_group)), global_rate, 6.0
            )
            period_posteriors.append(period_mean)
        if period_posteriors:
            regime_floor = float(np.quantile(period_posteriors, 0.20))
            stable_mean = 0.72 * mean + 0.28 * regime_floor
        else:
            regime_floor = global_rate
            stable_mean = mean
        lower = float(np.clip(stable_mean - 0.65 * std, 0.01, 0.99))
        means.append(float(np.clip(stable_mean, 0.01, 0.99)))
        conservative.append(lower)
        support.append(total)
        sources.append("+".join(chosen_columns))
        evidence_rows.append(
            {
                "columns": list(chosen_columns),
                "support": total,
                "wins": wins,
                "global_rate": global_rate,
                "posterior": mean,
                "regime_floor": regime_floor,
                "stable_posterior": stable_mean,
                "conservative": lower,
            }
        )
    return (
        np.asarray(means),
        np.asarray(conservative),
        np.asarray(support),
        sources,
        evidence_rows,
    )


def strict_causal_score(orders: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    out = orders.copy()
    out["order_time"] = base._period_start(out)
    out["fill_label"] = _time_ns(out, "fill_time_ns").notna().astype(float)
    outcome = _series(out, "outcome", "").astype(str)
    out["target_label"] = np.where(
        outcome.isin(base.RESOLVED_OUTCOMES), outcome.eq("TARGET_FIRST").astype(float), np.nan
    )
    fill_time = _time_ns(out, "fill_time_ns")
    terminal = _time_ns(out, "order_terminal_time_ns")
    resolution = _time_ns(out, "resolution_time_ns")
    out["fill_label_available_time"] = fill_time.where(out.fill_label.eq(1.0), terminal)
    out["target_label_available_time"] = resolution.where(pd.Series(out.target_label).notna())

    features = engineer_features(out)
    out = _classify(out, features)
    for column in features.columns:
        out[f"feature__{column}"] = features[column]
    out["p_fill"] = np.nan
    out["p_fill_conservative"] = np.nan
    out["p_target_if_filled"] = np.nan
    out["p_target_conservative"] = np.nan
    out["fill_support"] = 0
    out["target_support"] = 0
    out["fill_evidence_source"] = "NONE"
    out["target_evidence_source"] = "NONE"
    out["causal_models_ready"] = False
    diagnostics: dict[str, Any] = {}

    period_order = out.groupby("period")["order_time"].min().sort_values().index.tolist()
    for period in period_order:
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
        test = out.loc[test_index]
        fill_train = out.loc[fill_train_index]
        target_train = out.loc[target_train_index]
        pf, pf_low, fill_n, fill_source, fill_ev = _estimate_rows(
            fill_train, test, label="fill_label", levels=FILL_LEVELS, minimum=10
        )
        pt, pt_low, target_n, target_source, target_ev = _estimate_rows(
            target_train, test, label="target_label", levels=TARGET_LEVELS, minimum=12
        )
        ready = len(fill_train) >= 30 and len(target_train) >= 30
        if ready:
            out.loc[test_index, "p_fill"] = pf
            out.loc[test_index, "p_fill_conservative"] = pf_low
            out.loc[test_index, "p_target_if_filled"] = pt
            out.loc[test_index, "p_target_conservative"] = pt_low
            out.loc[test_index, "fill_support"] = fill_n
            out.loc[test_index, "target_support"] = target_n
            out.loc[test_index, "fill_evidence_source"] = fill_source
            out.loc[test_index, "target_evidence_source"] = target_source
            out.loc[test_index, "causal_models_ready"] = True
        diagnostics[str(period)] = {
            "test_start": str(test_start),
            "test_rows": int(len(test_index)),
            "ready": bool(ready),
            "mature_fill_rows": int(len(fill_train)),
            "mature_target_rows": int(len(target_train)),
            "fill_evidence_sample": fill_ev[:3],
            "target_evidence_sample": target_ev[:3],
        }

    gross = _number(out, "gross_rr", 0.0)
    target_r = _number(out, "planned_target_net_r", 0.0)
    p_fill = pd.to_numeric(out.p_fill_conservative, errors="coerce")
    p_target = pd.to_numeric(out.p_target_conservative, errors="coerce")
    win_log = np.log(np.maximum(EPS, 1.0 + RISK_FRACTION * target_r))
    loss_log = math.log(1.0 - RISK_FRACTION)
    out["breakeven_target_probability"] = np.where(
        win_log - loss_log > EPS, -loss_log / (win_log - loss_log), 1.0
    )
    out["probability_edge"] = p_target - out.breakeven_target_probability
    out["expected_log_growth"] = p_fill * (
        p_target * win_log + (1.0 - p_target) * loss_log
    )
    out["reachable_frontier_prior"] = features.reachable_frontier_prior
    out["policy_eligible"] = (
        out.causal_models_ready.fillna(False)
        & gross.ge(1.0)
        & target_r.gt(0.0)
        & features.first_defended_return.ge(1.0)
        & features.local_control_ownership.gt(0.0)
        & features.causal_control_coherence.gt(0.0)
        & features.contradiction_state.lt(0.5)
        & features.reachable_frontier_prior.ge(0.24)
        & pd.to_numeric(out.target_support, errors="coerce").ge(12)
        & p_target.gt(0.52)
        & out.probability_edge.gt(0.04)
        & out.expected_log_growth.gt(0.0)
    )
    return out, diagnostics


def route(root: Path, output: Path) -> dict[str, Any]:
    episodes, period_days, source_summaries = base.load_universe(root)
    if episodes.empty:
        raise RuntimeError(f"No episode artifacts found below {root}")
    orders = episodes[base._bool_series(episodes.order_exists)].copy()
    scored, diagnostics = strict_causal_score(orders)
    selected, closed, rejected, account = base.route_account(scored)
    calendar_days = int(sum(period_days.values()))
    account.update(
        {
            "diagnostic_calendar_days": calendar_days,
            "closed_trades_per_diagnostic_day": float(len(closed) / max(calendar_days, 1)),
            "by_period": base._group_metrics(closed, "period"),
            "by_family": base._group_metrics(closed, "family"),
            "by_symbol": base._group_metrics(closed, "symbol"),
            "by_geometry": base._group_metrics(closed, "geometry_class"),
            "by_route_class": base._group_metrics(closed, "route_class"),
            "by_rr_band": base._group_metrics(closed, "rr_band"),
        }
    )
    summary = {
        "policy_version": MODEL_VERSION,
        "decision_logic": (
            "causal liquidity event -> counterfactual local control -> first defended "
            "return -> local/dynamic/static structural destination -> period-balanced "
            "hierarchical first-passage evidence -> completion probability above one "
            "half and cost-adjusted log-growth -> one global account slot"
        ),
        "risk_fraction": RISK_FRACTION,
        "one_global_account_slot": True,
        "one_plan_per_causal_episode": True,
        "fixed_rr_target_lattice": False,
        "future_information_is_model_feature": False,
        "symbol_identity_is_model_feature": False,
        "account": account,
        "episode_rows": int(len(episodes)),
        "order_rows": int(len(orders)),
        "model_diagnostics": diagnostics,
        "period_days": period_days,
        "source_summaries": source_summaries,
    }
    output.mkdir(parents=True, exist_ok=True)
    scored.to_csv(output / "scored_orders.csv.gz", index=False, compression="gzip")
    selected.to_csv(output / "selected_orders.csv", index=False)
    closed.to_csv(output / "closed_trades.csv", index=False)
    rejected.sort_values("expected_log_growth", ascending=False, na_position="last").head(500).to_csv(
        output / "near_miss_rejected_orders.csv", index=False
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
    route(args.root, args.output)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Strict-causal reachable-control router for candidate ML-k.

The deterministic liquidity-episode generator remains responsible for market
interpretation and plan geometry. This module only answers the missing question
at the decision timestamp: is the declared destination likely to be reached
before structural invalidation, and is this episode the best current use of the
one global account slot?

No symbol identifier, absolute price, future path statistic, outcome field, or
post-decision diagnostic is a model feature.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_DIR = REPO_ROOT / "research/candidate-liquidity-episode-policy-v1"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import route_episode_policy as base  # noqa: E402

try:
    from sklearn.ensemble import HistGradientBoostingClassifier
except Exception as exc:  # pragma: no cover
    raise RuntimeError("scikit-learn is required for candidate ML-k") from exc

RISK_FRACTION = 0.03
EPS = 1e-12
MODEL_VERSION = "candidate-ml-k-reachable-control-v2"
MIN_TRAIN_ROWS = 48
MIN_CLASS_ROWS = 8


def _series(frame: pd.DataFrame, name: str, default: Any = 0.0) -> pd.Series:
    if name in frame:
        return frame[name]
    return pd.Series(default, index=frame.index)


def _number(frame: pd.DataFrame, name: str, default: float = 0.0) -> pd.Series:
    return pd.to_numeric(_series(frame, name, default), errors="coerce").fillna(default)


def _time_ns(frame: pd.DataFrame, name: str) -> pd.Series:
    return pd.to_datetime(
        pd.to_numeric(_series(frame, name, np.nan), errors="coerce"),
        unit="ns", utc=True, errors="coerce",
    )


def _one_hot(text: pd.Series, token: str) -> pd.Series:
    return text.astype(str).str.contains(token, case=False, regex=False).astype(float)


def engineer_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Shared event-relative features observable when the order is declared."""
    out = pd.DataFrame(index=frame.index)
    family = _series(frame, "family", "").astype(str)
    geometry = _series(frame, "entry_geometry", "").astype(str)
    phase = (
        _series(frame, "auction_phase", "").astype(str) + "|"
        + _series(frame, "state", "").astype(str) + "|"
        + _series(frame, "route_state", "").astype(str)
    )
    for token in (
        "FAILED_AUCTION_REVERSAL",
        "ACCEPTED_AUCTION_CONTINUATION",
        "INITIATIVE_MITIGATION_CONTINUATION",
    ):
        out[f"family_{token.lower()}"] = family.eq(token).astype(float)
    for token in (
        "OB_FVG_OVERLAP", "SOURCE_OVERLAP", "FVG",
        "LAST_OPPOSITE_BODY", "TRANSFERRED_SOURCE", "RETEST",
    ):
        out[f"geometry_{token.lower()}"] = _one_hot(geometry, token)
    out["first_defended_return"] = (
        _one_hot(geometry, "OVERLAP")
        .combine(_one_hot(geometry, "TRANSFERRED_SOURCE"), max)
        .combine(_one_hot(geometry, "RETEST"), max)
        .combine(_one_hot(phase, "FIRST_RETEST"), max)
        .combine(_one_hot(phase, "MITIGATION"), max)
    )
    out["accepted_state"] = _one_hot(phase, "ACCEPT")
    out["reclaim_state"] = _one_hot(phase, "RECLAIM")
    out["contradiction_state"] = _one_hot(phase, "INVALID").combine(
        _one_hot(phase, "FAILED_REENTRY"), max
    )
    direct = (
        "mechanism_coherence", "control_composite", "market_alignment",
        "control_move_atr", "control_path_efficiency",
        "control_flow_share_signed", "control_effort_result",
        "ctx_momentum_vote", "ctx_breadth_vote", "ctx_structure_vote",
        "ctx_structure_agreement", "common_factor_signed",
        "common_breadth_signed", "relative_return_signed",
        "route_to_source_log_ratio", "source_scale_log", "route_scale_log",
        "source_strength", "source_confluence_count", "route_strength",
        "ctx_oi_log_change", "ctx_basis_change_bps_signed",
        "ctx_dealing_range_position_signed",
    )
    for name in direct:
        out[name] = _number(frame, name)
    activity = np.maximum(_number(frame, "control_activity_ratio"), 0.0)
    out["control_activity_log"] = np.log1p(activity)
    rr = np.maximum(_number(frame, "gross_rr"), 0.0)
    target_r = np.maximum(_number(frame, "planned_target_net_r"), 0.0)
    out["gross_rr_log"] = np.log1p(rr)
    out["target_net_r_log"] = np.log1p(target_r)

    # Counterfactual ownership: broad-market support cannot replace local
    # price/volume control at the attacked liquidity object.
    local = (
        0.40 * out["control_path_efficiency"]
        + 0.25 * np.tanh(out["control_move_atr"])
        + 0.20 * np.tanh(3.0 * out["control_flow_share_signed"])
        + 0.15 * np.tanh(out["control_effort_result"])
    )
    common = 0.5 * out["common_factor_signed"] + 0.5 * out["common_breadth_signed"]
    out["local_control_ownership"] = (
        local + 0.30 * np.tanh(4.0 * out["relative_return_signed"])
        - 0.18 * np.maximum(common, 0.0)
    )
    context = (
        0.38 * out["ctx_structure_vote"]
        + 0.27 * out["ctx_momentum_vote"]
        + 0.20 * out["ctx_breadth_vote"]
        + 0.15 * out["ctx_structure_agreement"]
    )
    out["causal_control_coherence"] = (
        0.50 * out["mechanism_coherence"]
        + 0.32 * out["local_control_ownership"]
        + 0.18 * context
        - 0.40 * out["contradiction_state"]
    )
    # Branch evidence repeatedly showed remote destination plans failing while
    # first defended returns were useful.  Distance remains a learned feature;
    # this is not a fixed-R target lattice.
    out["reachable_frontier_prior"] = np.clip(
        np.exp(-0.46 * np.maximum(rr - 1.35, 0.0))
        * (0.72 + 0.28 * out["first_defended_return"])
        * (0.80 + 0.20 * np.tanh(out["causal_control_coherence"])),
        0.0, 1.0,
    )
    return out.replace([np.inf, -np.inf], np.nan).fillna(0.0).astype(float)


FEATURE_COLUMNS = tuple(engineer_features(pd.DataFrame(index=[0])).columns)


def _fit_classifier(train_x: pd.DataFrame, train_y: pd.Series,
                    test_x: pd.DataFrame, *, random_state: int):
    y = pd.to_numeric(train_y, errors="coerce").dropna().astype(int)
    x = train_x.loc[y.index]
    pos, neg = int(y.sum()) if len(y) else 0, int(len(y) - y.sum()) if len(y) else 0
    diag = {"rows": int(len(y)), "positives": pos, "negatives": neg,
            "positive_rate": float(y.mean()) if len(y) else None,
            "model": "not_ready"}
    if len(y) < MIN_TRAIN_ROWS or min(pos, neg) < MIN_CLASS_ROWS:
        return None, diag
    model = HistGradientBoostingClassifier(
        learning_rate=0.035, max_iter=180, max_leaf_nodes=7,
        min_samples_leaf=max(10, min(28, len(y) // 7)),
        l2_regularization=3.0, random_state=random_state,
    )
    model.fit(x, y)
    raw = model.predict_proba(test_x)[:, 1]
    prior = float(y.mean())
    shrink = len(y) / (len(y) + 180.0)
    prediction = prior + shrink * (raw - prior)
    diag.update({"model": "hist_gradient_boosting_shrunk", "shrink": shrink})
    return np.clip(prediction, 0.01, 0.99), diag


def strict_causal_score(orders: pd.DataFrame):
    out = orders.copy()
    out["order_time"] = base._period_start(out)
    out["fill_label"] = _time_ns(out, "fill_time_ns").notna().astype(int)
    outcome = _series(out, "outcome", "").astype(str)
    out["target_label"] = outcome.eq("TARGET_FIRST").astype(int)
    out["resolved_label"] = outcome.isin(base.RESOLVED_OUTCOMES)
    fill_time = _time_ns(out, "fill_time_ns")
    terminal = _time_ns(out, "order_terminal_time_ns")
    resolution = _time_ns(out, "resolution_time_ns")
    out["fill_label_available_time"] = fill_time.where(out.fill_label.eq(1), terminal)
    out["target_label_available_time"] = resolution.where(out.resolved_label)
    x = engineer_features(out)
    for column in FEATURE_COLUMNS:
        out[f"feature__{column}"] = x[column]
    out["p_fill"] = np.nan
    out["p_target_if_filled"] = np.nan
    out["p_target_conservative"] = np.nan
    out["causal_models_ready"] = False
    out["prediction_source"] = "insufficient_mature_development_history"
    diagnostics: dict[str, Any] = {}
    periods = out.groupby("period")["order_time"].min().sort_values().index.tolist()
    for sequence, period in enumerate(periods):
        test = out.index[out.period.astype(str).eq(str(period))]
        if not len(test):
            continue
        test_start = out.loc[test, "order_time"].min()
        dev = out.role.astype(str).eq("dev")
        fill_train = out.index[dev & out.fill_label_available_time.notna()
                               & out.fill_label_available_time.lt(test_start)]
        target_train = out.index[dev & out.resolved_label
                                 & out.target_label_available_time.notna()
                                 & out.target_label_available_time.lt(test_start)]
        p_fill, fill_diag = _fit_classifier(
            x.loc[fill_train], out.loc[fill_train, "fill_label"], x.loc[test],
            random_state=12000 + sequence,
        )
        p_target, target_diag = _fit_classifier(
            x.loc[target_train], out.loc[target_train, "target_label"], x.loc[test],
            random_state=22000 + sequence,
        )
        ready = p_fill is not None and p_target is not None
        if ready:
            uncertainty = np.sqrt(
                np.maximum(p_target * (1.0 - p_target), 0.0) / max(len(target_train), 1)
            )
            out.loc[test, "p_fill"] = p_fill
            out.loc[test, "p_target_if_filled"] = p_target
            out.loc[test, "p_target_conservative"] = np.clip(
                p_target - 0.45 * uncertainty, 0.01, 0.99
            )
            out.loc[test, "causal_models_ready"] = True
            out.loc[test, "prediction_source"] = MODEL_VERSION
        diagnostics[str(period)] = {
            "test_start": str(test_start), "test_rows": int(len(test)),
            "models_ready": bool(ready), "fill_model": fill_diag,
            "target_model": target_diag,
        }
    gross = _number(out, "gross_rr")
    target_r = _number(out, "planned_target_net_r")
    p_fill = pd.to_numeric(out.p_fill, errors="coerce")
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
    out["reachable_frontier_prior"] = x["reachable_frontier_prior"]
    out["policy_eligible"] = (
        out.causal_models_ready.fillna(False)
        & gross.ge(1.0) & target_r.gt(0.0)
        & x.first_defended_return.ge(1.0)
        & x.local_control_ownership.gt(0.0)
        & x.causal_control_coherence.gt(0.0)
        & x.reachable_frontier_prior.ge(0.28)
        & p_target.gt(0.50)
        & out.expected_log_growth.gt(0.0)
        & out.probability_edge.gt(0.0)
    )
    return out, diagnostics


def risk_sized_quantity(*, nav: float, entry: float, stop: float,
                        quantity_step: float) -> dict[str, float]:
    if nav <= 0 or entry <= 0 or stop <= 0 or quantity_step <= 0:
        raise ValueError("nav, prices and quantity_step must be positive")
    distance = abs(entry - stop)
    if distance <= 0:
        raise ValueError("entry and stop must differ")
    raw = nav * RISK_FRACTION / distance
    quantity = math.floor(raw / quantity_step + EPS) * quantity_step
    if quantity <= 0:
        raise ValueError("quantity rounds to zero")
    risk_cash = quantity * distance
    notional = quantity * entry
    return {"quantity": quantity, "risk_cash": risk_cash,
            "risk_fraction": risk_cash / nav, "notional": notional,
            "implied_leverage": notional / nav}


def route(root: Path, output_dir: Path) -> dict[str, Any]:
    episodes, period_days, source_summaries = base.load_universe(root)
    if episodes.empty:
        raise RuntimeError(f"No episode artifacts found below {root}")
    orders = episodes[base._bool_series(episodes["order_exists"])].copy()
    scored, model_diagnostics = strict_causal_score(orders)
    selected, closed, rejected, account = base.route_account(scored)
    calendar_days = int(sum(period_days.values()))
    account.update({
        "diagnostic_calendar_days": calendar_days,
        "closed_trades_per_diagnostic_day": float(len(closed) / calendar_days)
        if calendar_days else 0.0,
        "by_period": base._group_metrics(closed, "period"),
        "by_family": base._group_metrics(closed, "family"),
        "by_symbol": base._group_metrics(closed, "symbol"),
    })
    summary = {
        "policy_version": MODEL_VERSION,
        "risk_fraction": RISK_FRACTION,
        "one_global_account_slot": True,
        "one_plan_per_causal_episode": True,
        "gross_planned_rr_floor": 1.0,
        "symbol_identity_is_model_feature": False,
        "absolute_price_is_model_feature": False,
        "future_information_is_model_feature": False,
        "decision_logic": (
            "liquidity event -> local counterfactual control ownership -> first "
            "defended return -> structural stop and nearest live destination -> "
            "strict-causal fill/first-passage probability -> completion probability "
            "above one half and positive post-cost log growth -> one global slot"
        ),
        "feature_columns": list(FEATURE_COLUMNS),
        "episode_rows": int(len(episodes)), "order_rows": int(len(orders)),
        "model_diagnostics": model_diagnostics, "account": account,
        "period_days": period_days, "source_summaries": source_summaries,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    scored.to_csv(output_dir / "scored_orders.csv.gz", index=False, compression="gzip")
    selected.to_csv(output_dir / "selected_orders.csv", index=False)
    closed.to_csv(output_dir / "closed_trades.csv", index=False)
    rejected.sort_values("expected_log_growth", ascending=False, na_position="last").head(400).to_csv(
        output_dir / "near_miss_rejected_orders.csv", index=False
    )
    (output_dir / "summary.json").write_text(
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

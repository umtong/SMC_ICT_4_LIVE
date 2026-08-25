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
MODEL_VERSION = "candidate-ml-k-reachable-control-v1"
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
        unit="ns",
        utc=True,
        errors="coerce",
    )


def _one_hot(text: pd.Series, token: str) -> pd.Series:
    return text.astype(str).str.contains(token, case=False, regex=False).astype(float)


def engineer_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Build shared, event-relative, pointwise features available at decision time."""
    output = pd.DataFrame(index=frame.index)
    family = _series(frame, "family", "").astype(str)
    geometry = _series(frame, "entry_geometry", "").astype(str)
    state = (
        _series(frame, "state", "").astype(str)
        + "|"
        + _series(frame, "state_id", "").astype(str)
        + "|"
        + _series(frame, "route_state", "").astype(str)
    )

    for token in (
        "FAILED_AUCTION_REVERSAL",
        "ACCEPTED_AUCTION_CONTINUATION",
        "INITIATIVE_MITIGATION_CONTINUATION",
    ):
        output[f"family_{token.lower()}"] = family.eq(token).astype(float)
    for token in (
        "OB_FVG_OVERLAP",
        "FVG",
        "LAST_OPPOSITE_BODY",
        "TRANSFERRED_SOURCE",
        "CAUSAL_DEPARTURE_BAND",
        "RETEST",
    ):
        output[f"geometry_{token.lower()}"] = _one_hot(geometry, token)

    output["first_return"] = (
        _one_hot(geometry, "FIRST")
        .combine(_one_hot(state, "FIRST_RETEST"), max)
        .combine(_one_hot(state, "MITIGATION"), max)
    )
    output["acceptance_state"] = _one_hot(state, "ACCEPT")
    output["reclaim_state"] = _one_hot(state, "RECLAIM")
    output["response_state"] = _one_hot(state, "RESPONSE")

    direct = (
        "mechanism_coherence",
        "control_composite",
        "market_alignment",
        "control_move_atr",
        "control_path_efficiency",
        "control_flow_share_signed",
        "control_effort_result",
        "ctx_momentum_vote",
        "ctx_breadth_vote",
        "ctx_structure_vote",
        "ctx_structure_agreement",
        "route_to_source_log_ratio",
        "source_scale_log",
        "route_scale_log",
        "ctx_oi_log_change",
        "ctx_basis_change_bps_signed",
        "ctx_dealing_range_position_signed",
    )
    for name in direct:
        output[name] = _number(frame, name)

    activity = np.maximum(_number(frame, "control_activity_ratio"), 0.0)
    output["control_activity_log"] = np.log1p(activity)
    gross_rr = np.maximum(_number(frame, "gross_rr", 0.0), 0.0)
    target_net_r = np.maximum(_number(frame, "planned_target_net_r", 0.0), 0.0)
    output["gross_rr_log"] = np.log1p(gross_rr)
    output["target_net_r_log"] = np.log1p(target_net_r)
    output["rr_excess_over_local_2r"] = np.maximum(gross_rr - 2.0, 0.0)
    output["rr_excess_over_3r"] = np.maximum(gross_rr - 3.0, 0.0)

    # Repeated branch evidence showed that remote raw liquidity pools fail while
    # first defended returns toward the nearest inherited frontier are useful.
    # This descriptor is an input to the learned first-passage model, not a
    # fixed-R target lattice.
    control = np.tanh(
        0.45 * output["control_composite"]
        + 0.25 * output["control_move_atr"]
        + 0.20 * output["control_path_efficiency"]
        + 0.10 * output["market_alignment"]
    )
    frontier_proximity = np.exp(-0.55 * np.maximum(gross_rr - 1.35, 0.0))
    first_return_credit = 0.72 + 0.28 * output["first_return"]
    transfer_credit = (
        0.68
        + 0.16 * output["acceptance_state"]
        + 0.16 * output["reclaim_state"]
    )
    output["reachable_frontier_prior"] = np.clip(
        frontier_proximity
        * first_return_credit
        * transfer_credit
        * (0.82 + 0.18 * control),
        0.0,
        1.0,
    )
    return output.replace([np.inf, -np.inf], np.nan).fillna(0.0).astype(float)


FEATURE_COLUMNS = tuple(engineer_features(pd.DataFrame(index=[0])).columns)


def _fit_classifier(
    train_x: pd.DataFrame,
    train_y: pd.Series,
    test_x: pd.DataFrame,
    *,
    random_state: int,
) -> tuple[np.ndarray | None, dict[str, Any]]:
    y = pd.to_numeric(train_y, errors="coerce").dropna().astype(int)
    x = train_x.loc[y.index]
    positives = int(y.sum()) if len(y) else 0
    negatives = int(len(y) - positives)
    diagnostic: dict[str, Any] = {
        "rows": int(len(y)),
        "positives": positives,
        "negatives": negatives,
        "positive_rate": float(y.mean()) if len(y) else None,
        "model": "not_ready",
    }
    if len(y) < MIN_TRAIN_ROWS or min(positives, negatives) < MIN_CLASS_ROWS:
        return None, diagnostic
    model = HistGradientBoostingClassifier(
        learning_rate=0.04,
        max_iter=160,
        max_leaf_nodes=7,
        min_samples_leaf=max(10, min(28, len(y) // 7)),
        l2_regularization=2.0,
        random_state=random_state,
    )
    model.fit(x, y)
    raw = model.predict_proba(test_x)[:, 1]
    base_rate = float(y.mean())
    shrink = len(y) / (len(y) + 160.0)
    predicted = base_rate + shrink * (raw - base_rate)
    diagnostic.update(
        {"model": "hist_gradient_boosting_shrunk", "shrink": shrink}
    )
    return np.clip(predicted, 0.01, 0.99), diagnostic


def strict_causal_score(
    orders: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Score each period using only labels observable before that period starts."""
    output = orders.copy()
    output["order_time"] = base._period_start(output)
    output["fill_label"] = _time_ns(output, "fill_time_ns").notna().astype(int)
    outcome = _series(output, "outcome", "").astype(str)
    output["target_label"] = outcome.eq("TARGET_FIRST").astype(int)
    output["resolved_label"] = outcome.isin(base.RESOLVED_OUTCOMES)

    fill_time = _time_ns(output, "fill_time_ns")
    terminal_time = _time_ns(output, "order_terminal_time_ns")
    resolution_time = _time_ns(output, "resolution_time_ns")
    output["fill_label_available_time"] = fill_time.where(
        output.fill_label.eq(1), terminal_time
    )
    output["target_label_available_time"] = resolution_time.where(
        output.resolved_label
    )

    x_all = engineer_features(output)
    for column in FEATURE_COLUMNS:
        output[f"feature__{column}"] = x_all[column]
    output["p_fill"] = np.nan
    output["p_target_if_filled"] = np.nan
    output["p_target_conservative"] = np.nan
    output["causal_models_ready"] = False
    output["prediction_source"] = "insufficient_mature_development_history"

    diagnostics: dict[str, Any] = {}
    period_order = (
        output.groupby("period")["order_time"].min().sort_values().index.tolist()
    )
    for sequence, period in enumerate(period_order):
        test_index = output.index[output.period.astype(str).eq(str(period))]
        if not len(test_index):
            continue
        test_start = output.loc[test_index, "order_time"].min()
        development = output.role.astype(str).eq("dev")
        fill_index = output.index[
            development
            & output.fill_label_available_time.notna()
            & output.fill_label_available_time.lt(test_start)
        ]
        target_index = output.index[
            development
            & output.resolved_label
            & output.target_label_available_time.notna()
            & output.target_label_available_time.lt(test_start)
        ]
        fill_prediction, fill_diag = _fit_classifier(
            x_all.loc[fill_index],
            output.loc[fill_index, "fill_label"],
            x_all.loc[test_index],
            random_state=12000 + sequence,
        )
        target_prediction, target_diag = _fit_classifier(
            x_all.loc[target_index],
            output.loc[target_index, "target_label"],
            x_all.loc[test_index],
            random_state=22000 + sequence,
        )
        ready = fill_prediction is not None and target_prediction is not None
        if ready:
            output.loc[test_index, "p_fill"] = fill_prediction
            output.loc[test_index, "p_target_if_filled"] = target_prediction
            uncertainty = np.sqrt(
                np.maximum(target_prediction * (1.0 - target_prediction), 0.0)
                / max(len(target_index), 1)
            )
            conservative = np.clip(
                target_prediction - 0.35 * uncertainty, 0.01, 0.99
            )
            output.loc[test_index, "p_target_conservative"] = conservative
            output.loc[test_index, "causal_models_ready"] = True
            output.loc[test_index, "prediction_source"] = MODEL_VERSION
        diagnostics[str(period)] = {
            "test_start": str(test_start),
            "test_rows": int(len(test_index)),
            "models_ready": bool(ready),
            "fill_model": fill_diag,
            "target_model": target_diag,
            "latest_fill_label": (
                str(output.loc[fill_index, "fill_label_available_time"].max())
                if len(fill_index)
                else None
            ),
            "latest_target_label": (
                str(output.loc[target_index, "target_label_available_time"].max())
                if len(target_index)
                else None
            ),
        }

    gross_rr = _number(output, "gross_rr")
    target_net_r = _number(output, "planned_target_net_r")
    p_fill = pd.to_numeric(output.p_fill, errors="coerce")
    p_target = pd.to_numeric(output.p_target_conservative, errors="coerce")
    win_log = np.log(np.maximum(EPS, 1.0 + RISK_FRACTION * target_net_r))
    loss_log = math.log(1.0 - RISK_FRACTION)
    output["breakeven_target_probability"] = np.where(
        win_log - loss_log > EPS,
        -loss_log / (win_log - loss_log),
        1.0,
    )
    output["probability_edge"] = (
        p_target - output.breakeven_target_probability
    )
    output["expected_log_growth"] = p_fill * (
        p_target * win_log + (1.0 - p_target) * loss_log
    )
    reachable = x_all["reachable_frontier_prior"]
    output["reachable_frontier_prior"] = reachable
    output["policy_eligible"] = (
        output.causal_models_ready.fillna(False)
        & gross_rr.ge(1.0)
        & target_net_r.gt(0.0)
        & reachable.ge(0.20)
        & output.expected_log_growth.gt(0.0)
        & output.probability_edge.gt(0.0)
    )
    return output, diagnostics


def risk_sized_quantity(
    *, nav: float, entry: float, stop: float, quantity_step: float
) -> dict[str, float]:
    """Use the full account as margin while fixing stop risk at 3% of NAV."""
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
    return {
        "quantity": quantity,
        "risk_cash": risk_cash,
        "risk_fraction": risk_cash / nav,
        "notional": notional,
        "implied_leverage": notional / nav,
    }


def route(root: Path, output_dir: Path) -> dict[str, Any]:
    episodes, period_days, source_summaries = base.load_universe(root)
    if episodes.empty:
        raise RuntimeError(f"No episode artifacts found below {root}")
    orders = episodes[base._bool_series(episodes["order_exists"])].copy()
    scored, model_diagnostics = strict_causal_score(orders)
    selected, closed, rejected, account = base.route_account(scored)
    calendar_days = int(sum(period_days.values()))
    account.update(
        {
            "diagnostic_calendar_days": calendar_days,
            "closed_trades_per_diagnostic_day": (
                float(len(closed) / calendar_days) if calendar_days else 0.0
            ),
            "by_period": base._group_metrics(closed, "period"),
            "by_family": base._group_metrics(closed, "family"),
            "by_symbol": base._group_metrics(closed, "symbol"),
        }
    )
    summary = {
        "policy_version": MODEL_VERSION,
        "decision_policy": (
            "deterministic causal liquidity episode -> inherited local target and "
            "structural stop -> strict-causal fill and target-before-stop models -> "
            "reachable-frontier and positive expected log-growth -> one global "
            "pending/position slot"
        ),
        "risk_fraction": RISK_FRACTION,
        "one_global_account_slot": True,
        "one_plan_per_causal_episode": True,
        "gross_planned_rr_floor": 1.0,
        "symbol_identity_is_model_feature": False,
        "absolute_price_is_model_feature": False,
        "future_information_is_model_feature": False,
        "feature_columns": list(FEATURE_COLUMNS),
        "episode_rows": int(len(episodes)),
        "order_rows": int(len(orders)),
        "model_diagnostics": model_diagnostics,
        "account": account,
        "period_days": period_days,
        "source_summaries": source_summaries,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    scored.to_csv(
        output_dir / "scored_orders.csv.gz", index=False, compression="gzip"
    )
    selected.to_csv(output_dir / "selected_orders.csv", index=False)
    closed.to_csv(output_dir / "closed_trades.csv", index=False)
    rejected.sort_values(
        "expected_log_growth", ascending=False, na_position="last"
    ).head(400).to_csv(output_dir / "near_miss_rejected_orders.csv", index=False)
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
    route(args.root, args.output)


if __name__ == "__main__":
    main()

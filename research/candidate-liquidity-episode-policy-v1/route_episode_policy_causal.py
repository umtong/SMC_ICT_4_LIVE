#!/usr/bin/env python3
"""Strict causal router for the restored liquidity episode policy.

The base account router and report writer are reused, but both preprocessing and
prediction are replaced here:

- features are converted pointwise; no full-sample clipping or normalization;
- a historical label enters training only after that order's fill/cancel or
  TP/SL outcome was actually observable before the new period began;
- periods without enough mature history do not trade through a heuristic
  probability fallback;
- only models fitted on chronologically earlier development observations may
  occupy the single global account slot.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import route_episode_policy as base
from episode_policy_features import FEATURE_COLUMNS


if base.HistGradientBoostingClassifier is None:
    raise RuntimeError(
        "scikit-learn HistGradientBoostingClassifier is required for causal routing"
    )


def causal_numeric_features(frame: pd.DataFrame) -> pd.DataFrame:
    output = pd.DataFrame(index=frame.index)
    for column in FEATURE_COLUMNS:
        if column in frame:
            output[column] = pd.to_numeric(frame[column], errors="coerce")
        else:
            output[column] = 0.0
    return output.replace([np.inf, -np.inf], np.nan).fillna(0.0).astype(float)


def _time_ns(frame: pd.DataFrame, column: str) -> pd.Series:
    values = (
        pd.to_numeric(frame[column], errors="coerce")
        if column in frame
        else pd.Series(np.nan, index=frame.index)
    )
    return pd.to_datetime(values, unit="ns", utc=True, errors="coerce")


def strict_causal_predictions(
    orders: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    output = orders.copy()
    output["order_time"] = base._period_start(output)
    output["fill_label"] = _time_ns(output, "fill_time_ns").notna().astype(int)
    output["target_label"] = output.get(
        "outcome", pd.Series("", index=output.index)
    ).astype(str).eq("TARGET_FIRST").astype(int)
    output["resolved_label"] = output.get(
        "outcome", pd.Series("", index=output.index)
    ).astype(str).isin(base.RESOLVED_OUTCOMES)

    fill_time = _time_ns(output, "fill_time_ns")
    terminal_time = _time_ns(output, "order_terminal_time_ns")
    resolution_time = _time_ns(output, "resolution_time_ns")
    output["fill_label_available_time"] = fill_time.where(
        output.fill_label.eq(1), terminal_time
    )
    output["target_label_available_time"] = resolution_time.where(
        output.resolved_label
    )

    x_all = causal_numeric_features(output)
    output["p_fill"] = np.nan
    output["p_target_if_filled"] = np.nan
    output["prediction_source"] = "insufficient_mature_history"
    output["causal_models_ready"] = False

    period_order = (
        output.groupby("period")["order_time"].min().sort_values().index.tolist()
    )
    model_diagnostics: dict[str, object] = {}
    for sequence, period in enumerate(period_order):
        test_mask = output.period.astype(str).eq(str(period))
        test_index = output.index[test_mask]
        if not len(test_index):
            continue
        test_start = output.loc[test_index, "order_time"].min()

        fill_train_mask = (
            output.role.astype(str).eq("dev")
            & output.fill_label_available_time.notna()
            & output.fill_label_available_time.lt(test_start)
        )
        fill_train_index = output.index[fill_train_mask]
        target_train_mask = (
            output.role.astype(str).eq("dev")
            & output.resolved_label
            & output.target_label_available_time.notna()
            & output.target_label_available_time.lt(test_start)
        )
        target_train_index = output.index[target_train_mask]

        fill_pred, fill_diag = base._fit_predict_classifier(
            x_all.loc[fill_train_index],
            output.loc[fill_train_index, "fill_label"],
            x_all.loc[test_index],
            random_state=4100 + sequence,
        )
        target_pred, target_diag = base._fit_predict_classifier(
            x_all.loc[target_train_index],
            output.loc[target_train_index, "target_label"],
            x_all.loc[test_index],
            random_state=8100 + sequence,
        )
        ready = fill_pred is not None and target_pred is not None
        if ready:
            output.loc[test_index, "p_fill"] = fill_pred
            output.loc[test_index, "p_target_if_filled"] = target_pred
            output.loc[test_index, "causal_models_ready"] = True
            output.loc[test_index, "prediction_source"] = (
                f"strict_causal_models(fill={fill_diag['model']},"
                f"target={target_diag['model']})"
            )

        model_diagnostics[str(period)] = {
            "test_rows": int(len(test_index)),
            "test_start": str(test_start),
            "models_ready": bool(ready),
            "fill_training_periods": sorted(
                output.loc[fill_train_index, "period"].astype(str).unique().tolist()
            ),
            "target_training_periods": sorted(
                output.loc[target_train_index, "period"].astype(str).unique().tolist()
            ),
            "fill_label_latest_available_time": (
                str(output.loc[fill_train_index, "fill_label_available_time"].max())
                if len(fill_train_index)
                else None
            ),
            "target_label_latest_available_time": (
                str(output.loc[target_train_index, "target_label_available_time"].max())
                if len(target_train_index)
                else None
            ),
            "fill_model": fill_diag,
            "target_model": target_diag,
        }

    target_r = pd.to_numeric(
        output.get("planned_target_net_r", 0.0), errors="coerce"
    ).fillna(0.0)
    win_log = np.log(np.maximum(base.EPS, 1.0 + base.RISK_FRACTION * target_r))
    loss_log = math.log(1.0 - base.RISK_FRACTION)
    denominator = win_log - loss_log
    output["breakeven_target_probability"] = np.where(
        denominator > base.EPS, -loss_log / denominator, 1.0
    )

    p_fill = pd.to_numeric(output.p_fill, errors="coerce")
    p_target = pd.to_numeric(output.p_target_if_filled, errors="coerce")
    output["expected_log_growth"] = p_fill * (
        p_target * win_log + (1.0 - p_target) * loss_log
    )
    output["probability_edge"] = (
        p_target - output["breakeven_target_probability"]
    )
    output["policy_eligible"] = (
        output.causal_models_ready.fillna(False)
        & output.expected_log_growth.gt(0.0)
        & output.probability_edge.gt(0.0)
        & target_r.gt(0.0)
    )
    return output, model_diagnostics


base._numeric_features = causal_numeric_features
base.causal_predictions = strict_causal_predictions

if __name__ == "__main__":
    base.main()

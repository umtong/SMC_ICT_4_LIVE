#!/usr/bin/env python3
"""Causal feature contract for the TP/SL-only hierarchical router.

Only the number and geometry of plans that exist at the decision are features.  Whether
those plans later resolved or stayed open, their realized fill economics, and every
future barrier result remain labels.  Planned gross RR supplies the runtime reward side
of expected log growth; actual target R is accounting only.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import hierarchical_barrier_router as core


def _causal_feature_columns(frame: pd.DataFrame) -> tuple[list[str], list[str], list[str], list[str]]:
    categories = sorted(
        [column for column in core.CATEGORICAL_CANDIDATES if column in frame.columns]
    )
    numeric: list[str] = []
    explicitly_future = {
        "event_resolved_count",
        "event_censored_count",
        "event_win_share",
        "actual_target_net_r",
        "actual_stop_net_r",
        "actual_entry",
    }
    for column in frame.columns:
        lower = str(column).lower()
        if column in core.IDENTITY or column in categories or column in explicitly_future:
            continue
        if any(token in lower for token in core.LABEL_TOKENS):
            continue
        if column in {"risk_bps", "gross_rr"}:
            continue
        if (
            pd.api.types.is_numeric_dtype(frame[column])
            and frame[column].notna().mean() > 0.20
            and frame[column].nunique(dropna=True) > 1
        ):
            numeric.append(column)
    causal_event_shape = ["event_plan_count", "event_family_count"]
    event_numeric = sorted(set(numeric + causal_event_shape))
    plan_numeric = sorted(set(numeric + causal_event_shape + ["risk_bps", "gross_rr"]))
    event_categories = [column for column in ("family",) if column in categories]
    return event_numeric, event_categories, plan_numeric, categories


def _causal_predict(bundle: dict[str, object], frame: pd.DataFrame) -> pd.DataFrame:
    event = core._event_rows(frame)
    ze = bundle["event_prep"].transform(  # type: ignore[index,union-attr]
        event[bundle["event_numeric"] + bundle["event_categories"]]  # type: ignore[operator,index]
    )
    opportunity_tree = bundle["opportunity_tree"]  # type: ignore[assignment]
    opportunity_hist = bundle["opportunity_hist"]  # type: ignore[assignment]
    event_r_tree = bundle["event_r_tree"]  # type: ignore[assignment]
    event_r_hist = bundle["event_r_hist"]  # type: ignore[assignment]
    event_q25_model = bundle["event_q25"]  # type: ignore[assignment]
    event_p1 = opportunity_tree.predict_proba(ze)[:, 1]
    event_p2 = opportunity_hist.predict_proba(ze)[:, 1]
    event_r1 = event_r_tree.predict(ze)
    event_r2 = event_r_hist.predict(ze)
    event_map = event[["period", "event_id"]].copy()
    event_map["event_opportunity_p"] = 0.5 * (event_p1 + event_p2)
    event_map["event_predicted_best_r"] = 0.5 * (event_r1 + event_r2)
    event_map["event_q25_best_r"] = event_q25_model.predict(ze)
    event_map["event_disagreement"] = (
        0.45 * np.abs(event_p1 - event_p2) + 0.20 * np.abs(event_r1 - event_r2)
    )
    merged = frame.merge(
        event_map,
        on=["period", "event_id"],
        how="left",
        suffixes=("", "_prediction"),
    )

    zp = bundle["plan_prep"].transform(  # type: ignore[index,union-attr]
        merged[bundle["plan_numeric"] + bundle["plan_categories"]]  # type: ignore[operator,index]
    )
    best_tree = bundle["best_tree"]  # type: ignore[assignment]
    best_hist = bundle["best_hist"]  # type: ignore[assignment]
    win_tree = bundle["win_tree"]  # type: ignore[assignment]
    win_hist = bundle["win_hist"]  # type: ignore[assignment]
    r_tree = bundle["r_tree"]  # type: ignore[assignment]
    r_hist = bundle["r_hist"]  # type: ignore[assignment]
    r_q25_model = bundle["r_q25"]  # type: ignore[assignment]
    duration_tree = bundle["duration_tree"]  # type: ignore[assignment]
    duration_hist = bundle["duration_hist"]  # type: ignore[assignment]

    best_p1 = best_tree.predict_proba(zp)[:, 1]
    best_p2 = best_hist.predict_proba(zp)[:, 1]
    win_p1 = win_tree.predict_proba(zp)[:, 1]
    win_p2 = win_hist.predict_proba(zp)[:, 1]
    r1 = r_tree.predict(zp)
    r2 = r_hist.predict(zp)
    duration1 = duration_tree.predict(zp)
    duration2 = duration_hist.predict(zp)

    best_p = 0.5 * (best_p1 + best_p2)
    win_p = 0.5 * (win_p1 + win_p2)
    predicted_r = 0.5 * (r1 + r2)
    q25_r = r_q25_model.predict(zp)
    expected_minutes = np.expm1(0.5 * (duration1 + duration2)).clip(1.0, 10_080.0)

    # Gross RR, entry, stop and target all exist before the order.  A small causal
    # fee drag is estimated from planned price distances rather than realized fills.
    planned_rr = pd.to_numeric(merged["gross_rr"], errors="coerce").clip(lower=0.0).to_numpy()
    entry = pd.to_numeric(merged["entry"], errors="coerce").abs().to_numpy()
    stop = pd.to_numeric(merged["stop"], errors="coerce").abs().to_numpy()
    risk_price = np.abs(entry - stop)
    estimated_fee_drag_r = (
        (core.EPS + 0.0005 * entry + 0.0002 * np.abs(pd.to_numeric(merged["target"], errors="coerce").to_numpy()))
        / np.maximum(risk_price, core.EPS)
    ).clip(0.0, 3.0)
    planned_target_r = np.maximum(planned_rr - estimated_fee_drag_r, 0.001)
    expected_log = (
        win_p * np.log1p(core.RISK_FRACTION * planned_target_r)
        + (1.0 - win_p) * math.log1p(-core.RISK_FRACTION)
    )
    predicted_log = np.log1p(np.clip(core.RISK_FRACTION * predicted_r, -0.99, None))
    q25_log = np.log1p(np.clip(core.RISK_FRACTION * q25_r, -0.99, None))
    disagreement = (
        merged["event_disagreement"].to_numpy(float)
        + 0.30 * np.abs(best_p1 - best_p2)
        + 0.30 * np.abs(win_p1 - win_p2)
        + 0.18 * np.abs(r1 - r2)
    )
    event_quality = 0.50 + 0.50 * merged["event_opportunity_p"].to_numpy(float)
    plan_quality = 0.50 + 0.50 * best_p
    economic = (
        0.62 * expected_log + 0.23 * predicted_log + 0.15 * q25_log
    ) * event_quality * plan_quality
    score = economic / np.sqrt(expected_minutes / 60.0) - 0.0015 * disagreement

    output = merged.copy()
    output["planned_target_r_for_routing"] = planned_target_r
    output["event_opportunity_p"] = merged["event_opportunity_p"].to_numpy(float)
    output["event_predicted_best_r"] = merged["event_predicted_best_r"].to_numpy(float)
    output["event_q25_best_r"] = merged["event_q25_best_r"].to_numpy(float)
    output["plan_best_p"] = best_p
    output["plan_win_p"] = win_p
    output["plan_predicted_r"] = predicted_r
    output["plan_q25_r"] = q25_r
    output["expected_resolution_minutes"] = expected_minutes
    output["model_disagreement"] = disagreement
    output["expected_log_growth"] = expected_log
    output["policy_score"] = score
    return output


core._feature_columns = _causal_feature_columns
core._predict = _causal_predict

if __name__ == "__main__":
    core.main()

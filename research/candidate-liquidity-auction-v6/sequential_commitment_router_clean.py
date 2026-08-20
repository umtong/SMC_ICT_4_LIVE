#!/usr/bin/env python3
"""Leakage-free sequential commitment router.

Only information available at the completed arm bar and immutable plan geometry may
enter the models.  Fill, resolution, realized outcome, censoring, oracle best-plan and
future wait-value columns are labels only and are never features.
"""
from __future__ import annotations

import pandas as pd

import sequential_commitment_router as core

_BASE_LOAD = core.load_actions


def load_actions(root):
    frame = _BASE_LOAD(root)
    # Actual target R is a label-side execution field.  The planned value is known
    # before order submission and is the only economic geometry exposed at runtime.
    frame["target_net_r"] = pd.to_numeric(frame["planned_target_net_r"], errors="coerce")
    return frame


def feature_columns(frame: pd.DataFrame, *, state_only: bool = False):
    categorical = [
        column
        for column in (core.STATE_CATEGORICAL if state_only else core.PLAN_CATEGORICAL)
        if column in frame
    ]
    identifiers = {
        "period", "symbol", "action_id", "state_id", "episode_id",
        "order_time_ns", "departure_time_ns", "order_terminal_time_ns",
        "fill_time_ns", "resolution_time_ns", "terminal_ns",
    }
    post_decision_exact = {
        "outcome", "fill_state", "filled", "resolved", "win", "net_r",
        "mfe_r", "mae_r", "holding_minutes", "entry_wait_minutes",
        "actual_entry", "actual_target_net_r", "actual_stop_net_r",
        "actual_gross_rr", "realized_log", "state_best_log",
        "future_best_log", "future_positive_value", "tradeable_label",
        "known_actions", "target_net_r",
    }
    plan_only = {
        "entry", "stop", "target", "gross_rr", "risk_bps", "route_price",
        "route_rr", "planned_target_net_r", "arm_index",
    }
    forbidden_tokens = (
        "outcome", "fill_", "resolution", "terminal", "entry_wait",
        "holding", "actual_", "realized", "label", "future_", "state_best",
        "known_actions", "mfe", "mae", "diagnostic_response",
        "diagnostic_first_return", "diagnostic_retest", "response_",
    )
    numeric = []
    for column in frame.columns:
        low = column.lower()
        if column in identifiers or column in categorical or column in post_decision_exact:
            continue
        if state_only and column in plan_only:
            continue
        if any(token in low for token in forbidden_tokens):
            continue
        if low.startswith("diagnostic_") or low.endswith("_time_ns"):
            continue
        if not pd.api.types.is_numeric_dtype(frame[column]):
            continue
        if frame[column].notna().sum() < max(30, int(0.06 * len(frame))):
            continue
        if frame[column].nunique(dropna=True) <= 1:
            continue
        numeric.append(column)
    return numeric, categorical


core.load_actions = load_actions
core.feature_columns = feature_columns

if __name__ == "__main__":
    core.main()

#!/usr/bin/env python3
"""Add auction-value migration and effort/result features to sequential arming."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import sequential_commitment_harvest_fixed as executable

policy = executable.policy
core = policy.core
_BASE_METRICS = policy._arm_metrics
EPS = policy.EPS


def _number(row, *names, default=0.0):
    for name in names:
        value = row.get(name, np.nan)
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            return value
    return default


def rich_arm_metrics(data, candidate, arm, entry, stop):
    values = dict(_BASE_METRICS(data, candidate, arm, entry, stop))
    setup = candidate.setup
    departure = int(candidate.departure_index)
    side = str(setup.side)
    sign = 1.0 if side == "LONG" else -1.0
    atr = max(core._atr_price(data, departure), EPS)
    segment = data.iloc[departure:arm + 1]
    quote = pd.to_numeric(segment.get("quote_volume", pd.Series(0.0, index=segment.index)), errors="coerce").fillna(0.0)
    typical = (segment.high.astype(float) + segment.low.astype(float) + segment.close.astype(float)) / 3.0
    boundary = float(setup.upper if side == "LONG" else setup.lower)
    outside = typical > boundary if side == "LONG" else typical < boundary
    total_volume = max(float(quote.sum()), EPS)
    outside_volume = float(quote[outside].sum())
    value_centroid = float((typical * quote).sum() / total_volume) if total_volume > EPS else float(segment.close.iloc[-1])
    close = segment.close.to_numpy(float)
    directional = sign * (close - close[0])
    running_max = np.maximum.accumulate(directional)
    pullback = running_max - directional
    max_excursion = max(float(running_max.max()), EPS)
    current_progress = float(directional[-1])
    row = data.iloc[arm]
    futures_return = sign * math.log(max(float(segment.close.iloc[-1]), EPS) / max(float(segment.close.iloc[0]), EPS))
    index_return = sign * _number(row, "index_return_since_departure", "index_return_5m", "metric_index_return_5m")
    oi_change = _number(row, "metric_oi_log_change_1", "oi_log_change_1", "oi_log_change")
    basis_change = sign * _number(row, "basis_change_3m_bps", "basis_change_bps", "metric_basis_change_bps")
    mark_basis_change = sign * _number(row, "mark_basis_change_3m_bps", "mark_basis_change_bps", "metric_mark_basis_change_bps")
    flow_share = float(values.get("arm_flow_share_signed", 0.0))
    activity = max(float(values.get("arm_activity_ratio", 0.0)), 0.0)
    progress_atr = float(values.get("arm_progress_atr", 0.0))
    values.update({
        "arm_outside_volume_share": outside_volume / total_volume,
        "arm_value_centroid_distance_atr": sign * (value_centroid - boundary) / atr,
        "arm_current_retrace_fraction": float(pullback[-1]) / max_excursion,
        "arm_max_retrace_fraction": float(pullback.max()) / max_excursion,
        "arm_close_to_excursion_fraction": current_progress / max_excursion,
        "arm_effort_result_ratio": progress_atr / max(0.08, activity * (abs(flow_share) + 0.08)),
        "arm_futures_return_signed": futures_return,
        "arm_index_return_signed": index_return,
        "arm_futures_index_residual_signed": futures_return - index_return,
        "arm_oi_log_change_rich": oi_change,
        "arm_price_oi_alignment": progress_atr * oi_change,
        "arm_basis_change_signed_bps_rich": basis_change,
        "arm_mark_basis_change_signed_bps_rich": mark_basis_change,
    })
    return values


policy._arm_metrics = rich_arm_metrics

if __name__ == "__main__":
    core.main()

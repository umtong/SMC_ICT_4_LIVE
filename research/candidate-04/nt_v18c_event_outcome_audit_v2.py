#!/usr/bin/env python3
"""Corrected entry point for the V18c causal probe outcome audit.

The frozen strategy distinguishes a low- from high-side sweep with both the
rolling liquidity level and the past-only fair-value band. A wide minute bar can
cross both rolling levels while only one side reaches its statistical band. The
first audit entry point omitted the band term when reconstructing the side; this
wrapper restores byte-for-byte equivalent direction logic without changing any
trading rule.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

import nt_v18c_event_outcome_audit as audit


def _infer_probe(frame: pd.DataFrame, event: dict[str, Any]) -> audit.Probe:
    details = event["details"]
    index = audit._index_at(frame, int(event["ts_event"]))
    row = frame.iloc[index]
    prior_high = audit._as_float(details.get("prior_high"))
    prior_low = audit._as_float(details.get("prior_low"))
    fair_value = audit._as_float(details.get("fair_value"))
    dispersion = audit._as_float(details.get("dispersion"))
    band_sigma = audit._as_float(details.get("scale_band_sigma"), 1.50)
    lower_band = fair_value - band_sigma * dispersion
    upper_band = fair_value + band_sigma * dispersion
    low_swept = (
        float(row["low"]) < prior_low
        and float(row["low"]) <= lower_band
        and float(row["close"]) > prior_low
    )
    high_swept = (
        float(row["high"]) > prior_high
        and float(row["high"]) >= upper_band
        and float(row["close"]) < prior_high
    )
    if low_swept == high_swept:
        raise RuntimeError(
            f"cannot infer frozen sweep side at {frame.index[index]}: "
            f"low_swept={low_swept} high_swept={high_swept} "
            f"lower_band={lower_band} upper_band={upper_band}"
        )
    reversal_side = 1 if low_swept else -1
    return audit.Probe(
        scale=str(details.get("liquidity_scale")),
        armed_event=event,
        armed_index=index,
        reversal_side=reversal_side,
        continuation_side=-reversal_side,
        sweep_extreme=float(row["low"] if low_swept else row["high"]),
        reclaimed_boundary=prior_low if low_swept else prior_high,
        fair_value=fair_value,
        prior_high=prior_high,
        prior_low=prior_low,
        value_window=int(details.get("scale_value_window", 240)),
        liquidity_window=int(details.get("scale_liquidity_window", 30)),
    )


audit._infer_probe = _infer_probe


if __name__ == "__main__":
    audit.main()

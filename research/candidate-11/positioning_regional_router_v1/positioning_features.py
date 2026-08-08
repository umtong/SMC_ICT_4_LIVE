"""Reuse the already verified Candidate 05 Binance positioning features.

Candidate 05's feature builder already downloads, checksum-verifies, joins and
causally exposes Binance USD-M 5-minute metrics.  This module therefore adds no
second downloader and only derives the one missing five-minute OI-value change.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from spot_perp_features import load_range as load_spot_perp_range


def load_range(
    *,
    symbol: str,
    start: date,
    end: date,
    cache: Path,
    output: Path,
):
    klines, feature_path, raw_files, evidence = load_spot_perp_range(
        symbol=symbol,
        start=start,
        end=end,
        cache=cache,
        output=output,
    )
    frame = pd.read_csv(feature_path, compression="infer")
    required = {
        "sum_open_interest",
        "sum_open_interest_value",
        "sum_taker_long_short_vol_ratio",
        "oi_change_5m",
        "metrics_ready",
        "metrics_age_seconds",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise RuntimeError(
            f"verified Candidate 05 positioning contract drifted: {missing}",
        )

    frame["metrics_taker_ratio"] = pd.to_numeric(
        frame["sum_taker_long_short_vol_ratio"],
        errors="raise",
    )
    # Feature rows are completed minutes.  A five-row difference therefore
    # measures the change over the same five-minute cadence as Binance metrics.
    oi_value = pd.to_numeric(frame["sum_open_interest_value"], errors="raise")
    frame["oi_value_change_5m"] = oi_value.pct_change(periods=5)
    metrics_ready = frame["metrics_ready"].astype(str).str.lower().isin(
        {"true", "1", "yes"},
    )
    frame["positioning_feature_ready"] = (
        metrics_ready
        & frame[
            [
                "sum_open_interest",
                "sum_open_interest_value",
                "metrics_taker_ratio",
                "oi_change_5m",
                "oi_value_change_5m",
            ]
        ]
        .replace([np.inf, -np.inf], np.nan)
        .notna()
        .all(axis=1)
    )
    if int(frame["positioning_feature_ready"].sum()) == 0:
        raise RuntimeError("reused positioning features never became ready")
    base_ready = frame["feature_ready"].astype(str).str.lower().isin(
        {"true", "1", "yes"},
    )
    frame["feature_ready"] = base_ready & frame["positioning_feature_ready"]
    frame.to_csv(feature_path, index=False, compression="gzip")
    return klines, feature_path, raw_files, evidence


__all__ = ["load_range"]

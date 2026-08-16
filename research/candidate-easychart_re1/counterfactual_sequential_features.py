"""Causal sequential-evidence features for counterfactual plan research.

The source strategies describe structural events.  This module deliberately
imports methods from outside chart-pattern taxonomy: evidence accumulation,
process-control persistence, path efficiency and competing first-passage
geometry.  Every feature is computed from completed one-minute observations;
normalization uses a prior-only robust scale.  Cross-symbol same-minute fields
are research-safe for the existing synchronized four-symbol bucket, and lagged
copies are emitted for routers that do not guarantee such a watermark.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


SEQUENTIAL_FEATURE_POLICY = (
    "CAUSAL_EXTERNAL_METHOD:PRIOR_ONLY_ROBUST_NOISE_SCALE_PLUS_SIGNED_PATH_"
    "EFFICIENCY_PERSISTENCE_TURN_RATE_AND_FLOW_PRICE_IMPACT_AT_COMPLETED_MINUTES"
)
SYNCHRONIZED_COMMON_POLICY = (
    "CAUSAL_EXTERNAL_METHOD:SAME_MINUTE_COMMON_FIELDS_REQUIRE_AN_EXPLICIT_ALL_"
    "FOUR_SYMBOL_WATERMARK;LAG1_FIELDS_REMAIN_SAFE_WITHOUT_THAT_BARRIER"
)


def _prior_scale(values: pd.Series, window: int = 1440, minimum: int = 120) -> pd.Series:
    scale = values.abs().rolling(window, min_periods=minimum).median().shift(1)
    positive = scale[scale > 0.0]
    fallback = float(positive.median()) if not positive.empty else 1e-12
    return scale.where(scale > 0.0, fallback).fillna(fallback).clip(lower=1e-12)


def _symbol_features(symbol: str, frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy().sort_values("open_time_dt")
    data["ts"] = pd.DatetimeIndex(data["open_time_dt"]) + pd.Timedelta(minutes=1)
    data = data.set_index("ts", drop=True)
    close = data["close"].astype(float).clip(lower=1e-12)
    log_close = np.log(close)
    ret1 = log_close.diff()
    sigma1 = _prior_scale(ret1)
    abs_ret = ret1.abs()
    signed_quote = (
        2.0 * data["taker_buy_quote_volume"].astype(float)
        - data["quote_volume"].astype(float)
    )
    quote = data["quote_volume"].astype(float).clip(lower=0.0)
    direction = np.sign(ret1)
    turn = (direction != direction.shift(1)).astype(float).where(
        (direction != 0.0) & (direction.shift(1) != 0.0),
    )

    output = pd.DataFrame(index=data.index)
    output["symbol"] = symbol
    output["seq_prior_sigma_1m"] = sigma1
    prior_range = ((data["high"] - data["low"]) / close).rolling(
        1440,
        min_periods=120,
    ).median().shift(1)
    output["seq_prior_range_fraction_1m"] = prior_range

    for minutes in (5, 15, 30, 60, 90, 240):
        net = log_close.diff(minutes)
        total_variation = abs_ret.rolling(minutes, min_periods=minutes).sum()
        positive_fraction = (ret1 > 0.0).astype(float).rolling(
            minutes,
            min_periods=minutes,
        ).mean()
        negative_fraction = (ret1 < 0.0).astype(float).rolling(
            minutes,
            min_periods=minutes,
        ).mean()
        q = quote.rolling(minutes, min_periods=minutes).sum()
        d = signed_quote.rolling(minutes, min_periods=minutes).sum()
        delta_share = d / q.replace(0.0, np.nan)
        return_z = net / (sigma1 * math.sqrt(minutes))

        prefix = f"seq_{minutes}m"
        output[f"{prefix}_return"] = net
        output[f"{prefix}_return_z"] = return_z
        output[f"{prefix}_path_efficiency"] = net / total_variation.replace(0.0, np.nan)
        output[f"{prefix}_positive_fraction"] = positive_fraction
        output[f"{prefix}_negative_fraction"] = negative_fraction
        output[f"{prefix}_turn_rate"] = turn.rolling(
            minutes,
            min_periods=max(2, minutes - 1),
        ).mean()
        output[f"{prefix}_delta_share"] = delta_share
        output[f"{prefix}_impact_efficiency"] = return_z / (delta_share.abs() + 0.05)
        output[f"{prefix}_flow_progress_product"] = return_z * delta_share

    return output.replace([np.inf, -np.inf], np.nan)


def build_sequential_state(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    local = pd.concat(
        [_symbol_features(symbol, frame) for symbol, frame in frames.items()],
        axis=0,
    ).reset_index().rename(columns={"index": "ts"})

    common_sources = [
        column
        for column in local.columns
        if column.startswith("seq_")
        and any(
            token in column
            for token in (
                "return_z",
                "path_efficiency",
                "turn_rate",
                "delta_share",
                "impact_efficiency",
                "flow_progress_product",
            )
        )
    ]
    common = local.groupby("ts", sort=True)[common_sources].median().add_prefix("common_")
    dispersion = local.groupby("ts", sort=True)[common_sources].std(ddof=0).add_prefix(
        "dispersion_",
    )
    output = local.join(common, on="ts").join(dispersion, on="ts")

    for minutes in (5, 15, 30, 60, 90, 240):
        source = f"seq_{minutes}m_return_z"
        sign_frame = local.pivot(index="ts", columns="symbol", values=source)
        output = output.join(
            (sign_frame.gt(0.0).mean(axis=1)).rename(f"common_seq_{minutes}m_positive_breadth"),
            on="ts",
        )
        output = output.join(
            (sign_frame.lt(0.0).mean(axis=1)).rename(f"common_seq_{minutes}m_negative_breadth"),
            on="ts",
        )

    common_columns = [column for column in output.columns if column.startswith("common_")]
    common_lagged = (
        output[["ts", *common_columns]]
        .drop_duplicates("ts")
        .sort_values("ts")
        .set_index("ts")
        .shift(1)
        .add_suffix("_lag1")
    )
    output = output.join(common_lagged, on="ts")
    return output.set_index(["symbol", "ts"]).sort_index()

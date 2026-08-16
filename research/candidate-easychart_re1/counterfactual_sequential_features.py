"""Causal sequential-evidence features for counterfactual plan research.

The source strategies describe structural events. This module deliberately
imports methods from outside chart-pattern taxonomy: evidence accumulation,
process-control persistence, path efficiency and competing first-passage
geometry. Every feature is computed from completed one-minute observations;
all baselines are shifted so the current observation never normalizes itself.

Cross-symbol same-minute fields are research-safe only for the existing
synchronized four-symbol bar bucket. Lag-one copies are also emitted for any
future router which cannot prove such a watermark.
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
AGGREGATED_BAR_POLICY = (
    "CAUSAL_EXTERNAL_METHOD:FIXED_CLOCK_BAR_STATE_USES_ONLY_AGGREGATES_WHOSE_"
    "RIGHT_EDGE_IS_AT_OR_BEFORE_THE_PLAN_TIME"
)


def _prior_median(
    values: pd.Series,
    window: int = 1440,
    minimum: int = 120,
    floor: float = 1e-12,
) -> pd.Series:
    """Prior-only robust location without a full-sample fallback."""
    prior = values.shift(1)
    rolling = prior.rolling(window, min_periods=minimum).median()
    expanding = prior.expanding(min_periods=1).median()
    return rolling.combine_first(expanding).fillna(floor).clip(lower=floor)


def _prior_scale(
    values: pd.Series,
    window: int = 1440,
    minimum: int = 120,
) -> pd.Series:
    return _prior_median(values.abs(), window=window, minimum=minimum, floor=1e-12)


def _turn_rate(direction: pd.Series, window: int) -> pd.Series:
    turned = (direction != direction.shift(1)).astype(float).where(
        (direction != 0.0) & (direction.shift(1) != 0.0),
    )
    return turned.rolling(window, min_periods=max(2, window - 1)).mean()


def _aggregate_bar_features(
    close: pd.Series,
    minute_index: pd.DatetimeIndex,
    timeframe_minutes: int,
) -> pd.DataFrame:
    """Last-completed fixed-clock bar evidence, forward-filled to minute closes."""
    aggregate_close = close.resample(
        f"{timeframe_minutes}min",
        label="right",
        closed="right",
        origin="epoch",
    ).last().dropna()
    returns = np.log(aggregate_close.clip(lower=1e-12)).diff()
    direction = np.sign(returns)
    features = pd.DataFrame(index=aggregate_close.index)
    for bars in (4, 6, 8):
        net = np.log(aggregate_close.clip(lower=1e-12)).diff(bars)
        variation = returns.abs().rolling(bars, min_periods=bars).sum()
        positive = (returns > 0.0).astype(float).rolling(
            bars,
            min_periods=bars,
        ).mean()
        negative = (returns < 0.0).astype(float).rolling(
            bars,
            min_periods=bars,
        ).mean()
        prefix = f"bar{timeframe_minutes}_{bars}"
        features[f"{prefix}_net_return"] = net
        features[f"{prefix}_path_efficiency"] = net / variation.replace(0.0, np.nan)
        features[f"{prefix}_positive_fraction"] = positive
        features[f"{prefix}_negative_fraction"] = negative
        features[f"{prefix}_turn_rate"] = _turn_rate(direction, bars)
        features[f"{prefix}_last_return"] = returns
    return features.reindex(minute_index, method="ffill")


def _symbol_features(symbol: str, frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy().sort_values("open_time_dt")
    data["ts"] = pd.DatetimeIndex(data["open_time_dt"]) + pd.Timedelta(minutes=1)
    data = data.set_index("ts", drop=True)
    close = data["close"].astype(float).clip(lower=1e-12)
    log_close = np.log(close)
    ret1 = log_close.diff()
    sigma1 = _prior_scale(ret1)
    abs_ret = ret1.abs()
    quote = data["quote_volume"].astype(float).clip(lower=0.0)
    trade_count = data["count"].astype(float).clip(lower=0.0)
    signed_quote = (
        2.0 * data["taker_buy_quote_volume"].astype(float)
        - quote
    )
    direction = np.sign(ret1)

    output = pd.DataFrame(index=data.index)
    output["symbol"] = symbol
    output["seq_prior_sigma_1m"] = sigma1
    range_fraction = (data["high"] - data["low"]) / close
    output["seq_prior_range_fraction_1m"] = _prior_median(
        range_fraction,
        floor=1e-12,
    )

    # Causal replacements for the original local/common research state.
    for minutes in (5, 15, 60):
        raw_return = log_close.diff(minutes)
        horizon_scale = sigma1 * math.sqrt(minutes)
        q = quote.rolling(minutes, min_periods=minutes).sum()
        d = signed_quote.rolling(minutes, min_periods=minutes).sum()
        output[f"local_return_{minutes}m"] = raw_return
        output[f"local_return_z_{minutes}m"] = raw_return / horizon_scale
        output[f"local_delta_share_{minutes}m"] = d / q.replace(0.0, np.nan)
        output[f"local_quote_{minutes}m"] = q
        output[f"local_signed_quote_{minutes}m"] = d

    output["local_activity_ratio_1m"] = quote / _prior_median(
        quote,
        floor=1e-12,
    )
    output["local_trade_count_ratio_1m"] = trade_count / _prior_median(
        trade_count,
        floor=1.0,
    )
    output["local_delta_share_1m"] = signed_quote / quote.replace(0.0, np.nan)
    output["local_close_location_1m"] = (
        (data["close"] - data["low"])
        / (data["high"] - data["low"]).replace(0.0, np.nan)
    )
    output["local_range_fraction_1m"] = range_fraction
    output["local_body_fraction_1m"] = (
        (data["close"] - data["open"])
        / (data["high"] - data["low"]).replace(0.0, np.nan)
    )

    # Fixed-horizon evidence accumulation and process persistence.
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
        output[f"{prefix}_path_efficiency"] = net / total_variation.replace(
            0.0,
            np.nan,
        )
        output[f"{prefix}_positive_fraction"] = positive_fraction
        output[f"{prefix}_negative_fraction"] = negative_fraction
        output[f"{prefix}_turn_rate"] = _turn_rate(direction, minutes)
        output[f"{prefix}_delta_share"] = delta_share
        output[f"{prefix}_impact_efficiency"] = return_z / (
            delta_share.abs() + 0.05
        )
        output[f"{prefix}_flow_progress_product"] = return_z * delta_share

    minute_index = pd.DatetimeIndex(data.index)
    for timeframe in (5, 15, 60):
        output = output.join(
            _aggregate_bar_features(close, minute_index, timeframe),
            how="left",
        )

    return output.replace([np.inf, -np.inf], np.nan)


def build_sequential_state(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    local = pd.concat(
        [_symbol_features(symbol, frame) for symbol, frame in frames.items()],
        axis=0,
    ).reset_index().rename(columns={"index": "ts"})

    factor_columns = [
        column
        for column in local.columns
        if (
            column.startswith("local_return_z_")
            or column.startswith("local_delta_share_")
            or (
                column.startswith("seq_")
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
            )
            or (
                column.startswith("bar")
                and any(
                    token in column
                    for token in (
                        "net_return",
                        "path_efficiency",
                        "turn_rate",
                        "last_return",
                    )
                )
            )
        )
    ]
    common = local.groupby("ts", sort=True)[factor_columns].median().add_prefix(
        "common_",
    )
    dispersion = local.groupby("ts", sort=True)[factor_columns].std(ddof=0).add_prefix(
        "dispersion_",
    )
    output = local.join(common, on="ts").join(dispersion, on="ts")

    # Residual state separates local inventory transfer from common crypto motion.
    for column in factor_columns:
        output[f"residual_{column.removeprefix('local_')}"] = (
            output[column] - output[f"common_{column}"]
        )

    for minutes in (5, 15, 30, 60, 90, 240):
        source = f"seq_{minutes}m_return_z"
        sign_frame = local.pivot(index="ts", columns="symbol", values=source)
        output = output.join(
            sign_frame.gt(0.0).mean(axis=1).rename(
                f"common_seq_{minutes}m_positive_breadth",
            ),
            on="ts",
        )
        output = output.join(
            sign_frame.lt(0.0).mean(axis=1).rename(
                f"common_seq_{minutes}m_negative_breadth",
            ),
            on="ts",
        )

    common_columns = [
        column for column in output.columns if column.startswith("common_")
    ]
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

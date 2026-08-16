"""Causal derivatives-state features for counterfactual plan research.

Open interest and funding answer questions candle geometry cannot:

* did the move create new positions or destroy existing positions?
* is aggressive flow producing price progress or being absorbed?
* is the proposed side already crowded?

Every Binance metrics observation is delayed to the next completed minute before
it becomes available. Rolling normalizers are shifted and use only prior data.
The output is reindexed to the one-minute close clock used by the strategy.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from data_re1_derivatives import load_range_derivatives


DERIVATIVES_STATE_POLICY = (
    "CAUSAL_EXTERNAL_DATA:BINANCE_FIVE_MINUTE_OPEN_INTEREST_POSITIONING_AND_"
    "TAKER_RATIO_PLUS_FUNDING_ARE_AVAILABLE_ONLY_FROM_THE_NEXT_COMPLETED_MINUTE"
)
DERIVATIVES_MECHANISM_POLICY = (
    "MECHANISM_FEATURES:SEPARATE_POSITION_CREATION_POSITION_DESTRUCTION_"
    "CROWDING_AND_PRICE_PROGRESS_PER_UNIT_AGGRESSIVE_FLOW"
)


def _prior_median(
    values: pd.Series,
    window: int = 2016,
    minimum: int = 96,
    floor: float = 1e-12,
) -> pd.Series:
    prior = values.shift(1)
    rolling = prior.rolling(window, min_periods=minimum).median()
    expanding = prior.expanding(min_periods=1).median()
    return rolling.combine_first(expanding).fillna(floor).clip(lower=floor)


def _prior_scale(
    values: pd.Series,
    window: int = 2016,
    minimum: int = 96,
) -> pd.Series:
    return _prior_median(
        values.abs(),
        window=window,
        minimum=minimum,
        floor=1e-12,
    )


def _safe_log(values: pd.Series) -> pd.Series:
    return np.log(pd.to_numeric(values, errors="coerce").clip(lower=1e-12))


def _symbol_state(
    symbol: str,
    minute_frame: pd.DataFrame,
    start: date,
    end: date,
    cache: Path,
) -> pd.DataFrame:
    derivatives = load_range_derivatives(symbol, start, end, cache)
    metrics = derivatives.metrics.copy().sort_values("create_time")
    funding = derivatives.funding.copy().sort_values("calc_time")

    minute = minute_frame[["open_time_dt", "close"]].copy().sort_values(
        "open_time_dt",
    )
    minute["ts"] = pd.DatetimeIndex(minute["open_time_dt"]) + pd.Timedelta(minutes=1)
    minute = minute[["ts", "close"]]

    # The archive's create_time is a snapshot timestamp, not an exchange event
    # carrying an explicit receive sequence. Make it available at the next
    # completed minute to avoid same-timestamp optimism.
    metrics["available_ts"] = metrics["create_time"] + pd.Timedelta(minutes=1)
    metrics = pd.merge_asof(
        metrics.sort_values("available_ts"),
        minute.sort_values("ts"),
        left_on="available_ts",
        right_on="ts",
        direction="backward",
        allow_exact_matches=True,
    ).dropna(subset=["close"])

    if not funding.empty:
        funding["available_ts"] = funding["calc_time"] + pd.Timedelta(minutes=1)
        metrics = pd.merge_asof(
            metrics.sort_values("available_ts"),
            funding[["available_ts", "funding_interval_hours", "last_funding_rate"]]
            .sort_values("available_ts"),
            on="available_ts",
            direction="backward",
            allow_exact_matches=True,
        )
    else:
        metrics["funding_interval_hours"] = np.nan
        metrics["last_funding_rate"] = np.nan

    log_price = _safe_log(metrics["close"])
    log_oi = _safe_log(metrics["sum_open_interest"])
    log_oi_value = _safe_log(metrics["sum_open_interest_value"])
    taker_log_ratio = _safe_log(metrics["sum_taker_long_short_vol_ratio"])
    account_log_ratio = _safe_log(metrics["count_long_short_ratio"])
    top_account_log_ratio = _safe_log(metrics["count_toptrader_long_short_ratio"])
    top_position_log_ratio = _safe_log(metrics["sum_toptrader_long_short_ratio"])

    output = pd.DataFrame(index=pd.DatetimeIndex(metrics["available_ts"]))
    output["deriv_funding_rate"] = pd.to_numeric(
        metrics["last_funding_rate"],
        errors="coerce",
    ).to_numpy()
    output["deriv_funding_interval_hours"] = pd.to_numeric(
        metrics["funding_interval_hours"],
        errors="coerce",
    ).to_numpy()
    output["deriv_taker_log_ratio_5m"] = taker_log_ratio.to_numpy()
    output["deriv_account_log_ratio"] = account_log_ratio.to_numpy()
    output["deriv_top_account_log_ratio"] = top_account_log_ratio.to_numpy()
    output["deriv_top_position_log_ratio"] = top_position_log_ratio.to_numpy()
    output["deriv_top_position_minus_account_log_ratio"] = (
        top_position_log_ratio - top_account_log_ratio
    ).to_numpy()
    output["deriv_top_minus_all_account_log_ratio"] = (
        top_account_log_ratio - account_log_ratio
    ).to_numpy()

    for steps, label in ((1, "5m"), (3, "15m"), (12, "60m"), (48, "240m")):
        price_return = log_price.diff(steps)
        oi_change = log_oi.diff(steps)
        oi_value_change = log_oi_value.diff(steps)
        price_z = price_return / _prior_scale(price_return)
        oi_z = oi_change / _prior_scale(oi_change)
        oi_value_z = oi_value_change / _prior_scale(oi_value_change)
        taker_mean = taker_log_ratio.rolling(steps, min_periods=steps).mean()

        prefix = f"deriv_{label}"
        output[f"{prefix}_price_return"] = price_return.to_numpy()
        output[f"{prefix}_price_return_z"] = price_z.to_numpy()
        output[f"{prefix}_oi_log_change"] = oi_change.to_numpy()
        output[f"{prefix}_oi_change_z"] = oi_z.to_numpy()
        output[f"{prefix}_oi_value_log_change"] = oi_value_change.to_numpy()
        output[f"{prefix}_oi_value_change_z"] = oi_value_z.to_numpy()
        output[f"{prefix}_taker_log_ratio_mean"] = taker_mean.to_numpy()
        output[f"{prefix}_flow_progress_product"] = (
            price_z * taker_mean
        ).to_numpy()
        output[f"{prefix}_price_progress_per_flow"] = (
            price_z / (taker_mean.abs() + 0.05)
        ).to_numpy()
        output[f"{prefix}_new_position_pressure"] = (
            price_z.abs() * oi_z.clip(lower=0.0)
        ).to_numpy()
        output[f"{prefix}_position_destruction_pressure"] = (
            price_z.abs() * (-oi_z).clip(lower=0.0)
        ).to_numpy()
        output[f"{prefix}_price_oi_interaction"] = (
            price_z * oi_z
        ).to_numpy()
        output[f"{prefix}_flow_oi_interaction"] = (
            taker_mean * oi_z
        ).to_numpy()

    output = output[~output.index.duplicated(keep="last")].sort_index()
    minute_index = pd.DatetimeIndex(minute["ts"])
    output = output.reindex(minute_index, method="ffill")
    output["symbol"] = symbol
    output.index.name = "ts"
    return output.replace([np.inf, -np.inf], np.nan)


def build_derivatives_state(
    frames: dict[str, pd.DataFrame],
    start: date,
    end: date,
    cache: Path,
) -> pd.DataFrame:
    local = pd.concat(
        [
            _symbol_state(symbol, frame, start, end, cache)
            for symbol, frame in frames.items()
        ],
        axis=0,
    ).reset_index()

    common_sources = [
        column
        for column in local.columns
        if column.startswith("deriv_")
        and any(
            token in column
            for token in (
                "price_return_z",
                "oi_change_z",
                "taker_log_ratio",
                "flow_progress_product",
                "price_progress_per_flow",
                "new_position_pressure",
                "position_destruction_pressure",
                "price_oi_interaction",
                "flow_oi_interaction",
                "funding_rate",
            )
        )
    ]
    common = local.groupby("ts", sort=True)[common_sources].median().add_prefix(
        "common_",
    )
    dispersion = local.groupby("ts", sort=True)[common_sources].std(ddof=0).add_prefix(
        "dispersion_",
    )
    output = local.join(common, on="ts").join(dispersion, on="ts")
    for column in common_sources:
        output[f"residual_{column}"] = output[column] - output[f"common_{column}"]

    return output.set_index(["symbol", "ts"]).sort_index()

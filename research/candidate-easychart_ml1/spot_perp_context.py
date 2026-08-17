"""Causal spot/perpetual context for separating information from leverage flow.

Every feature uses completed, timestamp-aligned Binance spot and USD-M bars.
The module exposes raw venue-relative state plus a side-aligned decision snapshot.
Rare venue-specific missing bars remain explicit NaN observations; they are never
forward-filled and their local coverage is exposed to downstream decisions.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from auction_transition_study import make_features


def _rolling_sum(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).sum()


def _prior_robust_z(
    series: pd.Series,
    window: int = 1440,
    min_periods: int = 240,
) -> pd.Series:
    prior = series.shift(1)
    median = prior.rolling(window, min_periods=min_periods).median()
    deviation = (prior - median).abs()
    mad = deviation.rolling(window, min_periods=min_periods).median()
    scale = (1.4826 * mad).replace(0.0, np.nan)
    return (series - median) / scale


def add_spot_perp_context(
    perp: pd.DataFrame,
    symbol: str,
    spot_raw: pd.DataFrame,
) -> pd.DataFrame:
    """Return futures features enriched with exact-time spot context.

    A very small number of one-minute bars can be absent on one venue while the
    other remains open.  Treating that as a fatal range error discards otherwise
    valid causal episodes; forward filling would fabricate flow.  We therefore
    preserve NaN for those timestamps and expose coverage explicitly.
    """
    result = perp.copy().sort_index()
    spot = make_features(f"{symbol}-SPOT", spot_raw).sort_index()
    common_index = result.index.intersection(spot.index)
    if len(common_index) < max(60, int(0.95 * len(result))):
        raise RuntimeError(
            f"insufficient spot/perpetual timestamp overlap for {symbol}: "
            f"{len(common_index)}/{len(result)}"
        )
    spot = spot.reindex(result.index)
    available = spot["close"].notna()
    result["spot_bar_available"] = available.astype(float)
    result["spot_missing_count_60"] = (
        (~available).astype(float).rolling(60, min_periods=1).sum()
    )
    result["spot_coverage_60"] = available.astype(float).rolling(
        60,
        min_periods=1,
    ).mean()

    spot_close = spot["close"].astype(float).clip(lower=1e-12)
    perp_close = result["close"].astype(float).clip(lower=1e-12)
    result["spot_close"] = spot_close
    result["perp_spot_basis_log"] = np.log(perp_close / spot_close)
    result["spot_delta_share_1"] = spot["delta_share_1"]
    result["spot_range_ratio"] = spot["range_ratio"]
    result["spot_activity_ratio"] = spot["activity_ratio"]
    result["spot_trade_count_ratio"] = spot["trade_count_ratio"]

    perp_quote = result["quote_volume"].astype(float).clip(lower=0.0)
    spot_quote = spot["quote_volume"].astype(float).clip(lower=0.0)
    perp_count = result["count"].astype(float).clip(lower=0.0)
    spot_count = spot["count"].astype(float).clip(lower=0.0)
    perp_signed = (
        2.0 * result["taker_buy_quote_volume"].astype(float) - perp_quote
    )
    spot_signed = (
        2.0 * spot["taker_buy_quote_volume"].astype(float) - spot_quote
    )

    for window in (1, 5, 15, 30, 60):
        if window == 1:
            pq = perp_quote
            sq = spot_quote
            pc = perp_count
            sc = spot_count
            pdlt = perp_signed
            sdlt = spot_signed
        else:
            pq = _rolling_sum(perp_quote, window)
            sq = _rolling_sum(spot_quote, window)
            pc = _rolling_sum(perp_count, window)
            sc = _rolling_sum(spot_count, window)
            pdlt = _rolling_sum(perp_signed, window)
            sdlt = _rolling_sum(spot_signed, window)
        denominator = (pq + sq).replace(0.0, np.nan)
        result[f"perp_activity_share_{window}"] = pq / denominator
        result[f"spot_activity_share_{window}"] = sq / denominator
        result[f"perp_delta_share_context_{window}"] = (
            pdlt / pq.replace(0.0, np.nan)
        )
        result[f"spot_delta_share_context_{window}"] = (
            sdlt / sq.replace(0.0, np.nan)
        )
        result[f"venue_delta_gap_{window}"] = (
            result[f"perp_delta_share_context_{window}"]
            - result[f"spot_delta_share_context_{window}"]
        )
        result[f"venue_flow_agreement_{window}"] = (
            result[f"perp_delta_share_context_{window}"]
            * result[f"spot_delta_share_context_{window}"]
        )
        perp_mean_trade = pq / pc.replace(0.0, np.nan)
        spot_mean_trade = sq / sc.replace(0.0, np.nan)
        result[f"perp_spot_mean_trade_log_ratio_{window}"] = np.log(
            perp_mean_trade.clip(lower=1e-12)
            / spot_mean_trade.clip(lower=1e-12)
        )

        basis_change = result["perp_spot_basis_log"].diff(window)
        result[f"basis_change_{window}"] = basis_change
        result[f"basis_change_z_{window}"] = _prior_robust_z(basis_change)

        if window == 1:
            spot_ret = np.log(spot_close).diff()
            perp_ret = np.log(perp_close).diff()
            spot_sigma = (
                spot_ret.abs()
                .shift(1)
                .rolling(1440, min_periods=60)
                .median()
            )
            perp_sigma = (
                perp_ret.abs()
                .shift(1)
                .rolling(1440, min_periods=60)
                .median()
            )
            result["spot_ret_z_1"] = (
                spot_ret / spot_sigma.replace(0.0, np.nan)
            )
            result["perp_ret_z_context_1"] = (
                perp_ret / perp_sigma.replace(0.0, np.nan)
            )
        else:
            result[f"spot_ret_z_{window}"] = spot[f"ret_z_{window}"]
            result[f"perp_ret_z_context_{window}"] = result[
                f"ret_z_{window}"
            ]
        result[f"venue_return_gap_{window}"] = (
            result[f"perp_ret_z_context_{window}"]
            - result[f"spot_ret_z_{window}"]
        )
        result[f"venue_price_agreement_{window}"] = (
            result[f"perp_ret_z_context_{window}"]
            * result[f"spot_ret_z_{window}"]
        )

    result["basis_level_z"] = _prior_robust_z(
        result["perp_spot_basis_log"]
    )
    return result.replace([np.inf, -np.inf], np.nan)


def spot_perp_snapshot(
    frame: pd.DataFrame,
    ts: pd.Timestamp,
    side: int,
) -> dict[str, float]:
    """Side-align price, basis and flow differences at a completed bar."""
    before = frame.loc[frame.index <= ts]
    if before.empty:
        return {}
    row = before.iloc[-1]
    output: dict[str, float] = {}
    directional_prefixes = (
        "spot_ret_z_",
        "perp_ret_z_context_",
        "venue_return_gap_",
        "basis_change_",
        "basis_change_z_",
        "perp_delta_share_context_",
        "spot_delta_share_context_",
        "venue_delta_gap_",
    )
    neutral_prefixes = (
        "perp_activity_share_",
        "spot_activity_share_",
        "venue_flow_agreement_",
        "venue_price_agreement_",
        "perp_spot_mean_trade_log_ratio_",
        "spot_bar_available",
        "spot_missing_count_",
        "spot_coverage_",
    )
    for key, value in row.items():
        if not isinstance(key, str):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number):
            continue
        if key == "basis_level_z":
            output["aligned_basis_level_z"] = side * number
        elif key.startswith(directional_prefixes):
            output[f"aligned_{key}"] = side * number
        elif key.startswith(neutral_prefixes):
            output[key] = number
    return output


def venue_window_features(
    frame: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    side: int,
    prefix: str,
) -> dict[str, float]:
    """Summarise who drove an already completed event window."""
    window = frame.loc[(frame.index >= start) & (frame.index <= end)]
    if window.empty:
        return {}
    perp_quote = window["quote_volume"].astype(float).clip(lower=0.0)
    spot_quote = (
        window["spot_activity_share_1"].astype(float)
        * (
            perp_quote
            / window["perp_activity_share_1"].replace(0.0, np.nan)
        )
    ).replace([np.inf, -np.inf], np.nan)
    spot_available = window["spot_bar_available"].astype(float)
    spot_quote = spot_quote.where(spot_available > 0.5)
    perp_delta = (
        2.0 * window["taker_buy_quote_volume"].astype(float) - perp_quote
    )
    spot_delta_share = window[
        "spot_delta_share_context_1"
    ].astype(float)
    spot_delta = spot_delta_share * spot_quote
    total_quote = float(perp_quote.sum() + spot_quote.sum(min_count=1))
    valid_basis = window["perp_spot_basis_log"].dropna()
    basis_move = (
        float(valid_basis.iloc[-1] - valid_basis.iloc[0])
        if len(valid_basis) >= 2
        else math.nan
    )
    perp_share = float(perp_quote.sum()) / max(total_quote, 1e-12)
    perp_aligned = (
        side * float(perp_delta.sum()) / max(float(perp_quote.sum()), 1e-12)
    )
    spot_quote_sum = float(spot_quote.sum(min_count=1))
    spot_delta_sum = float(spot_delta.sum(min_count=1))
    spot_aligned = (
        side * spot_delta_sum / max(spot_quote_sum, 1e-12)
        if math.isfinite(spot_quote_sum) and math.isfinite(spot_delta_sum)
        else math.nan
    )
    output = {
        f"{prefix}_spot_coverage": float(spot_available.mean()),
        f"{prefix}_perp_activity_share": perp_share,
        f"{prefix}_perp_delta_aligned": perp_aligned,
        f"{prefix}_spot_delta_aligned": spot_aligned,
        f"{prefix}_basis_change_aligned": (
            side * basis_move if math.isfinite(basis_move) else math.nan
        ),
    }
    if math.isfinite(spot_aligned):
        output[f"{prefix}_venue_delta_gap_aligned"] = (
            perp_aligned - spot_aligned
        )
        output[f"{prefix}_venue_delta_agreement"] = (
            perp_aligned * spot_aligned
        )
    return output

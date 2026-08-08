"""Exact first-ten-second order flow on top of the V33 price-level footprint.

The authoritative Candidate 05 archive, checksum, timestamp, depth and aggregate
flow contracts remain unchanged.  This module adds buyer/seller initiated
notional and VWAP from seconds [0, 10) of every completed Binance minute.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from features_base import *  # noqa: F401,F403 - preserve reused public API
import features_base as _base
import features_v33 as _v33

_ORIGINAL_AGGREGATE = _v33.aggregate_agg_trades
_ORIGINAL_BUILD = _v33.build_features
_archive_spec = _base._archive_spec


def _first_ten_second_rows(path):
    frames: list[pd.DataFrame] = []
    for chunk in _base._agg_reader(path):
        price = pd.to_numeric(chunk["price"], errors="raise").astype(float)
        quantity = pd.to_numeric(chunk["quantity"], errors="raise").astype(float)
        transact = pd.to_numeric(chunk["transact_time"], errors="raise")
        unit = "us" if float(transact.iloc[0]) > 10**14 else "ms"
        timestamp = pd.to_datetime(transact, unit=unit, utc=True)
        elapsed = timestamp.dt.second + timestamp.dt.microsecond / 1_000_000.0
        selected = elapsed < 10.0
        if not bool(selected.any()):
            continue
        maker = _base._maker_mask(chunk["is_buyer_maker"]).to_numpy()[selected]
        local_price = price.to_numpy()[selected]
        local_quantity = quantity.to_numpy()[selected]
        notional = local_price * local_quantity
        work = pd.DataFrame(
            {
                "minute": timestamp[selected].dt.floor("min").to_numpy(),
                "first10_buy_notional": np.where(maker, 0.0, notional),
                "first10_sell_notional": np.where(maker, notional, 0.0),
                "first10_notional": notional,
                "first10_quantity": local_quantity,
                "first10_trade_count": np.ones(notional.size, dtype="int64"),
            }
        )
        frames.append(
            work.groupby("minute", sort=True)[
                [
                    "first10_buy_notional",
                    "first10_sell_notional",
                    "first10_notional",
                    "first10_quantity",
                    "first10_trade_count",
                ]
            ].sum()
        )
    if not frames:
        raise RuntimeError(f"no first-ten-second trades in archive: {path}")
    result = pd.concat(frames).groupby(level=0, sort=True).sum()
    total = result["first10_buy_notional"] + result["first10_sell_notional"]
    result["first10_order_imbalance"] = np.where(
        total > 0.0,
        (result["first10_buy_notional"] - result["first10_sell_notional"])
        / total,
        0.0,
    )
    result["first10_vwap"] = np.where(
        result["first10_quantity"] > 0.0,
        result["first10_notional"] / result["first10_quantity"],
        np.nan,
    )
    return result


def aggregate_agg_trades(path):
    base = _ORIGINAL_AGGREGATE(path)
    return base.join(_first_ten_second_rows(path), how="left")


def build_features(klines, agg, depth):
    result = _ORIGINAL_BUILD(klines, agg, depth)
    columns = [
        "first10_buy_notional",
        "first10_sell_notional",
        "first10_notional",
        "first10_quantity",
        "first10_trade_count",
        "first10_order_imbalance",
        "first10_vwap",
    ]
    aligned = agg.reindex(result.index)[columns]
    for column in columns:
        result[column] = aligned[column].to_numpy(copy=True)
    for column in (
        "first10_buy_notional",
        "first10_sell_notional",
        "first10_notional",
        "first10_quantity",
        "first10_trade_count",
        "first10_order_imbalance",
    ):
        result[column] = result[column].fillna(0.0)
    result["feature_ready"] = (
        result["feature_ready"]
        & result["first10_vwap"].notna()
        & result["first10_order_imbalance"].notna()
    )
    return result


def load_range(*args, **kwargs):
    """Delegate while preserving wrappers installed on the active module."""
    _base.download_checked = globals()["download_checked"]
    _base.aggregate_book_depth = globals()["aggregate_book_depth"]
    _base.aggregate_agg_trades = aggregate_agg_trades
    _base.build_features = globals()["build_features"]
    return _base.load_range(*args, **kwargs)


sha256_file = _base.sha256_file

__all__ = [
    "aggregate_agg_trades",
    "build_features",
    "load_range",
    "sha256_file",
]

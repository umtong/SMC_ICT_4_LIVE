"""Multi-symbol price-level footprint extension for Candidate 09 v37."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from features_base import *  # noqa: F401,F403
import features_base as _base

_ORIGINAL_AGGREGATE = _base.aggregate_agg_trades
_ORIGINAL_BUILD = _base.build_features
_TICK_SIZES = {
    "BTCUSDT": 0.1,
    "ETHUSDT": 0.01,
    "SOLUSDT": 0.001,
    "XRPUSDT": 0.0001,
}
_IMBALANCE_RATIO = 3.0
_archive_spec = _base._archive_spec


def _symbol_and_tick(path: Path) -> tuple[str, float]:
    name = path.name.upper()
    for symbol, tick in _TICK_SIZES.items():
        if name.startswith(symbol + "-") or symbol in name:
            return symbol, tick
    raise RuntimeError(f"unsupported footprint symbol in archive path: {path}")


def _longest_run(
    ticks: np.ndarray,
    flags: np.ndarray,
    tick_size: float,
) -> tuple[int, float, float]:
    best_length = 0
    best_start: int | None = None
    best_end: int | None = None
    current_length = 0
    current_start: int | None = None
    previous_tick: int | None = None
    for tick_value, flag_value in zip(ticks, flags, strict=True):
        tick = int(tick_value)
        if bool(flag_value):
            if previous_tick is not None and tick == previous_tick + 1 and current_length:
                current_length += 1
            else:
                current_length = 1
                current_start = tick
            if current_length > best_length:
                best_length = current_length
                best_start = current_start
                best_end = tick
        else:
            current_length = 0
            current_start = None
        previous_tick = tick
    if best_start is None or best_end is None:
        return 0, math.nan, math.nan
    return best_length, best_start * tick_size, best_end * tick_size


def _footprint_rows(path: Path) -> pd.DataFrame:
    _, tick_size = _symbol_and_tick(path)
    grouped: list[pd.DataFrame] = []
    for chunk in _base._agg_reader(path):
        price = pd.to_numeric(chunk["price"], errors="raise").astype(float)
        quantity = pd.to_numeric(chunk["quantity"], errors="raise").astype(float)
        transact = pd.to_numeric(chunk["transact_time"], errors="raise")
        unit = "us" if float(transact.iloc[0]) > 10**14 else "ms"
        timestamp = pd.to_datetime(transact, unit=unit, utc=True)
        maker = _base._maker_mask(chunk["is_buyer_maker"]).to_numpy()
        notional = (price * quantity).to_numpy()
        work = pd.DataFrame(
            {
                "minute": timestamp.dt.floor("min"),
                "tick": np.rint(price.to_numpy() / tick_size).astype("int64"),
                "buy_notional": np.where(maker, 0.0, notional),
                "sell_notional": np.where(maker, notional, 0.0),
            }
        )
        grouped.append(
            work.groupby(["minute", "tick"], sort=True)[
                ["buy_notional", "sell_notional"]
            ].sum()
        )
    if not grouped:
        raise RuntimeError(f"empty aggTrades footprint archive: {path}")
    levels = pd.concat(grouped).groupby(level=[0, 1], sort=True).sum()
    records: list[dict[str, float | int | pd.Timestamp]] = []
    for minute, group in levels.groupby(level=0, sort=True):
        local = group.droplevel(0).sort_index()
        ticks = local.index.to_numpy(dtype="int64")
        buy = local["buy_notional"].to_numpy(dtype=float)
        sell = local["sell_notional"].to_numpy(dtype=float)
        total = buy + sell
        active = total[total > 0.0]
        median_cell = float(np.median(active)) if active.size else 0.0
        denominator_floor = max(1.0, 0.10 * median_cell)
        minimum_numerator = max(1.0, median_cell)
        buy_by_tick = {
            int(tick): float(value)
            for tick, value in zip(ticks, buy, strict=True)
        }
        sell_by_tick = {
            int(tick): float(value)
            for tick, value in zip(ticks, sell, strict=True)
        }
        buy_flags = np.asarray(
            [
                numerator >= minimum_numerator
                and numerator
                >= _IMBALANCE_RATIO
                * max(
                    sell_by_tick.get(int(tick) - 1, 0.0),
                    denominator_floor,
                )
                for tick, numerator in zip(ticks, buy, strict=True)
            ],
            dtype=bool,
        )
        sell_flags = np.asarray(
            [
                numerator >= minimum_numerator
                and numerator
                >= _IMBALANCE_RATIO
                * max(
                    buy_by_tick.get(int(tick) + 1, 0.0),
                    denominator_floor,
                )
                for tick, numerator in zip(ticks, sell, strict=True)
            ],
            dtype=bool,
        )
        buy_run, buy_low, buy_high = _longest_run(ticks, buy_flags, tick_size)
        sell_run, sell_low, sell_high = _longest_run(ticks, sell_flags, tick_size)
        total_notional = float(total.sum())
        poc_index = int(np.argmax(total)) if total.size else 0
        records.append(
            {
                "minute": minute,
                "stacked_buy_imbalance_levels": int(buy_run),
                "stacked_sell_imbalance_levels": int(sell_run),
                "stacked_buy_low": buy_low,
                "stacked_buy_high": buy_high,
                "stacked_sell_low": sell_low,
                "stacked_sell_high": sell_high,
                "footprint_poc_price": (
                    float(ticks[poc_index]) * tick_size if ticks.size else math.nan
                ),
                "footprint_delta_60s": (
                    float((buy.sum() - sell.sum()) / total_notional)
                    if total_notional > 0.0
                    else 0.0
                ),
                "footprint_cell_median_notional": median_cell,
            }
        )
    return pd.DataFrame.from_records(records).set_index("minute").sort_index()


def aggregate_agg_trades(path: Path) -> pd.DataFrame:
    return _ORIGINAL_AGGREGATE(path).join(_footprint_rows(path), how="left")


def build_features(
    klines: pd.DataFrame,
    agg: pd.DataFrame,
    depth: pd.DataFrame,
) -> pd.DataFrame:
    result = _ORIGINAL_BUILD(klines, agg, depth)
    columns = [
        "stacked_buy_imbalance_levels",
        "stacked_sell_imbalance_levels",
        "stacked_buy_low",
        "stacked_buy_high",
        "stacked_sell_low",
        "stacked_sell_high",
        "footprint_poc_price",
        "footprint_delta_60s",
        "footprint_cell_median_notional",
    ]
    aligned = agg.reindex(result.index)[columns]
    for column in columns:
        result[column] = aligned[column].to_numpy(copy=True)
    result["stacked_buy_imbalance_levels"] = result[
        "stacked_buy_imbalance_levels"
    ].fillna(0.0)
    result["stacked_sell_imbalance_levels"] = result[
        "stacked_sell_imbalance_levels"
    ].fillna(0.0)
    result["feature_ready"] = (
        result["feature_ready"]
        & result["footprint_poc_price"].notna()
        & result["footprint_delta_60s"].notna()
    )
    return result


def load_range(*args: Any, **kwargs: Any):
    _base.read_kline = globals()["read_kline"]
    _base.download_checked = globals()["download_checked"]
    _base.aggregate_book_depth = globals()["aggregate_book_depth"]
    _base.aggregate_agg_trades = aggregate_agg_trades
    _base.build_features = globals()["build_features"]
    return _base.load_range(*args, **kwargs)


sha256_file = _base.sha256_file

__all__ = ["aggregate_agg_trades", "build_features", "load_range", "sha256_file"]

"""Causal top-of-book and aggressive-trade features for quote resiliency research.

The module contains no scenario outcome, execution, sizing, order, position or PnL logic.  It turns
ordered Binance USD-M bookTicker updates into economically signed quote events, aggregates only
completed ten-second buckets, joins the existing completed aggTrade bars and computes shifted robust
baselines.  Future rows cannot alter an already completed feature row.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any

import numpy as np
import pandas as pd


IMPLEMENTATION_REVISION = "CAUSAL_QUOTE_RESILIENCY_FEATURES_V1"
QUOTE_STATE_COLUMNS = (
    "best_bid_price",
    "best_bid_qty",
    "best_ask_price",
    "best_ask_qty",
)
QUOTE_FLOW_COLUMNS = (
    "bid_add_qty",
    "bid_remove_qty",
    "ask_add_qty",
    "ask_remove_qty",
    "quote_ofi_qty",
)
TRADE_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "signed_volume",
    "trade_count",
)


@dataclass(frozen=True, slots=True)
class QuoteResiliencyConfig:
    cadence_seconds: int = 10
    baseline_bars: int = 360
    minimum_history_bars: int = 90
    pressure_quantile: float = 0.90
    quote_ofi_quantile: float = 0.90
    minimum_outward_pressure_ratio: float = 1.0
    minimum_quote_response_ratio: float = 1.25
    minimum_same_side_support_ratio: float = 1.0
    maximum_spread_median_ratio: float = 1.5
    response_window_bars: int = 3
    confirmation_window_bars: int = 3
    setup_expiry_bars: int = 12
    minimum_confirmation_pressure_ratio: float = 0.5
    minimum_confirmation_quote_ofi_ratio: float = 0.25
    maximum_retest_pressure_fraction: float = 0.8
    quote_ofi_confirmation_required: bool = True

    def validate(self) -> None:
        if self.cadence_seconds <= 0:
            raise ValueError("cadence_seconds must be positive")
        if self.baseline_bars <= 0:
            raise ValueError("baseline_bars must be positive")
        if not 1 <= self.minimum_history_bars <= self.baseline_bars:
            raise ValueError("minimum_history_bars must be within baseline_bars")
        for name in ("pressure_quantile", "quote_ofi_quantile"):
            value = float(getattr(self, name))
            if not 0.5 <= value < 1.0:
                raise ValueError(f"{name} must be in [0.5, 1.0)")
        for name in (
            "minimum_outward_pressure_ratio",
            "minimum_quote_response_ratio",
            "minimum_same_side_support_ratio",
            "maximum_spread_median_ratio",
            "minimum_confirmation_pressure_ratio",
            "minimum_confirmation_quote_ofi_ratio",
            "maximum_retest_pressure_fraction",
        ):
            if float(getattr(self, name)) <= 0.0:
                raise ValueError(f"{name} must be positive")
        for name in (
            "response_window_bars",
            "confirmation_window_bars",
            "setup_expiry_bars",
        ):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")


def _require_time_index(frame: pd.DataFrame, *, name: str) -> None:
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise TypeError(f"{name} must use a timezone-aware DatetimeIndex")
    if frame.index.has_duplicates:
        raise ValueError(f"{name} timestamps must be unique")
    if not frame.index.is_monotonic_increasing:
        raise ValueError(f"{name} timestamps must be increasing")


def validate_exact_cadence(index: pd.DatetimeIndex, *, seconds: int) -> None:
    if not isinstance(index, pd.DatetimeIndex) or index.tz is None:
        raise TypeError("cadence index must be timezone-aware")
    if index.has_duplicates or not index.is_monotonic_increasing:
        raise ValueError("cadence index must be unique and increasing")
    if len(index) < 2:
        raise ValueError("cadence index must contain at least two rows")
    expected = int(seconds) * 1_000_000_000
    observed = np.diff(index.as_unit("ns").asi8)
    if not np.all(observed == expected):
        positions = np.flatnonzero(observed != expected)
        first = int(positions[0])
        raise ValueError(
            "completed quote/trade cadence is not exact: "
            f"{index[first].isoformat()} -> {index[first + 1].isoformat()}"
        )


def _coerce_quote_state(frame: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(set(QUOTE_STATE_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"quote updates missing columns: {missing}")
    result = frame.copy()
    for column in QUOTE_STATE_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    if result[list(QUOTE_STATE_COLUMNS)].isna().any(axis=None):
        raise ValueError("quote state contains missing or nonnumeric values")
    if (
        (result["best_bid_price"] <= 0.0).any()
        or (result["best_ask_price"] <= 0.0).any()
        or (result["best_bid_qty"] <= 0.0).any()
        or (result["best_ask_qty"] <= 0.0).any()
    ):
        raise ValueError("quote prices and quantities must be positive")
    if (result["best_bid_price"] > result["best_ask_price"]).any():
        raise ValueError("crossed quote observed")
    return result


def quote_event_rows(
    quote_updates: pd.DataFrame,
    *,
    previous_quote: dict[str, float] | None = None,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Return signed quote events and the final state for cross-chunk continuation.

    A price improvement introduces the new displayed quantity.  A price retreat removes the prior
    displayed quantity.  At an unchanged price, only the signed quantity change is counted.  This is
    the top-of-book specialization of order-flow imbalance and deliberately makes no claim about
    passive order identity.
    """

    _require_time_index(quote_updates, name="quote_updates")
    quote = _coerce_quote_state(quote_updates)
    if quote.empty:
        raise ValueError("quote_updates cannot be empty")

    previous = quote[list(QUOTE_STATE_COLUMNS)].shift(1)
    if previous_quote is not None:
        if set(previous_quote) != set(QUOTE_STATE_COLUMNS):
            raise ValueError("previous_quote must contain the exact quote state")
        for column in QUOTE_STATE_COLUMNS:
            value = float(previous_quote[column])
            if not isfinite(value) or value <= 0.0:
                raise ValueError("previous_quote contains invalid values")
            previous.iloc[0, previous.columns.get_loc(column)] = value

    result = quote.copy()
    bid = quote["best_bid_price"]
    bid_qty = quote["best_bid_qty"]
    ask = quote["best_ask_price"]
    ask_qty = quote["best_ask_qty"]
    previous_bid = previous["best_bid_price"]
    previous_bid_qty = previous["best_bid_qty"]
    previous_ask = previous["best_ask_price"]
    previous_ask_qty = previous["best_ask_qty"]

    same_bid = bid == previous_bid
    higher_bid = bid > previous_bid
    lower_bid = bid < previous_bid
    same_ask = ask == previous_ask
    lower_ask = ask < previous_ask
    higher_ask = ask > previous_ask

    bid_delta = bid_qty - previous_bid_qty
    ask_delta = ask_qty - previous_ask_qty
    result["bid_add_qty"] = np.where(
        higher_bid,
        bid_qty,
        np.where(same_bid, bid_delta.clip(lower=0.0), 0.0),
    )
    result["bid_remove_qty"] = np.where(
        lower_bid,
        previous_bid_qty,
        np.where(same_bid, (-bid_delta).clip(lower=0.0), 0.0),
    )
    result["ask_add_qty"] = np.where(
        lower_ask,
        ask_qty,
        np.where(same_ask, ask_delta.clip(lower=0.0), 0.0),
    )
    result["ask_remove_qty"] = np.where(
        higher_ask,
        previous_ask_qty,
        np.where(same_ask, (-ask_delta).clip(lower=0.0), 0.0),
    )
    result["quote_ofi_qty"] = (
        result["bid_add_qty"]
        - result["bid_remove_qty"]
        - result["ask_add_qty"]
        + result["ask_remove_qty"]
    )
    result["spread"] = ask - bid
    result["mid"] = 0.5 * (ask + bid)
    denominator = bid_qty + ask_qty
    result["microprice"] = (
        ask * bid_qty + bid * ask_qty
    ) / denominator.replace(0.0, np.nan)
    result["quote_imbalance"] = (bid_qty - ask_qty) / denominator.replace(0.0, np.nan)
    result["price_changed"] = (bid != previous_bid) | (ask != previous_ask)
    result["size_changed"] = (bid_qty != previous_bid_qty) | (ask_qty != previous_ask_qty)
    result["has_previous_quote"] = previous_bid.notna() & previous_ask.notna()

    first_without_history = ~result["has_previous_quote"]
    if first_without_history.any():
        result.loc[first_without_history, list(QUOTE_FLOW_COLUMNS)] = 0.0
        result.loc[first_without_history, ["price_changed", "size_changed"]] = False

    final = {
        column: float(quote.iloc[-1][column]) for column in QUOTE_STATE_COLUMNS
    }
    return result, final


def _bucket_labels(index: pd.DatetimeIndex, *, seconds: int) -> pd.DatetimeIndex:
    return index.floor(f"{seconds}s") + pd.Timedelta(seconds=seconds)


def aggregate_quote_events(
    event_rows: pd.DataFrame,
    *,
    cadence_seconds: int = 10,
) -> pd.DataFrame:
    """Aggregate top-of-book events into right-labeled completed buckets."""

    _require_time_index(event_rows, name="event_rows")
    required = set(QUOTE_STATE_COLUMNS) | set(QUOTE_FLOW_COLUMNS) | {
        "spread",
        "mid",
        "microprice",
        "quote_imbalance",
        "price_changed",
        "size_changed",
    }
    missing = sorted(required - set(event_rows.columns))
    if missing:
        raise ValueError(f"event_rows missing columns: {missing}")
    if cadence_seconds <= 0:
        raise ValueError("cadence_seconds must be positive")

    data = event_rows.copy()
    data["size_only_changed"] = data["size_changed"] & ~data["price_changed"]
    labels = _bucket_labels(data.index, seconds=cadence_seconds)
    grouped = data.groupby(labels, sort=True)
    result = grouped.agg(
        bid_open=("best_bid_price", "first"),
        bid_close=("best_bid_price", "last"),
        bid_qty_open=("best_bid_qty", "first"),
        bid_qty_close=("best_bid_qty", "last"),
        ask_open=("best_ask_price", "first"),
        ask_close=("best_ask_price", "last"),
        ask_qty_open=("best_ask_qty", "first"),
        ask_qty_close=("best_ask_qty", "last"),
        mid_open=("mid", "first"),
        mid_high=("mid", "max"),
        mid_low=("mid", "min"),
        mid_close=("mid", "last"),
        microprice_close=("microprice", "last"),
        quote_imbalance_close=("quote_imbalance", "last"),
        spread_open=("spread", "first"),
        spread_max=("spread", "max"),
        spread_median=("spread", "median"),
        spread_close=("spread", "last"),
        bid_add_qty=("bid_add_qty", "sum"),
        bid_remove_qty=("bid_remove_qty", "sum"),
        ask_add_qty=("ask_add_qty", "sum"),
        ask_remove_qty=("ask_remove_qty", "sum"),
        quote_ofi_qty=("quote_ofi_qty", "sum"),
        quote_update_count=("best_bid_price", "size"),
        quote_price_change_count=("price_changed", "sum"),
        quote_size_only_change_count=("size_only_changed", "sum"),
    )
    result.index = pd.DatetimeIndex(result.index)
    return result


def combine_quote_chunks(
    pieces: list[pd.DataFrame],
) -> pd.DataFrame:
    """Merge independently aggregated chunks, including buckets split at chunk boundaries."""

    if not pieces:
        raise ValueError("at least one quote aggregate piece is required")
    combined = pd.concat(pieces).sort_index()
    if not combined.index.has_duplicates:
        return combined

    flow_sum = list(QUOTE_FLOW_COLUMNS) + [
        "quote_update_count",
        "quote_price_change_count",
        "quote_size_only_change_count",
    ]
    rows: list[dict[str, Any]] = []
    labels: list[pd.Timestamp] = []
    for timestamp, group in combined.groupby(level=0, sort=True):
        ordered = group.reset_index(drop=True)
        row: dict[str, Any] = {
            "bid_open": float(ordered.iloc[0]["bid_open"]),
            "bid_close": float(ordered.iloc[-1]["bid_close"]),
            "bid_qty_open": float(ordered.iloc[0]["bid_qty_open"]),
            "bid_qty_close": float(ordered.iloc[-1]["bid_qty_close"]),
            "ask_open": float(ordered.iloc[0]["ask_open"]),
            "ask_close": float(ordered.iloc[-1]["ask_close"]),
            "ask_qty_open": float(ordered.iloc[0]["ask_qty_open"]),
            "ask_qty_close": float(ordered.iloc[-1]["ask_qty_close"]),
            "mid_open": float(ordered.iloc[0]["mid_open"]),
            "mid_high": float(ordered["mid_high"].max()),
            "mid_low": float(ordered["mid_low"].min()),
            "mid_close": float(ordered.iloc[-1]["mid_close"]),
            "microprice_close": float(ordered.iloc[-1]["microprice_close"]),
            "quote_imbalance_close": float(ordered.iloc[-1]["quote_imbalance_close"]),
            "spread_open": float(ordered.iloc[0]["spread_open"]),
            "spread_max": float(ordered["spread_max"].max()),
            "spread_median": float(
                np.average(
                    ordered["spread_median"],
                    weights=ordered["quote_update_count"],
                )
            ),
            "spread_close": float(ordered.iloc[-1]["spread_close"]),
        }
        for column in flow_sum:
            row[column] = float(ordered[column].sum())
        rows.append(row)
        labels.append(pd.Timestamp(timestamp))
    return pd.DataFrame(rows, index=pd.DatetimeIndex(labels))


def _rolling_quantile(
    series: pd.Series,
    *,
    bars: int,
    minimum: int,
    quantile: float,
) -> pd.Series:
    return series.shift(1).rolling(bars, min_periods=minimum).quantile(quantile)


def build_quote_resiliency_features(
    *,
    trade_bars: pd.DataFrame,
    quote_buckets: pd.DataFrame,
    tick: float,
    config: QuoteResiliencyConfig | None = None,
) -> pd.DataFrame:
    """Join completed streams and compute causal normalized liquidity-supply features."""

    cfg = config or QuoteResiliencyConfig()
    cfg.validate()
    if tick <= 0.0:
        raise ValueError("tick must be positive")
    _require_time_index(trade_bars, name="trade_bars")
    _require_time_index(quote_buckets, name="quote_buckets")
    validate_exact_cadence(trade_bars.index, seconds=cfg.cadence_seconds)
    missing_trade = sorted(set(TRADE_COLUMNS) - set(trade_bars.columns))
    if missing_trade:
        raise ValueError(f"trade_bars missing columns: {missing_trade}")

    quote = quote_buckets.reindex(trade_bars.index)
    state_columns = [
        "bid_open",
        "bid_close",
        "bid_qty_open",
        "bid_qty_close",
        "ask_open",
        "ask_close",
        "ask_qty_open",
        "ask_qty_close",
        "mid_open",
        "mid_high",
        "mid_low",
        "mid_close",
        "microprice_close",
        "quote_imbalance_close",
        "spread_open",
        "spread_max",
        "spread_median",
        "spread_close",
    ]
    missing_quote = sorted(
        set(state_columns)
        | set(QUOTE_FLOW_COLUMNS)
        | {
            "quote_update_count",
            "quote_price_change_count",
            "quote_size_only_change_count",
        }
        - set(quote.columns)
    )
    if missing_quote:
        raise ValueError(f"quote_buckets missing columns: {missing_quote}")
    quote[state_columns] = quote[state_columns].ffill()
    flow_columns = list(QUOTE_FLOW_COLUMNS) + [
        "quote_update_count",
        "quote_price_change_count",
        "quote_size_only_change_count",
    ]
    quote[flow_columns] = quote[flow_columns].fillna(0.0)

    result = trade_bars.loc[:, list(TRADE_COLUMNS)].copy()
    result = result.join(quote)
    result["imbalance"] = result["signed_volume"] / result["volume"].replace(0.0, np.nan)
    result["buy_volume"] = 0.5 * (result["volume"] + result["signed_volume"])
    result["sell_volume"] = 0.5 * (result["volume"] - result["signed_volume"])
    result["aggressive_flow_scale"] = _rolling_quantile(
        result["signed_volume"].abs(),
        bars=cfg.baseline_bars,
        minimum=cfg.minimum_history_bars,
        quantile=cfg.pressure_quantile,
    )
    result["quote_ofi_scale"] = _rolling_quantile(
        result["quote_ofi_qty"].abs(),
        bars=cfg.baseline_bars,
        minimum=cfg.minimum_history_bars,
        quantile=cfg.quote_ofi_quantile,
    )
    result["spread_causal_median"] = result["spread_close"].shift(1).rolling(
        cfg.baseline_bars,
        min_periods=cfg.minimum_history_bars,
    ).median()
    result["aggressive_pressure_ratio"] = (
        result["signed_volume"] / result["aggressive_flow_scale"].replace(0.0, np.nan)
    )
    result["quote_ofi_ratio"] = (
        result["quote_ofi_qty"] / result["quote_ofi_scale"].replace(0.0, np.nan)
    )
    result["spread_median_ratio"] = (
        result["spread_close"] / result["spread_causal_median"].replace(0.0, np.nan)
    )
    result["mid_progress_ticks"] = (result["mid_close"] - result["mid_open"]) / tick
    result["mid_excursion_up_ticks"] = (result["mid_high"] - result["mid_open"]) / tick
    result["mid_excursion_down_ticks"] = (result["mid_open"] - result["mid_low"]) / tick
    result["microprice_skew_ticks"] = (
        result["microprice_close"] - result["mid_close"]
    ) / tick
    result["bid_response_ratio"] = (
        result["bid_add_qty"] + tick * 1e-9
    ) / (result["bid_remove_qty"] + tick * 1e-9)
    result["ask_response_ratio"] = (
        result["ask_add_qty"] + tick * 1e-9
    ) / (result["ask_remove_qty"] + tick * 1e-9)
    result["quote_updates_per_second"] = (
        result["quote_update_count"] / float(cfg.cadence_seconds)
    )
    required_observable = list(TRADE_COLUMNS) + state_columns + [
        "aggressive_pressure_ratio",
        "quote_ofi_ratio",
        "spread_median_ratio",
    ]
    numeric = result[required_observable].apply(pd.to_numeric, errors="coerce")
    result["quote_resiliency_observable"] = np.isfinite(
        numeric.to_numpy(dtype=np.float64)
    ).all(axis=1)
    result.attrs["implementation_revision"] = IMPLEMENTATION_REVISION
    result.attrs["cadence_seconds"] = cfg.cadence_seconds
    return result


__all__ = [
    "IMPLEMENTATION_REVISION",
    "QUOTE_FLOW_COLUMNS",
    "QUOTE_STATE_COLUMNS",
    "QuoteResiliencyConfig",
    "aggregate_quote_events",
    "build_quote_resiliency_features",
    "combine_quote_chunks",
    "quote_event_rows",
    "validate_exact_cadence",
]

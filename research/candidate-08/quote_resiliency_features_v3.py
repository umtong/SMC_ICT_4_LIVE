"""Duplicate-timestamp-safe causal quote resiliency feature contracts.

Binance USD-M ``bookTicker`` can publish several ordered top-of-book updates with the same exchange
transaction millisecond.  Those are distinct queue events and must not be collapsed or rejected.
This revision therefore permits a nondecreasing timezone-aware index for raw quote updates while
retaining the exact, unique ten-second cadence contract for completed feature rows.

The module contains no signal outcome, order, fill, sizing, position, account or PnL logic.
"""

from __future__ import annotations

from math import isfinite
from typing import Any

import numpy as np
import pandas as pd

import quote_resiliency_features as base
import quote_resiliency_features_v2 as v2


IMPLEMENTATION_REVISION = "CAUSAL_QUOTE_RESILIENCY_FEATURES_V4_NATIVE_L1_SOURCE_TIME"
QUOTE_FLOW_COLUMNS = base.QUOTE_FLOW_COLUMNS
QUOTE_STATE_COLUMNS = base.QUOTE_STATE_COLUMNS
TRADE_COLUMNS = base.TRADE_COLUMNS
QuoteResiliencyConfig = base.QuoteResiliencyConfig
validate_exact_cadence = base.validate_exact_cadence


def _require_ordered_raw_time_index(frame: pd.DataFrame, *, name: str) -> None:
    """Require exchange-time order but permit distinct events sharing one timestamp."""

    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise TypeError(f"{name} must use a timezone-aware DatetimeIndex")
    if not frame.index.is_monotonic_increasing:
        raise ValueError(f"{name} timestamps must be nondecreasing")


def quote_event_rows(
    quote_updates: pd.DataFrame,
    *,
    previous_quote: dict[str, float] | None = None,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Convert ordered top-of-book states to economically signed event rows.

    Stable row order is authoritative when several updates share one exchange millisecond.  The
    production loader additionally orders equal timestamps by update id before calling this
    function.  No aggregation or deduplication occurs here.
    """

    _require_ordered_raw_time_index(quote_updates, name="quote_updates")
    quote = base._coerce_quote_state(quote_updates)
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
    result.attrs["raw_order_contract"] = (
        "NONDECREASING_TRANSACTION_TIME_STABLE_EQUAL_TIME_UPDATE_ID_ORDER"
    )
    return result, final


def _bucket_labels(index: pd.DatetimeIndex, *, seconds: int) -> pd.DatetimeIndex:
    return index.floor(f"{seconds}s") + pd.Timedelta(seconds=seconds)


def aggregate_quote_events(
    event_rows: pd.DataFrame,
    *,
    cadence_seconds: int = 10,
) -> pd.DataFrame:
    """Aggregate ordered raw events into right-labeled completed buckets.

    Duplicate raw timestamps are retained as separate events.  Row order inside each bucket is
    stable, so opening and closing state follow exchange update order rather than timestamp
    deduplication.
    """

    _require_ordered_raw_time_index(event_rows, name="event_rows")
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
    # Preserve exact source exchange time as datetime; float nanoseconds lose precision.
    data["quote_event_time"] = data.index
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
        quote_first_event_time=("quote_event_time", "first"),
        quote_last_event_time=("quote_event_time", "last"),
    )
    result.index = pd.DatetimeIndex(result.index)
    result.attrs["raw_duplicate_timestamp_events_preserved"] = True
    return result


def build_quote_resiliency_features(
    *,
    trade_bars: pd.DataFrame,
    quote_buckets: pd.DataFrame,
    tick: float,
    config: QuoteResiliencyConfig | None = None,
) -> pd.DataFrame:
    """Build exact-cadence features using the schema-safe V2 completed-stream join."""

    cfg = config or QuoteResiliencyConfig()
    cfg.validate()
    result = v2.build_quote_resiliency_features(
        trade_bars=trade_bars,
        quote_buckets=quote_buckets,
        tick=tick,
        config=cfg,
    )
    if "quote_last_event_time" not in result.columns:
        result["quote_last_event_ns"] = pd.array([pd.NA] * len(result.index), dtype="Int64")
        result["quote_source_age_ns"] = pd.array([pd.NA] * len(result.index), dtype="Int64")
        result["native_quote_snapshot_observable"] = False
    else:
        last_times = pd.to_datetime(
            result["quote_last_event_time"],
            utc=True,
            errors="coerce",
        )
        exact_last_ns = pd.array(
            [
                pd.NA if pd.isna(value) else int(value.as_unit("ns").value)
                for value in last_times
            ],
            dtype="Int64",
        )
        bucket_ns = result.index.as_unit("ns").asi8
        exact_age_ns = pd.array(
            [
                pd.NA if pd.isna(value) else int(end_ns) - int(value)
                for end_ns, value in zip(bucket_ns, exact_last_ns, strict=True)
            ],
            dtype="Int64",
        )
        result["quote_last_event_ns"] = exact_last_ns
        result["quote_source_age_ns"] = exact_age_ns
        cadence_ns = int(cfg.cadence_seconds) * 1_000_000_000
        valid_age = (
            result["quote_source_age_ns"].notna()
            & (result["quote_source_age_ns"] > 0)
            & (result["quote_source_age_ns"] < cadence_ns)
        )
        valid_state = (
            pd.to_numeric(result["bid_close"], errors="coerce").gt(0.0)
            & pd.to_numeric(result["ask_close"], errors="coerce").gt(0.0)
            & pd.to_numeric(result["bid_qty_close"], errors="coerce").gt(0.0)
            & pd.to_numeric(result["ask_qty_close"], errors="coerce").gt(0.0)
            & pd.to_numeric(result["bid_close"], errors="coerce").le(
                pd.to_numeric(result["ask_close"], errors="coerce")
            )
        )
        result["native_quote_snapshot_observable"] = (valid_age & valid_state).fillna(False)
    result.attrs["implementation_revision"] = IMPLEMENTATION_REVISION
    result.attrs["raw_quote_timestamp_contract"] = (
        "DUPLICATES_ALLOWED_ORDERED_BY_TRANSACTION_TIME_THEN_UPDATE_ID"
    )
    result.attrs["native_quote_snapshot_contract"] = (
        "LAST_SOURCE_EVENT_STRICTLY_INSIDE_COMPLETED_BUCKET"
    )
    return result


__all__ = [
    "IMPLEMENTATION_REVISION",
    "QUOTE_FLOW_COLUMNS",
    "QUOTE_STATE_COLUMNS",
    "QuoteResiliencyConfig",
    "aggregate_quote_events",
    "build_quote_resiliency_features",
    "quote_event_rows",
    "validate_exact_cadence",
]

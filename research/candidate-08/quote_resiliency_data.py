"""Checksum-verified streaming Binance USD-M bookTicker loader for candidate-08.

The loader preserves every ordered top-of-book update, including distinct updates sharing one
exchange transaction millisecond.  It carries the final *raw* open ten-second bucket across pandas
chunks and daily archives before event decomposition and aggregation.  Consequently bucket open,
close, median spread and quote OFI are invariant to parser chunk size.

This module is data infrastructure only.  It creates no scenario outcome, order, fill, position,
account, PnL, sizing or backtest engine.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np
import pandas as pd

import bookticker_data_probe as book_base
import bookticker_data_probe_v2 as book_v2
from data import BinanceDataError, _sha256_file
from quote_resiliency_features_v3 import (
    QUOTE_STATE_COLUMNS,
    aggregate_quote_events,
    quote_event_rows,
)


DATA_REVISION = "BINANCE_USDM_BOOKTICKER_COMPLETED_10S_V1_RAW_BUCKET_CARRY"


@dataclass(frozen=True, slots=True)
class QuoteSource:
    symbol: str
    day: str
    url: str
    checksum_url: str
    sha256: str
    size_bytes: int
    archive_member: str
    valid_rows: int


def _as_utc_timestamp(value: datetime | pd.Timestamp, *, name: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tz is None:
        raise TypeError(f"{name} must be timezone-aware")
    return timestamp.tz_convert("UTC")


def _daily_dates(start: pd.Timestamp, end: pd.Timestamp) -> Iterable[date]:
    cursor = start.date()
    final = (end - pd.Timedelta(nanoseconds=1)).date()
    while cursor <= final:
        yield cursor
        cursor += timedelta(days=1)


def _state_from_raw(row: pd.Series) -> dict[str, float]:
    return {column: float(row[column]) for column in QUOTE_STATE_COLUMNS}


def _coerce_ordered_raw_chunk(
    raw: pd.DataFrame,
    *,
    previous_transaction_ms: int | None,
    previous_update_id: int | None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Validate one archive chunk without silently dropping or reordering observations."""

    missing = sorted(set(book_base.BOOK_TICKER_COLUMNS) - set(raw.columns))
    if missing:
        raise BinanceDataError(f"bookTicker chunk missing columns: {missing}")
    chunk = raw.loc[:, list(book_base.BOOK_TICKER_COLUMNS)].copy()
    for column in book_base.BOOK_TICKER_COLUMNS:
        chunk[column] = pd.to_numeric(chunk[column], errors="coerce")
    invalid = chunk[list(book_base.BOOK_TICKER_COLUMNS)].isna().any(axis=1)
    if invalid.any():
        raise BinanceDataError(
            f"bookTicker chunk contains {int(invalid.sum())} malformed numeric rows"
        )
    if chunk.empty:
        return chunk, {
            "rows": 0,
            "duplicate_transaction_timestamps": 0,
            "duplicate_update_ids": 0,
            "maximum_gap_ms": 0,
            "gaps_over_one_second": 0,
        }

    for column in ("update_id", "transaction_time", "event_time"):
        chunk[column] = chunk[column].astype("int64")
    for column in QUOTE_STATE_COLUMNS:
        chunk[column] = chunk[column].astype("float64")

    if (
        (chunk["best_bid_price"] <= 0.0).any()
        or (chunk["best_ask_price"] <= 0.0).any()
        or (chunk["best_bid_qty"] <= 0.0).any()
        or (chunk["best_ask_qty"] <= 0.0).any()
    ):
        raise BinanceDataError("bookTicker chunk contains nonpositive price or quantity")
    if (chunk["best_bid_price"] > chunk["best_ask_price"]).any():
        raise BinanceDataError("bookTicker chunk contains crossed quote")

    transaction = chunk["transaction_time"].to_numpy(dtype=np.int64, copy=False)
    update_id = chunk["update_id"].to_numpy(dtype=np.int64, copy=False)
    if previous_transaction_ms is None:
        previous_transaction = transaction[:-1]
        current_transaction = transaction[1:]
        previous_updates = update_id[:-1]
        current_updates = update_id[1:]
    else:
        previous_transaction = np.concatenate(
            ([int(previous_transaction_ms)], transaction[:-1])
        )
        current_transaction = transaction
        if previous_update_id is None:
            raise BinanceDataError("previous update id missing for continued stream")
        previous_updates = np.concatenate(([int(previous_update_id)], update_id[:-1]))
        current_updates = update_id

    if current_transaction.size:
        time_delta = current_transaction - previous_transaction
        update_delta = current_updates - previous_updates
        if np.any(time_delta < 0):
            raise BinanceDataError("bookTicker transaction time regressed")
        # The archive row order is authoritative.  Equal exchange milliseconds are ordered by the
        # venue update id; a regression would make their causal sequence ambiguous and is rejected.
        equal_time_update_regression = (time_delta == 0) & (update_delta < 0)
        if np.any(equal_time_update_regression):
            raise BinanceDataError(
                "bookTicker update id regressed within an equal transaction timestamp"
            )
        maximum_gap_ms = int(np.maximum(time_delta, 0).max(initial=0))
        gaps_over_one_second = int(np.count_nonzero(time_delta > 1000))
        duplicate_times = int(np.count_nonzero(time_delta == 0))
        duplicate_updates = int(np.count_nonzero(update_delta == 0))
    else:
        maximum_gap_ms = 0
        gaps_over_one_second = 0
        duplicate_times = 0
        duplicate_updates = 0

    index = pd.to_datetime(chunk["transaction_time"], unit="ms", utc=True)
    chunk.index = pd.DatetimeIndex(index)
    return chunk, {
        "rows": len(chunk.index),
        "duplicate_transaction_timestamps": duplicate_times,
        "duplicate_update_ids": duplicate_updates,
        "maximum_gap_ms": maximum_gap_ms,
        "gaps_over_one_second": gaps_over_one_second,
    }


def aggregate_ordered_raw_quote_chunks(
    chunks: Iterable[pd.DataFrame],
    *,
    cadence_seconds: int = 10,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Aggregate a continuous raw stream with exact open-bucket carry semantics.

    Input chunks must already be numeric, validated and indexed by nondecreasing UTC transaction
    time.  They may contain duplicate timestamps.  This helper is intentionally exposed so the
    parser boundary can be tested without network access.
    """

    if cadence_seconds <= 0:
        raise ValueError("cadence_seconds must be positive")
    pieces: list[pd.DataFrame] = []
    carry: pd.DataFrame | None = None
    previous_quote_before_carry: dict[str, float] | None = None
    input_rows = 0
    emitted_raw_rows = 0

    for chunk in chunks:
        if not isinstance(chunk.index, pd.DatetimeIndex) or chunk.index.tz is None:
            raise TypeError("raw quote chunks must use a timezone-aware DatetimeIndex")
        if not chunk.index.is_monotonic_increasing:
            raise ValueError("raw quote chunk timestamps must be nondecreasing")
        if chunk.empty:
            continue
        input_rows += len(chunk.index)
        combined = chunk if carry is None else pd.concat([carry, chunk])
        if not combined.index.is_monotonic_increasing:
            raise ValueError("raw quote chunks are not globally time ordered")
        labels = combined.index.floor(f"{cadence_seconds}s") + pd.Timedelta(
            seconds=cadence_seconds
        )
        final_label = labels[-1]
        ready_mask = labels < final_label
        ready_raw = combined.loc[ready_mask]
        next_carry = combined.loc[~ready_mask]

        if not ready_raw.empty:
            events, _ = quote_event_rows(
                ready_raw,
                previous_quote=previous_quote_before_carry,
            )
            pieces.append(
                aggregate_quote_events(events, cadence_seconds=cadence_seconds)
            )
            previous_quote_before_carry = _state_from_raw(ready_raw.iloc[-1])
            emitted_raw_rows += len(ready_raw.index)
        carry = next_carry.copy()

    if carry is not None and not carry.empty:
        events, _ = quote_event_rows(
            carry,
            previous_quote=previous_quote_before_carry,
        )
        pieces.append(aggregate_quote_events(events, cadence_seconds=cadence_seconds))
        emitted_raw_rows += len(carry.index)

    if not pieces:
        raise BinanceDataError("no valid quote events were available for aggregation")
    completed = pd.concat(pieces).sort_index()
    if completed.index.has_duplicates:
        # Duplicate completed labels indicate a violated carry contract, not a condition to merge.
        duplicate = completed.index[completed.index.duplicated()][0]
        raise BinanceDataError(f"completed quote bucket emitted twice: {duplicate.isoformat()}")
    quality = {
        "input_rows": input_rows,
        "emitted_raw_rows": emitted_raw_rows,
        "completed_quote_buckets": len(completed.index),
        "first_completed_bucket": completed.index[0].isoformat(),
        "last_completed_bucket": completed.index[-1].isoformat(),
        "raw_open_bucket_carry": True,
        "raw_duplicate_timestamps_preserved": True,
    }
    return completed, quality


def _validated_archive_chunks(
    *,
    path: Path,
    chunksize: int,
    previous_transaction_ms: int | None,
    previous_update_id: int | None,
) -> tuple[str, Iterator[pd.DataFrame], dict[str, Any]]:
    member, raw_chunks = book_v2._read_chunks(path, chunksize=chunksize)
    metrics: dict[str, Any] = {
        "rows": 0,
        "duplicate_transaction_timestamps": 0,
        "duplicate_update_ids": 0,
        "maximum_gap_ms": 0,
        "gaps_over_one_second": 0,
        "last_transaction_ms": previous_transaction_ms,
        "last_update_id": previous_update_id,
    }

    def iterator() -> Iterator[pd.DataFrame]:
        nonlocal metrics
        previous_time = previous_transaction_ms
        previous_update = previous_update_id
        for raw in raw_chunks:
            chunk, chunk_quality = _coerce_ordered_raw_chunk(
                raw,
                previous_transaction_ms=previous_time,
                previous_update_id=previous_update,
            )
            if chunk.empty:
                continue
            metrics["rows"] += int(chunk_quality["rows"])
            metrics["duplicate_transaction_timestamps"] += int(
                chunk_quality["duplicate_transaction_timestamps"]
            )
            metrics["duplicate_update_ids"] += int(chunk_quality["duplicate_update_ids"])
            metrics["maximum_gap_ms"] = max(
                int(metrics["maximum_gap_ms"]),
                int(chunk_quality["maximum_gap_ms"]),
            )
            metrics["gaps_over_one_second"] += int(
                chunk_quality["gaps_over_one_second"]
            )
            previous_time = int(chunk.iloc[-1]["transaction_time"])
            previous_update = int(chunk.iloc[-1]["update_id"])
            metrics["last_transaction_ms"] = previous_time
            metrics["last_update_id"] = previous_update
            yield chunk

    return member, iterator(), metrics


def load_completed_quote_buckets(
    *,
    symbol: str,
    start: datetime | pd.Timestamp,
    end: datetime | pd.Timestamp,
    cache_dir: Path,
    chunksize: int = 500_000,
    cadence_seconds: int = 10,
) -> tuple[pd.DataFrame, tuple[QuoteSource, ...], dict[str, Any]]:
    """Load checksum-verified completed top-of-book buckets for ``(start, end]``."""

    start_ts = _as_utc_timestamp(start, name="start")
    end_ts = _as_utc_timestamp(end, name="end")
    if end_ts <= start_ts:
        raise ValueError("end must be after start")
    if chunksize <= 0:
        raise ValueError("chunksize must be positive")

    sources: list[QuoteSource] = []
    stream_chunks: list[Iterator[pd.DataFrame]] = []
    metric_refs: list[dict[str, Any]] = []
    previous_transaction_ms: int | None = None
    previous_update_id: int | None = None

    # Archives are opened lazily by their iterators, then consumed in chronological day order.
    # The previous id/time contract is propagated after each iterator is exhausted below.
    for day in _daily_dates(start_ts, end_ts):
        path, url, checksum_url = book_base._verified_archive(cache_dir / symbol, symbol, day)
        member, iterator, metrics = _validated_archive_chunks(
            path=path,
            chunksize=chunksize,
            previous_transaction_ms=previous_transaction_ms,
            previous_update_id=previous_update_id,
        )
        day_chunks = list(iterator)
        if not day_chunks:
            raise BinanceDataError(f"no valid bookTicker rows in {path.name}")
        previous_transaction_ms = int(metrics["last_transaction_ms"])
        previous_update_id = int(metrics["last_update_id"])
        stream_chunks.append(iter(day_chunks))
        metric_refs.append(metrics)
        sources.append(
            QuoteSource(
                symbol=symbol,
                day=day.isoformat(),
                url=url,
                checksum_url=checksum_url,
                sha256=_sha256_file(path),
                size_bytes=path.stat().st_size,
                archive_member=member,
                valid_rows=int(metrics["rows"]),
            )
        )

    def flattened() -> Iterator[pd.DataFrame]:
        for iterator in stream_chunks:
            yield from iterator

    completed, aggregate_quality = aggregate_ordered_raw_quote_chunks(
        flattened(),
        cadence_seconds=cadence_seconds,
    )
    selected = completed.loc[
        (completed.index > start_ts) & (completed.index <= end_ts)
    ].copy()
    if selected.empty:
        raise BinanceDataError("no completed quote buckets in requested interval")

    quality = {
        "data_revision": DATA_REVISION,
        "symbol": symbol,
        "requested_start": start_ts.isoformat(),
        "requested_end": end_ts.isoformat(),
        "rows": len(selected.index),
        "first_observed_time": selected.index[0].isoformat(),
        "last_observed_time": selected.index[-1].isoformat(),
        "source_rows": sum(int(metrics["rows"]) for metrics in metric_refs),
        "source_bytes": sum(source.size_bytes for source in sources),
        "duplicate_transaction_timestamps": sum(
            int(metrics["duplicate_transaction_timestamps"]) for metrics in metric_refs
        ),
        "duplicate_update_ids": sum(
            int(metrics["duplicate_update_ids"]) for metrics in metric_refs
        ),
        "maximum_transaction_gap_ms": max(
            (int(metrics["maximum_gap_ms"]) for metrics in metric_refs),
            default=0,
        ),
        "transaction_gaps_over_one_second": sum(
            int(metrics["gaps_over_one_second"]) for metrics in metric_refs
        ),
        "source_manifest": [asdict(source) for source in sources],
        **aggregate_quality,
    }
    return selected, tuple(sources), quality


__all__ = [
    "DATA_REVISION",
    "QuoteSource",
    "aggregate_ordered_raw_quote_chunks",
    "load_completed_quote_buckets",
]

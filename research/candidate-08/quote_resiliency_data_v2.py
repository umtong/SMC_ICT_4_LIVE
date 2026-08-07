"""True streaming revision of the checksum-verified quote bucket loader.

V1 established strict archive, ordering and raw open-bucket carry contracts but materialized each
whole day before aggregation.  V2 fixes only that implementation defect: archive chunks now flow
straight into the bucket carry generator and no daily list is retained in memory.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

import bookticker_data_probe as book_base
import quote_resiliency_data as base
from data import BinanceDataError, _sha256_file


DATA_REVISION = "BINANCE_USDM_BOOKTICKER_COMPLETED_10S_V2_TRUE_STREAMING"
QuoteSource = base.QuoteSource
aggregate_ordered_raw_quote_chunks = base.aggregate_ordered_raw_quote_chunks


def load_completed_quote_buckets(
    *,
    symbol: str,
    start: datetime | pd.Timestamp,
    end: datetime | pd.Timestamp,
    cache_dir: Path,
    chunksize: int = 500_000,
    cadence_seconds: int = 10,
) -> tuple[pd.DataFrame, tuple[QuoteSource, ...], dict[str, Any]]:
    """Load checksum-verified completed top-of-book buckets for ``(start, end]``.

    At most the current parser chunk plus one unfinished raw ten-second bucket is retained by this
    layer.  Source manifests are appended only after the corresponding archive iterator closes.
    """

    start_ts = base._as_utc_timestamp(start, name="start")
    end_ts = base._as_utc_timestamp(end, name="end")
    if end_ts <= start_ts:
        raise ValueError("end must be after start")
    if chunksize <= 0:
        raise ValueError("chunksize must be positive")
    if cadence_seconds <= 0:
        raise ValueError("cadence_seconds must be positive")

    sources: list[QuoteSource] = []
    metric_refs: list[dict[str, Any]] = []
    previous_transaction_ms: int | None = None
    previous_update_id: int | None = None

    def flattened() -> Iterator[pd.DataFrame]:
        nonlocal previous_transaction_ms, previous_update_id
        for day in base._daily_dates(start_ts, end_ts):
            path, url, checksum_url = book_base._verified_archive(
                cache_dir / symbol,
                symbol,
                day,
            )
            member, iterator, metrics = base._validated_archive_chunks(
                path=path,
                chunksize=chunksize,
                previous_transaction_ms=previous_transaction_ms,
                previous_update_id=previous_update_id,
            )
            yielded = False
            for chunk in iterator:
                yielded = True
                yield chunk
            if not yielded:
                raise BinanceDataError(f"no valid bookTicker rows in {path.name}")
            previous_transaction_ms = int(metrics["last_transaction_ms"])
            previous_update_id = int(metrics["last_update_id"])
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
        "memory_contract": "ONE_PARSER_CHUNK_PLUS_ONE_RAW_OPEN_BUCKET",
        **aggregate_quality,
    }
    return selected, tuple(sources), quality


__all__ = [
    "DATA_REVISION",
    "QuoteSource",
    "aggregate_ordered_raw_quote_chunks",
    "load_completed_quote_buckets",
]

"""Checksum-recorded causal L1 alignment cache for candidate 10."""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Iterable, Iterator

from c10_l1_data import ALIGNMENT_RECORD
from c10_l1_data import ALIGNMENT_SCHEMA_VERSION
from c10_l1_data import _archive_by_date
from c10_l1_data import _iter_raw_quotes
from c10_l1_data import _iter_raw_trades
from c10_l1_data import _sha256_file
from c10_l1_data import _source_sha_by_date
from c10_l1_data import align_latest_known_quotes

def _cache_is_reusable(
    cache_path: Path,
    metadata_path: Path,
    *,
    source_book_sha256: str,
    source_trade_sha256: str,
) -> dict[str, Any] | None:
    if not cache_path.exists() or not metadata_path.exists():
        return None
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    expected_size = int(metadata.get("record_count", -1)) * ALIGNMENT_RECORD.size
    if (
        int(metadata.get("schema_version", -1)) != ALIGNMENT_SCHEMA_VERSION
        or int(metadata.get("record_size", -1)) != ALIGNMENT_RECORD.size
        or str(metadata.get("source_book_sha256")) != source_book_sha256
        or str(metadata.get("source_trade_sha256")) != source_trade_sha256
        or cache_path.stat().st_size != expected_size
    ):
        return None
    if _sha256_file(cache_path) != str(metadata.get("cache_sha256")):
        return None
    metadata["cache_reused"] = True
    return metadata


def prepare_alignment_day(
    *,
    day: str,
    book_archive: Path,
    trade_archive: Path,
    cache_directory: Path,
    source_book_sha256: str,
    source_trade_sha256: str,
) -> tuple[Path, Path, dict[str, Any]]:
    cache_directory.mkdir(parents=True, exist_ok=True)
    cache_path = cache_directory / f"BTCUSDT-l1-aligned-{day}.bin"
    metadata_path = cache_directory / f"BTCUSDT-l1-aligned-{day}.json"
    reusable = _cache_is_reusable(
        cache_path,
        metadata_path,
        source_book_sha256=source_book_sha256,
        source_trade_sha256=source_trade_sha256,
    )
    if reusable is not None:
        return cache_path, metadata_path, reusable

    diagnostics: Counter[str] = Counter()
    temporary = cache_path.with_suffix(cache_path.suffix + ".partial")
    if temporary.exists():
        temporary.unlink()
    digest = sha256()
    record_count = 0
    quote_attached_count = 0
    first_trade_id: int | None = None
    last_trade_id: int | None = None
    first_trade_ts: int | None = None
    last_trade_ts: int | None = None
    lag_sum_ns = 0
    lag_max_ns = 0
    selected_nonpositive_spreads = 0

    quotes = _iter_raw_quotes(book_archive, diagnostics)
    trades = _iter_raw_trades(trade_archive, diagnostics)
    with temporary.open("wb") as stream:
        for aligned in align_latest_known_quotes(quotes, trades):
            trade = aligned.trade
            quote = aligned.quote
            first_trade_id = trade.trade_id if first_trade_id is None else first_trade_id
            last_trade_id = trade.trade_id
            first_trade_ts = trade.ts_ns if first_trade_ts is None else first_trade_ts
            last_trade_ts = trade.ts_ns
            if quote is None:
                diagnostics["trades_without_prior_same_day_quote"] += 1
                values = (
                    trade.trade_id,
                    trade.ts_ns,
                    float(trade.price),
                    float(trade.quantity),
                    trade.aggressor,
                    -1,
                    -1,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                )
            else:
                if quote.ts_ns > trade.ts_ns:
                    diagnostics["future_quote_violations"] += 1
                    raise RuntimeError(
                        f"future quote used on {day}: {quote.ts_ns} > {trade.ts_ns}",
                    )
                bid = float(quote.bid)
                ask = float(quote.ask)
                if ask <= bid:
                    selected_nonpositive_spreads += 1
                lag = trade.ts_ns - quote.ts_ns
                lag_sum_ns += lag
                lag_max_ns = max(lag_max_ns, lag)
                quote_attached_count += 1
                values = (
                    trade.trade_id,
                    trade.ts_ns,
                    float(trade.price),
                    float(trade.quantity),
                    trade.aggressor,
                    quote.update_id,
                    quote.ts_ns,
                    bid,
                    float(quote.bid_size),
                    ask,
                    float(quote.ask_size),
                )
            packed = ALIGNMENT_RECORD.pack(*values)
            stream.write(packed)
            digest.update(packed)
            record_count += 1

        # Exhaust the quote iterator so schema/order integrity covers the full archive.
        for _ in quotes:
            pass

    integrity_keys = (
        "duplicate_quote_update_ids",
        "nonmonotonic_quote_update_ids",
        "nonmonotonic_quote_event_times",
        "duplicate_trade_ids",
        "nonmonotonic_trade_ids",
        "nonmonotonic_trade_times",
        "future_quote_violations",
    )
    integrity_errors = {key: diagnostics[key] for key in integrity_keys if diagnostics[key]}
    if integrity_errors or selected_nonpositive_spreads:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(
            f"L1 alignment integrity failed for {day}: "
            f"errors={integrity_errors}, selected_nonpositive_spreads="
            f"{selected_nonpositive_spreads}",
        )
    os.replace(temporary, cache_path)
    metadata = {
        "schema_version": ALIGNMENT_SCHEMA_VERSION,
        "record_size": ALIGNMENT_RECORD.size,
        "record_count": record_count,
        "cache_sha256": digest.hexdigest(),
        "cache_bytes": cache_path.stat().st_size,
        "cache_reused": False,
        "date": day,
        "source_book_archive": book_archive.name,
        "source_book_sha256": source_book_sha256,
        "source_trade_archive": trade_archive.name,
        "source_trade_sha256": source_trade_sha256,
        "first_trade_id": first_trade_id,
        "last_trade_id": last_trade_id,
        "first_trade_ts_ns": first_trade_ts,
        "last_trade_ts_ns": last_trade_ts,
        "quote_attached_count": quote_attached_count,
        "trades_without_prior_same_day_quote": diagnostics[
            "trades_without_prior_same_day_quote"
        ],
        "quote_rows_scanned": diagnostics["quote_rows"],
        "trade_rows_scanned": diagnostics["trade_rows"],
        "future_quote_violation_count": diagnostics["future_quote_violations"],
        "selected_nonpositive_spread_count": selected_nonpositive_spreads,
        "mean_quote_age_ns": (
            lag_sum_ns / quote_attached_count if quote_attached_count else None
        ),
        "max_quote_age_ns": lag_max_ns,
        "quote_timestamp_semantics": "bookTicker event_time",
        "trade_timestamp_semantics": "aggregate-trade transact_time",
        "alignment_rule": "latest quote event_time <= trade transact_time",
    }
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return cache_path, metadata_path, metadata


def prepare_alignment_week(
    *,
    week_start: date,
    book_paths: Iterable[Path],
    trade_paths: Iterable[Path],
    book_metadata: dict[str, Any],
    trade_metadata: dict[str, Any],
    cache_directory: Path,
    warmup_days: int = 1,
) -> tuple[list[Path], list[Path], list[dict[str, Any]]]:
    books = _archive_by_date(book_paths, "bookTicker")
    trades = _archive_by_date(trade_paths, "aggTrades")
    book_shas = _source_sha_by_date(book_metadata)
    trade_shas = _source_sha_by_date(trade_metadata)
    first = week_start - timedelta(days=warmup_days)
    last_exclusive = week_start + timedelta(days=7)
    cache_paths: list[Path] = []
    metadata_paths: list[Path] = []
    reports: list[dict[str, Any]] = []
    cursor = first
    while cursor < last_exclusive:
        day = cursor.isoformat()
        if day not in books or day not in trades:
            raise RuntimeError(f"missing L1 source archive for {day}")
        cache_path, metadata_path, report = prepare_alignment_day(
            day=day,
            book_archive=books[day],
            trade_archive=trades[day],
            cache_directory=cache_directory,
            source_book_sha256=book_shas[day],
            source_trade_sha256=trade_shas[day],
        )
        cache_paths.append(cache_path)
        metadata_paths.append(metadata_path)
        reports.append(report)
        cursor += timedelta(days=1)
    return cache_paths, metadata_paths, reports


def iter_alignment_records(path: Path) -> Iterator[tuple[Any, ...]]:
    block_records = 16_384
    with path.open("rb") as stream:
        while True:
            block = stream.read(ALIGNMENT_RECORD.size * block_records)
            if not block:
                break
            if len(block) % ALIGNMENT_RECORD.size:
                raise RuntimeError(f"truncated alignment cache: {path}")
            for offset in range(0, len(block), ALIGNMENT_RECORD.size):
                yield ALIGNMENT_RECORD.unpack_from(block, offset)

def _summarize_alignment(reports: list[dict[str, Any]]) -> dict[str, Any]:
    record_count = sum(int(item["record_count"]) for item in reports)
    quote_attached = sum(int(item["quote_attached_count"]) for item in reports)
    no_quote = sum(
        int(item["trades_without_prior_same_day_quote"]) for item in reports
    )
    weighted_age = sum(
        (float(item["mean_quote_age_ns"]) if item["mean_quote_age_ns"] is not None else 0.0)
        * int(item["quote_attached_count"])
        for item in reports
    )
    return {
        "tick_count": record_count,
        "record_count": record_count,
        "quote_attached_count": quote_attached,
        "trades_without_prior_same_day_quote": no_quote,
        "future_quote_violation_count": sum(
            int(item["future_quote_violation_count"]) for item in reports
        ),
        "selected_nonpositive_spread_count": sum(
            int(item["selected_nonpositive_spread_count"]) for item in reports
        ),
        "mean_quote_age_ns": weighted_age / quote_attached if quote_attached else None,
        "max_quote_age_ns": max(
            (int(item["max_quote_age_ns"]) for item in reports),
            default=0,
        ),
        "cache_reused_days": sum(bool(item["cache_reused"]) for item in reports),
        "cache_bytes": sum(int(item["cache_bytes"]) for item in reports),
        "days": reports,
        "aggressor_mapping": {
            "is_buyer_maker=false": "BUYER",
            "is_buyer_maker=true": "SELLER",
        },
        "precision_normalization": {
            "price_precision": 1,
            "size_precision": 3,
            "value_changed": False,
            "representation_only": True,
        },
        "timestamp_semantics": (
            "TradeTick ts_event=aggregate-trade transact_time; QuoteTick "
            "ts_event=bookTicker event_time and ts_init=first trade observing it"
        ),
    }

__all__ = [
    "_summarize_alignment",
    "iter_alignment_records",
    "prepare_alignment_day",
    "prepare_alignment_week",
]

"""Checksum-verified Binance USD-M bookTicker availability and integrity probe.

This is a data-contract probe only.  It does not generate signals, orders, fills, PnL or a custom
backtest.  A single predeclared BTC day is streamed from the official Binance Vision archive to
establish the exact schema, timestamp ordering, top-of-book validity, event density and source size
before a quote-resiliency scenario is designed.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from typing import Any, Iterator
import zipfile

import pandas as pd

from data import BinanceDataError, _download, _sha256_file
from smc_ict_4.manifest import write_json_atomic


BOOK_TICKER_COLUMNS = (
    "update_id",
    "best_bid_price",
    "best_bid_qty",
    "best_ask_price",
    "best_ask_qty",
    "transaction_time",
    "event_time",
)
SCHEMA_ALIASES = {
    "u": "update_id",
    "update_id": "update_id",
    "updateid": "update_id",
    "b": "best_bid_price",
    "best_bid_price": "best_bid_price",
    "bestbidprice": "best_bid_price",
    "B": "best_bid_qty",
    "best_bid_qty": "best_bid_qty",
    "bestbidqty": "best_bid_qty",
    "a": "best_ask_price",
    "best_ask_price": "best_ask_price",
    "bestaskprice": "best_ask_price",
    "A": "best_ask_qty",
    "best_ask_qty": "best_ask_qty",
    "bestaskqty": "best_ask_qty",
    "T": "transaction_time",
    "transaction_time": "transaction_time",
    "transactiontime": "transaction_time",
    "E": "event_time",
    "event_time": "event_time",
    "eventtime": "event_time",
}


@dataclass(frozen=True, slots=True)
class SourceManifest:
    symbol: str
    day: str
    url: str
    checksum_url: str
    sha256: str
    size_bytes: int
    archive_member: str


def _source_urls(symbol: str, day: date) -> tuple[str, str, str]:
    day_text = day.isoformat()
    filename = f"{symbol}-bookTicker-{day_text}.zip"
    base = f"https://data.binance.vision/data/futures/um/daily/bookTicker/{symbol}"
    url = f"{base}/{filename}"
    return filename, url, f"{url}.CHECKSUM"


def _verified_archive(cache_dir: Path, symbol: str, day: date) -> tuple[Path, str, str]:
    filename, url, checksum_url = _source_urls(symbol, day)
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / filename
    checksum_text = _download(checksum_url, timeout=180).decode("utf-8").strip()
    expected = checksum_text.split()[0].lower()
    if len(expected) != 64:
        raise BinanceDataError(f"invalid checksum payload for {filename}: {checksum_text!r}")
    if destination.exists() and _sha256_file(destination) != expected:
        destination.unlink()
    if not destination.exists():
        payload = _download(url, timeout=600)
        actual = sha256(payload).hexdigest()
        if actual != expected:
            raise BinanceDataError(f"SHA-256 mismatch for {filename}: {actual} != {expected}")
        temporary = destination.with_suffix(".zip.tmp")
        temporary.write_bytes(payload)
        temporary.replace(destination)
    if _sha256_file(destination) != expected:
        raise BinanceDataError(f"cached SHA-256 mismatch for {filename}")
    return destination, url, checksum_url


def _normalise_header(values: list[Any]) -> tuple[str, ...] | None:
    names: list[str] = []
    for value in values:
        raw = str(value).strip()
        key = raw if raw in SCHEMA_ALIASES else raw.lower().replace(" ", "").replace("-", "_")
        mapped = SCHEMA_ALIASES.get(key)
        if mapped is None:
            return None
        names.append(mapped)
    result = tuple(names)
    return result if set(result) == set(BOOK_TICKER_COLUMNS) else None


def _read_chunks(path: Path, *, chunksize: int = 500_000) -> tuple[str, Iterator[pd.DataFrame]]:
    archive = zipfile.ZipFile(path)
    members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
    if len(members) != 1:
        archive.close()
        raise BinanceDataError(f"expected one CSV in {path.name}, found {members}")
    handle = archive.open(members[0])
    reader = pd.read_csv(handle, header=None, chunksize=chunksize, low_memory=False)

    def iterator() -> Iterator[pd.DataFrame]:
        first = True
        try:
            for raw in reader:
                if raw.shape[1] < len(BOOK_TICKER_COLUMNS):
                    raise BinanceDataError(
                        f"bookTicker file exposed {raw.shape[1]} columns; expected at least 7"
                    )
                raw = raw.iloc[:, : len(BOOK_TICKER_COLUMNS)].copy()
                if first:
                    first = False
                    header = _normalise_header(raw.iloc[0].tolist())
                    if header is not None:
                        raw = raw.iloc[1:].copy()
                        raw.columns = header
                    else:
                        raw.columns = BOOK_TICKER_COLUMNS
                else:
                    raw.columns = BOOK_TICKER_COLUMNS
                if not raw.empty:
                    yield raw
        finally:
            handle.close()
            archive.close()

    return members[0], iterator()


def probe_bookticker(*, symbol: str, day: date, cache_dir: Path) -> dict[str, Any]:
    path, url, checksum_url = _verified_archive(cache_dir, symbol, day)
    member, chunks = _read_chunks(path)
    rows = 0
    invalid_numeric = 0
    crossed_quotes = 0
    nonpositive_qty = 0
    duplicate_update_ids = 0
    nonmonotonic_update_ids = 0
    nonmonotonic_transaction_times = 0
    event_before_transaction = 0
    price_change_events = 0
    size_only_change_events = 0
    unchanged_events = 0
    first_rows: list[dict[str, Any]] = []
    first_transaction_ms: int | None = None
    last_transaction_ms: int | None = None
    maximum_gap_ms = 0
    gaps_over_one_second = 0
    spreads: list[pd.Series] = []
    previous: dict[str, float | int] | None = None
    seen_update_ids: set[int] = set()

    numeric_columns = BOOK_TICKER_COLUMNS
    for chunk in chunks:
        for column in numeric_columns:
            chunk[column] = pd.to_numeric(chunk[column], errors="coerce")
        invalid_mask = chunk[list(numeric_columns)].isna().any(axis=1)
        invalid_numeric += int(invalid_mask.sum())
        chunk = chunk.loc[~invalid_mask].copy()
        if chunk.empty:
            continue
        for column in ("update_id", "transaction_time", "event_time"):
            chunk[column] = chunk[column].astype("int64")
        for column in (
            "best_bid_price",
            "best_bid_qty",
            "best_ask_price",
            "best_ask_qty",
        ):
            chunk[column] = chunk[column].astype("float64")

        crossed_quotes += int((chunk["best_bid_price"] > chunk["best_ask_price"]).sum())
        nonpositive_qty += int(
            ((chunk["best_bid_qty"] <= 0.0) | (chunk["best_ask_qty"] <= 0.0)).sum()
        )
        event_before_transaction += int(
            (chunk["event_time"] < chunk["transaction_time"]).sum()
        )
        spreads.append(chunk["best_ask_price"] - chunk["best_bid_price"])

        for record in chunk.to_dict("records"):
            update_id = int(record["update_id"])
            transaction_ms = int(record["transaction_time"])
            if update_id in seen_update_ids:
                duplicate_update_ids += 1
            seen_update_ids.add(update_id)
            if previous is not None:
                if update_id < int(previous["update_id"]):
                    nonmonotonic_update_ids += 1
                previous_ms = int(previous["transaction_time"])
                if transaction_ms < previous_ms:
                    nonmonotonic_transaction_times += 1
                gap_ms = max(0, transaction_ms - previous_ms)
                maximum_gap_ms = max(maximum_gap_ms, gap_ms)
                if gap_ms > 1000:
                    gaps_over_one_second += 1
                price_changed = (
                    float(record["best_bid_price"]) != float(previous["best_bid_price"])
                    or float(record["best_ask_price"]) != float(previous["best_ask_price"])
                )
                size_changed = (
                    float(record["best_bid_qty"]) != float(previous["best_bid_qty"])
                    or float(record["best_ask_qty"]) != float(previous["best_ask_qty"])
                )
                if price_changed:
                    price_change_events += 1
                elif size_changed:
                    size_only_change_events += 1
                else:
                    unchanged_events += 1
            previous = record
            if first_transaction_ms is None:
                first_transaction_ms = transaction_ms
            last_transaction_ms = transaction_ms
            if len(first_rows) < 5:
                first_rows.append(
                    {
                        key: (int(value) if key in {"update_id", "transaction_time", "event_time"} else float(value))
                        for key, value in record.items()
                    }
                )
        rows += len(chunk.index)

    if rows == 0 or previous is None or first_transaction_ms is None or last_transaction_ms is None:
        raise BinanceDataError(f"no valid bookTicker rows in {path.name}")
    spread = pd.concat(spreads, ignore_index=True)
    finite_spread = spread[spread.map(isfinite)]
    if finite_spread.empty:
        raise BinanceDataError(f"no finite bid-ask spreads in {path.name}")

    manifest = SourceManifest(
        symbol=symbol,
        day=day.isoformat(),
        url=url,
        checksum_url=checksum_url,
        sha256=_sha256_file(path),
        size_bytes=path.stat().st_size,
        archive_member=member,
    )
    return {
        "probe_revision": "BINANCE_USDM_BOOKTICKER_DATA_CONTRACT_V1",
        "source": asdict(manifest),
        "schema": list(BOOK_TICKER_COLUMNS),
        "first_rows": first_rows,
        "quality": {
            "rows": rows,
            "invalid_numeric_rows": invalid_numeric,
            "crossed_quote_rows": crossed_quotes,
            "nonpositive_quantity_rows": nonpositive_qty,
            "duplicate_update_id_rows": duplicate_update_ids,
            "nonmonotonic_update_id_rows": nonmonotonic_update_ids,
            "nonmonotonic_transaction_time_rows": nonmonotonic_transaction_times,
            "event_time_before_transaction_time_rows": event_before_transaction,
            "first_transaction_time": pd.Timestamp(first_transaction_ms, unit="ms", tz="UTC").isoformat(),
            "last_transaction_time": pd.Timestamp(last_transaction_ms, unit="ms", tz="UTC").isoformat(),
            "maximum_transaction_gap_ms": maximum_gap_ms,
            "transaction_gaps_over_one_second": gaps_over_one_second,
        },
        "top_of_book_event_composition": {
            "price_change_events": price_change_events,
            "size_only_change_events": size_only_change_events,
            "unchanged_events": unchanged_events,
        },
        "spread": {
            "minimum": float(finite_spread.min()),
            "median": float(finite_spread.median()),
            "q90": float(finite_spread.quantile(0.90)),
            "q99": float(finite_spread.quantile(0.99)),
            "maximum": float(finite_spread.max()),
        },
        "usable_for_quote_resiliency_research": bool(
            invalid_numeric == 0
            and crossed_quotes == 0
            and nonpositive_qty == 0
            and nonmonotonic_transaction_times == 0
            and price_change_events + size_only_change_events > 0
        ),
        "limitations": [
            "Top-of-book only; no passive order identity or deeper queue reconstruction.",
            "Exchange transaction/event timestamps do not provide collector receive latency.",
            "A bookTicker update can reflect additions, cancellations, executions, or price-level replacement; causal interpretation must combine it with aggTrades.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--day", type=date.fromisoformat, default=date(2024, 4, 8))
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = probe_bookticker(
        symbol=args.symbol,
        day=args.day,
        cache_dir=args.cache.resolve(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(args.output.resolve(), result)
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    if not result["usable_for_quote_resiliency_research"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

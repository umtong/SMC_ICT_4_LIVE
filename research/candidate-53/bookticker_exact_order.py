"""Exact chronological reconstruction for preserved Binance bookTicker archives.

Some official daily ZIPs contain large out-of-order blocks.  This module does
not guess a lateness tolerance.  It performs an external merge sort per daily
archive by (observed timestamp, transaction timestamp, original row sequence),
keeping memory bounded and preserving every original quote event.
"""
from __future__ import annotations

import heapq
from pathlib import Path
import tempfile

import bookticker_source_v3 as source

CHUNK_ROWS = 200_000


def _record_from_row(row, sequence: int):
    if not row or not row[0] or not row[0][0].isdigit():
        return None
    if len(row) < 7:
        raise ValueError("bookTicker row too short")
    transaction_ns = source.normalize_timestamp_ns(int(row[5]))
    observed_ns = max(source.normalize_timestamp_ns(int(row[6])), transaction_ns)
    return (
        observed_ns,
        transaction_ns,
        sequence,
        int(row[0]),
        float(row[1]),
        float(row[2]),
        float(row[3]),
        float(row[4]),
    )


def _write_chunk(path: Path, rows) -> None:
    rows.sort(key=lambda item: (item[0], item[1], item[2]))
    with path.open("w", encoding="ascii", newline="") as stream:
        for obs, txn, seq, update_id, bid, bid_qty, ask, ask_qty in rows:
            stream.write(
                f"{obs}\t{txn}\t{seq}\t{update_id}\t"
                f"{bid:.17g}\t{bid_qty:.17g}\t{ask:.17g}\t{ask_qty:.17g}\n"
            )


def _read_sorted_chunk(path: Path):
    with path.open("r", encoding="ascii") as stream:
        for line in stream:
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 8:
                raise ValueError(f"invalid external-sort row in {path}")
            obs = int(parts[0]); txn = int(parts[1]); seq = int(parts[2])
            yield (
                obs, txn, seq, int(parts[3]),
                float(parts[4]), float(parts[5]), float(parts[6]), float(parts[7]),
            )


def _merge_chunks(paths):
    iterators = [iter(_read_sorted_chunk(path)) for path in paths]
    heap = []
    for chunk_index, iterator in enumerate(iterators):
        try:
            record = next(iterator)
        except StopIteration:
            continue
        heapq.heappush(heap, ((record[0], record[1], record[2]), chunk_index, record))
    while heap:
        _, chunk_index, record = heapq.heappop(heap)
        yield record
        try:
            following = next(iterators[chunk_index])
        except StopIteration:
            continue
        heapq.heappush(heap, ((following[0], following[1], following[2]), chunk_index, following))


def iter_book_ticker_paths_exact(paths):
    previous_observed = -1
    for path in sorted(paths, key=lambda item: item.name):
        with tempfile.TemporaryDirectory(prefix="c53-bookticker-sort-") as temporary:
            directory = Path(temporary)
            chunk_paths = []
            chunk = []
            sequence = 0
            archive, reader = source.one_csv_reader(path)
            try:
                for row in reader:
                    record = _record_from_row(row, sequence)
                    sequence += 1
                    if record is None:
                        continue
                    chunk.append(record)
                    if len(chunk) >= CHUNK_ROWS:
                        chunk_path = directory / f"chunk-{len(chunk_paths):05d}.tsv"
                        _write_chunk(chunk_path, chunk)
                        chunk_paths.append(chunk_path)
                        chunk = []
                if chunk:
                    chunk_path = directory / f"chunk-{len(chunk_paths):05d}.tsv"
                    _write_chunk(chunk_path, chunk)
                    chunk_paths.append(chunk_path)
            finally:
                archive.close()

            for obs, txn, _, update_id, bid, bid_qty, ask, ask_qty in _merge_chunks(chunk_paths):
                if obs < previous_observed:
                    raise ValueError(
                        f"bookTicker timestamp escaped its daily archive in {path}: "
                        f"{obs} < {previous_observed}",
                    )
                previous_observed = obs
                yield (update_id, bid, bid_qty, ask, ask_qty, txn, obs)
